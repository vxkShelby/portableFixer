from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m06_network" / "actions.yaml"


def test_m06_catalog_loads_7_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m06_network"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 7


def test_m06_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 3
    assert len(by_risk[RiskLevel.MODERATE]) == 2
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 2
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m06_catalog_only_hosts_reset_has_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["net_hosts_reset"].undo_command is not None
    for action_id in (
        "net_adapter_status",
        "net_ip_config_report",
        "net_flush_dns",
        "net_dhcp_renew",
        "net_winsock_reset",
        "net_tcpip_reset",
    ):
        assert by_id[action_id].undo_command is None


def test_m06_catalog_hosts_reset_backup_guarded_against_double_run():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "-not (Test-Path" in by_id["net_hosts_reset"].command
    assert "if (Test-Path" in by_id["net_hosts_reset"].undo_command
