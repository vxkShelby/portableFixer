from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m03_disk" / "actions.yaml"


def test_m03_catalog_loads_9_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m03_disk"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 9


def test_m03_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 6
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 1
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m03_catalog_scan_before_spotfix_before_full_scan_reboot():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    assert ids.index("disk_scan_readonly") < ids.index("disk_spotfix")
    assert ids.index("disk_spotfix") < ids.index("disk_full_scan_reboot")
    assert ids.index("disk_full_scan_reboot") < ids.index("disk_check_scheduled")


def test_m03_catalog_no_action_has_undo_command():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.undo_command is None


def test_m03_catalog_disk_stressing_actions_are_flagged():
    # G13: the pre-flight asks the disk health probe for exactly these. A
    # chkdsk /r reads every sector, a defrag rewrites much of an HDD and an
    # online NTFS repair writes to a file system that may sit on bad
    # sectors. The read-only Repair-Volume -Scan and the diagnostics are not
    # flagged: they are what a technician runs to decide.
    module = load_module(CATALOG_PATH)
    flagged = {a.id for a in module.actions if a.stresses_disk}
    assert flagged == {"disk_spotfix", "disk_optimize_volume", "disk_full_scan_reboot"}
    for action in module.actions:
        if action.stresses_disk:
            assert action.risk != RiskLevel.SAFE, action.id


def test_m03_catalog_disk_health_verdict_is_safe_and_before_the_repairs():
    module = load_module(CATALOG_PATH)
    ids = [a.id for a in module.actions]
    verdict = next(a for a in module.actions if a.id == "disk_health_verdict")
    assert verdict.risk == RiskLevel.SAFE
    assert verdict.changes_system is None and not verdict.stresses_disk
    assert ids.index("disk_health_verdict") < ids.index("disk_spotfix")
    assert verdict.description_sk and verdict.description_en
