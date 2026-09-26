"""What running one catalog action means, without any GUI (research G21).

The window (portablefix/gui/main_window.py) and the headless CLI
(portablefix/cli.py) both go through these functions, so a batch started
either way builds the same PowerShell plan, writes the same audit entry and
the same undo.ps1 steps:

* temp_protection()  - the %TEMP% self-protection of the two temp cleanups;
* prepare_plan()     - the ExecutionPlan, with the ops state file (G10) and
                       the selected-items file (G05) it needs;
* audit_entry()      - the audit record of a finished run, findings (G02)
                       and item ids included;
* undo_outcome()     - the undo.ps1 steps, or the "not reversible" line.

Qt-free: the CLI imports it before any QApplication exists.
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import items as items_mod
from . import ops, paths, pfjson
from .audit_log import AuditEntry, make_entry
from .executor import (
    CHECK_HARD_CAP_SEC,
    CHECK_INACTIVITY_SEC,
    ExecutionPlan,
    PlanRun,
    build_execution_plan,
    parse_check_state,
)
from .models import ActionDef, RiskLevel
from .target_user import TargetUser

USER_TEMP_ID = "user_temp"
SYSTEM_TEMP_ID = "system_temp"


def temp_protection(action: ActionDef, app_dir: Path | None = None) -> tuple[Path | None, str | None]:
    """(the %TEMP% child the action must never delete, refusal i18n key).

    A refusal key means the app's own root IS the temp root, or the root is
    redirected through a junction - there is no single safe child to spare,
    so the action must not run at all."""
    app_dir = app_dir or paths.get_base_dir()
    temp_protect = paths.compute_temp_protected_child(app_dir)
    windir_temp_protect = paths.compute_windir_temp_protected_child(app_dir)
    if action.id == USER_TEMP_ID and temp_protect is not None and temp_protect == paths.resolve_temp_root():
        return None, "user_temp_blocked_app_is_temp_root"
    if (
        action.id == SYSTEM_TEMP_ID
        and windir_temp_protect is not None
        and windir_temp_protect == paths.resolve_windir_temp_root()
    ):
        return None, "system_temp_blocked_app_is_temp_root"
    return (windir_temp_protect if action.id == SYSTEM_TEMP_ID else temp_protect), None


# The listing is SAFE and reads the system only; a hang there must not
# hold the batch for the default 5 minutes of silence.
ITEMS_LIST_INACTIVITY_SEC = 120
ITEMS_LIST_HARD_CAP_SEC = 600


def list_items(action: ActionDef, target_user: TargetUser | None, holder: list | None = None) -> list | None:
    """Runs the action's items_command (research G05) and returns its items,
    or None when the listing failed (non-zero exit). `holder` receives the
    PlanRun, so a caller on another thread can cancel it."""
    plan = build_execution_plan(action.items_command, dry_run=False, target_user=target_user)
    run = PlanRun(plan, inactivity_timeout_sec=ITEMS_LIST_INACTIVITY_SEC, hard_cap_sec=ITEMS_LIST_HARD_CAP_SEC)
    if holder is not None:
        holder.append(run)
    if run.run() != 0:
        return None
    return items_mod.parse_items(run.captured_output)


ALREADY_APPLIED = "already_applied"


def check_plan(action: ActionDef, *, dry_run: bool, target_user: TargetUser | None) -> ExecutionPlan | None:
    """The "already applied?" check a real run starts with (research G09),
    or None: a DRY-RUN previews instead, and most actions have no check."""
    if dry_run or not action.check_command:
        return None
    return build_execution_plan(action.check_command, dry_run=False, target_user=target_user)


def check_state(action: ActionDef, target_user: TargetUser | None, holder: list | None = None) -> str:
    """APPLIED / NOT_APPLIED / UNKNOWN for one action, for the status chip
    and the snapshot. `holder` receives the PlanRun so it can be cancelled."""
    plan = build_execution_plan(action.check_command, dry_run=False, target_user=target_user)
    run = PlanRun(plan, inactivity_timeout_sec=CHECK_INACTIVITY_SEC, hard_cap_sec=CHECK_HARD_CAP_SEC)
    if holder is not None:
        holder.append(run)
    if run.run() != 0:
        return "UNKNOWN"
    return parse_check_state(run.captured_output)


@dataclass
class PreparedRun:
    plan: ExecutionPlan
    ops_state: Path | None = None
    items_file: Path | None = None


def prepare_plan(
    action: ActionDef, *, dry_run: bool, state_dir: Path, run_id: str, target_user: TargetUser | None,
    temp_protect: Path | None = None, item_ids: list[str] | None = None,
) -> PreparedRun:
    """The plan for one run of `action`. A per-item action gets its ids
    written to a fresh file under Backups/<run_id>/items/ first (raises
    items.ItemsError for an invalid id); a real run of an `ops:` action gets
    a fresh state file path."""
    items_file = None
    if action.items_command:
        items_file = items_mod.write_items_file(
            items_mod.items_file_path(state_dir, run_id, action.id), list(item_ids or []),
        )
    if dry_run and action.preview_command:
        plan = build_execution_plan(
            action.preview_command, dry_run=False, temp_protect=temp_protect, target_user=target_user,
            items_file=items_file,
        )
        return PreparedRun(plan, None, items_file)
    ops_state = None
    if action.ops and not dry_run:
        # A fresh file per run of the action: a second run in the same
        # session must not overwrite the first capture.
        ops_state = ops.state_file_path(state_dir, run_id, action.id)
    plan = build_execution_plan(
        action.command, dry_run, temp_protect=temp_protect, ops_state=ops_state, target_user=target_user,
        items_file=items_file,
    )
    return PreparedRun(plan, ops_state, items_file)


def audit_entry(
    module_id: str, action: ActionDef, exit_code: int, output_lines: list[str], *, dry_run: bool, run_id: str,
    elevated: bool, warning_text: str = "", target_user: TargetUser | None = None,
    payloads: list[dict] | None = None, item_ids: list[str] | None = None, decision: str = "",
) -> AuditEntry:
    # warned/warning_text come from the confirmation actually shown and
    # accepted, not re-derived from the risk.
    return make_entry(
        module_id, action.id, action.command, exit_code, "\n".join(output_lines), dry_run, run_id,
        risk=action.risk.value, warned=bool(warning_text), elevated=elevated, warning_text=warning_text,
        decision=decision, items=list(item_ids or []), findings=pfjson.findings(payloads),
        **(target_user.audit_fields() if target_user is not None else {}),
    )


@dataclass
class UndoOutcome:
    steps: list[str] = field(default_factory=list)
    # One "not reversible" line for undo.ps1's header, or "".
    irreversible: str = ""


def _irreversible_text(action: ActionDef, language: str, exit_code: int, problem: str = "") -> str:
    text = f"[{action.risk.value}] {action.label(language)} ({action.id})"
    if problem:
        text += f" - {problem}"
    if exit_code != 0:
        # A failed run may still have changed part of the system, so it is
        # listed too - with its exit code, not hidden.
        text += f" - exit {exit_code}"
    return text


def _user_prelude(step: str, target_user: TargetUser | None) -> str:
    if "$__pfUser" not in step:
        return ""
    # undo.ps1 runs later, maybe as someone else entirely - it must restore
    # the same profile the change went to. Unknown target: no prelude, so
    # clear what an earlier step's prelude left in undo.ps1's one shared
    # scope - this step then falls back to HKCU: as it did at run time.
    prelude = target_user.prelude() if target_user is not None else ""
    return prelude or "Remove-Variable __pfUserHive,__pfUserSid -EA SilentlyContinue; "


def undo_outcome(
    action: ActionDef, exit_code: int, *, language: str, target_user: TargetUser | None = None,
    ops_state: Path | None = None, item_ids: list[str] | None = None, payloads: list[dict] | None = None,
) -> UndoOutcome:
    """What a real (not DRY-RUN) run of `action` adds to undo.ps1."""
    if action.ops:
        # Research G10: generated from the state the command captured - also
        # after a failure, since the ops applied before it are in the capture.
        try:
            step = ops.undo_step(action.id, action.ops, ops_state)
        except ops.OpsStateError as exc:
            return UndoOutcome(irreversible=_irreversible_text(action, language, exit_code, str(exc)))
        if step is not None:
            return UndoOutcome(steps=[step])
        if exit_code != 0:
            # Refused before capturing anything - so nothing changed.
            return UndoOutcome()
        return UndoOutcome(irreversible=_irreversible_text(
            action, language, exit_code, "the state file with the previous values is missing",
        ))
    if action.items_command:
        # Research G05: one step per item the command reported as changed.
        if action.undo_command:
            records = items_mod.undo_records(payloads, item_ids or [])
            prelude = _user_prelude(action.undo_command, target_user)
            steps = [
                items_mod.undo_step(action.id, action.undo_command, item_id, prior, user_prelude=prelude)
                for item_id, prior in records
            ]
            if steps:
                return UndoOutcome(steps=steps)
            if exit_code == 0:
                # Nothing reported as changed: nothing to put back.
                return UndoOutcome()
            return UndoOutcome(irreversible=_irreversible_text(
                action, language, exit_code, "no item reported what it changed",
            ))
        if action.risk == RiskLevel.SAFE:
            return UndoOutcome()
        return UndoOutcome(irreversible=_irreversible_text(
            action, language, exit_code, "items: " + ", ".join(item_ids or []),
        ))
    if action.undo_command:
        if exit_code != 0:
            return UndoOutcome()
        return UndoOutcome(steps=[_user_prelude(action.undo_command, target_user) + action.undo_command])
    if action.risk != RiskLevel.SAFE:
        return UndoOutcome(irreversible=_irreversible_text(action, language, exit_code))
    return UndoOutcome()
