from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m21_hardware_sensors" / "actions.yaml"


def test_m21_catalog_loads_2_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m21_hardware_sensors"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 2


def test_m21_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert by_risk[RiskLevel.SAFE] == ["pawnio_status_report"]
    assert by_risk[RiskLevel.MODERATE] == ["pawnio_install"]
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m21_catalog_only_install_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["pawnio_install"].undo_command is not None
    assert by_id["pawnio_status_report"].undo_command is None


def test_m21_catalog_install_uses_official_latest_release_url():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "pawnio_install")
    assert "github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe" in action.command
    assert "-install" in action.command
    assert "-silent" in action.command


def test_m21_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {"pawnio_status_report", "pawnio_install"}


def test_m21_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en
