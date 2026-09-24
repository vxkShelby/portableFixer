import ctypes
import os

from portablefix.elevation import is_admin, relaunch_as_admin


def test_is_admin_returns_bool():
    assert isinstance(is_admin(), bool)


def test_relaunch_as_admin_calls_shell_execute(monkeypatch):
    calls = []

    def fake_shell_execute(hwnd, verb, executable, params, directory, show_cmd):
        calls.append((verb, executable, params))
        return 42

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)
    result = relaunch_as_admin("C:/USB/PortableFix/App/PortableFix.exe", ["--flag", "value"])
    assert len(calls) == 1
    verb, executable, params = calls[0]
    assert verb == "runas"
    assert executable == "C:/USB/PortableFix/App/PortableFix.exe"
    assert params == "--flag value"
    assert result == 42


def test_relaunch_as_admin_quotes_args_containing_spaces(monkeypatch):
    calls = []

    def fake_shell_execute(hwnd, verb, executable, params, directory, show_cmd):
        calls.append(params)
        return 42

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)
    relaunch_as_admin("C:/Python/python.exe", ["C:\\USB Fixer\\main.py"])
    assert calls[0] == '"C:\\USB Fixer\\main.py"'


def test_relaunch_as_admin_passes_wait_pids_and_resets_the_onefile_environment(monkeypatch):
    calls = []

    def fake_shell_execute(hwnd, verb, executable, params, directory, show_cmd):
        calls.append((params, os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")))
        return 42

    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", fake_shell_execute)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)

    relaunch_as_admin("C:/USB/PortableFix/App/PortableFix.exe", [], wait_pids=[1234, 99])

    assert calls == [("--wait-pid 1234 --wait-pid 99", "1")]


def test_relaunch_as_admin_restores_the_environment_when_cancelled(monkeypatch):
    monkeypatch.setattr(ctypes.windll.shell32, "ShellExecuteW", lambda *a: 5)
    monkeypatch.delenv("PYINSTALLER_RESET_ENVIRONMENT", raising=False)

    assert relaunch_as_admin("C:/x.exe", ["a"], wait_pids=[1]) == 5
    assert "PYINSTALLER_RESET_ENVIRONMENT" not in os.environ
