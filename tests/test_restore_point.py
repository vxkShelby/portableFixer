import subprocess

from portablefix.executor import POWERSHELL_PREFIX
from portablefix.restore_point import create_restore_point


class _FakeResult:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr


def test_create_restore_point_success(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    success, detail = create_restore_point("test checkpoint")
    assert success is True
    assert detail == ""
    assert captured["argv"][: len(POWERSHELL_PREFIX)] == POWERSHELL_PREFIX
    command = captured["argv"][-1]
    assert "test checkpoint" in command
    assert "Checkpoint-Computer" in command


def test_create_restore_point_nonzero_returncode_is_false_with_stderr_detail(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout: _FakeResult(1, stderr=b"Access is denied."),
    )
    success, detail = create_restore_point("x")
    assert success is False
    assert detail == "Access is denied."


def test_create_restore_point_exception_is_false_with_exception_detail(monkeypatch):
    def raise_error(argv, capture_output, timeout):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", raise_error)
    success, detail = create_restore_point("x")
    assert success is False
    assert "boom" in detail


def test_create_restore_point_keeps_embedded_double_quotes_literal(monkeypatch):
    # Single-quoted PowerShell strings need no double-quote escaping - the
    # description is wrapped in single quotes, so an embedded " is literal.
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point('test "quoted" description')
    command = captured["argv"][-1]
    assert "'test \"quoted\" description'" in command


def test_create_restore_point_escapes_embedded_single_quote(monkeypatch):
    # A literal ' must be doubled ('') inside a single-quoted PS string, or
    # it prematurely ends the string - the exact bug class updater.py's
    # _ps_quote was written to prevent, now shared here too.
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point("O'Brien's PC")
    command = captured["argv"][-1]
    assert "O''Brien''s PC" in command


def test_create_restore_point_single_quotes_do_not_interpolate_dollar_sign(monkeypatch):
    # A literal '$' is legal in a description and must never be read as a
    # PowerShell variable reference - single-quoted strings never interpolate.
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point("PortableFix run$1")
    command = captured["argv"][-1]
    assert "'PortableFix run$1'" in command
