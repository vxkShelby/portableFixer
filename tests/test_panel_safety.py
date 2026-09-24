"""Protected programs and "is it running?" for the Uninstaller/winget panels (G01)."""

from types import SimpleNamespace

import pytest

from portablefix import panel_safety
from portablefix.panel_safety import RunningProcess, protected_reason, running_matches

ENV = {
    "SystemRoot": "C:\\Windows",
    "WINDIR": "C:\\Windows",
    "ProgramFiles": "C:\\Program Files",
    "ProgramFiles(x86)": "C:\\Program Files (x86)",
    "ProgramData": "C:\\ProgramData",
    "LOCALAPPDATA": "C:\\Users\\tech\\AppData\\Local",
    "APPDATA": "C:\\Users\\tech\\AppData\\Roaming",
    "USERPROFILE": "C:\\Users\\tech",
}


def _program(name="Foo", publisher="Foo Ltd", install_location=None, display_icon=None,
             registry_path="SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Foo"):
    return SimpleNamespace(
        name=name, publisher=publisher, install_location=install_location,
        display_icon=display_icon, registry_path=registry_path,
    )


@pytest.mark.parametrize(
    ("program", "expected"),
    [
        # NVIDIA localizes DisplayName; the key names are what identify it.
        (_program("NVIDIA Grafiktreiber 546.33", "NVIDIA Corporation",
                  registry_path="SOFTWARE\\...\\Uninstall\\{B2FE1952-0186-46C3-BAEC-A80AA35AC5B8}_Display.Driver"),
         panel_safety.PROTECTED_GPU_DRIVER),
        (_program("NVIDIA HD-Audiotreiber", "NVIDIA Corporation",
                  registry_path="SOFTWARE\\...\\Uninstall\\{B2FE1952-0186-46C3-BAEC-A80AA35AC5B8}_HDAudio.Driver"),
         panel_safety.PROTECTED_AUDIO_DRIVER),
        (_program("AMD Software", "Advanced Micro Devices, Inc."), panel_safety.PROTECTED_GPU_DRIVER),
        (_program("Intel(R) Graphics Driver", "Intel Corporation"), panel_safety.PROTECTED_GPU_DRIVER),
        (_program("Realtek High Definition Audio Driver", "Realtek Semiconductor Corp."),
         panel_safety.PROTECTED_AUDIO_DRIVER),
        (_program("Intel(R) Chipset Device Software", "Intel(R) Corporation"), panel_safety.PROTECTED_CHIPSET_DRIVER),
        (_program("AMD Chipset Software", "Advanced Micro Devices, Inc."), panel_safety.PROTECTED_CHIPSET_DRIVER),
        (_program("Microsoft Edge WebView2 Runtime", "Microsoft Corporation"), panel_safety.PROTECTED_RUNTIME),
        (_program("Microsoft Store", "Microsoft Corporation"), panel_safety.PROTECTED_WINDOWS_COMPONENT),
        (_program("App Installer", "Microsoft Corporation"), panel_safety.PROTECTED_WINDOWS_COMPONENT),
        (_program("Windows Security", "Microsoft Corporation"), panel_safety.PROTECTED_WINDOWS_COMPONENT),
        (_program("PortableFix 1.12.0", "PortableFix"), panel_safety.PROTECTED_SELF),
    ],
)
def test_protected_programs_are_recognised(program, expected):
    assert protected_reason(program) == expected


@pytest.mark.parametrize(
    "program",
    [
        # Replacing these is routine technician work - deliberately not protected.
        _program("ESET Security", "ESET, spol. s r.o."),
        _program("Microsoft Visual C++ 2015-2022 Redistributable (x64)", "Microsoft Corporation"),
        _program("NinjaRMMAgent", "NinjaRMM LLC"),
        _program("NVIDIA GeForce Experience", "NVIDIA Corporation",
                 registry_path="SOFTWARE\\...\\Uninstall\\{B2FE1952-0186-46C3-BAEC-A80AA35AC5B8}_Display.GFExperience"),
        _program("Intel(R) Driver & Support Assistant", "Intel"),
        _program("Mozilla Firefox (x64 en-US)", "Mozilla"),
        _program("Realtek USB Card Reader", "Realtek Semiconductor Corp."),
    ],
)
def test_ordinary_programs_are_not_protected(program):
    assert protected_reason(program) is None


def test_the_installed_copy_running_right_now_is_protected_by_its_folder():
    program = _program("Some Repair Suite", install_location="D:\\Tools\\Repair\\")
    assert protected_reason(program, app_dir="D:\\Tools\\Repair") == panel_safety.PROTECTED_SELF
    assert protected_reason(program, app_dir="D:\\Tools\\Repair\\App") == panel_safety.PROTECTED_SELF
    assert protected_reason(program, app_dir="D:\\Tools\\RepairOther") is None


def test_a_generic_install_location_never_makes_a_program_look_like_portablefix():
    # A broken entry with InstallLocation "C:\Program Files" must not be
    # "PortableFix" just because the app happens to be installed under it.
    program = _program("Broken Entry", install_location="C:\\Program Files")
    assert protected_reason(program, app_dir="C:\\Program Files\\PortableFix", env=ENV) is None


def test_running_process_under_the_install_folder_matches_case_insensitively():
    program = _program(install_location="C:\\Program Files\\Foo\\")
    processes = [
        RunningProcess(10, "c:\\program files\\foo\\bin\\foo.exe"),
        RunningProcess(11, "C:\\Program Files\\FooBar\\bar.exe"),
        RunningProcess(12, "C:\\Windows\\explorer.exe"),
    ]
    assert running_matches(program, processes, env=ENV, own_pid=1) == [processes[0]]


def test_display_icon_exe_matches_exactly_without_install_location():
    program = _program(display_icon='"C:\\Program Files\\Foo\\foo.exe",0')
    processes = [RunningProcess(10, "C:\\Program Files\\Foo\\foo.exe"), RunningProcess(11, "C:\\Program Files\\Foo\\helper.exe")]
    assert running_matches(program, processes, env=ENV, own_pid=1) == [processes[0]]


@pytest.mark.parametrize(
    "location", ["C:\\", "C:\\Program Files", "C:\\Program Files (x86)\\", "C:\\Windows", "C:\\Windows\\System32",
                 "C:\\ProgramData", "C:\\Users\\tech\\AppData\\Local"],
)
def test_generic_install_locations_match_nothing(location):
    # Half the machine runs from these folders - "this program is running"
    # would be a false alarm for every uninstall.
    program = _program(install_location=location)
    processes = [RunningProcess(10, "C:\\Program Files\\Other\\x.exe"), RunningProcess(11, "C:\\Windows\\System32\\svchost.exe")]
    assert running_matches(program, processes, env=ENV, own_pid=1) == []


def test_an_icon_inside_windows_matches_nothing():
    program = _program(display_icon="C:\\Windows\\System32\\msiexec.exe,0")
    processes = [RunningProcess(10, "C:\\Windows\\System32\\msiexec.exe")]
    assert running_matches(program, processes, env=ENV, own_pid=1) == []


def test_portablefix_own_process_is_never_reported():
    program = _program(install_location="D:\\Tools\\PortableFix")
    processes = [RunningProcess(42, "D:\\Tools\\PortableFix\\App\\PortableFix.exe")]
    assert running_matches(program, processes, env=ENV, own_pid=42) == []


def test_unknown_paths_match_nothing():
    assert running_matches(_program(), [RunningProcess(10, "C:\\x.exe")], env=ENV, own_pid=1) == []


def test_list_processes_off_windows_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(panel_safety.sys, "platform", "linux")
    assert panel_safety.list_processes() == []


def test_list_processes_failure_means_unknown(monkeypatch):
    def boom():
        raise OSError("access denied")

    monkeypatch.setattr(panel_safety.sys, "platform", "win32")
    monkeypatch.setattr(panel_safety, "_win32_list_processes", boom)
    assert panel_safety.list_processes() == []


def test_running_process_exe_name():
    assert RunningProcess(1, "C:\\Program Files\\Foo\\foo.exe").exe_name == "foo.exe"
