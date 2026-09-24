import sys

import pytest

from portablefix import preflight
from portablefix.models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from portablefix.preflight import (
    BLOCKER,
    WARNING,
    BatchProfile,
    PowerStatus,
    Probes,
    profile_for,
    run_preflight,
)

GB = 1024**3
CHANGING = BatchProfile(changes_system=True, needs_admin=True, long_or_reboot=True, servicing=True)
LIGHT = BatchProfile(changes_system=True, needs_admin=True, long_or_reboot=False, servicing=False)


def _action(action_id="a", risk=RiskLevel.MODERATE, **kwargs) -> ActionDef:
    return ActionDef(id=action_id, label_sk="A", label_en="A", risk=risk, command="x", **kwargs)


def _module(category=ModuleCategory.CLEANUP, *actions) -> ModuleDef:
    return ModuleDef(module_id="m", actions=list(actions), category=category)


def _healthy(**overrides) -> Probes:
    probes = Probes(
        power=lambda: PowerStatus(on_battery=False, percent=100),
        pending_reboot=lambda: [],
        system_free_bytes=lambda: 100 * GB,
        is_admin=lambda: True,
        busy_tasks=lambda: [],
    )
    for name, value in overrides.items():
        setattr(probes, name, value)
    return probes


def _codes(result, severity=None):
    return [i.code for i in result.issues if severity is None or i.severity == severity]


def test_healthy_pc_has_no_issues():
    assert run_preflight(CHANGING, _healthy()).issues == ()


def test_read_only_batch_is_never_checked():
    # Even a PC that would fail every check: a diagnostics-only batch
    # changes nothing, so nothing is worth blocking or asking about.
    probes = _healthy(
        busy_tasks=lambda: ["x"], is_admin=lambda: False, system_free_bytes=lambda: 1,
        pending_reboot=lambda: ["cbs"], power=lambda: PowerStatus(True, 1),
    )
    assert run_preflight(BatchProfile(), probes).issues == ()


def test_low_free_space_blocks_and_tight_space_warns():
    result = run_preflight(CHANGING, _healthy(system_free_bytes=lambda: 3 * GB))
    assert _codes(result, BLOCKER) == ["low_disk"]
    assert "3.0" in result.issues[0].text("en") and "3,0" in result.issues[0].text("sk")
    assert _codes(run_preflight(CHANGING, _healthy(system_free_bytes=lambda: 8 * GB)), WARNING) == ["disk_tight"]
    assert run_preflight(CHANGING, _healthy(system_free_bytes=lambda: 10 * GB)).issues == ()


def test_battery_below_threshold_blocks_long_or_reboot_batches_only():
    low = _healthy(power=lambda: PowerStatus(on_battery=True, percent=preflight.BATTERY_BLOCK_PERCENT - 1))
    assert _codes(run_preflight(CHANGING, low), BLOCKER) == ["battery_low"]
    # The same battery for a short batch is only worth a warning.
    assert _codes(run_preflight(LIGHT, low), WARNING) == ["on_battery"]
    assert _codes(run_preflight(LIGHT, low), BLOCKER) == []


def test_battery_above_threshold_or_unknown_percent_only_warns():
    ok = _healthy(power=lambda: PowerStatus(on_battery=True, percent=80))
    assert _codes(run_preflight(CHANGING, ok)) == ["on_battery"]
    unknown = _healthy(power=lambda: PowerStatus(on_battery=True, percent=None))
    result = run_preflight(CHANGING, unknown)
    assert _codes(result) == ["on_battery"] and "?" in result.issues[0].text("en")


def test_mains_power_and_desktop_are_fine():
    assert run_preflight(CHANGING, _healthy(power=lambda: PowerStatus(on_battery=False, percent=5))).issues == ()
    assert run_preflight(CHANGING, _healthy(power=lambda: PowerStatus(on_battery=None, percent=None))).issues == ()


def test_pending_servicing_reboot_blocks_servicing_batches():
    result = run_preflight(CHANGING, _healthy(pending_reboot=lambda: ["cbs", "file_rename"]))
    [issue] = result.issues
    assert issue.code == "pending_reboot" and issue.severity == BLOCKER
    assert issue.params["sources"] == ("cbs", "file_rename")
    assert "Windows component servicing" in issue.text("en")


def test_pending_reboot_only_warns_for_non_servicing_batch_or_file_rename_alone():
    assert _codes(run_preflight(LIGHT, _healthy(pending_reboot=lambda: ["wu"])), WARNING) == ["pending_reboot"]
    # PendingFileRenameOperations alone is left by countless installers.
    assert _codes(run_preflight(CHANGING, _healthy(pending_reboot=lambda: ["file_rename"])), WARNING) == [
        "pending_reboot"
    ]


def test_missing_admin_blocks_only_batches_that_need_it():
    assert _codes(run_preflight(CHANGING, _healthy(is_admin=lambda: False)), BLOCKER) == ["no_admin"]
    no_admin_needed = BatchProfile(changes_system=True)
    assert run_preflight(no_admin_needed, _healthy(is_admin=lambda: False)).issues == ()


def test_busy_job_is_a_blocker_that_cannot_be_overridden():
    result = run_preflight(CHANGING, _healthy(busy_tasks=lambda: ["winget", "uninstall"]))
    [issue] = result.blockers
    assert issue.code == "busy" and issue.overridable is False
    assert result.hard_blocked
    assert "winget, uninstall" in issue.text("en")
    assert not run_preflight(CHANGING, _healthy(system_free_bytes=lambda: GB)).hard_blocked


def test_failing_or_unknown_probes_never_block():
    def boom():
        raise OSError("probe failed")

    probes = Probes(power=boom, pending_reboot=boom, system_free_bytes=boom, is_admin=boom, busy_tasks=boom)
    assert run_preflight(CHANGING, probes).issues == ()
    # The defaults are all "unknown".
    assert run_preflight(CHANGING, Probes()).issues == ()


def test_summary_names_codes_for_the_audit_log():
    result = run_preflight(CHANGING, _healthy(system_free_bytes=lambda: GB, power=lambda: PowerStatus(True, 90)))
    assert result.summary() == "Pre-flight: blockers: low_disk; warnings: on_battery."
    assert run_preflight(CHANGING, _healthy()).summary() == "Pre-flight: no blockers, no warnings."


@pytest.mark.parametrize("language", ["sk", "en"])
def test_every_issue_text_is_translated(language):
    probes = _healthy(
        busy_tasks=lambda: ["x"], is_admin=lambda: False, system_free_bytes=lambda: GB,
        pending_reboot=lambda: ["cbs", "wu", "file_rename"], power=lambda: PowerStatus(True, 5),
    )
    issues = list(run_preflight(CHANGING, probes).issues)
    issues += list(run_preflight(CHANGING, _healthy(system_free_bytes=lambda: 8 * GB, power=lambda: PowerStatus(True, 90))).issues)
    assert {i.code for i in issues} == {
        "busy", "no_admin", "pending_reboot", "low_disk", "battery_low", "disk_tight", "on_battery",
    }
    for issue in issues:
        text = issue.text(language)
        assert "preflight_" not in text and "{" not in text


def test_profile_for_classifies_the_batch():
    safe_diag = (_module(ModuleCategory.DIAGNOSTICS), _action(risk=RiskLevel.SAFE))
    assert profile_for([safe_diag]) == BatchProfile()

    # G24: a read-only SAFE check in REPAIR no longer counts as a change...
    safe_repair = (_module(ModuleCategory.REPAIR), _action(risk=RiskLevel.SAFE))
    assert profile_for([safe_repair]) == BatchProfile()
    # ...but a SAFE action that declares changes_system is guarded by a
    # restore point - it needs neither admin nor servicing checks.
    safe_changing = (_module(ModuleCategory.REPAIR), _action(risk=RiskLevel.SAFE, changes_system=True))
    assert profile_for([safe_changing]) == BatchProfile(changes_system=True)

    moderate_cleanup = (_module(ModuleCategory.CLEANUP), _action())
    assert profile_for([moderate_cleanup]) == BatchProfile(changes_system=True, needs_admin=True)

    long_repair = (_module(ModuleCategory.REPAIR), _action(inactivity_timeout_sec=1800))
    assert profile_for([long_repair]) == BatchProfile(True, True, True, True)

    reboot = (_module(ModuleCategory.CLEANUP), _action(risk=RiskLevel.REQUIRES_REBOOT))
    assert profile_for([reboot]) == BatchProfile(True, True, True, True)


def test_needs_restore_point_follows_the_actions_effect_not_its_category():
    # G24: DESTRUCTIVE always; otherwise the explicit field; otherwise non-SAFE.
    assert preflight.needs_restore_point(_module(ModuleCategory.CLEANUP), _action(risk=RiskLevel.DESTRUCTIVE))
    assert not preflight.needs_restore_point(_module(ModuleCategory.WINGET), _action(risk=RiskLevel.SAFE))
    assert not preflight.needs_restore_point(_module(ModuleCategory.REPAIR), _action(risk=RiskLevel.SAFE))
    assert preflight.needs_restore_point(_module(ModuleCategory.CLEANUP), _action())
    assert preflight.needs_restore_point(_module(ModuleCategory.DIAGNOSTICS), _action(risk=RiskLevel.REQUIRES_REBOOT))
    assert preflight.needs_restore_point(
        _module(ModuleCategory.DIAGNOSTICS), _action(risk=RiskLevel.SAFE, changes_system=True)
    )
    assert not preflight.needs_restore_point(_module(ModuleCategory.CLEANUP), _action(changes_system=False))
    # An explicit false can never switch a DESTRUCTIVE action's restore point off.
    assert preflight.needs_restore_point(
        _module(ModuleCategory.CLEANUP), _action(risk=RiskLevel.DESTRUCTIVE, changes_system=False)
    )


def test_is_long_action():
    assert preflight.is_long_action(_action(inactivity_timeout_sec=600))
    assert preflight.is_long_action(_action(hard_cap_sec=21600))
    assert not preflight.is_long_action(_action(inactivity_timeout_sec=90))
    assert not preflight.is_long_action(_action())


@pytest.mark.real_preflight_probes
def test_system_probes_are_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(preflight.sys, "platform", "linux")
    probes = preflight.system_probes(is_admin=lambda: True, busy_tasks=lambda: [])
    assert probes.power() is None
    assert probes.pending_reboot() is None
    assert probes.system_free_bytes() is None
    assert run_preflight(CHANGING, probes).issues == ()


@pytest.mark.real_preflight_probes
@pytest.mark.skipif(sys.platform != "win32", reason="the real probes read Win32/registry state")
def test_real_windows_probes_return_well_formed_values():
    # Smoke test on the Windows CI runner: struct layout, winreg types and
    # the %SystemDrive% path are only exercised here.
    power = preflight._windows_power()
    assert power is None or isinstance(power, PowerStatus)
    if power is not None and power.percent is not None:
        assert 0 <= power.percent <= 100
    reboot = preflight._windows_pending_reboot()
    assert reboot is None or (
        isinstance(reboot, list)
        and all(s in (preflight.REBOOT_CBS, preflight.REBOOT_WU, preflight.REBOOT_FILE_RENAME) for s in reboot)
    )
    free = preflight._windows_system_free_bytes()
    assert isinstance(free, int) and free > 0


def test_healthy_probe_defaults_keep_the_host_state_out_of_tests():
    # tests/conftest.py: without the opt-out marker the real probes report
    # a healthy machine, so a pending restart on the host blocks no test.
    probes = preflight.system_probes(is_admin=lambda: True, busy_tasks=lambda: [])
    assert probes.pending_reboot() == []
    assert run_preflight(CHANGING, probes).issues == ()
