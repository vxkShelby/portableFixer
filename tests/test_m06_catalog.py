from pathlib import Path

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m06_network" / "actions.yaml"


def test_m06_catalog_loads_12_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m06_network"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 12


def test_m06_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 4
    assert len(by_risk[RiskLevel.MODERATE]) == 6
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 2
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m06_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "net_adapter_status",
        "net_ip_config_report",
        "net_wifi_diagnostics",
        "net_flush_dns",
        "net_hosts_reset",
        "net_dhcp_renew",
        "net_winsock_reset",
        "net_tcpip_reset",
        "net_firewall_reset",
        "net_print_spooler_reset",
        "net_adapter_power_disable",
        "net_set_public_dns",
    }


def test_m06_catalog_undo_commands_on_hosts_reset_and_firewall_reset():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "net_hosts_reset",
        "net_firewall_reset",
        "net_adapter_power_disable",
        "net_set_public_dns",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for action_id in (
        "net_adapter_status",
        "net_ip_config_report",
        "net_wifi_diagnostics",
        "net_flush_dns",
        "net_dhcp_renew",
        "net_winsock_reset",
        "net_tcpip_reset",
        "net_print_spooler_reset",
    ):
        assert by_id[action_id].undo_command is None, action_id


def test_m06_catalog_hosts_reset_backup_guarded_against_double_run():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "-not (Test-Path" in by_id["net_hosts_reset"].command
    assert "if (Test-Path" in by_id["net_hosts_reset"].undo_command


def test_m06_catalog_set_public_dns_verifies_it_actually_applied():
    # Set-DnsClientServerAddress silently no-ops without administrator (a CIM
    # permission error printed to the error stream, not a thrown exception
    # by default) - the command must use -EA Stop + try/catch per adapter and
    # only report success for adapters where it actually worked.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "net_set_public_dns")
    assert action.risk == RiskLevel.MODERATE
    assert "-EA Stop" in action.command
    assert "exit 1" in action.command
    assert "-EA Stop" in action.undo_command
    assert "exit 1" in action.undo_command
