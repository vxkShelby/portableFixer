import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m06_network" / "actions.yaml"


def test_m06_catalog_loads_18_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m06_network"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 18


def test_m06_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 5
    assert len(by_risk[RiskLevel.MODERATE]) == 10
    assert len(by_risk[RiskLevel.REQUIRES_REBOOT]) == 3
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m06_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "net_adapter_status",
        "net_ip_config_report",
        "net_wifi_diagnostics",
        "net_lan_inspector",
        "net_flush_dns",
        "net_hosts_reset",
        "net_disable_multimedia_throttling",
        "net_tcp_latency_tuning",
        "net_dns_reset_automatic",
        "net_dhcp_renew",
        "net_winsock_reset",
        "net_tcpip_reset",
        "net_firewall_reset",
        "net_print_spooler_reset",
        "net_adapter_power_disable",
        "net_set_public_dns",
        "net_time_sync_repair",
        "net_dns_client_restart",
    }


def test_m06_catalog_undo_commands_on_hosts_reset_and_firewall_reset():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "net_hosts_reset",
        "net_firewall_reset",
        "net_adapter_power_disable",
        "net_set_public_dns",
        "net_disable_multimedia_throttling",
        "net_tcp_latency_tuning",
        "net_dns_reset_automatic",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for action_id in (
        "net_adapter_status",
        "net_ip_config_report",
        "net_wifi_diagnostics",
        "net_lan_inspector",
        "net_flush_dns",
        "net_dhcp_renew",
        "net_winsock_reset",
        "net_tcpip_reset",
        "net_print_spooler_reset",
        "net_time_sync_repair",
        "net_dns_client_restart",
    ):
        assert by_id[action_id].undo_command is None, action_id


def test_m06_catalog_new_service_repair_actions_verify_the_restart_worked():
    # Stop-Service/Start-Service silently no-op without administrator - both
    # new actions must check the service's actual status afterward rather
    # than claim success on a no-op (same pattern already required of
    # net_set_public_dns and m09's tune_pause_background_services).
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("net_time_sync_repair", "net_dns_client_restart"):
        action = by_id[action_id]
        assert action.risk == RiskLevel.MODERATE
        assert "exit 1" in action.command


def test_m06_catalog_new_latency_tweaks_refresh_backup_on_every_run():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in ("net_disable_multimedia_throttling", "net_tcp_latency_tuning"):
        command = by_id[action_id].command
        assert "if (-not (Test-Path $bk))" not in command, action_id


def test_m06_catalog_tcp_latency_tuning_targets_only_active_adapters():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "net_tcp_latency_tuning")
    assert action.risk == RiskLevel.REQUIRES_REBOOT
    assert "-eq 'Up'" in action.command


def test_m06_catalog_lan_inspector_has_an_inactivity_timeout():
    # It probes a handful of router ports over the network (300ms timeout
    # each) on top of the ARP/DNS lookups - more headroom than the default
    # in case DNS reverse-lookup hangs on an unresponsive local resolver.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "net_lan_inspector")
    assert action.risk == RiskLevel.SAFE
    assert action.inactivity_timeout_sec == 60


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


def test_m06_catalog_print_spooler_reset_restart_failure_exits_non_zero():
    # "FAILED" in the output alone still exited 0 - the report/history said
    # success while the Print Spooler stayed stopped.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "net_print_spooler_reset").command
    failed = command.index("'FAILED'")
    assert "$startErrs" in command[command.rindex("if (", 0, failed):failed]
    assert "exit 1" in command[failed:command.index("}", failed)]


# --- behavioural runs against stubbed cmdlets ---------------------------------
#
# The commands run below in Windows PowerShell with every cmdlet or tool they
# touch shadowed by a function (functions win command lookup); the script
# exits 97 unless each name really resolves to its stub, so a test run never
# changes the host's registry, services, DNS or firewall. The registry stub
# keeps its values in a hashtable and converts a DWord the way the provider
# does ([int32]), so a value that does not fit fails exactly like on 5.1.

STUB_GUARD_EXIT = 97
THROTTLE_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Multimedia\\SystemProfile"
TCPIP_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters"
IFACE_GUID = "{a1b2c3d4-0000-4000-8000-000000000001}"
IFACE_KEY = TCPIP_KEY + "\\Interfaces\\" + IFACE_GUID


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_value(value) -> str:
    if value is None:
        return "$null"
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "@(" + ", ".join(_ps_value(v) for v in value) + ")"
    return _ps_quote(str(value))


def _ps_object(row: dict) -> str:
    return "[pscustomobject]@{ " + "; ".join(f"{k} = {_ps_value(v)}" for k, v in row.items()) + " }"


def _registry_stub(registry: dict, reg_file: Path) -> str:
    table = "@{ " + "; ".join(
        f"{_ps_quote(key)} = @{{ " + "; ".join(f"{_ps_quote(n)} = {_ps_value(v)}" for n, v in values.items()) + " }"
        for key, values in registry.items()
    ) + " }"
    return (
        f"$global:PfReg = {table}; $global:PfRegFile = {_ps_quote(str(reg_file))}; "
        "function Save-PfReg { ConvertTo-Json -InputObject $global:PfReg -Depth 4 | "
        "Set-Content -LiteralPath $global:PfRegFile -Encoding UTF8 }; Save-PfReg; "
        "function Test-Path { [CmdletBinding()] param([Parameter(Position=0)] [string] $Path, [string] $LiteralPath) "
        "$p = if ($LiteralPath) { $LiteralPath } else { $Path }; "
        "if ($p -like 'HKLM:*') { return $global:PfReg.ContainsKey($p) }; Microsoft.PowerShell.Management\\Test-Path -LiteralPath $p }; "
        "function Get-ItemProperty { [CmdletBinding()] param([string] $Path, [string] $LiteralPath, [string[]] $Name) "
        "$k = if ($LiteralPath) { $LiteralPath } else { $Path }; if (-not $global:PfReg.ContainsKey($k)) { return }; "
        "$h = $global:PfReg[$k]; if (-not $Name) { return [pscustomobject]$h }; $o = @{}; "
        "foreach ($n in $Name) { if ($h.ContainsKey($n)) { $o[$n] = $h[$n] } }; if ($o.Count) { [pscustomobject]$o } }; "
        "function Set-ItemProperty { [CmdletBinding()] param([string] $Path, [string] $Name, $Value, [string] $Type) "
        "Add-Content -Path $global:PfLog -Value ('set ' + $Path + ' ' + $Name + ' ' + $Value); "
        "if ($Type -eq 'DWord') { $Value = [int32]$Value }; "
        "if (-not $global:PfReg.ContainsKey($Path)) { $global:PfReg[$Path] = @{} }; "
        "$global:PfReg[$Path][$Name] = $Value; Save-PfReg }; "
        "function Remove-ItemProperty { [CmdletBinding()] param([string] $Path, [string] $Name) "
        "Add-Content -Path $global:PfLog -Value ('remove ' + $Path + ' ' + $Name); "
        "if ($global:PfReg.ContainsKey($Path)) { $global:PfReg[$Path].Remove($Name) }; Save-PfReg }"
    )


REGISTRY_NAMES = ["Test-Path", "Get-ItemProperty", "Set-ItemProperty", "Remove-ItemProperty"]


def _run_ps(tmp_path: Path, stubs: list, stubbed_names: list, command: str):
    """Runs `command` with %ProgramData% redirected to tmp_path/ProgramData
    (set inside the script - Windows PowerShell cannot start with a fake
    SystemRoot in its environment) and returns (result, logged calls)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    program_data = tmp_path / "ProgramData"
    program_data.mkdir(exist_ok=True)
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in stubbed_names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    script = "; ".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8", f"$global:PfLog = {_ps_quote(str(log))}",
         f"$env:ProgramData = {_ps_quote(str(program_data))}"] + stubs + [guard, command]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result, log.read_text(encoding="utf-8-sig").splitlines()


def _backup(tmp_path: Path, name: str):
    path = tmp_path / "ProgramData" / "PortableFix" / name
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else None


def _run_registry(tmp_path: Path, command: str, registry: dict, extra_stubs=(), extra_names=()):
    reg_file = tmp_path / "registry.json"
    stubs = [_registry_stub(registry, reg_file)] + list(extra_stubs)
    result, calls = _run_ps(tmp_path, stubs, REGISTRY_NAMES + list(extra_names), command)
    return result, calls, json.loads(reg_file.read_text(encoding="utf-8-sig"))


# --- net_disable_multimedia_throttling ----------------------------------------

def test_multimedia_throttling_writes_a_dword_that_fits_int32(tmp_path):
    # 0xffffffff is an [int64] in Windows PowerShell 5.1 and the registry
    # provider's Int32 conversion threw on every run - the action printed
    # "needs administrator" even as administrator.
    action = _action("net_disable_multimedia_throttling")
    assert "0xffffffff" not in action.command
    registry = {THROTTLE_KEY: {"NetworkThrottlingIndex": 10, "SystemResponsiveness": 20}}
    result, calls, after = _run_registry(tmp_path, action.command, registry)
    assert result.returncode == 0, result.stdout + result.stderr
    assert after[THROTTLE_KEY] == {"NetworkThrottlingIndex": -1, "SystemResponsiveness": 0}
    assert _backup(tmp_path, "network_throttle_backup.json") == {"NetworkThrottlingIndex": 10, "SystemResponsiveness": 20}
    check, _, _ = _run_registry(tmp_path / "check", action.check_command, after)
    assert check.stdout.strip() == "APPLIED"
    before, _, _ = _run_registry(tmp_path / "before", action.check_command, registry)
    assert before.stdout.strip() == "NOT_APPLIED"


def test_multimedia_throttling_undo_restores_the_backed_up_values(tmp_path):
    action = _action("net_disable_multimedia_throttling")
    registry = {THROTTLE_KEY: {"NetworkThrottlingIndex": 10, "SystemResponsiveness": 20}}
    _run_registry(tmp_path, action.command, registry)
    result, _, after = _run_registry(
        tmp_path, action.undo_command, {THROTTLE_KEY: {"NetworkThrottlingIndex": -1, "SystemResponsiveness": 0}}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert after[THROTTLE_KEY] == {"NetworkThrottlingIndex": 10, "SystemResponsiveness": 20}


# --- net_dns_client_restart ---------------------------------------------------

def _service_stubs(status="Running", start_type="Manual", stop_fails=False, start_fails_times=0):
    """Get-Service hands out an object whose Refresh() re-reads the shared
    status; Start-Service flips it to Running unless told to fail."""
    stop = "throw [System.InvalidOperationException]::new('Službu nie je možné zastaviť.')" if stop_fails else \
        "$global:PfSvcStatus = 'Stopped'"
    return [
        f"$global:PfSvcStatus = {_ps_quote(status)}; $global:PfStartFails = {start_fails_times}",
        "function Get-Service { [CmdletBinding()] param([string] $Name) "
        f"$o = [pscustomobject]@{{ Name = $Name; Status = $global:PfSvcStatus; StartType = {_ps_quote(start_type)} }}; "
        "$o | Add-Member -MemberType ScriptMethod -Name Refresh -Value { $this.Status = $global:PfSvcStatus } -PassThru }",
        "function Stop-Service { [CmdletBinding()] param([string] $Name, [switch] $Force) "
        f"Add-Content -Path $global:PfLog -Value ('stop ' + $Name); {stop} }}",
        "function Start-Service { [CmdletBinding()] param([string] $Name) "
        "Add-Content -Path $global:PfLog -Value ('start ' + $Name); "
        "if ($global:PfStartFails -gt 0) { $global:PfStartFails--; return }; $global:PfSvcStatus = 'Running' }",
        "function Set-Service { [CmdletBinding()] param([string] $Name, [string] $StartupType) "
        "Add-Content -Path $global:PfLog -Value ('set-service ' + $Name + ' ' + $StartupType) }",
    ]


SERVICE_NAMES = ["Get-Service", "Stop-Service", "Start-Service", "Set-Service"]


def test_dns_client_restart_reports_a_protected_service_instead_of_claiming_a_restart(tmp_path):
    # Dnscache cannot be stopped on Windows 10/11; the old command swallowed
    # the error and printed "restarted" because the service never stopped.
    result, calls = _run_ps(tmp_path, _service_stubs(stop_fails=True), SERVICE_NAMES, _action("net_dns_client_restart").command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DNS Client service restarted." not in result.stdout
    assert "protected service" in result.stdout and "Flush DNS cache" in result.stdout
    assert calls == ["stop Dnscache"]


def test_dns_client_restart_succeeds_when_the_stop_really_happened(tmp_path):
    result, calls = _run_ps(tmp_path, _service_stubs(), SERVICE_NAMES, _action("net_dns_client_restart").command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DNS Client service restarted." in result.stdout
    assert calls == ["stop Dnscache", "start Dnscache"]
