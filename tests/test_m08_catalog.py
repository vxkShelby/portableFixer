from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m08_security" / "actions.yaml"


def test_m08_catalog_loads_17_actions_in_security_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m08_security"
    assert module.category == ModuleCategory.SECURITY
    assert len(module.actions) == 17


def test_m08_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 11
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "sec_defender_quickscan",
        "sec_defender_update",
        "hard_defender_clear_exclusions",
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_restore_taskmgr_regedit",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m08_catalog_only_hardening_actions_have_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "hard_defender_clear_exclusions",
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_restore_taskmgr_regedit",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "sec_defender_status",
        "sec_firewall_status",
        "sec_defender_quickscan",
        "sec_defender_update",
        "sec_uac_status",
        "sec_defender_exclusions_list",
        "sec_rdp_status",
        "sec_autologon_check",
        "sec_listening_ports_audit",
        "sec_root_cert_audit",
        "sec_bootsector_check",
        "sec_hidden_process_heuristic",
        "sec_process_signature_audit",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m08_catalog_autologon_check_never_prints_the_password_itself():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "sec_autologon_check")
    assert "DefaultPassword" in action.command
    assert "+ $p.DefaultPassword" not in action.command
    assert "[bool]$p.DefaultPassword" in action.command


def test_m08_catalog_covers_expected_audit_surfaces():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "sec_defender_status",
        "sec_firewall_status",
        "sec_defender_quickscan",
        "sec_defender_update",
        "sec_uac_status",
        "sec_defender_exclusions_list",
        "sec_rdp_status",
        "sec_autologon_check",
        "hard_defender_clear_exclusions",
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_listening_ports_audit",
        "sec_root_cert_audit",
        "sec_bootsector_check",
        "sec_hidden_process_heuristic",
        "sec_process_signature_audit",
        "sec_restore_taskmgr_regedit",
    }


def test_m08_catalog_rootkit_adjacent_heuristics_disclose_their_own_limits():
    # These three deliberately do NOT claim to be real kernel-level rootkit
    # detection (that needs a signed kernel driver, which this app does not
    # have) - each description must say so explicitly rather than let a user
    # assume PortableFix caught something it structurally cannot catch.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "kernel" in by_id["sec_hidden_process_heuristic"].description_en.lower()
    assert "not real bootkit detection" in by_id["sec_bootsector_check"].description_en.lower()
    for action_id in ("sec_bootsector_check", "sec_hidden_process_heuristic", "sec_process_signature_audit"):
        assert by_id[action_id].risk == RiskLevel.SAFE, action_id


def test_m08_catalog_uac_restore_verifies_the_registry_write_actually_worked():
    # Set-ItemProperty on this HKLM policy key throws a non-terminating
    # SecurityException without administrator, which -EA SilentlyContinue
    # (or no -EA at all) would swallow while still claiming success.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "hard_uac_restore_default")
    assert "-EA Stop" in action.command
    assert "exit 1" in action.command
    assert "-EA Stop" in action.undo_command
    assert "exit 1" in action.undo_command
