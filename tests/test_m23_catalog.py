from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m23_antivirus" / "actions.yaml"


def test_m23_catalog_loads_5_actions_in_antivirus_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m23_antivirus"
    assert module.category == ModuleCategory.ANTIVIRUS
    assert len(module.actions) == 5


def test_m23_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "sec_defender_status",
        "sec_defender_update",
        "sec_defender_quickscan",
        "sec_defender_exclusions_list",
        "hard_defender_clear_exclusions",
    }


def test_m23_catalog_only_clear_exclusions_has_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["hard_defender_clear_exclusions"].undo_command is not None
    for not_undoable in ("sec_defender_status", "sec_defender_update", "sec_defender_quickscan", "sec_defender_exclusions_list"):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m23_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"sec_defender_status", "sec_defender_exclusions_list"}
    assert set(by_risk[RiskLevel.MODERATE]) == {"sec_defender_update", "sec_defender_quickscan", "hard_defender_clear_exclusions"}


def test_m23_clear_exclusions_fails_loudly_instead_of_faking_success():
    # Success in the report/history, and whether an undo is recorded, come
    # only from the exit code. Without administrator Get-MpPreference returns
    # "N/A: Must be an administrator to view exclusions" instead of the real
    # list - that placeholder used to overwrite the real backup, every
    # Remove-MpPreference failed non-terminating, and the action still
    # reported success with an undo that would add "N/A: ..." as exclusions.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "hard_defender_clear_exclusions").command
    assert "'N/A:*'" in command
    na_branch = command[command.index("'N/A:*'"):command.index("Set-Content")]
    assert "exit 1" in na_branch
    for kind in ("ExclusionPath", "ExclusionExtension", "ExclusionProcess", "ExclusionIpAddress"):
        assert f"Remove-MpPreference -{kind} $p.{kind} -EA Stop" in command, kind
    catch_branch = command[command.rindex("catch {"):]
    assert "exit 1" in catch_branch
    # Group-Policy-enforced exclusions survive Remove-MpPreference without an
    # error - only re-reading the preferences afterwards shows they're still there.
    after_removal = command[command.rindex("Remove-MpPreference"):command.rindex("catch {")]
    assert "Get-MpPreference -EA Stop" in after_removal
    assert "exit 1" in after_removal


def test_m23_clear_exclusions_undo_fails_loudly():
    module = load_module(CATALOG_PATH)
    undo = next(a for a in module.actions if a.id == "hard_defender_clear_exclusions").undo_command
    for key in ("Path", "Extension", "Process", "IpAddress"):
        assert f"Add-MpPreference -Exclusion{key} $b.{key} -EA Stop" in undo, key
    assert undo.index("try {") < undo.index("Add-MpPreference")
    assert "exit 1" in undo[undo.index("catch {"):]
