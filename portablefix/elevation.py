import ctypes
import os
import subprocess


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(executable: str, args: list[str] | None = None, wait_pids: list[int] | None = None) -> int:
    """ShellExecuteW 'runas'. wait_pids become --wait-pid arguments: the
    elevated copy waits for those processes to exit before it takes the
    single-instance mutex (see main.py)."""
    argv = list(args or [])
    for pid in wait_pids or ():
        argv += ["--wait-pid", str(int(pid))]
    # list2cmdline quotes each arg Windows-correctly - a plain " ".join broke
    # on any path containing a space (e.g. the script path in dev mode).
    params = subprocess.list2cmdline(argv) if argv else ""
    # A onefile child passes its _PYI_* variables on; without the reset the
    # new bootloader takes them as its own and reuses this process's _MEI
    # folder, which is deleted as soon as this process exits.
    previous = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
    if result <= 32:
        # The app keeps running - leave its environment as it was.
        if previous is None:
            os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        else:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = previous
    return result
