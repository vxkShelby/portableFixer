"""The Qt-free update core: the spawn, the staging, and the handshake.

Popen, the clock and the Win32 queries are faked here; the swap script
itself runs for real in test_update_swap_script.py, and the real spawn on
Windows in test_update_spawn_windows.py."""
import json
import os
import shutil
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

from portablefix import update_swap
from portablefix.update_swap import (
    CREATE_BREAKAWAY_FROM_JOB,
    CREATE_NO_WINDOW,
    ROUTE_DIRECT,
    ROUTE_NO_BREAKAWAY,
    SWAP_CREATIONFLAGS,
    UpdateStageCancelled,
    UpdateStageError,
    clean_child_env,
    launch_swap,
    onefile_parent_pid,
    spawn_swap,
    stage_update,
)
from update_fixtures import NEW_EXE, make_install, release_files, sums_for, write_release_zip

DETACHED_PROCESS = 0x00000008


# --- The spawn -------------------------------------------------------------


class _FakePopen:
    """Records the spawn; the test decides what the 'script' does: write a
    marker, print to the launch log, exit with a code, or keep running."""

    calls: list = []
    behaviour: dict = {}

    def __init__(self, argv, **kwargs):
        fail = self.behaviour.get("raise_first")
        if fail is not None and not _FakePopen.calls:
            _FakePopen.calls.append((argv, kwargs))
            raise fail
        _FakePopen.calls.append((argv, kwargs))
        self.argv = argv
        self.kwargs = kwargs
        self.pid = 4321
        self.killed = False
        self._polls = 0
        output = self.behaviour.get("output")
        if output:
            kwargs["stdout"].write(output)
        marker = self.behaviour.get("marker")
        if marker is not None:
            job = argv[-1].replace(".ps1", ".json")
            Path(json.loads(Path(job).read_text(encoding="ascii"))["MarkerFile"]).write_text(marker + "\n")

    def poll(self):
        self._polls += 1
        if self.killed:
            return 1
        return self.behaviour.get("exit_code")

    def wait(self, timeout=None):
        rc = self.poll()
        if rc is None:
            raise subprocess.TimeoutExpired("powershell", timeout)
        return rc

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_popen(monkeypatch):
    _FakePopen.calls = []
    _FakePopen.behaviour = {}
    monkeypatch.setattr(update_swap.subprocess, "Popen", _FakePopen)
    return _FakePopen


def test_swap_flags_hide_the_console_and_never_detach_it():
    # DETACHED_PROCESS is what kept every update up to 1.11.4 from ever
    # running: PowerShell with no console exits 0 before line 1.
    assert SWAP_CREATIONFLAGS & CREATE_NO_WINDOW
    assert not SWAP_CREATIONFLAGS & DETACHED_PROCESS
    assert SWAP_CREATIONFLAGS & CREATE_BREAKAWAY_FROM_JOB


def test_spawn_swap_starts_powershell_hidden_with_the_script_as_file(tmp_path, fake_popen):
    script = tmp_path / "swap_1_ab.ps1"
    env = {"A": "1"}

    proc, route = spawn_swap(script, tmp_path / "launch.log", cwd=tmp_path, env=env)

    argv, kwargs = fake_popen.calls[0]
    assert route == ROUTE_DIRECT
    assert proc.pid == 4321
    assert "-WindowStyle" not in argv
    assert argv[-2:] == ["-File", str(script)]
    assert {"-NoProfile", "-NonInteractive"} <= set(argv)
    assert kwargs["creationflags"] & CREATE_NO_WINDOW
    assert not kwargs["creationflags"] & DETACHED_PROCESS
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"] is env
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["stdout"].closed  # our copy of the log handle is not leaked
    assert kwargs["close_fds"] is True


def test_spawn_swap_retries_without_breakaway_when_the_job_forbids_it(tmp_path, fake_popen):
    fake_popen.behaviour = {"raise_first": PermissionError(13, "Access is denied")}

    _, route = spawn_swap(tmp_path / "s.ps1", tmp_path / "launch.log", cwd=tmp_path, env={})

    assert route == ROUTE_NO_BREAKAWAY
    first, second = (kwargs["creationflags"] for _, kwargs in fake_popen.calls)
    assert first == SWAP_CREATIONFLAGS
    assert second == SWAP_CREATIONFLAGS & ~CREATE_BREAKAWAY_FROM_JOB


def test_spawn_swap_retries_on_winerror_5(tmp_path, fake_popen):
    class _WinError(OSError):
        winerror = 5

    fake_popen.behaviour = {"raise_first": _WinError("Access is denied")}

    _, route = spawn_swap(tmp_path / "s.ps1", tmp_path / "launch.log", cwd=tmp_path, env={})

    assert route == ROUTE_NO_BREAKAWAY


def test_spawn_swap_does_not_retry_other_errors(tmp_path, fake_popen):
    fake_popen.behaviour = {"raise_first": FileNotFoundError(2, "powershell.exe not found")}

    with pytest.raises(FileNotFoundError):
        spawn_swap(tmp_path / "s.ps1", tmp_path / "launch.log", cwd=tmp_path, env={})
    assert len(fake_popen.calls) == 1


def test_clean_child_env_drops_the_onefile_state_of_the_dying_app(tmp_path):
    meipass = tmp_path / "_MEI12345"
    keep = tmp_path / "tools"
    environ = {
        "_PYI_ARCHIVE_FILE": "x",
        "_PYI_APPLICATION_HOME_DIR": str(meipass),
        "_pyi_parent_process_level": "1",
        "_MEIPASS2": str(meipass),
        "QT_PLUGIN_PATH": str(meipass / "PySide6" / "plugins"),
        "qml2_import_path": "x",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "x",
        "Path": os.pathsep.join([str(meipass), str(meipass / "PySide6"), str(keep)]),
        "USERPROFILE": "C:\\Users\\x",
        "PYINSTALLER_RESET_ENVIRONMENT": "0",
    }

    env = clean_child_env(environ, meipass=str(meipass))

    assert not [k for k in env if k.upper().startswith("_PYI_")]
    for dropped in ("_MEIPASS2", "QT_PLUGIN_PATH", "qml2_import_path", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        assert dropped not in env
    assert env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert env["Path"] == str(keep)
    assert env["USERPROFILE"] == "C:\\Users\\x"
    assert environ["_MEIPASS2"] == str(meipass)  # the caller's mapping is not modified


def test_clean_child_env_keeps_path_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    env = clean_child_env({"PATH": "a" + os.pathsep + "b"})
    assert env["PATH"] == "a" + os.pathsep + "b"


def test_onefile_parent_pid_is_none_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(update_swap, "_process_image", lambda pid: sys.executable)
    assert onefile_parent_pid() is None


def test_onefile_parent_pid_is_none_when_the_parent_runs_another_image(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_swap, "_process_image", lambda pid: str(tmp_path / "explorer.exe"))
    assert onefile_parent_pid() is None


def test_onefile_parent_pid_is_the_bootloader_running_the_same_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_swap, "_process_image", lambda pid: sys.executable)
    assert onefile_parent_pid() == os.getppid()


def test_onefile_parent_pid_is_none_when_the_parent_cannot_be_queried(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(update_swap, "_process_image", lambda pid: None)
    assert onefile_parent_pid() is None


@pytest.mark.skipif(sys.platform == "win32", reason="the Win32 queries are real there")
def test_windows_only_queries_degrade_to_neutral_answers_elsewhere(tmp_path):
    assert update_swap.short_path(tmp_path / "x [1]") == str(tmp_path / "x [1]")
    assert update_swap.job_facts(1234) == {}


def test_importing_the_update_core_does_not_load_qt():
    # The frozen end-to-end probe and the release build run this module in
    # processes that must never pull in PySide6.
    code = "import sys, portablefix.update_swap; print(sorted(m for m in sys.modules if m.startswith('PySide6')))"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


# --- Staging ---------------------------------------------------------------


def _zip(tmp_path: Path, files=None, **kwargs) -> Path:
    return write_release_zip(tmp_path / "dl" / "PortableFix-update.zip", files, **kwargs)


def test_stage_update_unpacks_a_release_shaped_zip(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = _zip(tmp_path)

    staged = stage_update(zip_path, install_dir, version="1.12.0")

    root = install_dir / "_update_stage" / "PortableFix"
    assert staged.stage_root == root
    assert staged.stage_dir == install_dir / "_update_stage"
    assert (root / "App" / "PortableFix.exe").read_bytes() == NEW_EXE
    assert (root / "Modules" / "m01_diagnostics" / "actions.yaml").read_bytes() == b"new-m"
    assert (root / "Vendor" / "Fonts" / "OFL.txt").read_bytes() == b"new-v"
    assert (root / "Data" / "SHA256SUMS").read_bytes() == sums_for(release_files())
    assert staged.file_count == len(release_files()) + 1
    assert staged.byte_count == sum(len(d) for d in release_files().values()) + len(sums_for(release_files()))
    assert (install_dir / "_update_stage" / "version.txt").read_text() == "1.12.0"
    assert not zip_path.exists()
    # Nothing live is touched by staging.
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == b"old-exe"


def test_stage_update_reports_progress(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    calls = []

    stage_update(_zip(tmp_path), install_dir, progress=lambda done, total: calls.append((done, total)))

    assert calls
    assert calls[-1][0] == calls[-1][1]
    assert [done for done, _ in calls] == sorted(done for done, _ in calls)


@pytest.mark.parametrize(
    "name",
    [
        "..\\x", "C:\\x", "/x", "a:b", "PortableFix\\CON", "x. ",
        "PortableFix\\..\\..\\escaped.txt", "PortableFix/../escaped.txt", "PortableFix\\Modules\\nul.txt",
        "PortableFix\\App\\PortableFix.exe:stream", "PortableFix\\App.\\x", "PortableFix\\COM1.log",
        "PortableFix\\Modules\\a\x01b", "\\\\server\\share\\x",
    ],
)
def test_stage_update_rejects_unsafe_entry_names(tmp_path, name):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = _zip(tmp_path, extra_names={name: b"pwned"})

    with pytest.raises(UpdateStageError):
        stage_update(zip_path, install_dir)

    assert not (install_dir / "_update_stage").exists()
    assert not list(tmp_path.rglob("escaped.txt"))
    assert zip_path.exists()


def _without(prefix: str) -> dict[str, bytes]:
    return {rel: data for rel, data in release_files().items() if not rel.startswith(prefix)}


@pytest.mark.parametrize(
    "files, kwargs, message",
    [
        (_without("Vendor/"), {}, "Vendor"),
        (_without("Modules/"), {}, "Modules"),
        (_without("App/"), {"sums": sums_for(_without("App/")) or b"\n"}, "PortableFix.exe"),
        (release_files(), {"sums": b""}, "SHA256SUMS"),
        (_without("PortableFix.cmd"), {}, "PortableFix.cmd"),
        (release_files(), {"sums": sums_for(release_files(), skip=("App/PortableFix.exe",))}, "App/PortableFix.exe"),
        (release_files(), {"sums": sums_for({**release_files(), "Modules/m01_diagnostics/actions.yaml": b"x"})},
         "does not match"),
        (release_files(), {"sums": sums_for({**release_files(), "Modules/gone.yaml": b"x"})}, "missing"),
        (release_files(), {"extra_names": {"Other\\x.txt": b"x"}}, "more than one top-level folder"),
        (release_files(), {"extra_names": {"loose.txt": b"x"}}, "more than one top-level folder"),
        (release_files(), {"extra_names": {"PortableFix\\modules\\M01_DIAGNOSTICS\\actions.yaml": b"x"}}, "duplicate"),
    ],
    ids=["no-vendor", "no-modules", "no-exe", "no-sums", "no-launcher", "exe-not-in-manifest", "hash-mismatch",
         "listed-file-missing", "two-top-folders", "loose-file", "case-duplicate"],
)
def test_stage_update_rejects_broken_packages(tmp_path, files, kwargs, message):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = _zip(tmp_path, files, **kwargs)

    with pytest.raises(UpdateStageError, match=message):
        stage_update(zip_path, install_dir)

    assert not (install_dir / "_update_stage").exists()
    assert zip_path.exists()


def test_stage_update_rejects_a_file_that_is_not_a_zip(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = tmp_path / "PortableFix-update.zip"
    zip_path.write_bytes(b"<html>rate limited</html>")

    with pytest.raises(UpdateStageError):
        stage_update(zip_path, install_dir)
    assert not (install_dir / "_update_stage").exists()


def test_stage_update_refuses_when_the_install_volume_is_too_full(tmp_path, monkeypatch):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(update_swap.shutil, "disk_usage", lambda path: usage(10**9, 10**9 - 1024, 1024))

    with pytest.raises(UpdateStageError, match="free space"):
        stage_update(_zip(tmp_path), install_dir)
    assert not (install_dir / "_update_stage").exists()


@pytest.mark.parametrize(
    "folder",
    [
        "Jano\u2019s \u2018x\u2019 \u201aq\u201b \u201cy\u201d \u201ez\u201d",
        "back`tick $env $(Set-Content INJ.txt x) [2024]",
        "\u013e\u0161\u010d\u0165\u017e\u00fd\u00e1\u00ed\u00e9 Ondrej \u010cu\u010dko",
    ],
)
def test_stage_update_handles_hostile_install_paths(tmp_path, folder):
    install_dir = tmp_path / folder / "PortableFix"
    make_install(install_dir)

    staged = stage_update(_zip(tmp_path), install_dir)

    assert (staged.stage_root / "App" / "PortableFix.exe").read_bytes() == NEW_EXE
    assert not list(tmp_path.rglob("INJ.txt"))


def test_stage_update_wipes_a_stale_stage_first(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    stale = install_dir / "_update_stage" / "PortableFix" / "App"
    stale.mkdir(parents=True)
    (stale / "old-leftover.dll").write_bytes(b"x")
    (install_dir / "_update_stage" / "junk.txt").write_bytes(b"x")

    stage_update(_zip(tmp_path), install_dir)

    assert not (stale / "old-leftover.dll").exists()
    assert not (install_dir / "_update_stage" / "junk.txt").exists()


def test_stage_update_leaves_no_partial_stage_when_interrupted(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = _zip(tmp_path)
    checks = []

    def stop_after_a_few():
        checks.append(1)
        return len(checks) > 3

    with pytest.raises(UpdateStageCancelled):
        stage_update(zip_path, install_dir, should_stop=stop_after_a_few)

    assert not (install_dir / "_update_stage").exists()
    assert zip_path.exists()


def test_stage_update_stages_the_real_release_zip(tmp_path):
    # PORTABLEFIX_TEST_RELEASE_ZIP=path\to\PortableFix-Portable.zip checks a
    # real release (v1.11.4 and later) against the exact client-side rules.
    source = os.environ.get("PORTABLEFIX_TEST_RELEASE_ZIP")
    if not source:
        pytest.skip("PORTABLEFIX_TEST_RELEASE_ZIP not set")
    install_dir = tmp_path / "install"
    make_install(install_dir)
    zip_path = tmp_path / "PortableFix-Portable.zip"
    shutil.copyfile(source, zip_path)  # staging deletes the zip it was given

    staged = stage_update(zip_path, install_dir)

    assert (staged.stage_root / "App" / "PortableFix.exe").stat().st_size > 1_000_000
    assert staged.file_count > 10


# --- The job and the handshake ---------------------------------------------


@pytest.fixture
def staged(tmp_path):
    install_dir = tmp_path / "install"
    make_install(install_dir)
    return stage_update(_zip(tmp_path), install_dir)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _launch(tmp_path, staged, **kwargs):
    clock = _Clock()
    kwargs.setdefault("pids", [os.getpid()])
    return launch_swap(
        staged, tmp_path / "install", log_dir=tmp_path / "temp" / "PortableFixUpdate",
        clock=clock, sleep=clock.sleep, **kwargs,
    )


def _status(tmp_path) -> str | None:
    path = tmp_path / "install" / "Data" / "update_status.txt"
    return path.read_text(encoding="ascii").strip() if path.exists() else None


def test_write_swap_job_installs_only_allowlisted_data_files(tmp_path, staged):
    job = update_swap.write_swap_job(staged, tmp_path / "install", [1, 2], tmp_path / "logs")

    data = json.loads(job.job_path.read_text(encoding="ascii"))
    installed = sorted(Path(c["Dst"]).name for c in data["DataCopies"])
    assert installed == [".gitkeep", "PortableFix-SelfSigned.cer", "SHA256SUMS"]
    assert [Path(c["Dst"]).name for c in data["RootCopies"]] == ["PortableFix.cmd"]
    assert data["Pids"] == [1, 2]
    assert data["MaxWaitSec"] == 600
    assert data["MutexName"] == "Global\\PortableFix_UpdateInProgress"
    assert [f["Name"] for f in data["Folders"]] == ["App", "Modules", "Vendor"]
    assert Path(data["MarkerFile"]).parent == tmp_path / "logs"
    assert job.script_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_launch_swap_succeeds_on_ready_and_marks_the_hand_off(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"marker": "ready 4321 5.1.19041.1"}

    result = _launch(tmp_path, staged)

    assert result.ok is True
    assert result.route == ROUTE_DIRECT
    assert result.child_pid == 4321
    assert _status(tmp_path) == update_swap.UPDATE_STATUS_HANDED_OFF
    argv, kwargs = fake_popen.calls[0]
    assert argv[-1].endswith(".ps1")
    assert kwargs["cwd"] == str(tmp_path / "temp" / "PortableFixUpdate")
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    text = result.diagnostics_path.read_text(encoding="utf-8")
    assert result.diagnostics_path.name == f"launch_{os.getpid()}.txt"
    assert "outcome: ok" in text
    assert "route: direct" in text
    assert "marker: ready 4321" in text


def test_launch_swap_counts_a_ready_marker_even_when_the_script_already_finished(tmp_path, staged, fake_popen):
    # When the processes it waits for exit right after 'ready' (a caller
    # that passes its own pids), the whole swap can finish between two
    # polls - that is a success, not an early exit.
    fake_popen.behaviour = {"marker": "ready 4321 5.1", "exit_code": 0}

    result = _launch(tmp_path, staged)

    assert result.ok is True


def test_launch_swap_passes_a_clean_environment(tmp_path, staged, fake_popen, monkeypatch):
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "C:\\old\\PortableFix.exe")
    monkeypatch.setenv("_MEIPASS2", "C:\\old\\_MEI1")
    fake_popen.behaviour = {"marker": "ready 1 5.1"}

    _launch(tmp_path, staged)

    env = fake_popen.calls[0][1]["env"]
    assert not [k for k in env if k.upper().startswith("_PYI_") or k.upper() == "_MEIPASS2"]
    text = (tmp_path / "temp" / "PortableFixUpdate" / f"launch_{os.getpid()}.txt").read_text(encoding="utf-8")
    assert "_PYI_ARCHIVE_FILE" in text  # listed among the removed keys


def test_launch_swap_waits_for_the_app_and_its_onefile_parent_by_default(tmp_path, staged, fake_popen, monkeypatch):
    monkeypatch.setattr(update_swap, "onefile_parent_pid", lambda: 777)
    fake_popen.behaviour = {"marker": "ready 1 5.1"}
    clock = _Clock()

    launch_swap(staged, tmp_path / "install", log_dir=tmp_path / "logs", clock=clock, sleep=clock.sleep)

    job = Path(fake_popen.calls[0][0][-1].replace(".ps1", ".json"))
    assert json.loads(job.read_text(encoding="ascii"))["Pids"] == [os.getpid(), 777]


def test_launch_swap_reports_a_blocked_language_mode(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"marker": "blocked ConstrainedLanguage", "exit_code": 3}

    result = _launch(tmp_path, staged)

    assert result.ok is False
    assert result.reason == update_swap.REASON_BLOCKED
    assert "ConstrainedLanguage" in result.detail
    assert result.exit_code == 3
    assert _status(tmp_path) is None
    assert "outcome: blocked_policy" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_explains_a_silent_exit(tmp_path, staged, fake_popen):
    # The signature of the DETACHED_PROCESS bug: exit code 0, no output.
    fake_popen.behaviour = {"exit_code": 0}

    result = _launch(tmp_path, staged)

    assert result.ok is False
    assert result.reason == update_swap.REASON_EXITED
    assert "0x00000000" in result.detail
    assert "no output" in result.detail
    assert _status(tmp_path) is None
    assert "outcome: exited" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_shows_what_powershell_printed(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"exit_code": 1, "output": b"At line:1 char:1\r\n+ ParserError: MissingEndCurlyBrace\r\n"}

    result = _launch(tmp_path, staged)

    assert result.reason == update_swap.REASON_EXITED
    assert "0x00000001" in result.detail
    assert "ParserError" in result.detail


def test_launch_swap_formats_negative_exit_codes_as_ntstatus(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"exit_code": -1073741502}

    result = _launch(tmp_path, staged)

    assert "0xC0000142" in result.detail


def test_launch_swap_reports_a_process_it_could_not_see(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"marker": "error pid 99 is not visible to the updater", "exit_code": 5}

    result = _launch(tmp_path, staged)

    assert result.reason == update_swap.REASON_EXITED
    assert "error pid 99" in result.detail
    assert _status(tmp_path) is None


def test_launch_swap_kills_an_updater_that_never_answers(tmp_path, staged, fake_popen):
    result = _launch(tmp_path, staged, timeout=45.0)

    assert result.ok is False
    assert result.reason == update_swap.REASON_TIMEOUT
    assert fake_popen.calls and _FakePopen.calls
    assert result.exit_code == 1  # the fake reports 1 once killed
    assert _status(tmp_path) is None
    assert "outcome: timeout" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_stops_waiting_when_cancelled(tmp_path, staged, fake_popen):
    result = _launch(tmp_path, staged, should_stop=lambda: True)

    assert result.reason == update_swap.REASON_CANCELLED
    assert _status(tmp_path) is None


def test_launch_swap_records_the_no_breakaway_route(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"raise_first": PermissionError(13, "Access is denied"), "marker": "ready 1 5.1"}

    result = _launch(tmp_path, staged)

    assert result.ok is True
    assert result.route == ROUTE_NO_BREAKAWAY
    assert "route: direct-no-breakaway" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_reports_a_spawn_error(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"raise_first": FileNotFoundError(2, "No such file", "powershell.exe")}

    result = _launch(tmp_path, staged)

    assert result.ok is False
    assert result.reason == update_swap.REASON_SPAWN_ERROR
    assert "No such file" in result.detail
    assert "outcome: spawn_error" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_warns_but_proceeds_when_the_updater_shares_a_kill_on_close_job(
    tmp_path, staged, fake_popen, monkeypatch
):
    monkeypatch.setattr(
        update_swap, "job_facts", lambda pid: {"app_in_job": True, "child_in_job": True, "kill_on_close": True}
    )
    fake_popen.behaviour = {"marker": "ready 1 5.1"}

    result = _launch(tmp_path, staged)

    assert result.ok is True
    assert result.warn_job is True
    assert "kill_on_close" in result.diagnostics_path.read_text(encoding="utf-8")


def test_launch_swap_appends_one_diagnostics_block_per_attempt(tmp_path, staged, fake_popen):
    fake_popen.behaviour = {"exit_code": 0}
    first = _launch(tmp_path, staged)
    fake_popen.behaviour = {"marker": "ready 1 5.1"}
    second = _launch(tmp_path, staged)

    assert first.diagnostics_path == second.diagnostics_path
    assert first.diagnostics_path.read_text(encoding="utf-8").count("=== update launch") == 2


def test_launch_swap_real_powershell_handshake(tmp_path, staged, monkeypatch):
    """The real spawn path with a real PowerShell: the handshake reaches
    'ready' while this process (the 'app') is alive, and the script is then
    killed before it could swap anything."""
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    monkeypatch.setattr(update_swap, "powershell_executable", lambda: exe)
    if sys.platform != "win32":
        monkeypatch.setattr(update_swap, "SWAP_CREATIONFLAGS", 0)

    result = launch_swap(staged, tmp_path / "install", log_dir=tmp_path / "temp" / "PortableFixUpdate", timeout=60)

    try:
        assert result.ok is True, result.detail
        assert result.route == ROUTE_DIRECT
        assert _status(tmp_path) == update_swap.UPDATE_STATUS_HANDED_OFF
    finally:
        if result.child_pid:
            try:
                os.kill(result.child_pid, 9)
            except OSError:
                pass
    assert (tmp_path / "install" / "App" / "PortableFix.exe").read_bytes() == b"old-exe"


# --- Startup helpers (main.py) ----------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 mutex")
def test_update_mutex_present_sees_a_held_update_mutex():
    assert update_swap.update_mutex_present() is False
    handle, error = update_swap.create_mutex(update_swap.UPDATE_MUTEX_NAME)
    try:
        assert handle and error == 0
        assert update_swap.update_mutex_present() is True
    finally:
        update_swap.close_handle(handle)
    assert update_swap.update_mutex_present() is False


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 process handle")
def test_wait_for_process_exit_waits_for_a_real_process():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        assert update_swap.wait_for_process_exit(proc.pid, 0.05) is False
        assert update_swap.wait_for_process_exit(proc.pid, 30) is True
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="the Win32 calls are real there")
def test_startup_helpers_are_harmless_off_windows():
    assert update_swap.update_mutex_present() is False
    assert update_swap.wait_for_process_exit(os.getpid(), 0.01) is True
