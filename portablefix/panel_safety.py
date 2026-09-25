"""Safety checks for the Uninstaller and winget panels (research G01).

Qt-free and registry-free: it works on anything shaped like
uninstaller.InstalledProgram (name, publisher, install_location,
display_icon, registry_path), so tests feed it plain objects on any OS.

Two questions:
- protected_reason(): is this a program PortableFix refuses to uninstall?
- running_matches(): is the program running right now (by process image
  path, never by localized window titles or process descriptions)?
"""

import ntpath
import os
import re
import sys
from dataclasses import dataclass

# --- protected programs -------------------------------------------------------
#
# Deliberately short. Only entries whose removal commonly leaves the PC
# without display, sound or working USB/power management, or takes out a
# component Windows itself and other apps depend on - the failures a
# technician then has to fix on-site (research G01; RAPR #353: a PC that no
# longer booted after a driver was removed). NOT protected, on purpose:
# antivirus products, RMM agents and Visual C++ runtimes - replacing or
# removing those is routine technician work, and the confirmation dialog
# already covers it. Matching uses publisher names, product brand tokens and
# registry key names, which Windows and vendors do not translate.

PROTECTED_SELF = "self"
PROTECTED_WINDOWS_COMPONENT = "windows_component"
PROTECTED_GPU_DRIVER = "gpu_driver"
PROTECTED_AUDIO_DRIVER = "audio_driver"
PROTECTED_CHIPSET_DRIVER = "chipset_driver"
PROTECTED_RUNTIME = "runtime"

# Store apps are normally not in the Win32 Uninstall list at all; listed
# anyway so a mirrored/injected entry for them can never be "uninstalled".
_WINDOWS_COMPONENT_NAMES = {
    "microsoft store",
    "app installer",
    "windows package manager",
    "windows security",
    "windows defender",
}
_AMD_PUBLISHERS = ("advanced micro devices", "amd")


def _key_name(program) -> str:
    return ntpath.basename(str(getattr(program, "registry_path", "") or "")).lower()


def _norm_dir(path: str | None) -> str:
    if not path:
        return ""
    text = str(path).strip().strip('"').rstrip("\\/")
    if not text:
        return ""
    return ntpath.normcase(ntpath.normpath(text))


def _within(child: str, parent: str) -> bool:
    return bool(child and parent) and (child == parent or child.startswith(parent + "\\"))


def protected_reason(program, app_dir: str | None = None, env: dict | None = None) -> str | None:
    """A PROTECTED_* code when the program must not be uninstalled, else None."""
    name = str(getattr(program, "name", "") or "").strip().lower()
    publisher = str(getattr(program, "publisher", "") or "").strip().lower()
    key = _key_name(program)

    # The running PortableFix itself (installed copy): its uninstaller would
    # delete the app, its Modules and this session's logs mid-run.
    if name.startswith("portablefix"):
        return PROTECTED_SELF
    location = _norm_dir(getattr(program, "install_location", None))
    app = _norm_dir(app_dir)
    if location and app and not is_generic_dir(location, env) and _within(app, location):
        return PROTECTED_SELF

    if name in _WINDOWS_COMPONENT_NAMES:
        return PROTECTED_WINDOWS_COMPONENT

    # NVIDIA's installer localizes DisplayName ("NVIDIA Grafiktreiber"), but
    # its Uninstall key names are fixed: "{GUID}_Display.Driver" and the HDMI
    # audio driver "{GUID}_HDAudio.Driver".
    if key.endswith("_display.driver"):
        return PROTECTED_GPU_DRIVER
    if key.endswith("_hdaudio.driver"):
        return PROTECTED_AUDIO_DRIVER
    if publisher.startswith(_AMD_PUBLISHERS) and ("amd software" in name or "radeon" in name):
        return PROTECTED_GPU_DRIVER
    if publisher.startswith("intel") and re.search(r"graphics.*driver", name):
        return PROTECTED_GPU_DRIVER
    if publisher.startswith("realtek") and "audio" in name:
        return PROTECTED_AUDIO_DRIVER
    if publisher.startswith(("intel", *_AMD_PUBLISHERS)) and "chipset" in name:
        return PROTECTED_CHIPSET_DRIVER

    # WebView2 is what Office, Teams, Widgets and many installers render with;
    # on Windows 11 it is part of the OS.
    if name.startswith("microsoft edge webview2") or key == "microsoft edgewebview":
        return PROTECTED_RUNTIME
    return None


# --- running programs ---------------------------------------------------------


def _env_dirs(env: dict | None) -> list[str]:
    env = os.environ if env is None else env
    names = ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
             "LOCALAPPDATA", "APPDATA", "USERPROFILE", "CommonProgramFiles", "CommonProgramFiles(x86)")
    return [d for d in (_norm_dir(env.get(n)) for n in names) if d]


def is_generic_dir(path: str, env: dict | None = None) -> bool:
    """A folder too broad to stand for one program: a drive root, Windows
    (and anything under it - drivers point into System32), Program Files,
    ProgramData, the profile folders. Broken Uninstall entries do carry an
    InstallLocation like "C:\\Program Files" - matching processes under it
    would flag half the machine as "this program is running"."""
    norm = _norm_dir(path)
    if not norm or re.fullmatch(r"[a-z]:", norm):
        return True
    return _under_windows(norm, env) or norm in _env_dirs(env)


def _under_windows(norm: str, env: dict | None) -> bool:
    env = os.environ if env is None else env
    return any(_within(norm, w) for w in (_norm_dir(env.get(n)) for n in ("SystemRoot", "WINDIR")) if w)


@dataclass(frozen=True)
class RunningProcess:
    pid: int
    image: str

    @property
    def exe_name(self) -> str:
        return ntpath.basename(self.image)


def program_paths(program, env: dict | None = None) -> tuple[str, str]:
    """(install folder, main exe) to match running processes against; either
    is "" when unknown or too generic to be trusted."""
    location = _norm_dir(getattr(program, "install_location", None))
    if location and is_generic_dir(location, env):
        location = ""
    exe = ""
    icon = str(getattr(program, "display_icon", "") or "")
    if icon:
        # "C:\...\app.exe,0" - strip the icon index, like uninstaller.launch_program.
        candidate = icon.rsplit(",", 1)[0].strip().strip('"')
        if candidate.lower().endswith(".exe"):
            exe = _norm_dir(candidate)
            # An exe inside Windows (explorer.exe, msiexec.exe as an icon)
            # would match processes that have nothing to do with the program.
            if _under_windows(exe, env):
                exe = ""
    return location, exe


def running_matches(program, processes: list[RunningProcess], env: dict | None = None,
                    own_pid: int | None = None) -> list[RunningProcess]:
    location, exe = program_paths(program, env)
    if not location and not exe:
        return []
    own = os.getpid() if own_pid is None else own_pid
    matches = []
    for proc in processes:
        if proc.pid == own:
            continue
        image = _norm_dir(proc.image)
        if not image:
            continue
        if (exe and image == exe) or (location and _within(image, location)):
            matches.append(proc)
    return matches


def list_processes() -> list[RunningProcess]:
    """Every process whose image path this (usually elevated) process can
    read. Win32 calls only - no tasklist/Get-Process text. [] off Windows
    or on any failure: an unknown must never block an uninstall."""
    if sys.platform != "win32":
        return []
    try:
        return _win32_list_processes()
    except Exception:  # noqa: BLE001 - any failure means "cannot tell"
        return []


def _win32_list_processes() -> list[RunningProcess]:
    import ctypes
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information = 0x1000

    count = 4096
    while True:
        pids = (wintypes.DWORD * count)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(needed)):
            return []
        if needed.value < ctypes.sizeof(pids):
            break
        count *= 2
    result = []
    for pid in pids[: needed.value // ctypes.sizeof(wintypes.DWORD)]:
        if not pid:
            continue
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            continue
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                result.append(RunningProcess(int(pid), buf.value))
        finally:
            kernel32.CloseHandle(handle)
    return result
