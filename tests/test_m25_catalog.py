"""m25_power_sleep (research N3): read-only sleep/wake reports.

The commands run in PowerShell against stubbed tools: powercfg.exe, the WMI
query, the registry read and icacls are shadowed by functions, and the script
exits 97 unless each name really resolves to the stub - a test run never reads
the host's power configuration. Stub output is Slovak on purpose: powercfg
prints localized text, so a command that keyed off English words would fail
here."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m25_power_sleep" / "actions.yaml"
IDS = ["wake_last_report", "wake_timers_report", "wake_armed_devices_report",
       "sleep_blockers_report", "modern_standby_report", "sleepstudy_report"]
STUB_GUARD_EXIT = 97
SK_TEXT = ["Počet histórie zobudení - 1", "Zdroj zobudenia: Časovač"]


def _module():
    return load_module(CATALOG_PATH)


def _action(action_id):
    return next(a for a in _module().actions if a.id == action_id)


# --- the catalog ---------------------------------------------------------------

def test_m25_catalog_is_six_read_only_reports():
    module = _module()
    assert module.module_id == "m25_power_sleep"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert [a.id for a in module.actions] == IDS
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE, action.id
        assert action.undo_command is None and action.preview_command is None, action.id
        assert action.label_sk and action.label_en and action.description_sk and action.description_en, action.id


@pytest.mark.parametrize("action_id", IDS)
def test_m25_actions_never_read_localized_text(action_id):
    command = _action(action_id).command
    # Verdicts come from exit codes, the registry and WMI instance paths.
    for forbidden in ("Select-String", "-match", "-imatch", "-replace", "-split", ".Message", "[regex]",
                      "IndexOf", "Out-String"):
        assert forbidden not in command, f"{action_id}: {forbidden}"


@pytest.mark.parametrize("action_id", IDS)
def test_m25_actions_change_nothing_but_the_sleepstudy_folder(action_id):
    command = _action(action_id).command
    for forbidden in ("powercfg.exe /change", "/setactive", "/setacvalueindex", "/setdcvalueindex",
                      "/deviceenablewake", "/devicedisablewake", "Set-ItemProperty", "New-ItemProperty",
                      "Stop-Service", "Restart-Computer"):
        assert forbidden not in command, f"{action_id}: {forbidden}"


def test_m25_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {a.id: a.command for a in _module().actions}
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    checker = (
        "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
        "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
        "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
        "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
        "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
        "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
        "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
        "Write-Output PARSE_DONE"
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", checker],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


# --- running the commands against stubs -----------------------------------------

def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _run(tmp_path, action_id, *, exit_code=0, text=SK_TEXT, cim=None, cim_throws=False, cs="absent",
         writes_report=True, root_link_to=None):
    """cim: list of (InstanceName, Enable) pairs; cs: 'absent' or the CsEnabled value."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "calls.log"
    pdata = tmp_path / "ProgramData"
    pdata.mkdir()
    if root_link_to is not None:
        # %ProgramData%\PortableFix is a junction to somewhere else.
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(pdata / "PortableFix"), str(root_link_to)],
                              capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if made.returncode != 0:
            pytest.skip("cannot create a junction here")
    out_lines = "; ".join(_q(t) for t in text) if text else "$null"
    stubs = [
        # powercfg.exe: logs its arguments, prints Slovak text, writes the
        # sleep study file where /output points (args: /sleepstudy /output <file> /duration 7).
        "function powercfg.exe { "
        f"Add-Content -Path {_q(str(log))} -Value ('powercfg ' + ($args -join ' ')); "
        f"if ({'$true' if writes_report else '$false'} -and $args[0] -eq '/sleepstudy') {{ Set-Content -LiteralPath $args[2] -Value '<html/>' }}; "
        f"$global:LASTEXITCODE = {exit_code}; @({out_lines}) }}",
    ]
    if cim_throws:
        stubs.append("function Get-CimInstance { [CmdletBinding()] param([string] $Namespace, [string] $ClassName) throw 'Neplatný priestor názvov' }")
    else:
        rows = "; ".join(f"[pscustomobject]@{{ InstanceName = {_q(n)}; Enable = {'$true' if e else '$false'} }}"
                         for n, e in (cim or []))
        stubs.append("function Get-CimInstance { [CmdletBinding()] param([string] $Namespace, [string] $ClassName) "
                     f"Add-Content -Path {_q(str(log))} -Value ('cim ' + $Namespace + ' ' + $ClassName); @({rows or '$null'}) | Where-Object {{ $_ }} }}")
    if cs == "absent":
        stubs.append("function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) throw 'Hodnota neexistuje' }")
    else:
        stubs.append("function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath, [string] $Name) "
                     f"[pscustomobject]@{{ CsEnabled = {cs} }} }}")
    names = ["powercfg.exe", "Get-CimInstance", "Get-ItemProperty"]
    guard = (
        "foreach ($n in " + ", ".join(_q(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    script = "; ".join(["[Console]::OutputEncoding=[Text.Encoding]::UTF8", f"$env:ProgramData = {_q(str(pdata))}",
                        f"$env:USERDOMAIN = 'PF'", *stubs, guard, _action(action_id).command])
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    calls = log.read_text(encoding="utf-8-sig").splitlines() if log.exists() else []
    return result, calls, pdata


@pytest.mark.parametrize("action_id, args", [
    ("wake_last_report", "/lastwake"),
    ("wake_timers_report", "/waketimers"),
    ("wake_armed_devices_report", "/devicequery wake_armed"),
    ("sleep_blockers_report", "/requests"),
    ("modern_standby_report", "/a"),
])
def test_powercfg_text_is_shown_raw_and_success_is_the_exit_code(tmp_path, action_id, args):
    result, calls, _ = _run(tmp_path, action_id)
    assert result.returncode == 0, result.stdout + result.stderr
    for line in SK_TEXT:
        assert line in result.stdout
    assert "powercfg " + args in calls
    assert "failed" not in result.stdout


@pytest.mark.parametrize("action_id, args", [
    ("wake_last_report", "/lastwake"),
    ("wake_timers_report", "/waketimers"),
    ("wake_armed_devices_report", "/devicequery wake_armed"),
    ("sleep_blockers_report", "/requests"),
    ("modern_standby_report", "/a"),
])
def test_a_failing_powercfg_fails_the_action_whatever_it_printed(tmp_path, action_id, args):
    result, _, _ = _run(tmp_path, action_id, exit_code=1, text=["Tento príkaz vyžaduje oprávnenia správcu."])
    assert result.returncode == 1
    assert "powercfg " + args + " failed (exit code 1)" in result.stdout
    assert "Tento príkaz vyžaduje oprávnenia správcu." in result.stdout  # the raw text stays visible


@pytest.mark.parametrize("action_id", ["wake_timers_report", "sleep_blockers_report"])
def test_admin_only_reports_say_so_on_failure(tmp_path, action_id):
    result, _, _ = _run(tmp_path, action_id, exit_code=1, text=[])
    assert "needs administrator" in result.stdout


def test_an_empty_powercfg_answer_with_exit_code_zero_is_not_an_error(tmp_path):
    # "No wake timers" prints a (localized) sentence, but even an empty answer is exit code 0.
    result, _, _ = _run(tmp_path, "wake_timers_report", text=[])
    assert result.returncode == 0


def test_wake_armed_lists_enabled_instances_by_path(tmp_path):
    cim = [("HID\\VID_046D&PID_C54D&MI_00\\7&1d4c56d6&0&0000_0", True), ("PCI\\VEN_8086&DEV_15BC\\3&1", False),
           ("USB\\ROOT_HUB30\\4&2", True)]
    result, calls, _ = _run(tmp_path, "wake_armed_devices_report", cim=cim)
    assert result.returncode == 0
    assert "(WMI MSPower_DeviceWakeEnable): 2" in result.stdout
    assert "HID\\VID_046D&PID_C54D&MI_00\\7&1d4c56d6&0&0000_0" in result.stdout
    assert "USB\\ROOT_HUB30\\4&2" in result.stdout
    assert "PCI\\VEN_8086" not in result.stdout
    assert "cim root\\wmi MSPower_DeviceWakeEnable" in calls


def test_wake_armed_without_wmi_still_shows_powercfg_and_succeeds(tmp_path):
    result, _, _ = _run(tmp_path, "wake_armed_devices_report", cim_throws=True)
    assert result.returncode == 0
    assert SK_TEXT[0] in result.stdout
    assert "not readable here" in result.stdout


@pytest.mark.parametrize("cs, verdict", [
    ("1", "MODERN STANDBY: yes"),
    ("0", "MODERN STANDBY: no"),
    ("absent", "MODERN STANDBY: no"),
])
def test_modern_standby_verdict_comes_from_the_registry_value(tmp_path, cs, verdict):
    # The powercfg text (Slovak here, with no English sleep-state names) plays no part.
    result, _, _ = _run(tmp_path, "modern_standby_report", cs=cs)
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.startswith("MODERN STANDBY: ")]
    assert len(lines) == 1 and lines[0].startswith(verdict), result.stdout


def test_sleepstudy_saves_the_html_in_a_programdata_folder(tmp_path):
    result, calls, pdata = _run(tmp_path, "sleepstudy_report")
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list((pdata / "PortableFix").glob("SleepStudy_*/SleepStudy.html"))
    assert len(reports) == 1
    assert str(reports[0]) in result.stdout
    assert any(c.startswith("powercfg /sleepstudy /output ") and c.endswith(" /duration 7") for c in calls)


@pytest.mark.parametrize("exit_code, writes_report", [(1, False), (1, True), (0, False)])
def test_sleepstudy_failure_exits_non_zero_and_leaves_no_empty_folder(tmp_path, exit_code, writes_report):
    result, _, pdata = _run(tmp_path, "sleepstudy_report", exit_code=exit_code, writes_report=writes_report,
                            text=["Chyba: Modern Standby nie je podporovaný."])
    assert result.returncode == 1
    assert f"(exit code {exit_code})" in result.stdout
    assert "Chyba: Modern Standby nie je podporovaný." in result.stdout
    assert list((pdata / "PortableFix").glob("SleepStudy_*")) == []


# --- the sleepstudy artifact folder -----------------------------------------------

def test_sleepstudy_runs_under_the_central_acl_prelude_and_adds_no_icacls_of_its_own():
    from portablefix.backup_acl import HARDEN_PS, references_backup_dir
    from portablefix.executor import build_execution_plan

    command = _action("sleepstudy_report").command
    assert references_backup_dir(command)
    assert "icacls" not in command  # a user grant on the folder would weaken the SYSTEM/Administrators-only ACL
    plan = build_execution_plan(command, dry_run=False, harden_backups=True)
    script = plan.argv[-1]
    assert script.startswith(HARDEN_PS)
    assert script.index(HARDEN_PS) < script.index(command)
    # Deleting is limited to the one file and an empty folder.
    assert "-Recurse" not in command and "Remove-Item" not in command
    assert "[IO.File]::Delete($reportPath)" in command and "[IO.Directory]::Delete($dest, $false)" in command


@pytest.mark.parametrize("action_id", [i for i in IDS if i != "sleepstudy_report"])
def test_the_other_actions_write_no_artifact_and_need_no_folder(tmp_path, action_id):
    from portablefix.backup_acl import references_backup_dir

    command = _action(action_id).command
    assert not references_backup_dir(command)
    for forbidden in ("/output", "Set-Content", "Out-File", "Add-Content", "New-Item", "Remove-Item",
                      "[IO.File]::", "Copy-Item", "Move-Item", ">"):
        assert forbidden not in command.replace("-Where-Object", ""), f"{action_id}: {forbidden}"


def test_sleepstudy_refuses_a_reparse_point_as_the_folder_and_writes_nothing(tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    result, calls, _ = _run(tmp_path / "run", "sleepstudy_report", root_link_to=target)
    assert result.returncode == 1
    assert "Refusing to write" in result.stdout
    assert not any(c.startswith("powercfg") for c in calls)
    assert list(target.iterdir()) == []
