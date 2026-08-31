import ctypes

from portablefix.elevation import is_admin, relaunch_as_admin


def test_is_admin_returns_bool():
    assert isinstance(is_admin(), bool)


def test_relaunch_as_admin_calls_shell_execute(monkeypatch):
    calls = []

    def fake_shell_execute(hwnd, verb, executable, params, directory, show_cmd):
        calls.append((verb, executable, params))
        return 42

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)
    relaunch_as_admin("C:/USB/PortableFix/App/PortableFix.exe", ["--flag", "value"])
    assert len(calls) == 1
    verb, executable, params = calls[0]
    assert verb == "runas"
    assert executable == "C:/USB/PortableFix/App/PortableFix.exe"
    assert params == "--flag value"
