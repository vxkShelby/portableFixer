"""How powershell.exe really behaves under the updater's spawn flags.

The in-app update never completed on any machine: the swap PowerShell was
created, lived 1-2 s and died without running line 1 of its script, and
without a trace in any Windows log. The suspected cause is DETACHED_PROCESS
(which apply_update has passed since the updater's first version): with no
console at all, PowerShell's console host cannot start and the process exits
quietly before it opens a runspace. Every other PowerShell the app starts
uses CREATE_NO_WINDOW and works.

These tests start the real powershell.exe on a one-line probe script (it
writes <script>.ran, so no path is written inside the script) under both flag
sets, and pin the difference down on a real Windows runner. They stay as a
guard against DETACHED_PROCESS ever coming back.

The rest drives the real hand-off end to end - stage, launch_swap, the
handshake, the swap and the relaunch - from hostile paths, from an app
started in its App folder, and from inside a kill-on-close Job Object.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from update_fixtures import make_install, release_files, write_release_zip

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows process creation")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def _launch_probe(tmp_path: Path, flags: int, extra_args: list[str]) -> dict:
    from portablefix.executor import powershell_executable

    script = tmp_path / "probe.ps1"
    script.write_text("Set-Content -LiteralPath ($PSCommandPath + '.ran') -Value ok", encoding="utf-8-sig")
    ran = Path(str(script) + ".ran")
    log = tmp_path / "launch.log"
    argv = [
        powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        *extra_args, "-File", str(script),
    ]
    route = "breakaway"
    started = time.monotonic()
    with open(log, "wb") as out:
        popen_kwargs = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
        try:
            proc = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
        except PermissionError:
            # The runner's job forbids breakaway - the same fallback the
            # updater needs; record it rather than fail on it.
            route = "no-breakaway"
            proc = subprocess.Popen(argv, creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **popen_kwargs)
        exit_code = proc.wait(60)
    facts = {
        "exit_code": f"0x{exit_code & 0xFFFFFFFF:08X}",
        "script_ran": ran.exists(),
        "output_bytes": log.stat().st_size,
        "output": log.read_bytes()[:500],
        "elapsed_s": round(time.monotonic() - started, 2),
        "route": route,
    }
    print(f"flags=0x{flags:08X} args={extra_args}: {facts}")
    return facts


def test_powershell_create_no_window_runs_script(tmp_path):
    facts = _launch_probe(tmp_path, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, [])
    assert facts["script_ran"], facts


def test_powershell_detached_process_never_runs_script(tmp_path):
    # The exact combination apply_update shipped with (<= 1.11.4).
    facts = _launch_probe(
        tmp_path,
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        ["-WindowStyle", "Hidden"],
    )
    assert not facts["script_ran"], (
        "F1 falsified: DETACHED_PROCESS is not the cause - stop and re-investigate with launch logs", facts,
    )


# --- The real hand-off: stage, launch_swap, swap, relaunch -----------------
#
# A launcher process (update_spawn_launcher.py) plays the app: it stages a
# release zip and calls the REAL launch_swap, which waits for the 'ready'
# marker and returns. A helper process stands in for the rest of the app:
# the swap waits for it, so the test decides when the swap may start. The
# fake install's exe is a copy of hostname.exe; the update's is a copy of
# whoami.exe, which the swap really starts when it relaunches the app.

TESTS_DIR = Path(__file__).resolve().parent
LAUNCHER = TESTS_DIR / "update_spawn_launcher.py"
HOSTILE_INSTALL = "Jano\u2019s $(Set-Content INJ.txt x) \u201eq\u201d [2024] \u013e\u0161\u010d\u0165"
HOSTILE_TEMP = "temp \u2019 [x]"
LAUNCH_TIMEOUT_SEC = 90  # staging a tiny zip + the 45 s handshake at most
SWAP_TIMEOUT_SEC = 45


def _system_exe(name: str) -> Path:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name


def _terminate_if_powershell(pid: int) -> None:
    """Kills a leftover swap PowerShell by PID - but only if that PID still
    is a PowerShell, so a recycled PID never takes an unrelated process."""
    from portablefix import update_swap

    image = update_swap._process_image(pid)
    if image and Path(image).name.lower() == "powershell.exe":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30)


@pytest.fixture
def spawned():
    """Every process a test starts; whatever still runs is killed at
    teardown, so a failed test leaves no updater waiting on the runner."""
    procs: list = []
    powershell_pids: list = []
    yield procs, powershell_pids
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    for pid in powershell_pids:
        _terminate_if_powershell(pid)


def _hand_off(tmp_path: Path, spawned, install_dir: Path, *, temp_dir: Path | None = None,
              cwd_in_app: bool = False, kill_on_close_job: bool = False):
    procs, powershell_pids = spawned
    make_install(install_dir)
    shutil.copyfile(_system_exe("hostname.exe"), install_dir / "App" / "PortableFix.exe")
    zip_path = write_release_zip(
        tmp_path / "download" / "PortableFix-Portable.zip",
        release_files(exe=_system_exe("whoami.exe").read_bytes()),
    )
    temp_dir = temp_dir or tmp_path / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    procs.append(helper)
    result_path = tmp_path / "result.json"
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps({
        "zip": str(zip_path), "install_dir": str(install_dir), "helper_pid": helper.pid,
        "result": str(result_path), "kill_on_close_job": kill_on_close_job,
    }), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() != "TMPDIR"}
    # The swap's script, job and logs go to %TEMP%\PortableFixUpdate of the
    # process that launches it.
    env["TEMP"] = env["TMP"] = str(temp_dir)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(TESTS_DIR.parent), env.get("PYTHONPATH")) if p)
    launcher = subprocess.Popen(
        [sys.executable, str(LAUNCHER), str(case_path)],
        cwd=str(install_dir / "App" if cwd_in_app else tmp_path), env=env,
    )
    procs.append(launcher)
    returncode = launcher.wait(timeout=LAUNCH_TIMEOUT_SEC)
    assert result_path.exists(), f"the launcher died before reporting (exit code {returncode})"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("child_pid"):
        powershell_pids.append(result["child_pid"])
    print(f"hand-off: {result}")
    return helper, result


def _status(install_dir: Path) -> str | None:
    try:
        return (install_dir / "Data" / "update_status.txt").read_text(encoding="ascii").strip()
    except OSError:
        return None


def _wait_for_status(install_dir: Path, expected: str, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while True:
        status = _status(install_dir)
        if status == expected or time.monotonic() >= deadline:
            return status
        time.sleep(0.25)


def _update_log(result: dict) -> str:
    try:
        log = Path(result["log_dir"]) / f"update_log_{result['pid']}.txt"
        # errors=replace: read while PowerShell appends, a line may end
        # halfway through a multi-byte character of a hostile path.
        return log.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def _finish_swap(install_dir: Path, helper, result: dict) -> None:
    """The app 'exits'; the swap must then complete and relaunch it."""
    # Nothing is touched while the app still runs.
    assert _status(install_dir) == "handed_off"
    helper.kill()
    helper.wait(timeout=10)
    status = _wait_for_status(install_dir, "ok", SWAP_TIMEOUT_SEC)
    assert status == "ok", (status, _update_log(result))
    deadline = time.monotonic() + 20
    while "update swap finished" not in _update_log(result) and time.monotonic() < deadline:
        time.sleep(0.25)
    log = _update_log(result)
    assert "relaunched " in log and "relaunch FAILED" not in log, log
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _system_exe("whoami.exe").read_bytes()
    assert (install_dir / "Data" / "settings.json").read_bytes() == b'{"k": "v"}'
    assert not (install_dir / "App.old").exists()


def _assert_route(result: dict, expected_outside_a_job: str) -> None:
    # Outside any Job Object nothing can refuse the breakaway flag; inside
    # the runner's own job it depends on that job's limits, so both routes
    # are legitimate there - the diagnostics must name the one taken.
    if not result["facts_before"].get("app_in_job"):
        assert result["route"] == expected_outside_a_job, result
    diagnostics = Path(result["diagnostics_path"]).read_text(encoding="utf-8")
    assert f"route: {result['route']}\n" in diagnostics


def test_real_hand_off_swaps_the_install_and_relaunches(tmp_path, spawned):
    install_dir = tmp_path / "install"
    helper, result = _hand_off(tmp_path, spawned, install_dir)

    assert result.get("ok"), result
    _assert_route(result, "direct")
    _finish_swap(install_dir, helper, result)


def test_real_hand_off_with_hostile_install_and_temp_paths(tmp_path, spawned):
    # -File gets the 8.3 path of this TEMP folder on Windows PowerShell 5.1;
    # the paths reach the script only through its ASCII JSON job.
    install_dir = tmp_path / HOSTILE_INSTALL
    helper, result = _hand_off(tmp_path, spawned, install_dir, temp_dir=tmp_path / HOSTILE_TEMP)

    assert result.get("ok"), result
    _finish_swap(install_dir, helper, result)
    assert list(tmp_path.rglob("INJ.txt")) == []


def test_real_hand_off_from_an_app_started_in_its_app_folder(tmp_path, spawned):
    # The installer shortcuts used to start the app with App\ as its current
    # directory; a swap PowerShell inheriting it could never rename App\.
    install_dir = tmp_path / "install"
    helper, result = _hand_off(tmp_path, spawned, install_dir, cwd_in_app=True)

    assert result.get("ok"), result
    assert os.path.samefile(result["cwd"], install_dir / "App")
    _finish_swap(install_dir, helper, result)


def test_real_hand_off_inside_a_kill_on_close_job(tmp_path, spawned):
    # Documents the one known way to lose the updater after the handshake:
    # a job that forbids breakaway and kills its members when it closes. The
    # launch still succeeds (without breakaway), the job closes as the app
    # exits and takes the updater with it - and 'handed_off' stays behind, so
    # the next start tells the user instead of staying silent.
    from portablefix import update_swap, updater

    install_dir = tmp_path / "install"
    helper, result = _hand_off(tmp_path, spawned, install_dir, kill_on_close_job=True)

    assert result.get("ok"), result
    assert result["route"] == update_swap.ROUTE_NO_BREAKAWAY
    assert result["warn_job"] is True
    diagnostics = Path(result["diagnostics_path"]).read_text(encoding="utf-8")
    assert "'child_in_job': True" in diagnostics and "'kill_on_close': True" in diagnostics
    assert update_swap.wait_for_process_exit(result["child_pid"], 30)
    helper.kill()
    helper.wait(timeout=10)
    assert _status(install_dir) == "handed_off"
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == _system_exe("hostname.exe").read_bytes()
    assert updater.update_status_message_key(_status(install_dir), []) == "update_status_incomplete"
