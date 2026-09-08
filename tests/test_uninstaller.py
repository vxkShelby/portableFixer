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
    ok, output = uninstaller.uninstall_program(program)
    assert ok is True
    assert "QUIET" in output
    assert "PLAIN" not in output


def test_uninstall_program_falls_back_to_plain_string():
    _make_entry("App1", DisplayName="Test App", UninstallString="cmd /c echo PLAIN")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    ok, output = uninstaller.uninstall_program(program)
    assert ok is True
    assert "PLAIN" in output


def test_uninstall_program_returns_false_when_no_uninstall_command():
    _make_entry("App1", DisplayName="Test App")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    ok, message = uninstaller.uninstall_program(program)
    assert ok is False
    assert "No uninstall command" in message


def test_uninstall_program_reports_failure_on_nonzero_exit():
    _make_entry("App1", DisplayName="Test App", UninstallString="cmd /c exit 1")
    program = uninstaller.list_installed_programs(reg_paths=_TEST_REG_PATHS)[0]
    ok, _ = uninstaller.uninstall_program(program)
    assert ok is False
