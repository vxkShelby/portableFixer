from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m20_software_updates" / "actions.yaml"


def test_m20_catalog_loads_6_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m20_software_updates"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 6


def test_m20_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "winget_list_installed", "winget_list_outdated",
        "winget_export_installed", "winget_source_list",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"winget_update_all", "winget_source_reset"}
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m20_catalog_no_action_has_undo():
    # winget upgrade/source reset have no generic downgrade/restore path -
    # none of these are undoable.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None, action.id


def test_m20_catalog_every_action_guards_missing_winget():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert "Get-Command winget" in action.command, action.id


def test_m20_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "winget_list_installed", "winget_list_outdated", "winget_update_all",
        "winget_export_installed", "winget_source_list", "winget_source_reset",
    }


def test_m20_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m20_catalog_export_installed_locks_down_its_output_folder():
    # winget_export_installed writes a package manifest under the shared
    # ProgramData folder - it must restrict it to the current user, SYSTEM,
    # and Administrators, same as the sibling export actions in m01/m10.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "winget_export_installed")
    command = action.command
    assert "icacls" in command
    assert "S-1-5-32-544" in command
    assert "S-1-5-18" in command
    assert "$env:USERDOMAIN" in command
    # The shared $env:ProgramData\PortableFix parent must also be locked
    # down unconditionally (not just the per-run subfolder) - icacls is
    # idempotent, so this must run every time, not only on first creation,
    # or an already-existing unhardened root from an older install never
    # gets fixed.
    assert 'icacls $root /inheritance:r' in command
    assert "Test-Path $root" not in command


def test_m20_catalog_source_reset_exit_code_reflects_actual_result():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "winget_source_reset")
    assert "$LASTEXITCODE" in action.command
    assert "exit 1" in action.command
