"""The real catalog under the effect-based restore point rule (research G24)."""

import re
from pathlib import Path

from portablefix import preflight
from portablefix.models import RiskLevel
from portablefix.module_engine import load_all_modules

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"


def _catalog():
    modules, errors = load_all_modules(MODULES_DIR)
    assert errors == []
    return {action.id: (module, action) for module in modules for action in module.actions}


def test_m13_debloat_changes_all_get_a_restore_point():
    # The gap that started G24: MODERATE registry/app removals in a CLEANUP
    # module ran with no restore point at all under the category rule.
    catalog = _catalog()
    debloat = [(m, a) for m, a in catalog.values() if m.module_id == "m13_debloat" and a.risk != RiskLevel.SAFE]
    assert len(debloat) >= 15
    assert all(preflight.needs_restore_point(m, a) for m, a in debloat)
    assert preflight.needs_restore_point(*catalog["debloat_remove_onedrive"])


def test_read_only_checks_in_repair_categories_never_trigger_one():
    catalog = _catalog()
    for action_id in ("disk_smart_status", "sfc_verifyonly", "dism_checkhealth", "sec_firewall_status",
                      "drv_problem_devices", "winget_list_installed", "boot_bcd_report"):
        assert not preflight.needs_restore_point(*catalog[action_id]), action_id


def test_every_destructive_action_still_gets_one():
    for module, action in _catalog().values():
        if action.risk == RiskLevel.DESTRUCTIVE:
            assert preflight.needs_restore_point(module, action), action.id


def test_cache_cleanups_keep_running_without_a_restore_point():
    # Today's behaviour kept where a restore point cannot help: caches, the
    # Recycle Bin, a Defender scan.
    catalog = _catalog()
    for action_id in ("recycle_bin", "browser_cache_sweep", "crash_dumps", "wipe_free_space",
                      "sec_defender_update", "sec_defender_quickscan"):
        module, action = catalog[action_id]
        assert action.changes_system is False, action_id
        assert not preflight.needs_restore_point(module, action), action_id


# Cmdlets/tools that change persistent system state. A SAFE action using
# one must say so with `changes_system: true` - otherwise it would silently
# run without a restore point under the risk-derived default.
_PERSISTENT_WRITES = re.compile(
    r"\b(Set-ItemProperty|New-ItemProperty|Remove-ItemProperty|Set-Service|Set-MpPreference|"
    r"Register-ScheduledTask|Unregister-ScheduledTask|Disable-ScheduledTask|Enable-ScheduledTask|"
    r"Disable-WindowsOptionalFeature|Enable-WindowsOptionalFeature|Remove-AppxPackage|"
    r"Remove-AppxProvisionedPackage|Set-NetFirewallProfile|Set-ExecutionPolicy)\b"
    r"|\breg(\.exe)?\s+(add|delete|import|restore)\b"
    r"|\bbcdedit(\.exe)?\s+/(set|delete|deletevalue)\b"
    r"|\bpnputil(\.exe)?\s+/(add-driver|delete-driver)\b",
    re.IGNORECASE,
)


def _code_only(command: str) -> str:
    # Single-quoted literals are text (a "remove it yourself with pnputil
    # /delete-driver" hint), not something the action runs.
    return re.sub(r"'(?:[^']|'')*'", "''", command)


def test_safe_actions_that_write_system_state_declare_it():
    offenders = [
        action.id for _, action in _catalog().values()
        if action.risk == RiskLevel.SAFE and action.changes_system is not True
        and _PERSISTENT_WRITES.search(_code_only(action.command))
    ]
    assert offenders == []


def test_the_persistent_write_pattern_catches_what_it_should():
    assert _PERSISTENT_WRITES.search("Set-ItemProperty -Path HKLM:\\X -Name Y -Value 1")
    assert _PERSISTENT_WRITES.search("reg.exe add HKLM\\X /v Y /d 1 /f")
    assert _PERSISTENT_WRITES.search("bcdedit /set {current} safeboot minimal")
    assert not _PERSISTENT_WRITES.search("Get-ItemProperty -Path HKLM:\\X; reg query HKLM\\X; bcdedit /enum")
    assert not _PERSISTENT_WRITES.search(_code_only("Write-Output 'remove it with pnputil /delete-driver oem1.inf'"))
