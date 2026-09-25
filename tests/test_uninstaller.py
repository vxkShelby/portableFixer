import sys
import winreg

import pytest

from portablefix import uninstaller

_TEST_BASE = r"SOFTWARE\PortableFixTestUninstall"
_TEST_REG_PATHS = ((winreg.HKEY_CURRENT_USER, _TEST_BASE),)


def _make_entry(subkey_name: str, **values):
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{_TEST_BASE}\\{subkey_name}")
    for name, value in values.items():
        if isinstance(value, int):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    key.Close()


@pytest.fixture(autouse=True)
def _clean_test_registry_tree():
    def _delete_tree():
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _TEST_BASE)
        except OSError:
            return
        with key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                winreg.DeleteKey(key, sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _TEST_BASE)

    yield
    _delete_tree()


def test_list_installed_programs_reads_fields():
    _make_entry(
        "App1",
        DisplayName="Test App",
        Publisher="Test Publisher",
        DisplayVersion="1.2.3",
        EstimatedSize=4096,
        InstallLocation=r"C:\Program Files\Test App",
        InstallDate="20240315",
        UninstallString=r'"C:\Program Files\Test App\uninstall.exe"',
    )
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert len(programs) == 1
    p = programs[0]
    assert p.name == "Test App"
    assert p.install_date == "2024-03-15"
    assert p.publisher == "Test Publisher"
    assert p.version == "1.2.3"
    assert p.estimated_size_kb == 4096
    assert p.install_location == r"C:\Program Files\Test App"
    assert p.uninstall_string == r'"C:\Program Files\Test App\uninstall.exe"'
    assert p.registry_hive == winreg.HKEY_CURRENT_USER


def test_list_installed_programs_leaves_install_date_none_when_missing_or_malformed():
    _make_entry("App1", DisplayName="No Date")
    _make_entry("App2", DisplayName="Bad Date", InstallDate="not-a-date")
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert all(p.install_date is None for p in programs)


def test_list_installed_programs_skips_entries_without_display_name():
    _make_entry("App1", Publisher="No Name Here")
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert programs == []


def test_list_installed_programs_skips_system_components():
    _make_entry("App1", DisplayName="Hidden Runtime", SystemComponent=1)
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert programs == []


def test_list_installed_programs_skips_updates_and_hotfixes():
    _make_entry("KB1", DisplayName="Security Patch", ReleaseType="Hotfix")
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert programs == []


def test_list_installed_programs_sorted_by_name():
    _make_entry("App1", DisplayName="Zebra App")
    _make_entry("App2", DisplayName="Alpha App")
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    assert [p.name for p in programs] == ["Alpha App", "Zebra App"]


def test_find_orphaned_uninstall_entries_flags_missing_install_location():
    _make_entry(
        "Orphan",
        DisplayName="Ghost App",
        InstallLocation=r"C:\this\path\definitely\does\not\exist\PortableFixTest",
    )
    _make_entry(
        "NotOrphan",
        DisplayName="Real App",
        InstallLocation=str(__import__("pathlib").Path(__file__).resolve().parent),
    )
    orphans = uninstaller.find_orphaned_uninstall_entries(reg_paths=_TEST_REG_PATHS)
    assert [p.name for p in orphans] == ["Ghost App"]


def test_registry_key_still_exists_true_then_false_after_removal():
    _make_entry("App1", DisplayName="Test App")
    programs = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)
    program = programs[0]
    assert uninstaller.registry_key_still_exists(program) is True
    assert uninstaller.remove_registry_key(program.registry_hive, program.registry_path) is True
    assert uninstaller.registry_key_still_exists(program) is False


def test_remove_registry_key_returns_false_for_missing_key():
    assert uninstaller.remove_registry_key(winreg.HKEY_CURRENT_USER, f"{_TEST_BASE}\\DoesNotExist") is False


def test_uninstall_program_prefers_quiet_string_over_plain():
    _make_entry(
        "App1",
        DisplayName="Test App",
        UninstallString="cmd /c echo PLAIN",
        QuietUninstallString="cmd /c echo QUIET",
    )
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    result = uninstaller.uninstall_program(program)
    assert result.ok is True
    assert "QUIET" in result.output
    assert "PLAIN" not in result.output


def test_uninstall_program_falls_back_to_plain_string():
    _make_entry("App1", DisplayName="Test App", UninstallString="cmd /c echo PLAIN")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    result = uninstaller.uninstall_program(program)
    assert result.ok is True
    assert "PLAIN" in result.output


def test_uninstall_program_returns_false_when_no_uninstall_command():
    _make_entry("App1", DisplayName="Test App")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    result = uninstaller.uninstall_program(program)
    assert result.ok is False
    assert "No uninstall command" in result.output


def test_uninstall_program_reports_failure_on_nonzero_exit():
    _make_entry("App1", DisplayName="Test App", UninstallString="cmd /c exit 1")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    result = uninstaller.uninstall_program(program)
    assert result.ok is False and result.exit_code == 1


def _program(name: str, **fields) -> uninstaller.InstalledProgram:
    values = dict(
        name=name, publisher="", version="", estimated_size_kb=None, install_location=None,
        install_date=None, uninstall_string=None, quiet_uninstall_string=None, display_icon=None,
        registry_hive=winreg.HKEY_CURRENT_USER, registry_path=f"{_TEST_BASE}\\{name}",
    )
    values.update(fields)
    return uninstaller.InstalledProgram(**values)


def _completed(args, returncode: int):
    import subprocess

    return subprocess.CompletedProcess(args, returncode, b"", b"")


def test_find_orphaned_uninstall_entries_skips_programs_on_missing_drives_or_shares(monkeypatch):
    # An unplugged USB disk (Q:) or an unmapped share looks exactly like a
    # deleted install folder - those entries must never be offered for
    # deletion. Only a folder missing from a drive that is there counts.
    programs = [
        _program("Gone From C", install_location=r"C:\PortableFixTest\Gone"),
        _program("On Unplugged USB", install_location=r"Q:\Apps\Tool"),
        _program("On Unmapped Share", install_location=r"\\nas\apps\Tool"),
        _program("Still Installed", install_location=r"C:\PortableFixTest\Present"),
        _program("Quoted Location", install_location='"C:\\PortableFixTest\\Present"'),
        _program("Unexpanded Variable", install_location=r"%ProgramFiles%\Tool"),
    ]
    monkeypatch.setattr(uninstaller, "list_installed_programs", lambda reg_paths=None: programs)
    existing = {"C:\\", r"C:\PortableFixTest\Present"}
    faked = existing | {"Q:\\", "\\\\nas\\apps\\", r"C:\PortableFixTest\Gone"}
    real_exists = uninstaller.Path.exists
    queried = []

    def fake_exists(self, *args, **kwargs):
        text = str(self)
        if text not in faked:
            return real_exists(self, *args, **kwargs)
        queried.append(text)
        return text in existing

    monkeypatch.setattr(uninstaller.Path, "exists", fake_exists)

    orphans = uninstaller.find_orphaned_uninstall_entries()

    assert [p.name for p in orphans] == ["Gone From C"]
    # The missing roots were checked - and the folders on them never were.
    assert "Q:\\" in queried and "\\\\nas\\apps\\" in queried
    assert r"Q:\Apps\Tool" not in queried


def test_orphan_location_is_verifiable_rejects_paths_it_cannot_check():
    for location in ("", r"Program Files\Tool", r"%ProgramFiles%\Tool", r"C:Tool", r"\\server"):
        assert uninstaller.orphan_location_is_verifiable(location) is False, location


def test_program_command_prefers_the_quiet_string():
    program = _program("App", uninstall_string="app.exe /uninstall", quiet_uninstall_string="app.exe /S")
    assert uninstaller.program_command(program) == "app.exe /S"
    program.quiet_uninstall_string = None
    assert uninstaller.program_command(program) == "app.exe /uninstall"
    program.uninstall_string = ""
    assert uninstaller.program_command(program) is None


def test_backup_registry_key_runs_reg_export_and_requires_the_file(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        (tmp_path / "Backups" / "run1" / "uninstall_App.reg").write_text("REGEDIT", encoding="utf-16")
        return _completed(args, 0)

    monkeypatch.setattr(uninstaller.subprocess, "run", fake_run)
    dest = tmp_path / "Backups" / "run1" / "uninstall_App.reg"

    assert uninstaller.backup_registry_key(winreg.HKEY_CURRENT_USER, rf"{_TEST_BASE}\App", dest) is True

    args, kwargs = calls[0]
    assert args == ["reg", "export", rf"HKCU\{_TEST_BASE}\App", str(dest), "/y"]
    assert kwargs["creationflags"] == uninstaller.subprocess.CREATE_NO_WINDOW
    assert kwargs["timeout"] > 0


@pytest.mark.skipif(
    winreg.HKEY_LOCAL_MACHINE == winreg.HKEY_CURRENT_USER, reason="winreg stand-in without distinct hive handles"
)
def test_backup_registry_key_maps_hklm_to_its_reg_exe_root(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        (tmp_path / "x.reg").write_text("REGEDIT", encoding="utf-8")
        return _completed(args, 0)

    monkeypatch.setattr(uninstaller.subprocess, "run", fake_run)
    assert uninstaller.backup_registry_key(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\X", tmp_path / "x.reg") is True
    assert calls[0][2] == r"HKLM\SOFTWARE\X"


def test_backup_registry_key_returns_false_on_failure(monkeypatch, tmp_path):
    def failed_export(args, **kwargs):
        # A failed export may still leave a (partial) file behind.
        (tmp_path / "a.reg").write_text("partial", encoding="utf-8")
        return _completed(args, 1)

    monkeypatch.setattr(uninstaller.subprocess, "run", failed_export)
    assert uninstaller.backup_registry_key(winreg.HKEY_CURRENT_USER, r"SOFTWARE\X", tmp_path / "a.reg") is False

    # Exit 0 but no file written: still not a backup.
    monkeypatch.setattr(uninstaller.subprocess, "run", lambda args, **kwargs: _completed(args, 0))
    assert uninstaller.backup_registry_key(winreg.HKEY_CURRENT_USER, r"SOFTWARE\X", tmp_path / "b.reg") is False

    def missing_reg_exe(args, **kwargs):
        raise FileNotFoundError("reg")

    monkeypatch.setattr(uninstaller.subprocess, "run", missing_reg_exe)
    assert uninstaller.backup_registry_key(winreg.HKEY_CURRENT_USER, r"SOFTWARE\X", tmp_path / "c.reg") is False


def test_backup_registry_key_refuses_an_unknown_hive(monkeypatch, tmp_path):
    monkeypatch.setattr(uninstaller.subprocess, "run", lambda *a, **k: pytest.fail("reg.exe must not run"))
    assert uninstaller.backup_registry_key(0x7FFF_0001, r"SOFTWARE\X", tmp_path / "x.reg") is False


def test_orphan_backup_path_sanitizes_the_name_and_never_reuses_a_file(tmp_path):
    first = uninstaller.orphan_backup_path(tmp_path, 'Ghost: App / "x64" Čeština')
    assert first == tmp_path / "uninstall_Ghost_App_x64_Čeština.reg"
    first.write_text("x", encoding="utf-8")
    # Same DisplayName in HKLM and WOW6432Node - the second backup must not
    # overwrite the first.
    assert uninstaller.orphan_backup_path(tmp_path, 'Ghost: App / "x64" Čeština') == (
        tmp_path / "uninstall_Ghost_App_x64_Čeština_2.reg"
    )
    assert uninstaller.orphan_backup_path(tmp_path, "???").name == "uninstall_entry.reg"


def test_uninstall_runner_stops_between_programs_once_interrupted(qtbot, monkeypatch):
    # Closing the window waits for the uninstaller already running, not for
    # the rest of the list (each one may take up to UNINSTALL_TIMEOUT_SEC).
    import threading

    first_started = threading.Event()
    release = threading.Event()
    ran = []

    def fake_uninstall(program, *a, **k):
        ran.append(program.name)
        first_started.set()
        release.wait(10)
        return True, ""

    monkeypatch.setattr(uninstaller, "uninstall_program", fake_uninstall)
    runner = uninstaller.UninstallRunner([_program("One"), _program("Two"), _program("Three")])
    with qtbot.waitSignal(runner.all_finished, timeout=10000):
        runner.start()
        assert first_started.wait(10)
        runner.requestInterruption()
        release.set()
    runner.wait(10000)
    assert ran == ["One"]


@pytest.mark.skipif(sys.platform != "win32", reason="reads the real registry")
def test_list_installed_programs_reads_installer_type_hints():
    # Research G15: WindowsInstaller=1, Inno's own "Inno Setup: ..." values
    # and an NSIS mention anywhere in the entry pick the silent switches.
    _make_entry("{AAAAAAAA-1111-2222-3333-444444444444}", DisplayName="Msi App", WindowsInstaller=1)
    _make_entry("App2", DisplayName="Inno App", **{"Inno Setup: App Path": r"C:\Inno"})
    _make_entry("App3", DisplayName="Nsis App", Comments="Made with NSIS")
    _make_entry("App4", DisplayName="Plain App", WindowsInstaller=0)
    programs = {p.name: p for p in uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)}
    assert programs["Msi App"].windows_installer is True
    assert programs["Inno App"].inno_setup is True
    assert programs["Nsis App"].nsis_marker is True
    plain = programs["Plain App"]
    assert (plain.windows_installer, plain.inno_setup, plain.nsis_marker) == (False, False, False)


def test_uninstall_program_never_goes_through_a_shell(monkeypatch):
    # Registry text reaches CreateProcess as written - no "cmd /c" wrapper
    # that would give "&" a meaning.
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _completed(args, 0)

    monkeypatch.setattr(uninstaller.uninstall_plan.subprocess, "run", fake_run)
    program = _program("App", uninstall_string=r'"C:\App\remove.exe" & calc')
    result = uninstaller.uninstall_program(program)
    assert result.ok is True
    args, kwargs = calls[0]
    assert args == r"C:\App\remove.exe & calc" and kwargs["shell"] is False


def test_program_command_is_the_planned_silent_command():
    guid = "{AAAAAAAA-1111-2222-3333-444444444444}"
    program = _program("Msi", uninstall_string=f"MsiExec.exe /I{guid}", windows_installer=True)
    command = uninstaller.program_command(program)
    assert command.endswith(f"msiexec.exe /x {guid} /qn /norestart")
    assert "/I" not in command
    assert uninstaller.program_command(_program("None")) is None


def test_uninstall_runner_uses_the_timeout_only_for_the_silent_queue(qtbot, monkeypatch):
    from portablefix import uninstall_plan

    seen = []

    def fake_uninstall(program, timeout_sec=None, plan=None):
        seen.append((program.name, timeout_sec, plan.kind))
        busy = program.name == "Busy"
        return uninstall_plan.UninstallResult(not busy, "", 0, "busy_retry" if busy else "ok")

    monkeypatch.setattr(uninstaller, "uninstall_program", fake_uninstall)
    plans = {
        "Clicky": uninstall_plan.UninstallPlan(uninstall_plan.KIND_INTERACTIVE, ("u.exe",), False),
        "Busy": uninstall_plan.UninstallPlan(uninstall_plan.KIND_MSI, ("msiexec.exe",), True),
    }
    runner = uninstaller.UninstallRunner([_program("Clicky"), _program("Busy")], plans=plans)
    finished = []
    runner.program_finished.connect(lambda *args: finished.append(args))
    with qtbot.waitSignal(runner.all_finished, timeout=10000):
        runner.start()
    runner.wait(10000)
    assert seen == [
        ("Clicky", None, uninstall_plan.KIND_INTERACTIVE),
        ("Busy", uninstaller.UNINSTALL_TIMEOUT_SEC, uninstall_plan.KIND_MSI),
    ]
    assert finished == [("Clicky", True, "", "ok"), ("Busy", False, "", "busy_retry")]
