import subprocess

from portablefix.executor import POWERSHELL_PREFIX
from portablefix import restore_point
from portablefix.restore_point import (
    RESULT_MARKER,
    RestorePointRunner,
    build_restore_point_command,
    create_restore_point,
    parse_restore_point_output,
)


class _FakeResult:
    def __init__(self, returncode: int, stderr: bytes = b"", stdout: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_create_restore_point_success(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout, creationflags):
        captured["argv"] = argv
        captured["creationflags"] = creationflags
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    success, detail = create_restore_point("test checkpoint")
    assert success is True
    assert detail == ""
    assert captured["argv"][: len(POWERSHELL_PREFIX)] == POWERSHELL_PREFIX
    command = captured["argv"][-1]
    assert "test checkpoint" in command
    assert "Checkpoint-Computer" in command


def test_create_restore_point_suppresses_console_window(monkeypatch):
    # Regression test: without creationflags=CREATE_NO_WINDOW, this call
    # pops a visible console window every time a restore point is created
    # (which happens automatically before every destructive/repair/security
    # batch).
    captured = {}

    def fake_run(argv, capture_output, timeout, creationflags):
        captured["creationflags"] = creationflags
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point("test checkpoint")
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_create_restore_point_nonzero_returncode_is_false_with_stderr_detail(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout, creationflags: _FakeResult(1, stderr=b"Access is denied."),
    )
    success, detail = create_restore_point("x")
    assert success is False
    assert detail == "Access is denied."


def test_create_restore_point_exception_is_false_with_exception_detail(monkeypatch):
    def raise_error(argv, capture_output, timeout, creationflags):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", raise_error)
    success, detail = create_restore_point("x")
    assert success is False
    assert "boom" in detail


def test_create_restore_point_keeps_embedded_double_quotes_literal(monkeypatch):
    # Single-quoted PowerShell strings need no double-quote escaping - the
    # description is wrapped in single quotes, so an embedded " is literal.
    captured = {}

    def fake_run(argv, capture_output, timeout, creationflags):
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

    def fake_run(argv, capture_output, timeout, creationflags):
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

    def fake_run(argv, capture_output, timeout, creationflags):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point("PortableFix run$1")
    command = captured["argv"][-1]
    assert "'PortableFix run$1'" in command


def test_restore_point_command_uses_system_drive_not_hard_coded_c():
    # Windows isn't always installed on C: - protection must follow the OS drive.
    command = build_restore_point_command("x")
    assert "$env:SystemDrive" in command
    assert '"C:\\"' not in command


def test_restore_point_command_lifts_24h_throttle_and_restores_it():
    # Checkpoint-Computer silently skips (warning + exit 0) when a restore
    # point already exists from the last 24h - the batch then ran with no
    # fresh restore point while the app reported success.
    command = build_restore_point_command("x")
    assert "SystemRestorePointCreationFrequency" in command
    assert "-Value 0" in command
    # The previous value is put back (or removed if it didn't exist) in a
    # finally block, so the system setting is never left modified.
    finally_block = command[command.index("finally"):]
    assert "Set-ItemProperty" in finally_block
    assert "Remove-ItemProperty" in finally_block


def test_restore_point_command_turns_skip_warning_into_failure():
    command = build_restore_point_command("x")
    assert "-WarningAction Stop" in command
    assert "exit 1" in command


# --- created restore point identity (research-reporting.md F1) ---


def test_restore_point_command_looks_up_created_point_only_after_success():
    # The lookup must come after the try/catch/finally: the catch exits 1,
    # so it only runs once Checkpoint-Computer really created a point, and
    # the 24h throttle is already restored by then.
    command = build_restore_point_command("PortableFix run_1")
    lookup = command.index("Get-ComputerRestorePoint")
    assert lookup > command.index("finally")
    assert "$desc = 'PortableFix run_1'" in command
    assert "Checkpoint-Computer -Description $desc" in command
    assert "$_.Description -eq $desc" in command[lookup:]
    assert "Sort-Object SequenceNumber | Select-Object -Last 1" in command[lookup:]
    assert RESULT_MARKER in command[lookup:]


def test_restore_point_lookup_failure_cannot_fail_the_command():
    # A created restore point whose number can't be read is still a success:
    # the lookup sits in its own try with an empty catch and never exits 1.
    command = build_restore_point_command("x")
    lookup_part = command[command.index("Get-ComputerRestorePoint"):]
    assert lookup_part.rstrip().endswith("catch { }")
    assert "exit" not in lookup_part


def test_parse_restore_point_output_reads_marker_line_among_noise():
    stdout = (
        "some cmdlet noise\r\n"
        f'{RESULT_MARKER}{{"sequence":123,"created":"20260924101530.123456-000","description":"PortableFix r1"}}\r\n'
    )
    assert parse_restore_point_output(stdout) == {
        "sequence_number": 123,
        "creation_time": "20260924101530.123456-000",
        "description": "PortableFix r1",
    }


def test_parse_restore_point_output_missing_or_malformed_is_empty():
    assert parse_restore_point_output("") == {}
    assert parse_restore_point_output("WARNING: something\n") == {}
    assert parse_restore_point_output(f"{RESULT_MARKER}{{not json") == {}
    assert parse_restore_point_output(f"{RESULT_MARKER}[1, 2]") == {}
    assert parse_restore_point_output(f'{RESULT_MARKER}{{"created":"x"}}') == {}
    assert parse_restore_point_output(f'{RESULT_MARKER}{{"sequence":"12"}}') == {}
    assert parse_restore_point_output(f'{RESULT_MARKER}{{"sequence":true}}') == {}


def test_create_restore_point_returns_sequence_and_still_unpacks_as_pair(monkeypatch):
    stdout = f'{RESULT_MARKER}{{"sequence":42,"created":"c","description":"d"}}\n'.encode()
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout, creationflags: _FakeResult(0, stdout=stdout),
    )
    result = create_restore_point("d")
    success, detail = result
    assert (success, detail) == (True, "")
    assert result == (True, "")
    assert result.sequence_number == 42
    assert result.info == {"sequence_number": 42, "creation_time": "c", "description": "d"}


def test_create_restore_point_success_without_marker_has_no_sequence(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout, creationflags: _FakeResult(0, stdout=b"noise\n"),
    )
    result = create_restore_point("d")
    assert result == (True, "")
    assert result.sequence_number is None
    assert result.info == {}


def test_create_restore_point_failure_has_no_sequence(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout, creationflags: _FakeResult(1, stderr=b"denied"),
    )
    result = create_restore_point("d")
    assert result == (False, "denied")
    assert result.sequence_number is None


# --- throttle safety net when powershell.exe is killed (timeout) ---


class _FakeWinreg:
    """Just enough of winreg for the SystemRestore key: one value store."""

    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 1
    KEY_SET_VALUE = 2
    KEY_WOW64_64KEY = 0x100
    REG_DWORD = 4

    def __init__(self, value=None):
        # None = value absent; else (data, reg_type).
        self.value = value
        self.writes = []
        self.opened = []

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, subkey, reserved, access):
        self.opened.append((root, subkey, access))
        return self._Key()

    def QueryValueEx(self, key, name):
        assert name == "SystemRestorePointCreationFrequency"
        if self.value is None:
            raise FileNotFoundError(name)
        return self.value

    def SetValueEx(self, key, name, reserved, reg_type, data):
        self.writes.append(("set", name, data, reg_type))
        self.value = (data, reg_type)

    def DeleteValue(self, key, name):
        self.writes.append(("delete", name))
        if self.value is None:
            raise FileNotFoundError(name)
        self.value = None


def _timeout_run_that_zeroes_throttle(reg):
    # What a killed run leaves behind: the script already set the throttle
    # to 0, then TerminateProcess stopped it before its finally.
    def fake_run(argv, capture_output, timeout, creationflags):
        reg.value = (0, reg.REG_DWORD)
        raise subprocess.TimeoutExpired(argv, timeout)

    return fake_run


def test_timeout_restores_previous_throttle_value(monkeypatch):
    reg = _FakeWinreg(value=(1440, _FakeWinreg.REG_DWORD))
    monkeypatch.setattr(restore_point, "_winreg", reg)
    monkeypatch.setattr(subprocess, "run", _timeout_run_that_zeroes_throttle(reg))

    success, detail = create_restore_point("x")

    assert success is False
    assert "timed out" in detail and "Checkpoint-Computer -Description" not in detail
    assert reg.writes == [("set", "SystemRestorePointCreationFrequency", 1440, reg.REG_DWORD)]
    assert reg.value == (1440, reg.REG_DWORD)
    # The 64-bit registry view that 64-bit PowerShell wrote to.
    assert all(access & reg.KEY_WOW64_64KEY for _, _, access in reg.opened)
    assert {subkey for _, subkey, _ in reg.opened} == {
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
    }


def test_timeout_removes_throttle_value_that_was_absent(monkeypatch):
    reg = _FakeWinreg(value=None)
    monkeypatch.setattr(restore_point, "_winreg", reg)
    monkeypatch.setattr(subprocess, "run", _timeout_run_that_zeroes_throttle(reg))

    create_restore_point("x")

    assert reg.writes == [("delete", "SystemRestorePointCreationFrequency")]
    assert reg.value is None


def test_normal_end_does_not_rewrite_already_restored_throttle(monkeypatch):
    # The PowerShell finally already put the value back - no second write.
    reg = _FakeWinreg(value=(60, _FakeWinreg.REG_DWORD))
    monkeypatch.setattr(restore_point, "_winreg", reg)
    monkeypatch.setattr(
        subprocess, "run", lambda argv, capture_output, timeout, creationflags: _FakeResult(0),
    )
    assert create_restore_point("x") == (True, "")
    monkeypatch.setattr(
        subprocess, "run", lambda argv, capture_output, timeout, creationflags: _FakeResult(1, stderr=b"no"),
    )
    assert create_restore_point("x") == (False, "no")
    assert reg.writes == []


def test_any_abnormal_end_leaving_throttle_changed_is_repaired(monkeypatch):
    # e.g. powershell.exe killed from outside: exit code 1, finally never ran.
    reg = _FakeWinreg(value=(60, _FakeWinreg.REG_DWORD))
    monkeypatch.setattr(restore_point, "_winreg", reg)

    def killed_run(argv, capture_output, timeout, creationflags):
        reg.value = (0, reg.REG_DWORD)
        return _FakeResult(1)

    monkeypatch.setattr(subprocess, "run", killed_run)
    create_restore_point("x")
    assert reg.value == (60, reg.REG_DWORD)


def test_unreadable_throttle_disables_safety_net(monkeypatch):
    # Can't know the original value -> never guess and overwrite it.
    reg = _FakeWinreg(value=(1440, _FakeWinreg.REG_DWORD))

    def denied(key, name):
        raise PermissionError("access denied")

    reg.QueryValueEx = denied
    monkeypatch.setattr(restore_point, "_winreg", reg)
    monkeypatch.setattr(subprocess, "run", _timeout_run_that_zeroes_throttle(reg))
    create_restore_point("x")
    assert reg.writes == []


def test_throttle_safety_net_is_a_no_op_without_winreg(monkeypatch):
    monkeypatch.setattr(restore_point, "_winreg", None)
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, capture_output, timeout, creationflags: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(argv, timeout)
        ),
    )
    success, detail = create_restore_point("x")
    assert success is False and "timed out" in detail


def test_restore_point_timeout_allows_slow_vss(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout, creationflags):
        captured["timeout"] = timeout
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    create_restore_point("x")
    assert captured["timeout"] >= 300


def test_runner_emits_info_and_tolerates_plain_tuple_stubs(monkeypatch):
    # run() is called directly (synchronously): only the emitted values matter.
    emitted = []
    monkeypatch.setattr(
        restore_point, "create_restore_point",
        lambda description: restore_point.RestorePointResult(True, "", {"sequence_number": 5}),
    )
    runner = RestorePointRunner("d")
    runner.result_ready.connect(lambda *args: emitted.append(args))
    runner.run()
    # Older stubs (and any caller-provided fake) return a bare 2-tuple.
    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: (False, "boom"))
    runner.run()
    assert emitted == [(True, "", {"sequence_number": 5}), (False, "boom", {})]
