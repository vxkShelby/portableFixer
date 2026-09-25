import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m14_printing" / "actions.yaml"


def test_m14_catalog_loads_9_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m14_printing"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 9


def test_m14_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
        "print_driver_class_report",
        "print_smb_compat_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"print_remove_offline_printers", "print_backup_printbrm"}
    assert set(by_risk[RiskLevel.DESTRUCTIVE]) == {
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
    }
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m14_catalog_only_offline_printer_removal_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["print_remove_offline_printers"].undo_command is not None
    for not_undoable in (
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
        "print_driver_class_report",
        # PrintBrm -R re-adds every printer in the file - the technician
        # runs it from the printed hint, never an automatic undo.
        "print_backup_printbrm",
        "print_smb_compat_report",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m14_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "print_installed_printers_report",
        "print_driver_store_report",
        "print_offline_ghost_printers_report",
        "print_remove_offline_printers",
        "print_remove_orphaned_drivers",
        "print_reset_print_system",
        "print_driver_class_report",
        "print_backup_printbrm",
        "print_smb_compat_report",
    }


def test_m14_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m14_catalog_reset_print_system_verifies_it_actually_worked():
    # Stop-Service/Start-Service on Spooler silently no-op without
    # administrator - the command must check via -EA Stop/try-catch and a
    # final Get-Printer re-check, not just claim success unconditionally.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "print_reset_print_system")
    assert "-EA Stop" in action.command
    assert "exit 1" in action.command
    assert "Get-Printer -EA SilentlyContinue" in action.command


def test_m14_reset_print_system_removes_printers_only_once_the_spooler_is_back():
    # Remove-Printer goes through the spooler: run while Spooler was stopped
    # (the old order) it removed nothing, and the final re-check then failed
    # every time. Stop -> clear the spool folder -> Start -> Remove -> re-check.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "print_reset_print_system").command
    stop = command.index("Stop-Service")
    spool = command.index("spool\\PRINTERS")
    start = command.index("Start-Service")
    remove = command.index("Remove-Printer")
    assert stop < spool < start < remove
    # @() so a single remaining printer object still counts and joins by name
    recheck = command[remove:]
    assert "@($stillThere).Count" in recheck
    assert "(@($stillThere).Name -join ', ')" in recheck
    assert "exit 1" in recheck


def test_m14_remove_orphaned_drivers_never_treats_every_driver_as_orphaned():
    # A failing Get-Printer (spooler down, corrupt queue) left $inUse empty,
    # so EVERY driver - including ones real printers use - looked orphaned
    # and was deleted, and failed removals still exited 0.
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "print_remove_orphaned_drivers").command
    assert "try { $inUse = @((Get-Printer -EA Stop).DriverName)" in command
    listing_catch = command[command.index("catch {"):]
    assert "exit 1" in listing_catch[:listing_catch.index("}")]
    assert command.index("exit 1") < command.index("Remove-PrinterDriver")
    assert "$failed++" in command
    assert "exit 1" in command[command.rindex("if ($failed -gt 0)"):]


def test_print_remove_orphaned_drivers_removes_each_driver_per_environment():
    # Get-PrinterDriver returns one row per environment (x64, x86), so a
    # driver installed for both came up twice by name; removing by name alone
    # made the second removal fail and, since failures exit 1, a clean run
    # was reported as failed.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "print_remove_orphaned_drivers")
    assert "Remove-PrinterDriver -Name $name -PrinterEnvironment $envName" in action.command


# --- G26: driver classes / WPP readiness, PrintBRM backup, SMB compatibility --
#
# The three commands run below in PowerShell against stubbed cmdlets and
# tools: each is shadowed by a function (functions win command lookup) and
# the script exits 97 unless every name really resolves to the stub, so a
# test run never reads the host's printers or starts PrintBrm. Registry reads
# go through a stubbed Get-ItemProperty fed from a per-test table.

STUB_GUARD_EXIT = 97
DRIVER_CLASS_ID = "print_driver_class_report"
BACKUP_ID = "print_backup_printbrm"
SMB_ID = "print_smb_compat_report"
PRINT_ENV_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Print\\Environments\\"
WPP_KEY = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\Printers\\WPP"
PRINTERS_POLICY_KEY = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows NT\\Printers"
WS_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LanmanWorkstation\\Parameters"
WS_POLICY_KEY = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\LanmanWorkstation"
SRV_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Parameters"
MRXSMB10_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\mrxsmb10"
PRINT_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Print"
CURRENT_VERSION_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion"


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
        # [uint64] so packed driver versions above Int64 range still parse
        return f"[uint64]{value}" if value > 0x7FFFFFFF else str(value)
    return _ps_quote(str(value))


def _ps_object(row: dict) -> str:
    return "[pscustomobject]@{ " + "; ".join(f"{k} = {_ps_value(v)}" for k, v in row.items()) + " }"


def _ps_objects(rows) -> str:
    return "@(" + ", ".join(_ps_object(row) for row in rows) + ")"


def _registry_stub(registry: dict) -> str:
    table = "@{ " + "; ".join(
        f"{_ps_quote(key)} = @{{ " + "; ".join(f"{name} = {_ps_value(v)}" for name, v in values.items()) + " }"
        for key, values in registry.items()
    ) + " }"
    return (
        f"$global:PfReg = {table}; "
        "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Path, [string[]] $Name) "
        "$k = if ($LiteralPath) { $LiteralPath } else { $Path }; "
        "if ($global:PfReg.ContainsKey($k)) { [pscustomobject]$global:PfReg[$k] } }"
    )


def _service_stub(status) -> str:
    # status None = the Spooler service does not exist at all
    body = "" if status is None else f"[pscustomobject]@{{ Name = 'Spooler'; Status = {_ps_quote(status)} }}"
    return f"function Get-Service {{ [CmdletBinding()] param([string] $Name) {body} }}"


def _run_ps(tmp_path, stubs: list, stubbed_names: list, command: str, extra_env=None):
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in stubbed_names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    # Redirected variables are set inside the script, not in the child's
    # environment: Windows PowerShell cannot start with a fake SystemRoot.
    env_lines = [f"$env:{name} = {_ps_quote(value)}" for name, value in (extra_env or {}).items()]
    script = "; ".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8", f"$global:PfLog = {_ps_quote(str(log))}"]
        + env_lines + stubs + [guard, command]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    calls = log.read_text(encoding="utf-8-sig").splitlines()
    return result, calls


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


def _lines(stdout: str, prefix: str) -> list:
    return [line for line in stdout.splitlines() if line.startswith(prefix)]


def test_m14_commands_parse_as_powershell(tmp_path):
    # One-line YAML commands are easy to break with a stray brace or quote;
    # parse every m14 command, preview and undo with the real parser.
    checks = []
    for action in load_module(CATALOG_PATH).actions:
        for field in ("command", "preview_command", "undo_command"):
            text = getattr(action, field)
            if not text:
                continue
            path = tmp_path / f"{action.id}.{field}.ps1"
            path.write_text(text, encoding="utf-8")
            checks.append(
                "$e = $null; $t = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
                f"{_ps_quote(str(path))}, [ref]$t, [ref]$e); "
                f"if ($e.Count) {{ Write-Output ({_ps_quote(path.name)} + ': ' + $e[0].Message) }}"
            )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", "; ".join(checks)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout


def test_g26_reports_never_change_anything():
    # The SMB report explains what an old device needs; weakening SMB or
    # print RPC security is exactly what it must never do on its own.
    for action_id in (DRIVER_CLASS_ID, SMB_ID):
        command = _action(action_id).command
        for verb in ("Set-", "New-Item", "Remove-", "Enable-", "Disable-", "Start-Service", "Stop-Service",
                     "reg add", "reg delete", "icacls"):
            assert verb not in command, f"{action_id}: {verb}"


def test_g26_commands_never_parse_localized_text():
    # Decisions come from enum names, numbers and registry values - never
    # from the text a native tool or an error message prints.
    for action_id in (DRIVER_CLASS_ID, BACKUP_ID, SMB_ID):
        action = _action(action_id)
        for text in (action.command, action.preview_command or ""):
            for pattern in ("Select-String", "Exception.Message -match", "Exception.Message -like", "$out -match"):
                assert pattern not in text, f"{action_id}: {pattern}"


# --- print_driver_class_report ----------------------------------------------

IPP_DRIVER = {"Name": "Microsoft IPP Class Driver", "PrinterEnvironment": "Windows x64", "MajorVersion": 4,
              "DriverVersion": (10 << 48) | (0 << 32) | (26100 << 16) | 1, "Manufacturer": "Microsoft",
              "Provider": "Microsoft"}
PDF_DRIVER = {"Name": "Microsoft Print To PDF", "PrinterEnvironment": "Windows x64", "MajorVersion": 4,
              "DriverVersion": (10 << 48) | (26100 << 16), "Manufacturer": "Microsoft", "Provider": "Microsoft"}
HP_V3_X64 = {"Name": "HP Universal Printing PCL 6", "PrinterEnvironment": "Windows x64", "MajorVersion": 3,
             "DriverVersion": (61 << 48) | (250 << 32) | (1 << 16) | 24923, "Manufacturer": "HP", "Provider": "HP"}
HP_V3_X86 = dict(HP_V3_X64, PrinterEnvironment="Windows NT x86")
# Microsoft ships these (Provider Microsoft), but none is the IPP class
# driver, so Windows Protected Print blocks them.
HP_PCL6_CLASS = {"Name": "HP LaserJet PCL6 Class Driver", "PrinterEnvironment": "Windows x64", "MajorVersion": 4,
                 "DriverVersion": (10 << 48) | (26100 << 16), "Manufacturer": "HP", "Provider": "Microsoft"}
GENERIC_TEXT = {"Name": "Generic / Text Only", "PrinterEnvironment": "Windows x64", "MajorVersion": 3,
                "DriverVersion": (10 << 48) | (26100 << 16), "Manufacturer": "Generic", "Provider": "Microsoft"}
MS_FAX_V3 = {"Name": "Microsoft Shared Fax Driver", "PrinterEnvironment": "Windows x64", "MajorVersion": 3,
             "DriverVersion": (10 << 48) | (26100 << 16), "Manufacturer": "Microsoft", "Provider": "Microsoft"}
KYO_V4 = {"Name": "Kyocera TASKalfa 2553ci KX", "PrinterEnvironment": "Windows x64", "MajorVersion": 4,
          "DriverVersion": 0, "Manufacturer": "Kyocera", "Provider": "Kyocera"}
PORTS = [
    {"Name": "IP_192.168.1.20", "PortMonitor": "Standard TCP/IP Port", "PrinterHostAddress": "192.168.1.20"},
    {"Name": "WSD-1234", "PortMonitor": "WSD Port", "PrinterHostAddress": None},
    {"Name": "PORTPROMPT:", "PortMonitor": "Local Port", "PrinterHostAddress": None},
]


def _printer(name, driver, port="IP_192.168.1.20", shared=False):
    return {"Name": name, "DriverName": driver, "PortName": port, "Type": "Local", "Shared": shared}


def _run_driver_class(tmp_path, spooler="Running", printers=(), drivers=(), ports=PORTS, registry=None,
                      list_fails=False):
    fail = "throw [System.ComponentModel.Win32Exception]::new(1722)" if list_fails else ""
    stubs = [
        _service_stub(spooler),
        "function Get-Printer { [CmdletBinding()] param() Add-Content -Path $global:PfLog -Value 'Get-Printer'; "
        f"{fail}; {_ps_objects(printers)} }}",
        f"function Get-PrinterDriver {{ [CmdletBinding()] param() {_ps_objects(drivers)} }}",
        f"function Get-PrinterPort {{ [CmdletBinding()] param() {_ps_objects(ports)} }}",
        _registry_stub(registry or {}),
    ]
    names = ["Get-Service", "Get-Printer", "Get-PrinterDriver", "Get-PrinterPort", "Get-ItemProperty"]
    return _run_ps(tmp_path, stubs, names, _action(DRIVER_CLASS_ID).command)


@pytest.mark.parametrize("status", ["Stopped", "StartPending"])
def test_driver_class_report_fails_clearly_when_the_spooler_is_not_running(tmp_path, status):
    result, calls = _run_driver_class(tmp_path, spooler=status, printers=[_printer("A", IPP_DRIVER["Name"])],
                                      drivers=[IPP_DRIVER])
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Print Spooler is not running (status: {status})" in result.stdout
    assert "Get-Printer" not in calls
    assert "VERDICT" not in result.stdout


def test_driver_class_report_fails_when_the_spooler_service_is_missing(tmp_path):
    result, calls = _run_driver_class(tmp_path, spooler=None)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Print Spooler service not found" in result.stdout
    assert "Get-Printer" not in calls


def test_driver_class_report_fails_when_the_printer_list_cannot_be_read(tmp_path):
    result, _ = _run_driver_class(tmp_path, list_fails=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not list printers, drivers or ports" in result.stdout
    assert "VERDICT" not in result.stdout


def test_driver_class_report_classifies_drivers_printers_and_ports(tmp_path):
    hp_key = PRINT_ENV_KEY + "Windows x64\\Drivers\\Version-3\\" + HP_V3_X64["Name"]
    ipp_key = PRINT_ENV_KEY + "Windows x64\\Drivers\\Version-4\\" + IPP_DRIVER["Name"]
    result, _ = _run_driver_class(
        tmp_path,
        printers=[
            _printer("Kancelaria HP", HP_V3_X64["Name"], shared=True),
            _printer("Kyocera chodba", KYO_V4["Name"], port="WSD-1234"),
            _printer("Brother IPP", IPP_DRIVER["Name"], port="IP_192.168.1.30"),
            _printer("Microsoft Print to PDF", PDF_DRIVER["Name"], port="PORTPROMPT:"),
        ],
        drivers=[HP_V3_X86, HP_V3_X64, KYO_V4, IPP_DRIVER, PDF_DRIVER],
        registry={hp_key: {"DriverIsolation": 0}, ipp_key: {"DriverIsolation": 2}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Windows Protected Print: not enabled" in out
    assert "PrintDriverIsolationExecutionPolicy): not set" in out
    # one row per driver and environment, with the decoded packed version
    assert ("DRIVER: HP Universal Printing PCL 6 [Windows x64] - Type 3, version 61.250.1.24923, third-party, "
            "isolation: 0 - runs inside the spooler, used by: Kancelaria HP") in out
    assert "DRIVER: HP Universal Printing PCL 6 [Windows NT x86] - Type 3" in out
    assert ("DRIVER: Microsoft IPP Class Driver [Windows x64] - Type 4, version 10.0.26100.1, "
            "Microsoft IPP class driver, isolation: 2 - supports isolation, used by: Brother IPP") in out
    assert ("Kyocera TASKalfa 2553ci KX [Windows x64] - Type 4, version unknown, third-party, "
            "isolation: not declared") in out
    printers = _lines(out, "PRINTER: ")
    assert len(printers) == 4
    hp = next(p for p in printers if p.startswith("PRINTER: Kancelaria HP"))
    # the x64 row wins over the x86 row of the same driver
    assert "driver: HP Universal Printing PCL 6 (Type 3, third-party)" in hp
    assert "port: IP_192.168.1.20 (monitor: Standard TCP/IP Port, host: 192.168.1.20)" in hp
    assert "shared: yes" in hp and "type: Local" in hp
    assert "WPP: NOT READY - third-party Type 3 driver" in hp
    kyo = next(p for p in printers if p.startswith("PRINTER: Kyocera chodba"))
    assert "port: WSD-1234 (monitor: WSD Port)" in kyo
    assert "WPP: NOT READY - third-party Type 4 driver" in kyo
    # a port the port list does not know is still shown by name
    brother = next(p for p in printers if p.startswith("PRINTER: Brother IPP"))
    assert "port: IP_192.168.1.30, type" in brother
    assert "WPP: READY - inbox IPP class driver" in brother
    pdf = next(p for p in printers if p.startswith("PRINTER: Microsoft Print to PDF"))
    assert "WPP: READY - inbox Microsoft Print To PDF" in pdf
    assert "Printers: 4, drivers: 5 (Type 3: 2, Type 4: 3), ports: 3" in out
    verdict = _verdict(out)
    assert verdict.startswith("VERDICT: 2 of 4 printer(s) not ready for Windows Protected Print "
                              "(2 on third-party drivers, 0 on Microsoft-provided non-IPP drivers)")
    assert "July 2027" in verdict


def test_driver_class_report_wpp_on_with_third_party_driver_needs_attention(tmp_path):
    result, _ = _run_driver_class(
        tmp_path, printers=[_printer("HP", HP_V3_X64["Name"]), _printer("IPP", IPP_DRIVER["Name"])],
        drivers=[HP_V3_X64, IPP_DRIVER], registry={WPP_KEY: {"WindowsProtectedPrintGroupPolicyState": 1}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Windows Protected Print: ENABLED" in result.stdout
    assert _verdict(result.stdout).startswith(
        "VERDICT: ATTENTION - Windows Protected Print is on, but 1 of 2 printer(s) do not use the inbox IPP "
        "class driver (1 on third-party drivers, 0 on Microsoft-provided non-IPP drivers)")


def test_driver_class_report_all_inbox_drivers_is_ok(tmp_path):
    result, _ = _run_driver_class(
        tmp_path,
        printers=[_printer("IPP", IPP_DRIVER["Name"]), _printer("PDF", PDF_DRIVER["Name"], port="PORTPROMPT:")],
        drivers=[IPP_DRIVER, PDF_DRIVER],
        registry={WPP_KEY: {"WindowsProtectedPrintMode": 1},
                  PRINTERS_POLICY_KEY: {"PrintDriverIsolationExecutionPolicy": 0}},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 - isolation turned off" in result.stdout
    assert _verdict(result.stdout).startswith(
        "VERDICT: OK - all 2 printer(s) use the inbox IPP class driver or Microsoft Print To PDF")


MS_NON_IPP = [HP_PCL6_CLASS, GENERIC_TEXT, MS_FAX_V3]


@pytest.mark.parametrize("wpp", [True, False])
def test_driver_class_report_microsoft_provided_non_ipp_drivers_are_not_ready(tmp_path, wpp):
    # Provider=Microsoft (or even Manufacturer=Microsoft) is not the IPP
    # class driver: WPP blocks these, so they must never read as READY/OK.
    registry = {WPP_KEY: {"WindowsProtectedPrintGroupPolicyState": 1}} if wpp else {}
    result, _ = _run_driver_class(
        tmp_path,
        printers=[_printer("HP", HP_PCL6_CLASS["Name"]), _printer("Gen", GENERIC_TEXT["Name"]),
                  _printer("Fax", MS_FAX_V3["Name"]), _printer("IPP", IPP_DRIVER["Name"])],
        drivers=MS_NON_IPP + [IPP_DRIVER], registry=registry,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "inbox Microsoft driver" not in out
    assert ("DRIVER: HP LaserJet PCL6 Class Driver [Windows x64] - Type 4, version 10.0.26100.0, "
            "Microsoft-provided non-IPP driver") in out
    for name, major in (("HP", 4), ("Gen", 3), ("Fax", 3)):
        line = _lines(out, f"PRINTER: {name} ")[0]
        assert f"WPP: NOT READY - Microsoft-provided Type {major} driver, but not the IPP class driver" in line, line
    assert "WPP: READY - inbox IPP class driver" in _lines(out, "PRINTER: IPP ")[0]
    verdict = _verdict(out)
    split = "3 of 4 printer(s)"
    if wpp:
        assert verdict.startswith(f"VERDICT: ATTENTION - Windows Protected Print is on, but {split} do not use")
        assert "(0 on third-party drivers, 3 on Microsoft-provided non-IPP drivers)" in verdict
    else:
        assert verdict.startswith(f"VERDICT: {split} not ready for Windows Protected Print "
                                  "(0 on third-party drivers, 3 on Microsoft-provided non-IPP drivers)")


def test_driver_class_report_ipp_named_driver_from_another_maker_is_not_ready(tmp_path):
    # the IPP name alone is not enough - a vendor's "IPP Class Driver" with
    # Provider Microsoft is still not the inbox Microsoft one
    fake = dict(IPP_DRIVER, Name="Contoso IPP Class Driver", Manufacturer="Contoso")
    result, _ = _run_driver_class(tmp_path, printers=[_printer("C", fake["Name"])], drivers=[fake])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WPP: NOT READY - Microsoft-provided Type 4 driver" in _lines(result.stdout, "PRINTER: C ")[0]
    assert _verdict(result.stdout).startswith("VERDICT: 1 of 1 printer(s) not ready")


def test_driver_class_report_without_printers(tmp_path):
    result, _ = _run_driver_class(tmp_path, printers=[], drivers=[HP_V3_X64], ports=[])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "used by: no printer" in result.stdout
    assert _verdict(result.stdout) == "VERDICT: NO PRINTERS - nothing to assess"


def test_driver_class_report_printer_whose_driver_is_missing(tmp_path):
    result, _ = _run_driver_class(tmp_path, printers=[_printer("Stara", "Zmazany ovladac")], drivers=[IPP_DRIVER])
    assert result.returncode == 0, result.stdout + result.stderr
    line = _lines(result.stdout, "PRINTER: Stara")[0]
    assert "driver: Zmazany ovladac (not in the driver list)" in line
    assert "WPP: UNKNOWN - driver not found" in line
    assert _verdict(result.stdout).startswith("VERDICT: OK")


# --- print_backup_printbrm ----------------------------------------------------

def _run_backup(tmp_path, spooler="Running", brm_exists=True, brm_exit=0, brm_writes=True, start_fails=False,
                preview=False):
    windir = tmp_path / "Windows"
    tools = windir / "System32" / "spool" / "tools"
    tools.mkdir(parents=True)
    if brm_exists:
        (tools / "PrintBrm.exe").write_bytes(b"MZ")
    program_data = tmp_path / "ProgramData"
    program_data.mkdir()
    write = "Set-Content -LiteralPath $f -Value 'PRINTERS' -NoNewline" if brm_writes else ""
    fail = "throw [System.ComponentModel.Win32Exception]::new(2)" if start_fails else ""
    stubs = [
        _service_stub(spooler),
        "function icacls { Add-Content -Path $global:PfLog -Value ('icacls ' + ($args -join ' ')); "
        "$global:LASTEXITCODE = 0 }",
        "function Start-Process { [CmdletBinding()] param([string] $FilePath, [string[]] $ArgumentList, "
        "[switch] $NoNewWindow, [switch] $Wait, [switch] $PassThru) "
        "Add-Content -Path $global:PfLog -Value ('Start-Process ' + $FilePath + ' | ' + ($ArgumentList -join ' ')); "
        f"{fail}; $f = $ArgumentList[2].Trim([char]34); {write}; [pscustomobject]@{{ ExitCode = {brm_exit} }} }}",
        "function Get-Printer { [CmdletBinding()] param() Add-Content -Path $global:PfLog -Value 'Get-Printer'; "
        "@([pscustomobject]@{ Name = 'A' }, [pscustomobject]@{ Name = 'B' }) }",
    ]
    names = ["Get-Service", "icacls", "Start-Process", "Get-Printer"]
    action = _action(BACKUP_ID)
    command = action.preview_command if preview else action.command
    result, calls = _run_ps(tmp_path, stubs, names, command,
                            extra_env={"WINDIR": str(windir), "ProgramData": str(program_data)})
    backups = sorted((program_data / "PortableFix" / "printer_backups").glob("*.printerExport"))
    return result, calls, backups, program_data


def test_backup_printbrm_writes_the_export_into_the_locked_down_folder(tmp_path):
    result, calls, backups, _ = _run_backup(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(backups) == 1
    assert re.fullmatch(r"\d{8}_\d{6}\.printerExport", backups[0].name)
    starts = [c for c in calls if c.startswith("Start-Process ")]
    assert len(starts) == 1
    tool, args = starts[0][len("Start-Process "):].split(" | ")
    assert tool.endswith("PrintBrm.exe")
    assert args.startswith('-B -F "') and args.endswith('.printerExport"')
    # both the PortableFix root and the backup folder get the admin/SYSTEM/
    # current-user ACL before anything is written into them
    icacls = [c for c in calls if c.startswith("icacls ")]
    assert len(icacls) == 2
    for call in icacls:
        assert "/inheritance:r" in call
        assert "*S-1-5-32-544:(OI)(CI)F" in call and "*S-1-5-18:(OI)(CI)F" in call
    assert icacls[1].split(" ")[1].endswith("printer_backups")
    assert calls.index(icacls[1]) < calls.index(starts[0])
    assert re.search(r"Printer backup saved to: .+\.printerExport \(1 KB\)", result.stdout)
    assert 'PrintBrm.exe -R -F "' in result.stdout
    assert "never restores it automatically" in result.stdout


def test_backup_printbrm_missing_tool_fails_without_running_anything(tmp_path):
    result, calls, backups, _ = _run_backup(tmp_path, brm_exists=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PrintBrm.exe not found" in result.stdout
    assert not [c for c in calls if c.startswith("Start-Process")]
    assert backups == []


@pytest.mark.parametrize("spooler", ["Stopped", None])
def test_backup_printbrm_refuses_while_the_spooler_is_not_running(tmp_path, spooler):
    result, calls, backups, _ = _run_backup(tmp_path, spooler=spooler)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Print Spooler is not running (status: {spooler or 'not installed'})" in result.stdout
    assert not [c for c in calls if c.startswith("Start-Process")]
    assert backups == []


def test_backup_printbrm_failure_exits_one_and_removes_the_partial_file(tmp_path):
    result, _, backups, _ = _run_backup(tmp_path, brm_exit=5)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PrintBrm backup failed (exit code 5" in result.stdout
    assert "Printer backup saved" not in result.stdout
    assert backups == []


def test_backup_printbrm_success_code_without_a_file_is_a_failure(tmp_path):
    result, _, backups, _ = _run_backup(tmp_path, brm_writes=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PrintBrm backup failed (exit code 0" in result.stdout
    assert backups == []


def test_backup_printbrm_start_failure_exits_one(tmp_path):
    result, _, backups, _ = _run_backup(tmp_path, start_fails=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not start PrintBrm" in result.stdout
    assert backups == []


@pytest.mark.parametrize("spooler,brm_exists", [("Running", True), ("Stopped", False)])
def test_backup_printbrm_preview_is_read_only(tmp_path, spooler, brm_exists):
    result, calls, _, program_data = _run_backup(tmp_path, spooler=spooler, brm_exists=brm_exists, preview=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Would back up all printers, drivers, ports and queues" in result.stdout
    assert not [c for c in calls if c.startswith(("Start-Process", "icacls"))]
    assert not (program_data / "PortableFix").exists()
    if brm_exists:
        assert "PrintBrm.exe: found" in result.stdout
        assert "Print Spooler: Running" in result.stdout
        assert "Printers now: 2" in result.stdout
    else:
        assert "PrintBrm.exe: NOT FOUND" in result.stdout
        assert "Print Spooler: Stopped - the backup would fail" in result.stdout
        assert "Get-Printer" not in calls


def test_backup_printbrm_has_no_automatic_undo_and_no_restore_point():
    action = _action(BACKUP_ID)
    assert action.risk == RiskLevel.MODERATE
    assert action.undo_command is None
    assert action.changes_system is False
    assert "PrintBrm -R -F" in action.description_en
    assert "PrintBrm -R -F" in action.description_sk


# --- print_smb_compat_report --------------------------------------------------

def _run_smb(tmp_path, spooler="Running", build="26100", client=None, server=None, registry=None):
    """client/server: Get-Smb*Configuration properties, None = the cmdlet
    fails (the command must then read the registry instead)."""
    def cmdlet(name, props):
        if props is None:
            body = "throw [System.UnauthorizedAccessException]::new('Prístup bol odmietnutý.')"
        else:
            body = _ps_object(props)
        return f"function {name} {{ [CmdletBinding()] param() {body} }}"

    reg = {CURRENT_VERSION_KEY: {"CurrentBuild": build}}
    reg.update(registry or {})
    stubs = [
        _service_stub(spooler),
        cmdlet("Get-SmbClientConfiguration", client),
        cmdlet("Get-SmbServerConfiguration", server),
        _registry_stub(reg),
    ]
    names = ["Get-Service", "Get-SmbClientConfiguration", "Get-SmbServerConfiguration", "Get-ItemProperty"]
    result, _ = _run_ps(tmp_path, stubs, names, _action(SMB_ID).command)
    return result


SECURE_CLIENT = {"RequireSecuritySignature": True, "EnableInsecureGuestLogons": False}
SECURE_SERVER = {"EnableSMB1Protocol": False, "RequireSecuritySignature": True}


def test_smb_compat_secure_defaults_explain_what_old_devices_need(tmp_path):
    result = _run_smb(tmp_path, client=SECURE_CLIENT, server=SECURE_SERVER)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Windows build: 26100" in out
    assert "Print Spooler: Running" in out
    assert "SMB1 client (mrxsmb10): not installed" in out
    assert "SMB1 server: disabled" in out
    assert "SMB signing required (outgoing): yes" in out
    assert "SMB signing required (incoming): yes" in out
    assert "Insecure guest logons (AllowInsecureGuestAuth): blocked [SMB client configuration]" in out
    assert "Print RPC privacy (RpcAuthnLevelPrivacyEnabled): not set - enforced" in out
    assert "RpcUseNamedPipeProtocol): RPC over TCP" in out
    assert "RpcAuthentication): default" in out
    assert "(RpcProtocols / RpcTcpPort): default / dynamic port" in out
    assert "administrators only" in out
    assert not _lines(out, "RELAXED: ")
    notes = "\n".join(_lines(out, "NOTE: "))
    for expected in ("only speaks SMB1", "SMB2 or newer", "SMB signing is required for outgoing",
                     "signing for incoming", "guest (anonymous) access is refused", "CVE-2021-1678",
                     "needs an administrator once"):
        assert expected in notes, expected
    assert "never changes them" in out
    assert _verdict(out).startswith("VERDICT: SECURE")


def test_smb_compat_reports_every_relaxation_as_weakened(tmp_path):
    result = _run_smb(
        tmp_path,
        client={"RequireSecuritySignature": False, "EnableInsecureGuestLogons": False},
        server={"EnableSMB1Protocol": True, "RequireSecuritySignature": False},
        registry={
            MRXSMB10_KEY: {"Start": 3},
            # Group Policy wins over the SMB client configuration
            WS_POLICY_KEY: {"AllowInsecureGuestAuth": 1},
            PRINT_KEY: {"RpcAuthnLevelPrivacyEnabled": 0},
            PRINTERS_POLICY_KEY + "\\RPC": {"RpcUseNamedPipeProtocol": 1, "RpcAuthentication": 2,
                                           "RpcProtocols": 7, "RpcTcpPort": 49155},
            PRINTERS_POLICY_KEY + "\\PointAndPrint": {"RestrictDriverInstallationToAdministrators": 0},
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "SMB1 client (mrxsmb10): ENABLED (Start=3)" in out
    assert "SMB1 server: ENABLED" in out
    assert "SMB signing required (outgoing): no" in out
    assert "Insecure guest logons (AllowInsecureGuestAuth): ALLOWED [Group Policy]" in out
    assert "Print RPC privacy (RpcAuthnLevelPrivacyEnabled): DISABLED (0)" in out
    assert "RPC over named pipes (SMB) - policy 1" in out
    assert "RpcAuthentication): DISABLED" in out
    assert "(RpcProtocols / RpcTcpPort): 0x7 / 49155" in out
    assert "ANY USER (0)" in out
    relaxed = _lines(out, "RELAXED: ")
    assert len(relaxed) == 7, relaxed
    assert any("RequireSecuritySignature=0" in r for r in relaxed)
    assert _verdict(out).startswith("VERDICT: WEAKENED - 7 setting(s) lower security")


def test_smb_compat_falls_back_to_the_registry_when_the_cmdlets_fail(tmp_path):
    result = _run_smb(
        tmp_path, build="19045", client=None, server=None,
        registry={
            MRXSMB10_KEY: {"Start": 4},
            SRV_KEY: {"SMB1": 0, "RequireSecuritySignature": 0},
            WS_KEY: {"AllowInsecureGuestAuth": 0},
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "falling back to the registry" in out
    assert "SMB1 client (mrxsmb10): disabled (Start=4)" in out
    assert "SMB1 server: disabled" in out
    # not set on Windows 10: signing is not required by default there
    assert "SMB signing required (outgoing): not set - Windows default (not required before Windows 11 24H2)" in out
    assert "SMB signing required (incoming): no" in out
    assert "Insecure guest logons (AllowInsecureGuestAuth): blocked [LanmanWorkstation registry]" in out
    assert "SMB signing is required for outgoing" not in out
    assert _verdict(out).startswith("VERDICT: SECURE")


def test_smb_compat_unset_values_on_older_windows_are_not_called_weakened(tmp_path):
    result = _run_smb(tmp_path, build="19045", client=None, server=None)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "SMB1 server: unknown" in out
    assert "default depends on the edition" in out
    assert "[Windows default]" in out
    assert "guest (anonymous) access is refused" not in out
    assert _verdict(out).startswith("VERDICT: SECURE")


def test_smb_compat_unset_signing_and_guest_on_24h2_follow_the_new_defaults(tmp_path):
    result = _run_smb(tmp_path, build="26100", client=None, server=None)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "not set - Windows default (required on Windows 11 24H2 and newer)" in out
    assert "blocked by default on Windows 11 24H2" in out
    notes = "\n".join(_lines(out, "NOTE: "))
    assert "SMB signing is required for outgoing" in notes
    assert "guest (anonymous) access is refused" in notes


def test_smb_compat_guest_allowed_by_the_edition_default_is_not_called_relaxed(tmp_path):
    # Windows 10 / 11 before 24H2 on Home/Pro allow guest logons out of the
    # box - nobody relaxed anything, so no RELAXED line and no WEAKENED.
    result = _run_smb(tmp_path, build="19045",
                      client={"RequireSecuritySignature": False, "EnableInsecureGuestLogons": True},
                      server={"EnableSMB1Protocol": False, "RequireSecuritySignature": False})
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert ("Insecure guest logons (AllowInsecureGuestAuth): ALLOWED - default of this edition before "
            "Windows 11 24H2 (recommended: block) [SMB client configuration]") in out
    assert not _lines(out, "RELAXED: ")
    weak = _lines(out, "WEAK DEFAULT: ")
    assert len(weak) == 1 and "nobody relaxed it" in weak[0]
    assert _verdict(out).startswith("VERDICT: WEAK DEFAULT - nothing was relaxed, but 1 Windows default(s)")


@pytest.mark.parametrize("registry", [
    {WS_KEY: {"AllowInsecureGuestAuth": 1}},
    {WS_POLICY_KEY: {"AllowInsecureGuestAuth": 1}},
])
def test_smb_compat_guest_allowed_by_an_explicit_value_is_relaxed(tmp_path, registry):
    result = _run_smb(tmp_path, build="19045",
                      client={"RequireSecuritySignature": False, "EnableInsecureGuestLogons": True},
                      server={"EnableSMB1Protocol": False, "RequireSecuritySignature": False},
                      registry=registry)
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert not _lines(out, "WEAK DEFAULT: ")
    assert any("Insecure guest logons are allowed" in r for r in _lines(out, "RELAXED: "))
    assert _verdict(out).startswith("VERDICT: WEAKENED - 1 setting(s)")


def test_smb_compat_guest_allowed_on_24h2_without_a_value_is_relaxed(tmp_path):
    result = _run_smb(tmp_path, build="26100",
                      client={"RequireSecuritySignature": True, "EnableInsecureGuestLogons": True},
                      server=SECURE_SERVER)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not _lines(result.stdout, "WEAK DEFAULT: ")
    assert _verdict(result.stdout).startswith("VERDICT: WEAKENED - 1 setting(s)")


@pytest.mark.parametrize("spooler", ["Stopped", None])
def test_smb_compat_still_reports_when_the_spooler_is_not_running(tmp_path, spooler):
    result = _run_smb(tmp_path, spooler=spooler, client=SECURE_CLIENT, server=SECURE_SERVER)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Print Spooler: {spooler or 'not installed'}" in result.stdout
    assert "NOTE: The Print Spooler is not running" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: SECURE")
