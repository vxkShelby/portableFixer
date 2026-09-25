import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m19_win_features" / "actions.yaml"


def test_m19_catalog_loads_5_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m19_win_features"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 5


def test_m19_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "feature_list_report",
        "feature_legacy_insecure_report",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {"feature_disable_powershell_v2"}
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {
        "feature_enable_dotnet35",
        "feature_enable_sandbox",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m19_catalog_every_toggleable_feature_has_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "feature_disable_powershell_v2",
        "feature_enable_dotnet35",
        "feature_enable_sandbox",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in ("feature_list_report", "feature_legacy_insecure_report"):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m19_catalog_report_actions_degrade_gracefully_without_admin():
    # Get-WindowsOptionalFeature -Online requires elevation even to just
    # list features; a SAFE action must not surface a raw COMException.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for report_id in ("feature_list_report", "feature_legacy_insecure_report"):
        command = by_id[report_id].command
        assert "try {" in command
        assert "catch {" in command
        assert "needs administrator" in command


def test_m19_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "feature_list_report",
        "feature_legacy_insecure_report",
        "feature_enable_dotnet35",
        "feature_disable_powershell_v2",
        "feature_enable_sandbox",
    }


def test_m19_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m19_catalog_powershell_v2_undo_restores_the_saved_prior_state():
    # The undo used to re-enable PowerShell v2 unconditionally, and undo.ps1
    # records it for every exit-0 run - so running this on a PC where v2 was
    # already off and then running undo.ps1 turned the downgrade vector on.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "feature_disable_powershell_v2")
    backup = "feature_disable_powershell_v2_backup.json"
    assert backup in action.command
    assert backup in action.undo_command
    # Refreshed every run, written before the change, and a write failure
    # aborts the run instead of leaving undo to act on a stale file.
    assert "Set-Content -Path $bk -Encoding UTF8 -EA Stop" in action.command
    assert action.command.index("Set-Content -Path $bk") < action.command.index("Disable-WindowsOptionalFeature")
    assert "icacls $root /inheritance:r" in action.command
    assert "if (Test-Path $bk)" in action.undo_command
    assert "if ([string]$b.State -eq 'Enabled')" in action.undo_command
    undo = action.undo_command
    assert undo.index("$b.State -eq 'Enabled'") < undo.index("Enable-WindowsOptionalFeature")
    assert "No backup found - nothing changed." in undo
    assert "undo restores the state saved before the run" in action.description_en
    assert "undo obnoví stav uložený pred behom" in action.description_sk


def test_m19_catalog_sandbox_failure_exits_nonzero():
    # The catch used to only print the error, so a failed enable exited 0,
    # showed as OK and recorded an undo for a change that never happened.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "feature_enable_sandbox")
    catch = action.command.split("catch", 1)[1]
    assert "exit 1" in catch


def test_m19_catalog_undos_never_exit_0_early():
    # undo.ps1 concatenates every recorded undo_command into one script, so
    # an `exit 0` in one step would silently skip every undo step after it.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        if action.undo_command:
            assert "exit 0" not in action.undo_command, action.id


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


# Target is Windows PowerShell 5.1: every script must parse, and none may use
# PS7-only syntax (??, ?., ternary, &&/||) that pwsh accepts but 5.1 rejects.
PARSE_CHECKER = (
    "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
    "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
    "Write-Output PARSE_DONE"
)


def test_m19_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    module = load_module(CATALOG_PATH)
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in module.actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    env = os.environ.copy()
    env["PFSCRIPTS_FILE"] = str(scripts_file)
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=env, capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


def test_m19_catalog_powershell_v2_absent_feature_counts_as_nothing_to_do():
    # Windows 11 24H2+ no longer has the PowerShell 2.0 feature at all:
    # Get-WindowsOptionalFeature then throws "unknown feature" (0x800f080c),
    # which must read as "not present - nothing changed", not as a failure
    # blamed on missing administrator rights.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "feature_disable_powershell_v2")
    command = action.command
    assert "-2146498548" in command and "800f080c" in command
    lookup = command.index("Get-WindowsOptionalFeature")
    assert command.index("$f = $null", lookup) < command.index("$prior", lookup)


@pytest.mark.parametrize("lookup_error, expected_exit, expected_text", [
    # Unknown feature (24H2+): nothing to disable, nothing changed.
    ("[System.Runtime.InteropServices.COMException]::new('Feature name is unknown.', -2146498548)", 0,
     "state: NotPresent"),
    # Any other failure (e.g. not elevated) still fails the action.
    ("[System.Runtime.InteropServices.COMException]::new('The requested operation requires elevation.', -2147024156)", 1,
     "needs administrator"),
])
def test_m19_catalog_powershell_v2_lookup_errors_run(tmp_path, lookup_error, expected_exit, expected_text):
    exe = _powershell_or_skip()
    module = load_module(CATALOG_PATH)
    command = next(a for a in module.actions if a.id == "feature_disable_powershell_v2").command
    log = tmp_path / "calls.log"
    prelude = (
        f"$env:ProgramData = '{tmp_path}'; "
        f"function Get-WindowsOptionalFeature {{ throw ({lookup_error}) }}; "
        f"function Disable-WindowsOptionalFeature {{ Add-Content -Path '{log}' -Value 'DISABLE' }}; "
        "function icacls { $global:LASTEXITCODE = 0 }; "
        "foreach ($n in 'Get-WindowsOptionalFeature', 'Disable-WindowsOptionalFeature', 'icacls') { "
        "if ((Get-Command $n).CommandType -ne 'Function') { exit 97 } }; "
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", prelude + command],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == expected_exit, result.stdout + result.stderr
    assert expected_text in result.stdout
    assert not log.exists()  # never tried to disable anything
