import ctypes
import subprocess


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(executable: str, args: list[str] | None = None) -> int:
    # list2cmdline quotes each arg Windows-correctly - a plain " ".join broke
    # on any path containing a space (e.g. the script path in dev mode).
    params = subprocess.list2cmdline(args) if args else ""
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
