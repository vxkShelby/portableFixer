import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m08_security" / "actions.yaml"


def test_m08_catalog_loads_24_actions_in_security_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m08_security"
    assert module.category == ModuleCategory.SECURITY
    assert len(module.actions) == 24


def test_m08_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 15
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_restore_taskmgr_regedit",
        "hard_disable_smb1",
        "hard_disable_rdp",
        "hard_firewall_enable_all",
        "hard_disable_autologon",
        "hard_smartscreen_default",
    }
    assert set(by_risk[RiskLevel.REQUIRES_REBOOT]) == {"hard_lsa_protection_enable"}
    assert RiskLevel.DESTRUCTIVE not in by_risk


def test_m08_catalog_only_hardening_actions_have_undo_command():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in (
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_restore_taskmgr_regedit",
        "hard_disable_smb1",
        "hard_disable_rdp",
        "hard_firewall_enable_all",
        "hard_disable_autologon",
        "hard_lsa_protection_enable",
        "hard_smartscreen_default",
    ):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "sec_firewall_status",
        "sec_uac_status",
        "sec_rdp_status",
        "sec_autologon_check",
        "sec_listening_ports_audit",
        "sec_root_cert_audit",
        "sec_bootsector_check",
        "sec_hidden_process_heuristic",
        "sec_process_signature_audit",
        "sec_local_admins",
        "sec_recent_local_accounts",
        "sec_password_never_expires",
        "sec_windows_update_last",
        "sec_hosts_anomaly",
        "sec_suspicious_scheduled_tasks",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m08_catalog_rdp_and_lsa_protection_excluded_from_select_all():
    # hard_disable_rdp can self-lockout an active RDP session the instant it
    # runs, and hard_lsa_protection_enable can break older AV/backup/VPN
    # drivers at the *next* boot when the technician may not be present -
    # both must require a deliberate, individual checkbox, never get swept
    # in by a bulk "select all" in their risk tab or category.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["hard_disable_rdp"].exclude_from_select_all is True
    assert by_id["hard_lsa_protection_enable"].exclude_from_select_all is True
    for action_id in (
        "hard_disable_smb1",
        "hard_firewall_enable_all",
        "hard_disable_autologon",
        "hard_smartscreen_default",
    ):
        assert by_id[action_id].exclude_from_select_all is False, action_id


def test_m08_catalog_lsa_protection_uses_reversible_non_uefi_locked_value():
    # Per Microsoft's LSA protection docs it is RunAsPPL=1 that sets a UEFI
    # variable (on Secure Boot PCs) which a registry delete cannot clear -
    # 2 is the no-UEFI-lock value a registry undo can really turn off, but
    # Windows only enforces 2 from Windows 11 22H2 (build 22621). Older
    # builds must be refused outright rather than quietly given 1.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "hard_lsa_protection_enable")
    assert "-Value 2" in action.command
    assert "-Value 1" not in action.command
    assert "[Environment]::OSVersion.Version.Build" in action.command
    assert "-ge 22621" in action.command
    refusal = action.command.split("if (-not $supported)", 1)[1].split("}", 1)[0]
    assert "exit 1" in refusal
    assert "RunAsPPL=2" in action.description_en
    assert "22621" in action.description_en
    assert "22621" in action.description_sk


def test_m08_catalog_autologon_undo_never_restores_the_password():
    # Deliberate design choice from the source research: persisting the
    # recovered plaintext password anywhere (registry or our own backup
    # file) would just relocate the exact secret-at-rest problem this
    # action exists to fix.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "hard_disable_autologon")
    assert "DefaultPassword" not in action.undo_command


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
        "sec_firewall_status",
        "sec_uac_status",
        "sec_rdp_status",
        "sec_autologon_check",
        "hard_uac_restore_default",
        "sec_wpbt_disable",
        "sec_listening_ports_audit",
        "sec_root_cert_audit",
        "sec_bootsector_check",
        "sec_hidden_process_heuristic",
        "sec_process_signature_audit",
        "sec_restore_taskmgr_regedit",
        "sec_local_admins",
        "sec_recent_local_accounts",
        "sec_password_never_expires",
        "sec_windows_update_last",
        "sec_hosts_anomaly",
        "sec_suspicious_scheduled_tasks",
        "hard_disable_smb1",
        "hard_disable_rdp",
        "hard_firewall_enable_all",
        "hard_disable_autologon",
        "hard_lsa_protection_enable",
        "hard_smartscreen_default",
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


def test_m08_catalog_new_account_and_network_audits_are_safe_and_reversible_dont_apply():
    # sec_local_admins / sec_recent_local_accounts / sec_password_never_expires /
    # sec_windows_update_last / sec_hosts_anomaly / sec_suspicious_scheduled_tasks
    # are read-only account/network audit additions (research-security-additions.md
    # Bucket A) - all SAFE, none mutate anything so none need an undo_command.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in (
        "sec_local_admins",
        "sec_recent_local_accounts",
        "sec_password_never_expires",
        "sec_windows_update_last",
        "sec_hosts_anomaly",
        "sec_suspicious_scheduled_tasks",
    ):
        assert by_id[action_id].risk == RiskLevel.SAFE, action_id
        assert by_id[action_id].undo_command is None, action_id


def test_m08_catalog_noisy_new_audits_disclose_their_false_positive_risk():
    # Both of these routinely flag entirely benign, common cases (a household
    # with its own ad-block hosts file; legit auto-updaters running from
    # AppData) - the description must say so, not present raw output as a verdict.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "benign" in by_id["sec_hosts_anomaly"].description_en.lower()
    assert "not an automatic verdict" in by_id["sec_suspicious_scheduled_tasks"].description_en.lower()


def test_m08_catalog_password_never_expires_only_flags_enabled_accounts():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "sec_password_never_expires")
    assert "$_.Enabled -eq $true" in action.command


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


# --- Hardening undos restore the captured prior state ---

# Each of these used to undo to a fixed weak state (SMB1 on, RDP on,
# AutoAdminLogon '1', RunAsPPL/DisableWpbtExecution removed), and undo.ps1
# records an undo for every exit-0 run - so re-running the batch on an
# already-hardened PC and then running undo.ps1 left it *less* secure than
# before PortableFix touched it.
BACKED_UP_HARDENING_IDS = (
    "hard_disable_smb1",
    "hard_disable_rdp",
    "hard_disable_autologon",
    "sec_wpbt_disable",
    "hard_lsa_protection_enable",
)


def test_m08_catalog_hardening_actions_save_prior_state_and_undo_reads_it_conditionally():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in BACKED_UP_HARDENING_IDS:
        action = by_id[action_id]
        backup = f"{action_id}_backup.json"
        assert backup in action.command, action_id
        assert backup in action.undo_command, action_id
        # Refreshed on every run (not only the first), and written with
        # -EA Stop so a backup that can't be written aborts the run before
        # anything changes instead of leaving undo to act on a stale file.
        assert "if (-not (Test-Path $bk))" not in action.command, action_id
        assert "Set-Content -Path $bk -Encoding UTF8 -EA Stop" in action.command, action_id
        assert "if (Test-Path $bk)" in action.undo_command, action_id
        assert "No backup found - nothing changed." in action.undo_command, action_id


def test_m08_catalog_hardening_backups_live_in_the_locked_down_programdata_folder():
    # The backup decides whether undo weakens security, so a standard user
    # must not be able to edit it between the run and the undo - same
    # unconditional icacls lock-down as the m01/m10/m18/m20 exports.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in BACKED_UP_HARDENING_IDS:
        command = by_id[action_id].command
        assert "icacls $root /inheritance:r" in command, action_id
        assert "S-1-5-32-544" in command, action_id
        assert "S-1-5-18" in command, action_id
        assert "Test-Path $root" not in command, action_id


def test_m08_catalog_hardening_backup_is_written_before_the_change():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    first_change = {
        "hard_disable_smb1": "Set-SmbServerConfiguration",
        "hard_disable_rdp": "Set-ItemProperty",
        "hard_disable_autologon": "Set-ItemProperty",
        "sec_wpbt_disable": "Set-ItemProperty",
        "hard_lsa_protection_enable": "Set-ItemProperty",
    }
    for action_id, change in first_change.items():
        command = by_id[action_id].command
        assert command.index("Set-Content -Path $bk") < command.index(change), action_id


def test_m08_catalog_hardening_undos_only_weaken_when_the_saved_state_was_weak():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}

    smb1 = by_id["hard_disable_smb1"].undo_command
    assert "if ($b.EnableSMB1Protocol -eq $true)" in smb1
    assert smb1.index("$b.EnableSMB1Protocol -eq $true") < smb1.index("-EnableSMB1Protocol $true")

    rdp = by_id["hard_disable_rdp"]
    assert "EnabledRuleNames" in rdp.command
    assert "'absent' -eq $b.fDenyTSConnections" in rdp.undo_command
    assert "-Value ([int]$b.fDenyTSConnections)" in rdp.undo_command
    assert "-Value 0" not in rdp.undo_command
    # Re-enables exactly the rules that were enabled before, never the
    # whole group (which would also open rules that were deliberately off).
    assert "Enable-NetFirewallRule -Name $names" in rdp.undo_command
    assert "Enable-NetFirewallRule -Group" not in rdp.undo_command

    autologon = by_id["hard_disable_autologon"].undo_command
    assert "if ([string]$b.AutoAdminLogon -eq '1')" in autologon
    assert autologon.index("$b.AutoAdminLogon -eq '1'") < autologon.index("-Value '1'")

    wpbt = by_id["sec_wpbt_disable"].undo_command
    assert "'absent' -eq $b.DisableWpbtExecution" in wpbt
    assert wpbt.index("'absent' -eq $b.DisableWpbtExecution") < wpbt.index("Remove-ItemProperty")

    lsa = by_id["hard_lsa_protection_enable"].undo_command
    assert "[string]$b.Wrote -ne '2'" in lsa
    assert "'absent' -eq $b.RunAsPPL" in lsa
    assert lsa.index("$b.Wrote -ne '2'") < lsa.index("Remove-ItemProperty")


def test_m08_catalog_lsa_protection_leaves_an_already_enabled_setting_alone():
    # 1 (possibly UEFI-locked on purpose by the owner/IT) or 2 already
    # protects LSASS - overwriting either is pointless at best; the backup
    # still records 'unchanged' so an undo recorded for this no-op run
    # changes nothing.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "hard_lsa_protection_enable")
    assert "$already = ($v -eq 1 -or $v -eq 2)" in action.command
    assert "'unchanged'" in action.command
    already = action.command.split("if ($already)", 1)[1].split("}", 1)[0]
    assert "exit 0" in already
    assert action.command.index("if ($already)") < action.command.index("-Value 2")


def test_m08_catalog_autologon_backup_never_stores_the_password():
    # Only AutoAdminLogon is backed up - DefaultPassword appears solely in
    # the Remove-ItemProperty that deletes it.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "hard_disable_autologon")
    assert action.command.count("DefaultPassword") == 1
    assert "Remove-ItemProperty -Path $w -Name DefaultPassword" in action.command
    assert "[PSCustomObject]@{ AutoAdminLogon = $prior }" in action.command


def test_m08_catalog_undos_never_exit_0_early():
    # undo.ps1 concatenates every recorded undo_command into one script, so
    # an `exit 0` in one step ("nothing to do") would silently skip every
    # undo step after it.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        if action.undo_command:
            assert "exit 0" not in action.undo_command, action.id


def test_m08_catalog_selectors_are_locale_independent():
    # Display names are translated on non-English Windows (the Remote
    # Desktop rule group and the Administrators group both have Slovak
    # names), so -DisplayGroup 'Remote Desktop' / -Group 'Administrators'
    # silently matched nothing there. The resource ID and well-known SID
    # are the same on every language.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action in module.actions:
        for text in (action.command, action.undo_command, action.preview_command):
            if text:
                assert "-DisplayGroup '" not in text, action.id
                assert "-Group 'Administrators'" not in text, action.id
    assert "-Group '@FirewallAPI.dll,-28752'" in by_id["sec_rdp_status"].command
    assert "$grp = '@FirewallAPI.dll,-28752'" in by_id["hard_disable_rdp"].command
    assert "Get-NetFirewallRule -Group $grp" in by_id["hard_disable_rdp"].command
    assert "Disable-NetFirewallRule -Group $grp" in by_id["hard_disable_rdp"].command
    assert "Get-LocalGroupMember -SID 'S-1-5-32-544'" in by_id["sec_local_admins"].command


def test_m08_catalog_changed_undo_descriptions_mention_the_saved_prior_state():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for action_id in BACKED_UP_HARDENING_IDS:
        action = by_id[action_id]
        assert "undo restores the state saved before the run" in action.description_en, action_id
        assert "undo obnoví stav uložený pred behom" in action.description_sk, action_id
        assert f"{action_id}_backup.json" in action.description_en, action_id
        assert f"{action_id}_backup.json" in action.description_sk, action_id


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


# The target is Windows PowerShell 5.1: every script must parse, and none may
# use PS7-only syntax (??, ?., ternary, &&/||) that pwsh accepts but 5.1
# rejects at run time - so the check still means something under pwsh.
# Scripts go through a file, not an env var: together they exceed the
# 32767-character limit of a single Windows environment variable.
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


def test_m08_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
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
