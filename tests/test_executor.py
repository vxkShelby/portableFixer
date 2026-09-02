from unittest.mock import patch

from portablefix.executor import POWERSHELL_PREFIX, ActionRunner, _clean_line, build_execution_plan
from portablefix import executor as executor_module


def test_clean_line_strips_embedded_null_bytes():
    assert _clean_line("Y\x00o\x00u\x00 \x00m\x00u\x00s\x00t\x00\n") == "You must"


def test_clean_line_passes_through_normal_text_unchanged():
    assert _clean_line("normal output line\n") == "normal output line"


def test_build_execution_plan_dry_run():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=True)
    assert plan.mode == "dry_run"
    assert plan.display_command == "Write-Output 'hi'"
    assert plan.argv is None


def test_build_execution_plan_real_run():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    assert plan.mode == "run"
    assert plan.argv == POWERSHELL_PREFIX + [
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output 'hi'"
    ]


def test_build_execution_plan_real_run_forces_utf8_but_keeps_display_command_clean():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    assert plan.argv[-1] == "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output 'hi'"
    assert plan.display_command == "Write-Output 'hi'"


def test_action_runner_dry_run_emits_without_subprocess(qtbot):
    plan = build_execution_plan("Write-Output 'hi'", dry_run=True)
    runner = ActionRunner(plan)
    with qtbot.waitSignal(runner.finished_with_code, timeout=2000) as blocker:
        runner.start()
    assert blocker.args == [0]
    assert runner.captured_output == ["[DRY-RUN] Write-Output 'hi'"]


def test_action_runner_real_run_executes_powershell(qtbot):
    plan = build_execution_plan("Write-Output 'hello-from-test'", dry_run=False)
    runner = ActionRunner(plan)
    with qtbot.waitSignal(runner.finished_with_code, timeout=10000) as blocker:
        runner.start()
    assert blocker.args == [0]
    assert any("hello-from-test" in line for line in runner.captured_output)


def test_action_runner_emits_sentinel_code_on_read_error(qtbot):
    class FakeStdout:
        def fileno(self):
            return 99

    class FakeProcess:
        stdout = FakeStdout()
        returncode = 0

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    reads = [b"first line\n", None]

    def fake_read(fd, size):
        assert fd == 99
        chunk = reads.pop(0)
        if chunk is None:
            raise OSError("boom")
        return chunk

    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    runner = ActionRunner(plan)
    with patch("portablefix.executor.subprocess.Popen", return_value=FakeProcess()):
        with patch("portablefix.executor.os.read", side_effect=fake_read):
            with qtbot.waitSignal(runner.finished_with_code, timeout=2000) as blocker:
                runner.start()
    assert blocker.args == [-1]
    assert runner.captured_output == ["first line"]


def test_action_runner_splits_progress_on_bare_carriage_return(qtbot):
    class FakeStdout:
        def fileno(self):
            return 7

    class FakeProcess:
        stdout = FakeStdout()
        returncode = 0

        def wait(self):
            return self.returncode

        def kill(self):
            pass

    reads = [b"10%\r20%\r30%\n", b""]

    def fake_read(fd, size):
        return reads.pop(0)

    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    runner = ActionRunner(plan)
    with patch("portablefix.executor.subprocess.Popen", return_value=FakeProcess()):
        with patch("portablefix.executor.os.read", side_effect=fake_read):
            with qtbot.waitSignal(runner.finished_with_code, timeout=2000) as blocker:
                runner.start()
    assert blocker.args == [0]
    assert runner.captured_output == ["10%", "20%", "30%"]


def test_action_runner_cancel_kills_process_and_reports_cancelled_code(qtbot):
    plan = build_execution_plan("Start-Sleep -Seconds 30", dry_run=False)
    runner = ActionRunner(plan)
    results = []
    runner.finished_with_code.connect(results.append)
    runner.start()
    qtbot.waitUntil(lambda: runner._process is not None, timeout=5000)
    runner.cancel()
    qtbot.waitUntil(lambda: len(results) == 1, timeout=10000)
    assert results[0] == ActionRunner.CANCELLED_EXIT_CODE


def test_action_runner_reports_sentinel_code_when_powershell_not_found(qtbot):
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    runner = ActionRunner(plan)
    lines = []
    runner.output_line.connect(lines.append)
    with patch("portablefix.executor.subprocess.Popen", side_effect=FileNotFoundError("no powershell")):
        with qtbot.waitSignal(runner.finished_with_code, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [ActionRunner.POWERSHELL_NOT_FOUND_EXIT_CODE]
    assert any("PowerShell was not found" in line for line in lines)


def test_action_runner_watchdog_kills_process_after_inactivity_timeout(qtbot):
    plan = build_execution_plan("Start-Sleep -Seconds 30", dry_run=False)
    runner = ActionRunner(plan)
    results = []
    runner.finished_with_code.connect(results.append)
    with patch.object(executor_module, "INACTIVITY_TIMEOUT_SEC", 0.2):
        with patch.object(executor_module, "WATCHDOG_POLL_SEC", 0.1):
            runner.start()
            qtbot.waitUntil(lambda: len(results) == 1, timeout=10000)
    assert results[0] == ActionRunner.TIMEOUT_EXIT_CODE
