from portablefix.executor import POWERSHELL_PREFIX, ActionRunner, build_execution_plan


def test_build_execution_plan_dry_run():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=True)
    assert plan.mode == "dry_run"
    assert plan.display_command == "Write-Output 'hi'"
    assert plan.argv is None


def test_build_execution_plan_real_run():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    assert plan.mode == "run"
    assert plan.argv == POWERSHELL_PREFIX + ["Write-Output 'hi'"]


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
