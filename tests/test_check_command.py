"""Research G09: "already applied / nothing to do" checks.

The generated check of an `ops:` action is exercised against a throw-away
HKCU key; the hand-written checks in the catalog are run for real (they only
read) to prove each answers with one of the three states."""

import uuid
import winreg
from pathlib import Path

import pytest

from portablefix import action_service, ops
from portablefix.executor import PlanRun, build_execution_plan, parse_check_state
from portablefix.models import ActionDef, RiskLevel
from portablefix.module_engine import ModuleLoadError, load_all_modules, load_module

REPO = Path(__file__).resolve().parent.parent


def test_parse_check_state_reads_the_last_line_only():
    assert parse_check_state(["noise", "APPLIED"]) == "APPLIED"
    assert parse_check_state(["APPLIED", "not_applied  ", ""]) == "NOT_APPLIED"
    assert parse_check_state(["APPLIED", "something else"]) == "UNKNOWN"
    assert parse_check_state([]) == "UNKNOWN"


def _plan(command: str):
    return build_execution_plan(command, dry_run=False)


def test_an_applied_check_skips_the_command_and_counts_as_success():
    run = PlanRun(_plan("Write-Output 'RAN'; exit 5"), check_plan=_plan("Write-Output 'checking'; Write-Output 'APPLIED'"))
    assert run.run() == 0
    assert run.skipped_applied is True and run.check_state == "APPLIED"
    assert run.captured_output == ["[PortableFix] Already applied - nothing to do (state check: APPLIED). Skipped."]


@pytest.mark.parametrize("check", ["Write-Output 'NOT_APPLIED'", "Write-Output 'UNKNOWN'", "Write-Output 'APPLIED'; exit 3",
                                   "throw 'broken'"])
def test_anything_but_a_clean_applied_runs_the_command(check):
    run = PlanRun(_plan("Write-Output 'RAN'"), check_plan=_plan(check))
    assert run.run() == 0
    assert run.skipped_applied is False
    assert run.captured_output == ["RAN"]


def test_a_successful_run_is_checked_again_so_the_state_is_the_one_after_it(tmp_path):
    # The snapshot keeps this state: the next visit compares with it (drift).
    flag = tmp_path / "flag.txt"
    check = _plan(f"if (Test-Path -LiteralPath '{flag}') {{ 'APPLIED' }} else {{ 'NOT_APPLIED' }}")
    run = PlanRun(_plan(f"Set-Content -LiteralPath '{flag}' -Value x"), check_plan=check)
    assert run.run() == 0 and run.skipped_applied is False
    assert run.check_state == "APPLIED"
    failed = PlanRun(_plan("exit 4"), check_plan=_plan("'NOT_APPLIED'"))
    assert failed.run() == 4 and failed.check_state == "NOT_APPLIED"


def test_a_dry_run_never_checks():
    run = PlanRun(build_execution_plan("x", dry_run=True), check_plan=_plan("Write-Output 'APPLIED'"))
    assert run.run() == 0 and run.skipped_applied is False


def test_check_plan_only_for_a_real_run_of_an_action_with_a_check():
    action = ActionDef(id="a", label_sk="a", label_en="a", risk=RiskLevel.MODERATE, command="x", check_command="Write-Output APPLIED")
    assert action_service.check_plan(action, dry_run=True, target_user=None) is None
    assert action_service.check_plan(action, dry_run=False, target_user=None).argv[-1].endswith("Write-Output APPLIED")
    action.check_command = None
    assert action_service.check_plan(action, dry_run=False, target_user=None) is None


@pytest.fixture
def test_key():
    rel = f"Software\\PortableFixTest\\{uuid.uuid4().hex}"
    yield rel
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rel)
    except OSError:
        pass


def test_generated_ops_check_follows_the_live_state(test_key, tmp_path):
    op_list = ops.parse_ops([{"reg_set": {"path": "HKCU\\" + test_key, "name": "Level", "type": "DWord", "value": 3}}])
    check = ops.check_script(op_list)
    action = ActionDef(id="t", label_sk="t", label_en="t", risk=RiskLevel.MODERATE, command="x", check_command=check)
    assert action_service.check_state(action, None) == "NOT_APPLIED"
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, test_key)
    winreg.SetValueEx(key, "Level", 0, winreg.REG_DWORD, 3)
    winreg.CloseKey(key)
    assert action_service.check_state(action, None) == "APPLIED"
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, test_key, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, "Level", 0, winreg.REG_DWORD, 1)
    winreg.CloseKey(key)
    assert action_service.check_state(action, None) == "NOT_APPLIED"


def test_every_ops_action_gets_a_generated_check_and_a_hand_written_one_is_refused(tmp_path):
    modules, errors = load_all_modules(REPO / "Modules")
    assert errors == []
    for module in modules:
        for action in module.actions:
            if action.ops:
                assert action.check_command == ops.check_script(action.ops), action.id
    path = tmp_path / "actions.yaml"
    base = "module_id: m99\nactions:\n  - id: a\n    label_sk: X\n    label_en: X\n"
    for body in (
        "    risk: MODERATE\n    check_command: \"x\"\n    ops:\n      - service_start_type: {name: X, start_type: manual}\n",
        "    risk: SAFE\n    command: \"x\"\n    check_command: \"Write-Output APPLIED\"\n",
        "    risk: MODERATE\n    command: \"x\"\n    items_command: \"y\"\n    check_command: \"z\"\n",
        "    risk: MODERATE\n    command: \"x\"\n    check_command: \"\"\n",
    ):
        path.write_text(base + body, encoding="utf-8")
        with pytest.raises(ModuleLoadError):
            load_module(path)


def _catalog_checks():
    modules, _ = load_all_modules(REPO / "Modules")
    return [(a.id, a) for m in modules for a in m.actions if a.check_command and not a.ops]


@pytest.mark.parametrize("action_id,action", _catalog_checks(), ids=lambda v: v if isinstance(v, str) else "")
def test_catalog_checks_only_read_and_answer_with_a_state(action_id, action):
    for verb in ("Set-", "New-Item", "Remove-", "Disable-", "Enable-", "Stop-", "sc.exe", "reg ", "icacls"):
        assert verb not in action.check_command, (action_id, verb)
    run = PlanRun(_plan(action.check_command), inactivity_timeout_sec=60, hard_cap_sec=90)
    assert run.run() == 0, (action_id, run.captured_output)
    assert run.captured_output and run.captured_output[-1].strip() in ("APPLIED", "NOT_APPLIED", "UNKNOWN"), (
        action_id, run.captured_output,
    )
