import ctypes


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def relaunch_as_admin(executable: str, args: list[str] | None = None) -> None:
    params = " ".join(args or [])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
