"""Batches across a restart (research G03): ordering, the resume file's
lifecycle and the keep-awake calls - all Qt-free."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from portablefix import batch_resume
from portablefix.batch_resume import (
    ES_CONTINUOUS,
    ES_SYSTEM_REQUIRED,
    KeepAwake,
    PendingBatch,
    discard_pending,
    load_pending,
    order_restarting_last,
    plan_restart_split,
    resume_path,
    save_pending,
)
from portablefix.module_engine import ModuleLoadError, load_all_modules, load_module

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


# --- ordering ---------------------------------------------------------------

def test_restarting_actions_move_to_the_end_keeping_both_orders():
    restarts = {"offline", "offline2"}.__contains__
    queue = ["a", "offline", "b", "offline2", "c"]
    assert order_restarting_last(queue, restarts) == ["a", "b", "c", "offline", "offline2"]
    assert order_restarting_last(["a", "b"], restarts) == ["a", "b"]
    assert order_restarting_last([], restarts) == []


def test_restart_split_names_what_waits_for_the_restart():
    before_next = {"chkdsk"}.__contains__
    restarts = {"offline"}.__contains__
    split = plan_restart_split(["clean", "chkdsk", "sfc", "dism"], restarts, before_next)
    assert split.restart_action_id == "chkdsk" and split.waiting_ids == ("sfc", "dism")
    # Last in the batch: it stops nothing.
    assert plan_restart_split(["clean", "chkdsk"], restarts, before_next).waiting_ids == ()
    # A restarting action stops whatever is queued after it.
    split = plan_restart_split(["clean", "offline", "offline_b"], {"offline", "offline_b"}.__contains__, before_next)
    assert split.restart_action_id == "offline" and split.waiting_ids == ("offline_b",)
    assert plan_restart_split(["clean"], restarts, before_next) == batch_resume.RestartSplit()


# --- resume file ------------------------------------------------------------

def _pending(**overrides) -> PendingBatch:
    values = dict(
        run_id="20260925T090000-abcd1234", action_ids=["sfc_scannow", "dism_restorehealth"],
        restart_after="disk_full_scan_reboot", job={"technician": "Jana", "client": "Novák", "note": "pomalý PC"},
        undo_steps=["Write-Output 'undo'"], irreversible=["[MODERATE] X (x)"], hive_backups=["C:\\\\hb"],
        snapshot_before={"free_bytes": 123},
    )
    values.update(overrides)
    return PendingBatch(**values)


def test_resume_file_round_trips_on_the_same_computer(tmp_path):
    path = save_pending(tmp_path, _pending(), now=NOW, computer="PC-KLIENT")
    assert path == tmp_path / "Data" / "pending_batch.json"
    assert not path.with_suffix(".json.tmp").exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1 and raw["computer"] == "PC-KLIENT" and raw["dry_run"] is False
    # Diacritics stay readable in the file.
    assert "Novák" in path.read_text(encoding="utf-8")

    loaded = load_pending(tmp_path, now=NOW + timedelta(minutes=10), computer="pc-klient")
    assert loaded == _pending(computer="PC-KLIENT", created=NOW.isoformat())
    # Loading offers it; it is not consumed yet.
    assert path.exists()


def test_resume_file_keeps_the_dry_run_flag(tmp_path):
    save_pending(tmp_path, _pending(dry_run=True), now=NOW, computer="PC")
    assert load_pending(tmp_path, now=NOW, computer="PC").dry_run is True


@pytest.mark.parametrize("age", [timedelta(hours=24, seconds=1), timedelta(days=3)])
def test_stale_resume_file_is_deleted_not_offered(tmp_path, age):
    save_pending(tmp_path, _pending(), now=NOW, computer="PC")
    assert load_pending(tmp_path, now=NOW + age, computer="PC") is None
    assert not resume_path(tmp_path).exists()


def test_resume_file_just_under_a_day_is_still_offered(tmp_path):
    save_pending(tmp_path, _pending(), now=NOW, computer="PC")
    assert load_pending(tmp_path, now=NOW + timedelta(hours=23, minutes=59), computer="PC") is not None


def test_resume_file_from_another_computer_is_deleted(tmp_path):
    # The USB stick went on to the next client's PC.
    save_pending(tmp_path, _pending(), now=NOW, computer="PC-KLIENT-1")
    assert load_pending(tmp_path, now=NOW, computer="PC-KLIENT-2") is None
    assert not resume_path(tmp_path).exists()


def test_resume_file_dated_in_the_future_is_deleted(tmp_path):
    save_pending(tmp_path, _pending(), now=NOW + timedelta(hours=2), computer="PC")
    assert load_pending(tmp_path, now=NOW, computer="PC") is None
    assert not resume_path(tmp_path).exists()


@pytest.mark.parametrize("content", [
    "not json",
    "[]",
    json.dumps({"version": 99, "run_id": "r", "action_ids": ["a"], "computer": "PC", "created": NOW.isoformat()}),
    json.dumps({"version": 1, "run_id": "", "action_ids": ["a"], "computer": "PC", "created": NOW.isoformat()}),
    json.dumps({"version": 1, "run_id": "r", "action_ids": [], "computer": "PC", "created": NOW.isoformat()}),
    json.dumps({"version": 1, "run_id": "r", "action_ids": [3], "computer": "PC", "created": NOW.isoformat()}),
    json.dumps({"version": 1, "run_id": "r", "action_ids": ["a"], "computer": "PC", "created": "yesterday"}),
    # A naive timestamp can't be compared safely - not trusted.
    json.dumps({"version": 1, "run_id": "r", "action_ids": ["a"], "computer": "PC", "created": "2026-09-25T10:00:00"}),
])
def test_unreadable_resume_file_is_deleted(tmp_path, content):
    path = resume_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert load_pending(tmp_path, now=NOW, computer="PC") is None
    assert not path.exists()


def test_corrupt_optional_fields_are_dropped_not_fatal(tmp_path):
    path = resume_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "version": 1, "run_id": "r", "action_ids": ["a"], "computer": "PC", "created": NOW.isoformat(),
        "job": ["x"], "undo_steps": "nope", "irreversible": [1, "kept"], "snapshot_before": 5, "dry_run": "yes",
    }), encoding="utf-8")
    pending = load_pending(tmp_path, now=NOW, computer="PC")
    assert pending.job == {} and pending.undo_steps == [] and pending.irreversible == ["kept"]
    assert pending.snapshot_before == {} and pending.dry_run is False


def test_no_resume_file_means_nothing_to_offer(tmp_path):
    assert load_pending(tmp_path, now=NOW, computer="PC") is None


def test_discard_removes_the_file_and_tolerates_a_missing_one(tmp_path):
    save_pending(tmp_path, _pending(), now=NOW, computer="PC")
    discard_pending(tmp_path)
    assert not resume_path(tmp_path).exists()
    discard_pending(tmp_path)


def test_save_replaces_an_older_resume_file(tmp_path):
    save_pending(tmp_path, _pending(action_ids=["old"]), now=NOW, computer="PC")
    save_pending(tmp_path, _pending(action_ids=["new"]), now=NOW, computer="PC")
    assert load_pending(tmp_path, now=NOW, computer="PC").action_ids == ["new"]


def test_a_snapshot_json_cannot_hold_does_not_cost_the_resume(tmp_path):
    save_pending(tmp_path, _pending(snapshot_before={"odd": object()}), now=NOW, computer="PC")
    pending = load_pending(tmp_path, now=NOW, computer="PC")
    assert pending.snapshot_before == {} and pending.action_ids == ["sfc_scannow", "dism_restorehealth"]


def test_save_failure_is_an_oserror_for_the_caller(tmp_path):
    blocker = tmp_path / "Data"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    with pytest.raises(OSError):
        save_pending(tmp_path, _pending(), now=NOW, computer="PC")


def test_nothing_is_registered_to_start_with_windows():
    # G03 on purpose resumes only when the technician starts PortableFix.
    source = (REPO / "portablefix" / "batch_resume.py").read_text(encoding="utf-8")
    for needle in ("RunOnce", "CurrentVersion\\\\Run", "schtasks", "Startup"):
        assert needle not in source


# --- keep-awake -------------------------------------------------------------

def test_keep_awake_sets_and_clears_the_execution_state_once():
    calls = []
    lock = KeepAwake(setter=lambda flags: calls.append(flags) or 1)
    lock.acquire()
    lock.acquire()
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED] and lock.active
    lock.release()
    lock.release()
    assert calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED, ES_CONTINUOUS] and not lock.active
    # The display may still turn off - only system sleep is held off.
    assert not calls[0] & 0x00000002


def test_keep_awake_swallows_a_failing_call():
    def broken(flags):
        raise OSError("no kernel32")

    lock = KeepAwake(setter=broken)
    lock.acquire()
    lock.release()
    assert not lock.active


def test_keep_awake_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(batch_resume.sys, "platform", "linux")
    lock = KeepAwake()
    lock.acquire()
    lock.release()
    assert lock._setter is None


# --- catalog flags ----------------------------------------------------------

def _yaml(tmp_path, risk, flag):
    path = tmp_path / "actions.yaml"
    path.write_text(
        "module_id: mx\nactions:\n  - id: a\n    label_sk: A\n    label_en: A\n"
        f"    risk: {risk}\n    command: \"Write-Output 'a'\"\n    {flag}\n",
        encoding="utf-8",
    )
    return path


def test_restart_flags_load_and_default_to_false(tmp_path):
    assert load_module(_yaml(tmp_path, "REQUIRES_REBOOT", "restarts_pc: true")).actions[0].restarts_pc is True
    action = load_module(_yaml(tmp_path, "REQUIRES_REBOOT", "restart_before_next: true")).actions[0]
    assert action.restart_before_next is True and action.restarts_pc is False
    action = load_module(_yaml(tmp_path, "REQUIRES_REBOOT", "undo_command: null")).actions[0]
    assert not action.restarts_pc and not action.restart_before_next


@pytest.mark.parametrize("flag", ["restarts_pc: true", "restart_before_next: true"])
def test_restart_flags_need_the_requires_reboot_tier(tmp_path, flag):
    with pytest.raises(ModuleLoadError, match="REQUIRES_REBOOT"):
        load_module(_yaml(tmp_path, "MODERATE", flag))


def test_restart_flags_must_be_real_booleans(tmp_path):
    with pytest.raises(ModuleLoadError):
        load_module(_yaml(tmp_path, "REQUIRES_REBOOT", 'restarts_pc: "yes"'))


def test_catalog_flags_exactly_the_actions_that_restart_or_need_a_restart_first():
    modules, errors = load_all_modules(REPO / "Modules")
    assert errors == []
    actions = [a for m in modules for a in m.actions]
    # Only Defender Offline restarts Windows by itself (Start-MpWDOScan).
    assert {a.id for a in actions if a.restarts_pc} == {"sec_defender_offline_scan"}
    assert {a.id for a in actions if a.restart_before_next} == {"disk_full_scan_reboot", "wu_uninstall_last_update"}
    for action in actions:
        if "Start-MpWDOScan" in action.command or "Restart-Computer" in action.command:
            assert action.restarts_pc, action.id
