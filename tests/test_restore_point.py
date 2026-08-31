import subprocess

from portablefix.executor import POWERSHELL_PREFIX
from portablefix.restore_point import create_restore_point


class _FakeResult:
    def __init__(self, returncode: int):
        self.returncode = returncode


def test_create_restore_point_success(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert create_restore_point("test checkpoint") is True
    assert captured["argv"][: len(POWERSHELL_PREFIX)] == POWERSHELL_PREFIX
    command = captured["argv"][-1]
    assert "test checkpoint" in command
    assert "Checkpoint-Computer" in command


def test_create_restore_point_nonzero_returncode_is_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, capture_output, timeout: _FakeResult(1))
    assert create_restore_point("x") is False


def test_create_restore_point_exception_is_false(monkeypatch):
    def raise_error(argv, capture_output, timeout):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", raise_error)
    assert create_restore_point("x") is False


def test_create_restore_point_escapes_embedded_quotes(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point('test "quoted" description')
    command = captured["argv"][-1]
    assert '"quoted"' not in command
    assert "'quoted'" in command
