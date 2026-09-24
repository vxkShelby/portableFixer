"""The static swap script (portablefix/update_swap_script.py), run for real.

Each test stages a release-shaped zip with the same stage_update the app
uses, writes the job with write_swap_job, and runs the script through
PowerShell: Windows PowerShell 5.1 on the Windows CI runner, pwsh elsewhere
(PORTABLEFIX_TEST_PWSH or PATH). Nothing is stubbed except where a test says
so - the job's small MaxWaitSec/RenameTries values keep the retry loops short.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from portablefix import update_swap
from portablefix.update_swap import stage_update, write_swap_job
from portablefix.update_swap_script import SWAP_SCRIPT
from update_fixtures import make_install, release_files, sums_for, write_release_zip

_FAST_JOB = dict(max_wait_sec=10, rename_tries=2, rename_delay_ms=10, poll_ms=50, sums_tries=2, sums_delay_ms=10)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Nothing in these paths may ever be parsed as code: typographic quotes that
# PowerShell treats as string delimiters, a subexpression, wildcards and
# non-ASCII letters.
_HOSTILE_INSTALL = "Jano\u2019s $(Set-Content INJ.txt x) \u201eq\u201d [2024] \u013e\u0161\u010d\u0165"
_HOSTILE_TEMP = "O\u2019Neil [x] $(Set-Content INJ.txt x) \u017e"


def _powershell_or_skip() -> str:
    """Windows PowerShell where it exists, else PowerShell 7 (pwsh) - which
    lets the swap script be parsed and actually run on a non-Windows dev box
    too. Skips when neither is installed."""
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _parse_errors(script: str) -> list[str]:
    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command",
         "$e = $null; [System.Management.Automation.Language.Parser]::ParseInput($env:PFCMD, [ref]$null, [ref]$e) | Out-Null; "
         "if ($e.Count) { $e | ForEach-Object { Write-Output ('ERR ' + $_.Message) } } else { Write-Output PARSE_OK }"],
        env=env, capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
    )
    assert "PARSE_OK" in result.stdout or "ERR" in result.stdout, result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("ERR")]


def _relaunch_probe() -> bytes:
    """The staged 'exe': something that can really be started, so the
    relaunch is observed rather than stubbed. On Windows a copy of
    whoami.exe (it just prints an error for --post-update); elsewhere a
    shell script that records its arguments and working directory."""
    if sys.platform == "win32":
        return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe").read_bytes()
    return b'#!/bin/sh\nd="$(dirname "$0")/.."\necho "$@" > "$d/relaunched.txt"\npwd >> "$d/relaunched.txt"\n'


_STAND_INS: list[subprocess.Popen] = []
# Plays the running app: alive when the script pins it, gone right after the
# script's 'ready' marker - the order the real hand-off guarantees.
_STAND_IN_CODE = (
    "import glob, os, sys, time\n"
    "deadline = time.monotonic() + 120\n"
    "while not glob.glob(os.path.join(glob.escape(sys.argv[1]), '*.marker')) and time.monotonic() < deadline:\n"
    "    time.sleep(0.02)\n"
)


@pytest.fixture(autouse=True)
def _reap_stand_ins():
    yield
    while _STAND_INS:
        proc = _STAND_INS.pop()
        proc.kill()
        proc.wait()


def _stand_in_app(log_dir: Path) -> int:
    proc = subprocess.Popen([sys.executable, "-c", _STAND_IN_CODE, str(log_dir)])
    _STAND_INS.append(proc)
    # Reaped as soon as it exits: on Linux an unreaped child stays a zombie,
    # which pwsh's HasExited reports as still running.
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc.pid


def _prepare(tmp_path: Path, *, install_parent: str = "", log_name: str = "temp", files=None, pids=None, **job_options):
    install_dir = tmp_path / install_parent / "PF" if install_parent else tmp_path / "install"
    make_install(install_dir)
    if files is None:
        files = release_files(exe=_relaunch_probe())
    zip_path = write_release_zip(tmp_path / "dl" / "PortableFix-update.zip", files)
    staged = stage_update(zip_path, install_dir)
    exe = staged.stage_root / "App" / "PortableFix.exe"
    exe.chmod(0o755)
    log_dir = tmp_path / log_name / "PortableFixUpdate"
    if pids is None:
        pids = [_stand_in_app(log_dir)]
    job = write_swap_job(staged, install_dir, list(pids), log_dir, **{**_FAST_JOB, **job_options})
    return install_dir, staged, job


# Where an override of the script's own functions goes: straight after
# Remove-WithRetry, so it replaces the definition the script just made (a
# prepended function would be redefined by the script itself).
_OVERRIDE_POINT = "\n# Puts back any X.old whose live X is missing"


def _undeletable(*patterns: str) -> str:
    """Override making every tree whose path matches one of the -like
    patterns undeletable (AV holding it), the rest deleted for real."""
    cond = " -or ".join(f"($Path -like '{p}')" for p in patterns)
    return (
        "\n${function:Remove-TreeNoFollowReal} = ${function:Remove-TreeNoFollow}\n"
        f"function Remove-TreeNoFollow([string]$Path) {{ if ({cond}) {{ return 1 }}; Remove-TreeNoFollowReal $Path }}\n"
    )


def _run(job, tmp_path: Path, *, prepend: str = "", override: str = "", timeout: int = 120) -> subprocess.CompletedProcess:
    if prepend or override:
        assert SWAP_SCRIPT.count(_OVERRIDE_POINT) == 1
        script = SWAP_SCRIPT.replace(_OVERRIDE_POINT, override + _OVERRIDE_POINT)
        job.script_path.write_text(prepend + script, encoding="utf-8-sig")
    return subprocess.run(_argv(job), capture_output=True, text=True, timeout=timeout, cwd=str(tmp_path), creationflags=_NO_WINDOW)


def _argv(job) -> list[str]:
    # The app's own command line (short -File path included), only with the
    # PowerShell this test run found.
    return [_powershell_or_skip()] + update_swap.swap_argv(job.script_path)[1:]


def _log(job) -> str:
    try:
        return job.log_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _status(install_dir: Path, job) -> str | None:
    path = install_dir / "Data" / "update_status.txt"
    status = path.read_text(encoding="utf-8-sig").strip() if path.exists() else None
    if status != update_swap.UPDATE_STATUS_OK:
        # The script's own log is the only record of *why* - print it so a
        # failing assert shows it (e.g. on the CI runner's PowerShell 5.1).
        print(f"--- {job.log_file}\n{_log(job)}")
    return status


def _marker(job) -> str:
    return job.marker_path.read_text(encoding="ascii").strip() if job.marker_path.exists() else ""


def _assert_untouched(install_dir: Path) -> None:
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Modules" / "mod.yaml").read_bytes() == b"old-m"
    assert (install_dir / "Vendor" / "vendor.dll").read_bytes() == b"old-v"
    for leftover in ("App.old", "Modules.old", "Vendor.old"):
        assert not (install_dir / leftover).exists(), leftover


def _assert_relaunch_attempted(install_dir: Path, job) -> None:
    assert "relaunch" in _log(job)
    if sys.platform != "win32":
        recorded = install_dir / "relaunched.txt"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (recorded.exists() and recorded.read_text().count("\n") >= 2):
            time.sleep(0.05)
        args, cwd = recorded.read_text().splitlines()[:2]
        assert args == "--post-update"
        assert Path(cwd).resolve() == install_dir.resolve()


# --- The text itself -------------------------------------------------------


def test_swap_script_is_pure_ascii():
    # Windows PowerShell 5.1 reads a script by BOM or ANSI codepage; ASCII
    # reads the same either way.
    assert SWAP_SCRIPT.isascii()


def test_swap_script_parses_with_the_powershell_parser():
    assert _parse_errors(SWAP_SCRIPT) == []


def test_swap_script_does_no_path_arithmetic_and_uses_literal_paths_only():
    code = "\n".join(line for line in SWAP_SCRIPT.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("Join-Path", "New-Item", "Resolve-Path", "Start-Process", "Expand-Archive", "-WindowStyle"):
        assert forbidden not in code, forbidden
    # -Path treats [ and ] as wildcards: 'X:\Tools [2024]\App' tested as
    # missing although it existed.
    for cmdlet in ("Test-Path", "Remove-Item", "Get-ChildItem", "Get-Content", "Set-Content", "Add-Content"):
        assert f"{cmdlet} -Path " not in code, cmdlet
        assert f"{cmdlet} '" not in code, cmdlet
        assert f"{cmdlet} $" not in code, cmdlet


def test_swap_script_times_the_wait_for_the_app_with_a_monotonic_clock():
    # Get-Date is local wall-clock time: a DST change or an NTP correction
    # mid-wait would stretch the 600 s limit to 70 min or end it at once.
    code = "\n".join(line for line in SWAP_SCRIPT.splitlines() if not line.lstrip().startswith("#"))
    assert "(Get-Date).AddSeconds" not in code
    assert "[System.Diagnostics.Stopwatch]::StartNew()" in code


def test_swap_script_bytes_are_identical_for_any_paths(tmp_path):
    _, _, plain = _prepare(tmp_path / "a")
    _, _, hostile = _prepare(tmp_path / "b", install_parent=_HOSTILE_INSTALL, log_name=_HOSTILE_TEMP)
    assert plain.script_path.read_bytes() == hostile.script_path.read_bytes()
    assert plain.script_path.read_bytes() == b"\xef\xbb\xbf" + SWAP_SCRIPT.encode("ascii")


def test_swap_job_json_is_ascii_and_round_trips_hostile_paths(tmp_path):
    install_dir, staged, job = _prepare(tmp_path, install_parent=_HOSTILE_INSTALL, log_name=_HOSTILE_TEMP, pids=[4242])
    raw = job.job_path.read_bytes()
    assert raw.isascii()
    loaded = json.loads(raw)
    assert loaded["InstallDir"] == str(install_dir)
    assert loaded["AppExe"] == str(install_dir / "App" / "PortableFix.exe")
    assert [f["Staged"] for f in loaded["Folders"]] == [str(staged.stage_root / n) for n in ("App", "Modules", "Vendor")]
    assert loaded["Pids"] == [4242]


def test_powershell_reads_the_job_json_like_python_does(tmp_path):
    # ConvertFrom-Json on 5.1 has its own ideas about one-element and nested
    # arrays - read the job the way the script does and compare.
    install_dir, _, job = _prepare(tmp_path, install_parent=_HOSTILE_INSTALL, log_name=_HOSTILE_TEMP, pids=[4242])
    out = tmp_path / "read.txt"
    reader = tmp_path / "read_job.ps1"
    reader.write_text(
        "$Cfg = Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\n"
        "$lines = @($Cfg.InstallDir, [string]@($Cfg.Pids).Count, [string]@($Cfg.Pids)[0], [string]@($Cfg.Folders).Count,\n"
        "  @($Cfg.Folders)[2].Backup, [string]@($Cfg.DataCopies).Count, @($Cfg.DataCopies)[0].Dst, [string]@($Cfg.RootCopies).Count)\n"
        "[System.IO.File]::WriteAllText($args[1], ($lines -join \"`n\"), [System.Text.Encoding]::UTF8)\n",
        encoding="utf-8-sig",
    )
    subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(reader),
         str(job.job_path), str(out)],
        capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW,
    )
    lines = out.read_text(encoding="utf-8-sig").split("\n")
    assert lines == [
        str(install_dir), "1", "4242", "3", str(install_dir / "Vendor.old"),
        "3", str(install_dir / "Data" / "SHA256SUMS"), "1",
    ]


# --- Running it ------------------------------------------------------------


def test_swap_replaces_the_program_folders_and_keeps_user_data(tmp_path):
    install_dir, _, job = _prepare(tmp_path)

    result = _run(job, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    assert _marker(job).startswith("ready ")
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    assert (install_dir / "Modules" / "m01_diagnostics" / "actions.yaml").read_bytes() == b"new-m"
    assert not (install_dir / "Modules" / "mod.yaml").exists()
    assert (install_dir / "Vendor" / "Fonts" / "OFL.txt").read_bytes() == b"new-v"
    # Data by allowlist only: never the package's settings.json.
    assert (install_dir / "Data" / "settings.json").read_bytes() == b'{"k": "v"}'
    assert (install_dir / "Data" / "notes.txt").read_bytes() == b"user file"
    assert (install_dir / "Data" / "SHA256SUMS").read_bytes() == sums_for(release_files(exe=_relaunch_probe()))
    assert (install_dir / "Data" / "PortableFix-SelfSigned.cer").read_bytes() == b"new-cer"
    assert (install_dir / "PortableFix.cmd").read_bytes() == b"@echo off\r\nnew-launcher\r\n"
    for leftover in ("App.old", "Modules.old", "Vendor.old", "_update_stage"):
        assert not (install_dir / leftover).exists(), leftover
    _assert_relaunch_attempted(install_dir, job)


def test_swap_on_hostile_install_and_temp_paths_runs_no_injected_code(tmp_path):
    install_dir, _, job = _prepare(tmp_path, install_parent=_HOSTILE_INSTALL, log_name=_HOSTILE_TEMP)

    result = _run(job, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    assert (install_dir / "Data" / "settings.json").read_bytes() == b'{"k": "v"}'
    assert not [p for p in tmp_path.rglob("INJ.txt")]
    assert not (Path.cwd() / "INJ.txt").exists()
    _assert_relaunch_attempted(install_dir, job)


def test_swap_waits_for_the_app_process_before_touching_anything(tmp_path):
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        install_dir, _, job = _prepare(tmp_path, pids=[helper.pid], max_wait_sec=90)
        script = subprocess.Popen(
            _argv(job),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(tmp_path), creationflags=_NO_WINDOW,
        )
        deadline = time.monotonic() + 60
        while not _marker(job) and time.monotonic() < deadline and script.poll() is None:
            time.sleep(0.05)
        assert _marker(job).startswith("ready "), _log(job)
        time.sleep(0.5)
        # The app is still "running": nothing may have moved yet.
        _assert_untouched(install_dir)
        assert not (install_dir / "Data" / "update_status.txt").exists()
    finally:
        helper.kill()
        helper.wait()
    assert script.wait(timeout=60) == 0
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    assert f"pid {helper.pid} exited" in _log(job)


def test_swap_aborts_without_relaunch_when_the_app_never_exits(tmp_path):
    # This test process is alive for the whole run.
    install_dir, _, job = _prepare(tmp_path, pids=[os.getpid()], max_wait_sec=2)

    result = _run(job, tmp_path)

    assert result.returncode == 4
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ABORTED
    _assert_untouched(install_dir)
    # The old app is still the running one - a relaunch would only lose to
    # its single-instance mutex.
    assert "relaunch" not in _log(job)
    assert not (install_dir / "relaunched.txt").exists()


def test_swap_refuses_to_run_in_constrained_language_mode(tmp_path):
    install_dir, _, job = _prepare(tmp_path)

    result = _run(job, tmp_path, prepend="$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'\n")

    assert result.returncode == 3
    assert _marker(job) == "blocked ConstrainedLanguage"
    _assert_untouched(install_dir)
    assert not (install_dir / "Data" / "update_status.txt").exists()
    assert (install_dir / "_update_stage").exists()


def test_swap_exits_2_without_a_marker_when_the_job_is_unreadable(tmp_path):
    install_dir, _, job = _prepare(tmp_path)
    job.job_path.unlink()

    result = _run(job, tmp_path)

    assert result.returncode == 2
    assert _marker(job) == ""
    _assert_untouched(install_dir)


def test_swap_refuses_a_job_with_nothing_to_wait_for(tmp_path):
    install_dir, _, job = _prepare(tmp_path)
    data = json.loads(job.job_path.read_text(encoding="ascii"))
    data["Pids"] = []
    job.job_path.write_text(json.dumps(data), encoding="ascii")

    result = _run(job, tmp_path)

    assert result.returncode == 2
    _assert_untouched(install_dir)


def test_swap_refuses_when_a_process_to_wait_for_is_not_visible(tmp_path):
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    install_dir, _, job = _prepare(tmp_path, pids=[gone.pid])

    result = _run(job, tmp_path)

    assert result.returncode == 5
    assert _marker(job).startswith("error pid ")
    _assert_untouched(install_dir)


def test_swap_recovers_leftovers_of_an_interrupted_swap_before_swapping(tmp_path):
    # State after a USB stick was pulled mid-swap: App\ only exists as
    # App.old, plus a stale Modules.old next to a live Modules\.
    install_dir, _, job = _prepare(tmp_path)
    (install_dir / "App").rename(install_dir / "App.old")
    (install_dir / "Modules.old").mkdir()
    (install_dir / "Modules.old" / "stale.yaml").write_bytes(b"stale")

    _run(job, tmp_path)

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    assert (install_dir / "Modules" / "m01_diagnostics" / "actions.yaml").read_bytes() == b"new-m"
    assert not (install_dir / "App.old").exists()
    assert not (install_dir / "Modules.old").exists()


def test_swap_reports_stale_manifest_when_the_sums_copy_cannot_be_verified(tmp_path):
    install_dir, _, job = _prepare(tmp_path)
    # A folder where the manifest file should be can never be read back as
    # the expected bytes - the same outcome as a copy AV keeps locking.
    installed_sums = install_dir / "Data" / "SHA256SUMS"
    installed_sums.unlink()
    installed_sums.mkdir()

    _run(job, tmp_path)

    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK_SUMS_STALE


def test_swap_verifies_the_last_sums_copy_too(tmp_path):
    # The DataCopies copy of SHA256SUMS "fails" (Test-Path on the staged
    # manifest says no, once), so only Update-Sums' single re-copy installs
    # it - which must then be checked, not reported as stale unchecked.
    install_dir, _, job = _prepare(tmp_path, sums_tries=1)
    stub = (
        "$script:sumsMisses = 1\n"
        "function Test-Path { [CmdletBinding()] param([string]$LiteralPath, [string]$PathType) "
        "if (($script:sumsMisses -gt 0) -and ($LiteralPath -like '*_update_stage*SHA256SUMS')) { $script:sumsMisses--; return $false } "
        "if ($PathType) { Microsoft.PowerShell.Management\\Test-Path -LiteralPath $LiteralPath -PathType $PathType } "
        "else { Microsoft.PowerShell.Management\\Test-Path -LiteralPath $LiteralPath } }\n"
    )

    _run(job, tmp_path, prepend=stub)

    assert "SHA256SUMS check 0" in _log(job)
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    assert (install_dir / "Data" / "SHA256SUMS").read_bytes() == sums_for(release_files(exe=_relaunch_probe()))


def test_swap_writes_the_status_and_relaunches_before_removing_the_backups(tmp_path):
    # A backup AV keeps open costs RenameTries x RenameDelayMs (30 s in
    # production) - after the relaunch, not before it, and never with the
    # status still saying 'in_progress'.
    install_dir, _, job = _prepare(tmp_path)
    _run(job, tmp_path, override=_undeletable("*App.old"))

    log = _log(job)
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    stuck = log.index("could not remove " + str(install_dir / "App.old"))
    assert log.index("status: ok") < stuck
    assert log.index("relaunch") < stuck
    # The other backups and the stage still go.
    for gone in ("Modules.old", "Vendor.old", "_update_stage"):
        assert not (install_dir / gone).exists(), gone


def test_swap_does_not_claim_a_rollback_it_could_not_finish(tmp_path):
    # The new App\ can neither be parked (a leftover App.failed is in the
    # way and cannot be removed) nor deleted: the old App cannot go back.
    install_dir, staged, job = _prepare(tmp_path)
    shutil.rmtree(staged.stage_root / "Vendor")
    (staged.stage_root / "Vendor").mkdir()
    (install_dir / "App.failed").mkdir()
    (install_dir / "App.failed" / "x").write_bytes(b"x")
    _run(job, tmp_path, override=_undeletable("*App.failed", "*App"))

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ROLLBACK_FAILED
    # The only good copy of the old exe must survive the cleanup.
    assert (install_dir / "App.old" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Modules" / "mod.yaml").read_bytes() == b"old-m"
    assert (install_dir / "Vendor" / "vendor.dll").read_bytes() == b"old-v"


def test_swap_rollback_keeps_the_old_folders_and_manifest(tmp_path):
    install_dir, staged, job = _prepare(tmp_path)
    # Emptied after staging: the moves succeed, the verification of the new
    # folders does not - the rollback path.
    shutil.rmtree(staged.stage_root / "Vendor")
    (staged.stage_root / "Vendor").mkdir()

    _run(job, tmp_path)

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ROLLED_BACK
    _assert_untouched(install_dir)
    assert (install_dir / "Data" / "SHA256SUMS").read_bytes() == b"old-sums"
    assert (install_dir / "PortableFix.cmd").read_bytes() == b"@echo off\r\nold-launcher\r\n"
    assert "relaunch" in _log(job)
    for parked in ("App.failed", "Modules.failed", "Vendor.failed", "_update_stage"):
        assert not (install_dir / parked).exists(), parked


def test_swap_aborts_cleanly_when_the_staged_update_vanished(tmp_path):
    install_dir, staged, job = _prepare(tmp_path)
    shutil.rmtree(staged.stage_root / "Modules")

    _run(job, tmp_path)

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ABORTED
    _assert_untouched(install_dir)
    assert "relaunch" in _log(job)


def test_swap_aborts_before_moving_anything_when_a_stale_backup_cannot_be_removed(tmp_path):
    install_dir, _, job = _prepare(tmp_path)
    (install_dir / "Vendor.old").mkdir()
    (install_dir / "Vendor.old" / "stale.dll").write_bytes(b"stale")
    _run(job, tmp_path, override=_undeletable("*Vendor.old"))

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ABORTED
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"
    assert (install_dir / "Modules" / "mod.yaml").read_bytes() == b"old-m"
    assert not (install_dir / "App.old").exists()
    assert not (install_dir / "Modules.old").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="relies on Windows mandatory file locking")
def test_swap_aborts_without_moving_anything_when_the_old_exe_is_locked(tmp_path):
    install_dir, _, job = _prepare(tmp_path)

    with open(install_dir / "App" / "PortableFix.exe", "r+b"):
        _run(job, tmp_path)

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ABORTED
    _assert_untouched(install_dir)


@pytest.mark.skipif(sys.platform != "win32", reason="relies on Windows mandatory file locking")
def test_swap_puts_back_folders_already_moved_when_a_later_one_is_locked(tmp_path):
    # App\ and Modules\ are moved aside, then Vendor\ is locked: they used to
    # stay stranded as *.old and the relaunched app had no modules at all.
    install_dir, _, job = _prepare(tmp_path)

    with open(install_dir / "Vendor" / "vendor.dll", "r+b"):
        _run(job, tmp_path)

    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ABORTED
    _assert_untouched(install_dir)


# --- Link-safe cleanup of App.old / *.failed / the stage ---------------------
#
# Windows PowerShell 5.1's Remove-Item -Recurse walks into directory
# junctions and symlinks. The install folder is often user-writable (a USB
# stick, a folder under the user's profile), so a link planted in App\ or a
# stale App.old would have the updater delete whatever it points at. The
# cleanup uses the same no-follow walk as the catalog's Remove-PfSafe; these
# runs prepend the 5.1-behaving Remove-Item stand-in from test_safe_delete,
# so a regression back to Remove-Item fails here even on pwsh 7.

from test_safe_delete import PS51_REMOVE_ITEM, STUB_GUARD_EXIT  # noqa: E402


def _swap_victim(tmp_path: Path) -> Path:
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True)
    (victim / "keep.txt").write_bytes(b"precious")
    (victim / "sub" / "deep.txt").write_bytes(b"precious too")
    return victim


def _assert_swap_victim_intact(victim: Path) -> None:
    assert (victim / "keep.txt").read_bytes() == b"precious"
    assert (victim / "sub" / "deep.txt").read_bytes() == b"precious too"


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here (Windows without Developer Mode / privilege)")


def test_swap_script_never_uses_remove_item():
    code = "\n".join(line for line in SWAP_SCRIPT.splitlines() if not line.lstrip().startswith("#"))
    assert "Remove-Item" not in code
    assert "Remove-TreeNoFollow $Target" in code


def test_swap_cleanup_never_follows_a_link_planted_in_the_old_app_folder(tmp_path):
    # The realistic attack: a link inside the live App\ becomes App.old at
    # the swap and is dropped with the backups afterwards.
    victim = _swap_victim(tmp_path)
    install_dir, _, job = _prepare(tmp_path)
    (install_dir / "App" / "nested").mkdir()
    _symlink_or_skip(install_dir / "App" / "nested" / "to_victim", victim)
    _symlink_or_skip(install_dir / "App" / "file_link.txt", victim / "keep.txt")

    result = _run(job, tmp_path, prepend=PS51_REMOVE_ITEM + "\n")

    assert result.returncode != STUB_GUARD_EXIT, result.stdout + result.stderr
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    _assert_swap_victim_intact(victim)
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    for gone in ("App.old", "Modules.old", "Vendor.old", "_update_stage"):
        assert not os.path.lexists(install_dir / gone), gone


def test_swap_cleanup_unlinks_stale_backups_that_are_links_or_hold_links(tmp_path):
    # Leftovers planted before the swap: an App.old holding a link to the
    # victim, a Modules.old that IS a link to it and a dangling Vendor.old
    # link (Test-Path says it is not there; it must still go, or it would
    # block the backup rename).
    victim = _swap_victim(tmp_path)
    install_dir, _, job = _prepare(tmp_path)
    (install_dir / "App.old" / "deep" / "er").mkdir(parents=True)
    (install_dir / "App.old" / "deep" / "old.txt").write_bytes(b"x")
    _symlink_or_skip(install_dir / "App.old" / "deep" / "er" / "to_victim", victim)
    _symlink_or_skip(install_dir / "Modules.old", victim)
    os.symlink(tmp_path / "nothing_here", install_dir / "Vendor.old", target_is_directory=True)

    result = _run(job, tmp_path, prepend=PS51_REMOVE_ITEM + "\n")

    assert result.returncode != STUB_GUARD_EXIT, result.stdout + result.stderr
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_OK
    _assert_swap_victim_intact(victim)
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _relaunch_probe()
    assert (install_dir / "Modules" / "m01_diagnostics" / "actions.yaml").read_bytes() == b"new-m"
    for gone in ("App.old", "Modules.old", "Vendor.old", "_update_stage"):
        assert not os.path.lexists(install_dir / gone), gone


def test_swap_rollback_removes_the_parked_new_folders_without_following_links(tmp_path):
    # *.failed and the stage go through the same cleanup: a link inside the
    # staged update (the stage lives in the install folder too) is parked as
    # part of App.failed by the rollback and then removed as the link only.
    victim = _swap_victim(tmp_path)
    install_dir, staged, job = _prepare(tmp_path)
    shutil.rmtree(staged.stage_root / "Vendor")
    (staged.stage_root / "Vendor").mkdir()
    _symlink_or_skip(staged.stage_root / "App" / "to_victim", victim)

    result = _run(job, tmp_path, prepend=PS51_REMOVE_ITEM + "\n")

    assert result.returncode != STUB_GUARD_EXIT, result.stdout + result.stderr
    assert _status(install_dir, job) == update_swap.UPDATE_STATUS_ROLLED_BACK
    _assert_swap_victim_intact(victim)
    _assert_untouched(install_dir)
    for parked in ("App.failed", "Modules.failed", "Vendor.failed", "_update_stage"):
        assert not os.path.lexists(install_dir / parked), parked


def test_swap_logs_how_many_items_a_stuck_backup_still_holds(tmp_path):
    install_dir, _, job = _prepare(tmp_path)

    _run(job, tmp_path, override=_undeletable("*App.old"))

    assert "could not remove " + str(install_dir / "App.old") + " (1 item(s) left)" in _log(job)
