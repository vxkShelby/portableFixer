import html
import json
import platform
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import branding as branding_mod
from . import health, intake, redaction
from .audit_log import audit_log_path
from .i18n import translate
from .models import ActionDef, ModuleDef
from .snapshot import compare_snapshots, new_autostart_entries


def _find_action(modules: list[ModuleDef], module_id: str, action_id: str) -> ActionDef | None:
    for module in modules:
        if module.module_id != module_id:
            continue
        for action in module.actions:
            if action.id == action_id:
                return action
    return None


def _module_category(modules: list[ModuleDef], module_id: str) -> str:
    for module in modules:
        if module.module_id == module_id:
            category = module.category
            return getattr(category, "value", str(category))
    return "UNKNOWN"


def _build_module_summary(actions: list[dict]) -> list[dict]:
    # Grouped by module_id (the audit log has no category of its own), in the
    # order each module first ran so the table mirrors the chronological log.
    summary: dict[str, dict] = {}
    for a in actions:
        row = summary.setdefault(
            a["module_id"],
            {"module_id": a["module_id"], "category": a.get("category", "UNKNOWN"),
             "total": 0, "ok": 0, "failed": 0, "dry_run": 0},
        )
        row["total"] += 1
        if a["exit_code"] == 0:
            row["ok"] += 1
        else:
            row["failed"] += 1
        if a["dry_run"]:
            row["dry_run"] += 1
    return list(summary.values())


_REQUIRED_ENTRY_FIELDS = ("module_id", "action_id", "timestamp", "exit_code", "dry_run")


def _read_audit_entries(base_dir: Path, run_id: str) -> list[dict]:
    path = audit_log_path(base_dir, run_id)
    if not path.exists():
        return []
    entries = []
    # Decoded line by line, not as one file: a single line with invalid
    # UTF-8 (a torn write, bytes from a tool writing in the ANSI code page)
    # made read_text raise for the whole log and aborted the report, taking
    # every good entry with it.
    for raw_line in path.read_bytes().splitlines():
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        # A truncated write mid-crash can leave a syntactically valid JSON
        # object missing fields the report needs - skip it rather than
        # letting a KeyError abort the whole report for every good entry.
        if not isinstance(entry, dict) or any(f not in entry for f in _REQUIRED_ENTRY_FIELDS):
            continue
        entries.append(entry)
    return entries


def read_audit_entries(base_dir: Path, run_id: str) -> list[dict]:
    """The run's audit entries as the report reads them (the intake /
    outtake forms and the work timer load their saved state from here)."""
    return _read_audit_entries(base_dir, run_id)


_MAX_PREVIOUS_REPORT_BYTES = 10 * 1024 * 1024
# How many of the newest earlier reports are tried before giving up - enough
# to step over a few unreadable ones, bounded so a Reports folder full of
# broken files can't stall the report written at every batch end.
_MAX_PREVIOUS_REPORT_CANDIDATES = 20


def _find_previous_report(reports_dir: Path, hostname: str, run_id: str | None = None) -> dict | None:
    if not reports_dir.exists():
        return None
    # Filtered by prefix rather than glob(f"{hostname}_*.json") - a Windows
    # computer name can itself contain glob metacharacters like [ or ].
    # Sorted by filename, not mtime: run_id starts with a UTC timestamp (see
    # main.py's run_id format), so filenames already sort chronologically -
    # and stay correct after a USB copy/backup/restore resets mtimes.
    prefix = f"{hostname}_"
    # The current run's own report is rewritten after every batch of the
    # session - without excluding it, the second batch compared the run
    # against itself ("since last visit": +0.0 GB, same run id).
    own_name = f"{hostname}_{run_id}.json" if run_id else None
    candidates = sorted(
        (p for p in reports_dir.glob("*.json") if p.name.startswith(prefix) and p.name != own_name),
        key=lambda p: p.name,
    )
    # Newest first, falling back to older visits: one unreadable report (not
    # UTF-8, cut short by an unplugged USB, hand-edited) must neither abort
    # this run's report nor hide every earlier visit behind it.
    for candidate in reversed(candidates[-_MAX_PREVIOUS_REPORT_CANDIDATES:]):
        try:
            if candidate.stat().st_size > _MAX_PREVIOUS_REPORT_BYTES:
                continue
            parsed = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # ValueError covers both UnicodeDecodeError and JSONDecodeError.
            continue
        # A prior report that's syntactically valid JSON but not the shape
        # this tool itself ever writes (corrupted, hand-edited, or from a
        # future/past schema) is skipped rather than let a malformed field
        # crash the comparison a few lines later.
        if isinstance(parsed, dict):
            return parsed
    return None


def _build_comparison(previous: dict | None, actions: list[dict], snapshot_after: dict) -> dict | None:
    if previous is None:
        return None
    prev_snapshot = previous.get("snapshot_after")
    if not isinstance(prev_snapshot, dict):
        prev_snapshot = {}
    prev_free = prev_snapshot.get("free_gb")
    curr_free = snapshot_after.get("free_gb")
    free_gb_delta = None
    if isinstance(prev_free, (int, float)) and isinstance(curr_free, (int, float)):
        free_gb_delta = round(curr_free - prev_free, 2)
    return {
        "previous_run_id": previous.get("run_id"),
        "previous_generated_at": previous.get("generated_at"),
        "free_gb_delta": free_gb_delta,
        "previous_action_count": len(prev_actions) if isinstance(prev_actions := previous.get("actions"), list) else 0,
        "action_count": len(actions),
    }


SYSTEM_MODULE_ID = "_system"


def _frozen_risk(entry: dict, action: ActionDef | None) -> str:
    # The risk tier recorded at execution time is what the technician saw
    # and confirmed - a later catalog edit must not rewrite history. Logs
    # written before the field existed (or with it empty) fall back to the
    # current catalog, as before.
    risk = entry.get("risk")
    if isinstance(risk, str) and risk:
        return risk
    return action.risk.value if action else "UNKNOWN"


# Subjects main_window gives the Programs/winget panels' events - not catalog
# "module/action" pairs (catalog module ids never start with "_").
_UNINSTALL_SUBJECT = "_uninstaller/"
_LEFTOVER_SUBJECT = "_uninstaller/orphan_cleanup:"
_WINGET_SUBJECT = "_winget/"


def _is_panel_subject(subject: str) -> bool:
    return subject.startswith("_")


def _subject_label(subject: str, modules: list[ModuleDef], language: str) -> str:
    # What the event was about, in the report's language: "Uninstall: 7-Zip"
    # rather than the raw "_uninstaller/7-Zip" key. Order matters - the
    # leftover-cleanup prefix is itself an uninstaller subject.
    if subject.startswith(_LEFTOVER_SUBJECT):
        return translate("report_subject_leftover", language).format(name=subject[len(_LEFTOVER_SUBJECT):])
    if subject.startswith(_UNINSTALL_SUBJECT):
        return translate("report_subject_uninstall", language).format(name=subject[len(_UNINSTALL_SUBJECT):])
    if subject.startswith(_WINGET_SUBJECT):
        return translate("report_subject_winget", language).format(package=subject[len(_WINGET_SUBJECT):])
    if "/" in subject:
        module_id, _, action_id = subject.partition("/")
        action = _find_action(modules, module_id, action_id)
        return action.label(language) if action else action_id
    return ""


def _guarded_subjects_label(subject: str, subjects: list[str], language: str) -> str:
    # One panel restore point guarded several programs/packages picked at
    # once: name them all ("Uninstall: A, B, C") - naming only the first
    # read as if the others had no way back. "" when the list does not fit
    # (not a panel subject, or fewer than two); the caller names `subject`.
    # The leftover prefix is itself an uninstaller one, so it goes first.
    prefix = next((p for p in (_LEFTOVER_SUBJECT, _UNINSTALL_SUBJECT, _WINGET_SUBJECT) if subject.startswith(p)), "")
    if not prefix:
        return ""
    names = [
        item[len(prefix):] for item in subjects
        if item.startswith(prefix) and (prefix != _UNINSTALL_SUBJECT or not item.startswith(_LEFTOVER_SUBJECT))
    ]
    if len(names) < 2:
        return ""
    return _subject_label(prefix + ", ".join(names), [], language)


def _find_action_by_id(modules: list[ModuleDef], action_id: str) -> ActionDef | None:
    # Action ids are unique across the catalog (main_window looks them up
    # by id alone), and a resume event records bare ids.
    for module in modules:
        for action in module.actions:
            if action.id == action_id:
                return action
    return None


# The fixed English sentences main_window / main.py write for the restart
# and resume events (research G03) - PortableFix's own text, so reading the
# action ids back out of them is not parsing localized output. Display only;
# the event keeps the sentence verbatim.
_RESTART_IMMEDIATE = "restarts Windows immediately"
_RESTART_SAVED = re.compile(r"Saved to continue after the restart: (?P<ids>.+?)\.\s*$")
_RESTART_NOT_SAVED = re.compile(r"Could not save the rest of the batch \((?P<ids>.+?)\) - start it again by hand")
_RESUMED = re.compile(r"Continuing the batch after a restart \(\d+ action\(s\): (?P<ids>.+?)\)\.\s*$")
_RESUME_SKIPPED = re.compile(r"Not in this version's catalog, not continued: (?P<ids>.+?)\.\s*$")
_RESUME_DECLINED = re.compile(r"Technician declined to continue the batch after the restart: (?P<ids>.+?)\.\s*$")
_RESUME_HIVE_MISSING = re.compile(r"Registry hive backup of the first half not found: (?P<paths>.+?)\.\s*$")
_RESUME_PATTERNS = {
    "restart_pending": (_RESTART_SAVED, _RESTART_NOT_SAVED),
    "resumed_after_reboot": (_RESUMED,),
    "resume_skipped": (_RESUME_SKIPPED,),
    "resume_declined": (_RESUME_DECLINED,),
}


def _resume_action_labels(kind: str, output: str, modules: list[ModuleDef], language: str) -> list[str]:
    """The actions a restart/resume event names, as labels in the report's
    language ([] when it names none or the sentence is not recognised). An
    id no longer in the catalog stays as the id."""
    for pattern in _RESUME_PATTERNS.get(kind, ()):
        match = pattern.search(output)
        if match:
            labels = []
            for action_id in (item.strip() for item in match.group("ids").split(",")):
                action = _find_action_by_id(modules, action_id)
                labels.append(action.label(language) if action else action_id)
            return labels
    return []


def _build_event(entry: dict, modules: list[ModuleDef], language: str) -> dict:
    # str(): a corrupted log can hold any JSON value here.
    subject = str(entry.get("subject") or "")
    raw_subjects = entry.get("subjects")
    # Missing in logs written before the field existed.
    subjects = [str(item) for item in raw_subjects] if isinstance(raw_subjects, list) else []
    subject_label = _guarded_subjects_label(subject, subjects, language) or _subject_label(subject, modules, language)
    kind = entry["action_id"]
    event = {
        "timestamp": entry["timestamp"],
        "kind": kind,
        "exit_code": entry["exit_code"],
        "dry_run": entry["dry_run"],
        "output": entry.get("output", ""),
        "subject": subject,
        "subject_label": subject_label,
        # Everything one panel restore point guarded; [] = just `subject`.
        "subjects": subjects,
        "risk": entry.get("risk") or "",
        "warning_text": entry.get("warning_text", ""),
        "decision": entry.get("decision", ""),
        # None in logs written before the field existed.
        "restore_point_sequence": entry.get("restore_point_sequence"),
    }
    if kind in _RESUME_PATTERNS:
        # The saved / continued / dropped actions by name (G03) - the
        # English sentence in `output` lists bare ids.
        event["action_labels"] = _resume_action_labels(kind, str(entry.get("output") or ""), modules, language)
    return event


def _summarize_restore_points(events: list[dict]) -> list[dict]:
    # One restore point per batch, plus one before each real uninstall /
    # leftover cleanup / winget update started from a panel (G01). A panel's
    # point is marked and names what it guarded - otherwise the report
    # header showed it as if the batch had a restore point. The
    # technician's "continue anyway?" answer (only asked when it failed)
    # belongs to the latest point of the same subject; logs without
    # subjects pair it with the latest point, as before.
    points: list[dict] = []
    for event in events:
        subject = event.get("subject") or ""
        if event["kind"] == "restore_point":
            points.append({
                "timestamp": event["timestamp"],
                "created": event["exit_code"] == 0,
                "detail": event["output"],
                "decision": None,
                "sequence": event.get("restore_point_sequence"),
                "subject": subject,
                "subject_label": event.get("subject_label") or "",
                "subjects": event.get("subjects") or [],
                "panel": _is_panel_subject(subject),
            })
        elif event["kind"] == "restore_point_decision":
            for point in reversed(points):
                if not subject or not point["subject"] or point["subject"] == subject:
                    if point["decision"] is None:
                        point["decision"] = event["decision"] or None
                    break
    return points


def _summarize_elevation(entries: list[dict]) -> bool | None:
    values = {e["elevated"] for e in entries if isinstance(e.get("elevated"), bool)}
    # None when not recorded (older log) or, defensively, inconsistent.
    return values.pop() if len(values) == 1 else None


def _summarize_target_user(entries: list[dict]) -> dict:
    """Whose hive per-user settings went to (research G25), from the audit
    entries. The last recorded entry wins - detection runs once per start,
    and a batch continued after a restart is the newest start. {} when no
    entry recorded it (older log, or detection could not run)."""
    for entry in reversed(entries):
        status = entry.get("target_user_status")
        if isinstance(status, str) and status and status != "unknown":
            return {
                "status": status,
                "user": str(entry.get("target_user") or ""),
                "sid": str(entry.get("target_user_sid") or ""),
            }
    return {}


def build_report_data(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
    job: dict | None = None,
    storage_fallback: bool = False,
    branding: dict | None = None,
) -> dict:
    entries = _read_audit_entries(base_dir, run_id)
    actions = []
    events = []
    for entry in entries:
        if entry["module_id"] == SYSTEM_MODULE_ID:
            if entry["action_id"] in intake.EVENT_KINDS:
                # Forms and work time have sections of their own (G20).
                continue
            # Restore points, safety-prompt answers and batch stops are
            # facts about the run, not actions - keep them out of the action
            # counts/chips and list them in their own safety section.
            events.append(_build_event(entry, modules, language))
            continue
        action = _find_action(modules, entry["module_id"], entry["action_id"])
        actions.append(
            {
                "timestamp": entry["timestamp"],
                "module_id": entry["module_id"],
                "action_id": entry["action_id"],
                "label": action.label(language) if action else entry["action_id"],
                "risk": _frozen_risk(entry, action),
                "exit_code": entry["exit_code"],
                "dry_run": entry["dry_run"],
                "output": entry.get("output", ""),
                "category": _module_category(modules, entry["module_id"]),
                # None = not recorded (log written before these fields existed).
                "warned": entry.get("warned"),
                "warning_text": entry.get("warning_text", ""),
                "elevated": entry.get("elevated"),
                # Research G09: skipped by the "already applied?" check.
                "already_applied": entry.get("decision") == "already_applied",
                # Research G05 / G02: the ids it ran on, what it found.
                "items": entry.get("items") if isinstance(entry.get("items"), list) else [],
                "findings": entry.get("findings") if isinstance(entry.get("findings"), list) else [],
            }
        )
    hostname = socket.gethostname()
    previous = _find_previous_report(base_dir / "Reports", hostname, run_id)
    generated_at = datetime.now(timezone.utc)
    data = {
        "run_id": run_id,
        "language": language,
        "hostname": hostname,
        "generated_at": generated_at.isoformat(),
        "os": platform.platform(),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "actions": actions,
        # Only a real, successful run left a change that a restart applies -
        # a dry-run or a failed attempt listed here sent the client into a
        # needless reboot.
        "requires_restart": [
            a for a in actions
            if a["risk"] == "REQUIRES_REBOOT" and a["exit_code"] == 0 and not a["dry_run"]
        ],
        "previous_comparison": _build_comparison(previous, actions, snapshot_after),
        "module_summary": _build_module_summary(actions),
        "job": _clean_job(job),
        "events": events,
        "restore_points": _summarize_restore_points(events),
        "elevated": _summarize_elevation(entries),
        "target_user": _summarize_target_user(entries),
        "storage_fallback": bool(storage_fallback),
        # Research G02: Found / Fixed / Recommended atop report.html, from
        # the findings the diagnostics reported - and every area's verdict.
        "client_summary": health.client_summary(
            actions, language,
            label_of=lambda aid: _find_action_by_id(modules, aid).label(language) if _find_action_by_id(modules, aid) else None,
        ),
        "health_areas": {
            area: verdict["state"] for area, verdict in health.area_verdicts(health.latest_findings(actions)).items()
        },
    }
    # Research G20, each key only when there is something to show - an
    # unused form adds nothing to the report.
    intake_data = _build_intake(entries, language)
    if intake_data:
        data["intake"] = intake_data
    outtake_data = _build_outtake(entries, language)
    if outtake_data:
        data["outtake"] = outtake_data
    work_time = _build_work_time(entries, generated_at)
    if work_time:
        data["work_time"] = work_time
    branding_data = branding_mod.clean_branding(branding)
    if branding_data:
        data["branding"] = branding_data
    # Research G04: what starts with Windows now that did not at the last
    # visit - measured on arrival (snapshot_before), before this run's work.
    if previous is not None:
        new_autostart = new_autostart_entries(previous.get("snapshot_after"), snapshot_before)
        if new_autostart is not None:
            data["new_autostart"] = {"previous_run_id": previous.get("run_id"), "entries": new_autostart}
        # Research G09: applied at the last visit, not any more on arrival.
        drift = _check_drift(previous.get("snapshot_after"), snapshot_before)
        if drift:
            data["drift"] = [
                {"action_id": aid, "label": (_find_action_by_id(modules, aid).label(language)
                                             if _find_action_by_id(modules, aid) else aid)}
                for aid in drift
            ]
    return data


def _check_drift(previous_snapshot, current_snapshot) -> list[str]:
    before = previous_snapshot.get("checks") if isinstance(previous_snapshot, dict) else None
    now = current_snapshot.get("checks") if isinstance(current_snapshot, dict) else None
    if not isinstance(before, dict) or not isinstance(now, dict):
        return []
    return sorted(aid for aid, state in now.items() if state == "NOT_APPLIED" and before.get(aid) == "APPLIED")


def _build_intake(entries: list[dict], language: str) -> dict:
    found = intake.latest_intake(entries)
    if found is None:
        return {}
    form, timestamp = found
    data = form.to_dict()
    data["timestamp"] = timestamp
    # The codes are for machines; the labels, in the report's language, are
    # what the page shows - both in the JSON.
    data["condition_labels"] = [translate(f"intake_flag_{flag}", language) for flag in form.condition_flags]
    data["backup_label"] = translate(f"intake_backup_{form.backup}", language) if form.backup else ""
    data["password_handling_label"] = (
        translate(f"intake_password_{form.password_handling}", language) if form.password_handling else ""
    )
    return data


def _build_outtake(entries: list[dict], language: str) -> dict:
    found = intake.latest_outtake(entries)
    if found is None:
        return {}
    form, timestamp = found
    return {
        "timestamp": timestamp,
        "checks": [
            {
                "key": key, "label": translate(f"outtake_check_{key}", language),
                "result": form.checks[key], "result_label": translate(f"outtake_result_{form.checks[key]}", language),
            }
            for key in intake.OUTTAKE_CHECKS if key in form.checks
        ],
        "handed_to": form.handed_to,
        "handed_at": form.handed_at,
    }


def _build_work_time(entries: list[dict], now: datetime) -> dict:
    batch_total, batch_count = intake.batch_seconds(entries)
    timer = intake.timer_state(entries)
    timer_total = timer.total_seconds(now)
    if not batch_count and not timer_total and timer.running_since is None:
        return {}
    return {
        # The sum of every real (not DRY-RUN) batch of the run (G20) - the
        # billable machine time.
        "batch_seconds": batch_total,
        "batch_count": batch_count,
        # The technician's own start/stop timer; a running one counts up to
        # the moment the report was written.
        "timer_seconds": timer_total,
        "timer_running": timer.running_since is not None,
    }


_JOB_FIELDS = ("technician", "client", "note")


def _clean_job(job: dict | None) -> dict:
    """Technician / client / note for the report header - strings only,
    trimmed, empty fields dropped (so an unset job adds nothing)."""
    if not isinstance(job, dict):
        return {}
    cleaned = {}
    for key in _JOB_FIELDS:
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            cleaned[key] = value.strip()
    return cleaned


def _render_job(job: dict, t, target_user: dict | None = None) -> str:
    target_line = _render_target_user(target_user or {}, t)
    if not job and not target_line:
        return ""
    parts = []
    if job.get("technician"):
        parts.append(f"{t('report_job_technician')}: <strong>{html.escape(job['technician'])}</strong>")
    if job.get("client"):
        parts.append(f"{t('report_job_client')}: <strong>{html.escape(job['client'])}</strong>")
    line = " &middot; ".join(parts)
    note = ""
    if job.get("note"):
        note = f'<div class="job-note"><span class="lbl">{t("report_job_note")}</span>{html.escape(job["note"])}</div>'
    if target_line:
        line = f"{line}<br>{target_line}" if line else target_line
    return f'<div class="job">{line}{note}</div>'


def _render_target_user(target: dict, t) -> str:
    """The profile per-user settings went to - next to the technician's
    name, where a reader sees at once that it was the client's profile."""
    user = target.get("user") or target.get("sid")
    if not user:
        return ""
    text = f"{t('report_target_user')}: <strong>{html.escape(user)}</strong>"
    if target.get("sid") and target.get("sid") != user:
        text += f" ({html.escape(target['sid'])})"
    if target.get("status") == "different":
        text += f" &mdash; {t('report_target_user_differs')}"
    elif target.get("status") in ("ambiguous", "no_user"):
        text += f" &mdash; {t('report_target_user_unsure')}"
    return text


_RISK_COLORS = {
    "SAFE": "#9ece6a",
    "MODERATE": "#e0af68",
    "DESTRUCTIVE": "#f7768e",
    "REQUIRES_REBOOT": "#bb9af7",
    "UNKNOWN": "#8b93b8",
}

_CSS = """
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { font-family: 'Segoe UI', sans-serif; background: #1a1b26; color: #c0caf5;
       margin: 0; padding: 32px; font-size: 14px; }
.wrap { max-width: 900px; margin: 0 auto; }
h1 { color: #7aa2f7; font-size: 22px; margin: 0 0 4px 0; }
.meta { color: #9aa5ce; margin-bottom: 20px; line-height: 1.6; }
.chips { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 24px; }
.chip { background: #24283b; border-radius: 8px; padding: 10px 18px; text-align: center; min-width: 88px; }
.chip .num { font-size: 20px; font-weight: bold; display: block; }
.chip.ok .num { color: #9ece6a; }
.chip.fail .num { color: #f7768e; }
.chip.dry .num { color: #e0af68; }
.chip .lbl { font-size: 11px; color: #9aa5ce; text-transform: uppercase; }
.client-summary .cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.client-summary .cols > div { background: #24283b; border-radius: 8px; padding: 8px 14px; }
.client-summary h3 { margin: 4px 0; font-size: 14px; }
.client-summary ul { margin: 4px 0; padding-left: 18px; }
.sev.critical { color: #f7768e; font-weight: bold; }
.sev.attention { color: #e0af68; font-weight: bold; }
.card { background: #24283b; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
        border-left: 3px solid transparent; scroll-margin-top: 16px; }
.card.fail { border-left: 3px solid #f7768e; }
.card:target { outline: 2px solid #7aa2f7; outline-offset: 2px; }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status { font-weight: bold; font-size: 12px; border-radius: 8px; padding: 1px 9px; color: #1a1b26; }
.status.ok { background: #9ece6a; }
.status.fail { background: #f7768e; }
.label { font-weight: 600; flex: 1; min-width: 12em; }
.badge { font-size: 10px; font-weight: bold; border-radius: 7px; padding: 1px 8px; color: #1a1b26; }
.mod { color: #9aa5ce; font-size: 12px; }
.ts { color: #9aa5ce; font-size: 11px; }
.dry-tag { color: #e0af68; font-size: 11px; font-weight: bold; }
details { margin-top: 8px; }
summary { color: #7aa2f7; cursor: pointer; font-size: 12px; }
pre { background: #16161e; border-radius: 6px; padding: 10px; overflow-x: auto;
      font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; color: #a9b1d6;
      white-space: pre-wrap; word-break: break-word; }
h2 { color: #bb9af7; font-size: 16px; margin: 28px 0 10px 0; }
ul { color: #c0caf5; }
a { color: #7aa2f7; }
.table-wrap { overflow-x: auto; margin-bottom: 8px; }
table.summary { border-collapse: collapse; width: 100%; background: #24283b; border-radius: 8px;
                overflow: hidden; font-size: 13px; }
table.summary th, table.summary td { padding: 7px 12px; text-align: left; border-bottom: 1px solid #1a1b26; }
table.summary th { color: #9aa5ce; font-size: 11px; text-transform: uppercase; font-weight: 600;
                   background: #1f2335; }
table.summary td.n, table.summary th.n { text-align: right; font-variant-numeric: tabular-nums; }
table.summary tr:last-child td { border-bottom: none; }
table.summary td.cat { color: #9aa5ce; font-size: 12px; }
table.summary td.ok-n { color: #9ece6a; }
table.summary td.fail-n { color: #f7768e; font-weight: bold; }
table.summary td.dry-n { color: #e0af68; }
table.summary td.zero { color: #6b7394; font-weight: normal; }
.failed-list { background: #24283b; border-left: 3px solid #f7768e; border-radius: 8px;
               padding: 10px 16px 10px 34px; margin: 0; }
.failed-list li { margin: 3px 0; }
.failed-list .mod { margin-left: 6px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px;
           position: sticky; top: 0; z-index: 1; background: #1a1b26; padding: 8px 0; }
.seg { display: inline-flex; border: 1px solid #3b4261; border-radius: 8px; overflow: hidden; }
.seg button { border: none; border-right: 1px solid #3b4261; }
.seg button:last-child { border-right: none; }
button { font: inherit; font-size: 13px; background: #24283b; color: #c0caf5; padding: 6px 12px;
         cursor: pointer; border: 1px solid #3b4261; border-radius: 8px; }
.seg button { border-radius: 0; }
button:hover { background: #2f3549; }
button[aria-pressed="true"] { background: #7aa2f7; color: #1a1b26; font-weight: 600; }
button:focus-visible, input:focus-visible, a:focus-visible { outline: 2px solid #7aa2f7; outline-offset: 2px; }
input[type="search"] { font: inherit; font-size: 13px; background: #16161e; color: #c0caf5;
                       border: 1px solid #3b4261; border-radius: 8px; padding: 6px 10px;
                       flex: 1; min-width: 160px; }
input[type="search"]::placeholder { color: #8b93b8; }
.print-btn { margin-left: auto; }
.empty { color: #9aa5ce; font-style: italic; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden;
           clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
.job { background: #24283b; border-radius: 8px; padding: 10px 14px; margin: 0 0 14px 0; line-height: 1.6; }
.job strong { color: #c0caf5; }
.job-note { white-space: pre-wrap; margin-top: 4px; color: #c0caf5; }
.job-note .lbl { display: block; font-size: 11px; color: #9aa5ce; text-transform: uppercase; }
.banner { background: #3b2a1a; border-left: 3px solid #e0af68; color: #f5d9a8; border-radius: 8px;
          padding: 10px 14px; margin: 0 0 14px 0; font-weight: 600; }
.banner.redacted { background: #1f2a44; border-left-color: #7aa2f7; color: #c0caf5; font-weight: normal; }
.warned-tag { color: #e0af68; font-size: 11px; font-weight: bold; }
.warn-text { color: #9aa5ce; font-size: 12px; margin-top: 6px; font-style: italic; white-space: pre-line; }
.rp-fail { color: #f7768e; font-weight: bold; }
.events { background: #24283b; border-radius: 8px; padding: 10px 16px 10px 34px; margin: 0; }
.events li { margin: 4px 0; }
.events .ts { margin-right: 6px; }
table.snapshot tbody th { font-weight: 600; color: #c0caf5; text-transform: none; font-size: 13px;
                          background: none; }
table.snapshot td.delta { font-weight: bold; color: #9aa5ce; white-space: nowrap; }
table.snapshot td.delta.good { color: #9ece6a; }
table.snapshot td.delta.bad { color: #f7768e; }
.snap-note { color: #9aa5ce; font-size: 12px; margin: 4px 0 0 0; }
.brand { display: flex; align-items: center; gap: 14px; margin: 0 0 12px 0; }
.brand-logo { max-height: 64px; max-width: 220px; object-fit: contain; }
.brand-text { line-height: 1.5; color: #9aa5ce; }
.brand-text strong { color: #c0caf5; font-size: 15px; }
.brand-contact { white-space: pre-line; }
dl.form { background: #24283b; border-radius: 8px; padding: 10px 16px; margin: 0;
          display: grid; grid-template-columns: minmax(9em, max-content) 1fr; gap: 6px 16px; }
dl.form dt { color: #9aa5ce; font-size: 12px; }
dl.form dd { margin: 0; white-space: pre-wrap; word-break: break-word; }
.form-note { color: #9aa5ce; font-size: 12px; margin: 6px 0 0 0; }
table.summary td.check-pass { color: #9ece6a; font-weight: bold; }
table.summary td.check-fail { color: #f7768e; font-weight: bold; }
table.summary td.check-na { color: #9aa5ce; }
@media print {
  .brand-text, .brand-text strong { color: #111; }
  dl.form { background: #fff; border: 1px solid #bbb; }
  dl.form dt, .form-note { color: #444; }
  dl.form dd { color: #111; }
  table.summary td.check-pass { color: #1e7b34; }
  table.summary td.check-fail { color: #c0392b; }
  table.summary td.check-na { color: #444; }
  table.snapshot tbody th { color: #111; }
  table.snapshot td.delta { color: #444; }
  table.snapshot td.delta.good { color: #1e7b34; }
  table.snapshot td.delta.bad { color: #c0392b; }
  .snap-note { color: #444; }
  .banner { background: #fff; color: #111; border: 1px solid #9a6700; border-left: 4px solid #9a6700; }
  .banner.redacted { background: #fff; color: #111; border-color: #555; }
  .warned-tag { color: #9a6700; }
  .client-summary .cols > div { background: #fff; border: 1px solid #bbb; }
  .sev.critical { color: #c0392b; }
  .sev.attention { color: #9a6700; }
  .warn-text { color: #444; }
  .rp-fail { color: #c0392b; }
  .events { background: #fff; border: 1px solid #bbb; }
  .events li { color: #111; }
  @page { margin: 14mm; }
  .job { background: #fff; border: 1px solid #bbb; color: #111; }
  .job strong, .job-note { color: #111; }
  .job-note .lbl { color: #444; }
  body { background: #fff; color: #111; padding: 0; font-size: 11pt; }
  .wrap { max-width: none; }
  h1 { color: #111; }
  h2 { color: #222; border-bottom: 1px solid #999; padding-bottom: 2px; break-after: avoid; }
  .meta, .mod, .ts, .chip .lbl, table.summary td.cat, table.summary th { color: #444; }
  .toolbar, .no-print { display: none !important; }
  #pf-no-match { display: none !important; }
  /* Print the whole log even if a screen filter is active. */
  .card[hidden] { display: block !important; }
  .chip, .card, table.summary, .failed-list { background: #fff; border: 1px solid #bbb; }
  .card { break-inside: avoid; page-break-inside: avoid; }
  .card.fail, .failed-list { border-left: 4px solid #c0392b; }
  .card:target { outline: none; }
  .chip.ok .num, table.summary td.ok-n { color: #1e7b34; }
  .chip.fail .num, table.summary td.fail-n { color: #c0392b; }
  .chip.dry .num, table.summary td.dry-n, .dry-tag { color: #9a6700; }
  table.summary td.zero { color: #888; }
  table.summary th { background: #eee; }
  table.summary th, table.summary td { border-bottom: 1px solid #ccc; }
  tr { break-inside: avoid; }
  ul, .failed-list li { color: #111; }
  a { color: #111; text-decoration: none; }
  summary { color: #333; }
  details::details-content { content-visibility: visible; display: block; }
  pre { background: #f4f4f4; color: #111; border: 1px solid #ddd; }
  .status, .badge { border: 1px solid #555; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
"""

# Inline, dependency-free progressive enhancement. The toolbar ships with the
# `hidden` attribute and only this script reveals it, so with JavaScript off
# the page is simply the full, unfiltered log.
_JS = """
(function () {
  var bar = document.getElementById('pf-toolbar');
  if (!bar) return;
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card[data-status]'));
  var buttons = Array.prototype.slice.call(bar.querySelectorAll('button[data-filter]'));
  var search = document.getElementById('pf-search');
  var noMatch = document.getElementById('pf-no-match');
  var mode = 'all';
  function apply() {
    var term = search.value.trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (c) {
      var ok = mode === 'all' ||
        (mode === 'fail' && c.getAttribute('data-status') === 'fail') ||
        (mode === 'changes' && c.getAttribute('data-dry') === '0');
      if (ok && term) ok = (c.getAttribute('data-search') || '').indexOf(term) !== -1;
      c.hidden = !ok;
      if (ok) shown++;
    });
    buttons.forEach(function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-filter') === mode ? 'true' : 'false');
    });
    if (noMatch) noMatch.hidden = shown !== 0 || cards.length === 0;
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () { mode = b.getAttribute('data-filter'); apply(); });
  });
  search.addEventListener('input', apply);
  var printBtn = document.getElementById('pf-print');
  if (printBtn) printBtn.addEventListener('click', function () { window.print(); });
  // A link from the failed-actions list must never land on a filtered-out card.
  document.addEventListener('click', function (e) {
    var a = e.target.closest ? e.target.closest('a[href^="#action-"]') : null;
    if (!a) return;
    var target = document.getElementById(a.getAttribute('href').slice(1));
    if (target && target.hidden) { mode = 'all'; search.value = ''; apply(); }
  });
  // Browsers without ::details-content would print collapsed output - open
  // every <details> for the printout and restore the user's state afterwards.
  var reopened = [];
  window.addEventListener('beforeprint', function () {
    reopened = Array.prototype.slice.call(document.querySelectorAll('details:not([open])'));
    reopened.forEach(function (d) { d.open = true; });
  });
  window.addEventListener('afterprint', function () {
    reopened.forEach(function (d) { d.open = false; });
    reopened = [];
  });
  bar.hidden = false;
  apply();
})();
"""


def _format_timestamp(value) -> str:
    # Audit timestamps are ISO-8601 UTC with microseconds - readable for
    # machines (the JSON keeps them as-is) but noisy on screen.
    try:
        return datetime.fromisoformat(str(value)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return str(value)


# executor.ActionRunner's sentinel exit codes (TIMEOUT / CANCELLED /
# POWERSHELL_NOT_FOUND). Mirrored rather than imported: executor.py is the
# QThread process runner and resolves powershell.exe on disk at import time -
# rendering a report from logged data shouldn't depend on any of that.
# tests/test_report.py pins these to ActionRunner's values.
_SENTINEL_EXIT_KEYS = {
    -2: "report_exit_timeout",
    -3: "report_exit_cancelled",
    -4: "report_exit_no_powershell",
}


def _exit_text(code, language: str) -> str:
    # "kód -2" told the client nothing - these codes are PortableFix's own
    # markers, not something the command returned, so say what happened.
    # isinstance: a corrupted log can hold any JSON value, and a list or
    # dict isn't even hashable for the lookup.
    key = _SENTINEL_EXIT_KEYS.get(code) if isinstance(code, int) else None
    if key is not None:
        return translate(key, language)
    return translate("report_exit", language).format(code=code)


def _render_action_card(a: dict, language: str, index: int) -> str:
    def t(key: str) -> str:
        return html.escape(translate(key, language))

    ok = a["exit_code"] == 0
    status_cls = "ok" if ok else "fail"
    status_txt = t("report_status_ok") if ok else t("report_status_failed")
    badge_color = _RISK_COLORS.get(a["risk"], _RISK_COLORS["UNKNOWN"])
    dry_tag = '<span class="dry-tag">DRY-RUN</span>' if a["dry_run"] else ""
    output_block = ""
    if a.get("output"):
        output_block = (
            f"<details><summary>{t('report_output')}</summary><pre>{html.escape(a['output'])}</pre></details>"
        )
    exit_note = "" if ok else f'<span class="mod">{html.escape(_exit_text(a["exit_code"], language))}</span>'
    # a.get(): report JSON written before these fields existed has no key.
    warned_tag = f'<span class="warned-tag">{t("report_warned_tag")}</span>' if a.get("warned") else ""
    if a.get("already_applied"):
        # Research G09: skipped because the check found it already in place.
        warned_tag += f'<span class="dry-tag">{t("report_already_applied")}</span>'
    warn_text = ""
    if a.get("warned") and a.get("warning_text"):
        # The exact copy the technician accepted - the report is the
        # document handed to the client, so the proof belongs here too.
        warn_text = f'<div class="warn-text">&bdquo;{html.escape(a["warning_text"])}&ldquo;</div>'
    search_text = f"{a['label']} {a['module_id']} {a['action_id']}".lower()
    return (
        f'<div class="card {status_cls}" id="action-{index}" data-status="{status_cls}" '
        f'data-dry="{1 if a["dry_run"] else 0}" data-search="{html.escape(search_text, quote=True)}">'
        f'<div class="row">'
        f'<span class="status {status_cls}">{status_txt}</span>'
        f'<span class="label">{html.escape(a["label"])}</span>'
        f"{dry_tag}{warned_tag}{exit_note}"
        f'<span class="badge" style="background:{badge_color}">{html.escape(a["risk"])}</span>'
        f'<span class="mod">{html.escape(a["module_id"])}</span>'
        f'<span class="ts">{html.escape(_format_timestamp(a["timestamp"]))}</span>'
        f"</div>{warn_text}{output_block}</div>"
    )


def _render_module_summary(rows: list[dict], language: str) -> str:
    if not rows:
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    def category_name(raw) -> str:
        # Reuse the app's own category names ("Čistenie", "Oprava systému");
        # translate() returns the key itself when unknown, so fall back to
        # the raw value for UNKNOWN or anything the catalog doesn't name.
        key = f"category_{str(raw).lower()}"
        name = translate(key, language)
        return html.escape(str(raw) if name == key else name)

    def num(value: int, cls: str) -> str:
        return f'<td class="n {cls}{" zero" if value == 0 else ""}">{value}</td>'

    body = "".join(
        "<tr>"
        f'<td>{html.escape(str(r["module_id"]))}</td>'
        f'<td class="cat">{category_name(r["category"])}</td>'
        f'<td class="n">{r["total"]}</td>'
        f'{num(r["ok"], "ok-n")}{num(r["failed"], "fail-n")}{num(r["dry_run"], "dry-n")}'
        "</tr>"
        for r in rows
    )
    return (
        f'<section aria-labelledby="pf-h-modules"><h2 id="pf-h-modules">{t("report_by_module")}</h2>'
        '<div class="table-wrap"><table class="summary"><thead><tr>'
        f'<th scope="col">{t("report_col_module")}</th>'
        f'<th scope="col">{t("report_col_category")}</th>'
        f'<th scope="col" class="n">{t("report_col_total")}</th>'
        f'<th scope="col" class="n">{t("report_chip_ok")}</th>'
        f'<th scope="col" class="n">{t("report_chip_failed")}</th>'
        f'<th scope="col" class="n">{t("report_chip_dry_run")}</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div></section>"
    )


def _render_snapshot_table(before, after, language: str) -> str:
    """The "Before / after" table - measurable impact to show the client.
    Rows come from snapshot.compare_snapshots (metrics known both times).
    Skipped when free space would be the only row: the meta line above
    already says that, and old reports (free_gb only) stay as they were."""
    rows = compare_snapshots(before, after)
    if not any(r["key"] != "free_gb" for r in rows):
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    def delta_cell(r: dict) -> str:
        if r["delta"] is None:
            return '<td class="n delta">?</td>'
        trend = r["trend"]
        marker = ""
        if trend == "good":
            marker = f'<span aria-hidden="true">&#10003; </span><span class="sr-only">{t("snapshot_improved")}: </span>'
        elif trend == "bad":
            marker = f'<span aria-hidden="true">! </span><span class="sr-only">{t("snapshot_worsened")}: </span>'
        return f'<td class="n delta {trend}">{marker}{html.escape(r["delta"])}</td>'

    body = "".join(
        "<tr>"
        f'<th scope="row">{t(r["label_key"])}</th>'
        f'<td class="n">{html.escape(r["before"])}</td>'
        f'<td class="n">{html.escape(r["after"])}</td>'
        f"{delta_cell(r)}"
        "</tr>"
        for r in rows
    )
    note = ""
    if any(r["lower_bound"] for r in rows):
        note = f'<p class="snap-note">{t("snapshot_lower_bound_note")}</p>'
    return (
        f'<section aria-labelledby="pf-h-snapshot"><h2 id="pf-h-snapshot">{t("snapshot_heading")}</h2>'
        '<div class="table-wrap"><table class="summary snapshot"><thead><tr>'
        f'<th scope="col">{t("snapshot_col_metric")}</th>'
        f'<th scope="col" class="n">{t("snapshot_col_before")}</th>'
        f'<th scope="col" class="n">{t("snapshot_col_after")}</th>'
        f'<th scope="col" class="n">{t("snapshot_col_change")}</th>'
        f"</tr></thead><tbody>{body}</tbody></table></div>{note}</section>"
    )


def _render_failed_list(actions: list[dict], language: str) -> str:
    failed = [(i, a) for i, a in enumerate(actions, start=1) if a["exit_code"] != 0]
    if not failed:
        return ""
    items = "".join(
        f'<li><a href="#action-{i}">{html.escape(a["label"])}</a>'
        f'<span class="mod">{html.escape(a["module_id"])} &middot; '
        f'{html.escape(_exit_text(a["exit_code"], language))}</span></li>'
        for i, a in failed
    )
    return (
        f'<section aria-labelledby="pf-h-failed"><h2 id="pf-h-failed">{html.escape(translate("report_failed_actions", language))}'
        f" ({len(failed)})</h2><ol class=\"failed-list\">{items}</ol></section>"
    )


def _restore_point_text(point: dict, language: str) -> str:
    def t(key: str) -> str:
        return html.escape(translate(key, language))

    when = html.escape(_format_timestamp(point["timestamp"]))
    title = t("report_restore_point")
    # point.get(): report JSON written before panels had restore points.
    if point.get("panel"):
        # A panel's point guarded that one change, not the batch.
        title += f" ({html.escape(str(point.get('subject_label') or point.get('subject') or '?'))})"
    if point["created"]:
        # "#123" is the SequenceNumber rstrui / Get-ComputerRestorePoint
        # show - lets anyone find the exact point later. Omitted when not
        # recorded (older logs, or the lookup failed).
        sequence = point.get("sequence")
        number = f" (#{sequence})" if isinstance(sequence, int) and not isinstance(sequence, bool) else ""
        return f"{title}: {t('report_rp_created')}{number} ({when})"
    text = f"{title}: <span class=\"rp-fail\">{t('report_rp_failed')}</span> ({when})"
    if point.get("decision") == "proceed":
        text += f" &mdash; {t('report_rp_proceeded')}"
    elif point.get("decision") == "skip":
        # A panel's "No" stopped that one change; the batch's "No" skipped
        # its high-risk actions and ran the rest.
        text += f" &mdash; {t('report_rp_skipped_panel' if point.get('panel') else 'report_rp_skipped')}"
    return text


# The English sentence main_window logs in front of the restore-point failure
# reason ("... failed: <reason>") - the line it is shown on already says
# "FAILED" / "NEPODARIL SA" in the report's own language.
_RP_FAILED_PREFIX = re.compile(r"\s*System Restore Point creation failed(?::\s*|\.\s*$|\s+|$)")


def _restore_point_failure_reason(output: str) -> str:
    # Display only: the JSON event and the audit log keep the text verbatim.
    match = _RP_FAILED_PREFIX.match(output)
    return output[match.end():] if match else output


# The fixed English sentences main_window writes for these events (its own
# text, never Windows' - so matching them is not parsing localized output).
# Display only, like the restore-point prefix above; anything else is shown
# verbatim.
_HIVE_PREFIX_OK = re.compile(r"\s*Registry hive backup saved(?::\s*|\.\s*$|$)")
_HIVE_PREFIX_FAILED = re.compile(r"\s*Registry hive backup failed(?::\s*|\.\s*$|$)")
_HIVE_REQUESTED = "Full registry hive backup requested"
# "Uninstall refused - protected program (gpu_driver)." - the reason code
# names an uninstaller_protected_* text the uninstaller panel showed.
_PROTECTED_REASON = re.compile(r"\(([a-z0-9_]+)\)\.?\s*$")


def _strip_prefix(pattern: re.Pattern, output: str) -> str:
    match = pattern.match(output)
    return output[match.end():] if match else output


def _protected_reason(output: str, language: str) -> str:
    match = _PROTECTED_REASON.search(output)
    if not match:
        return ""
    key = f"uninstaller_protected_{match.group(1)}"
    text = translate(key, language)
    # translate() returns the key itself when unknown - show the bare code.
    return match.group(1) if text == key else text


def _render_event(event: dict, language: str) -> str:
    def t(key: str) -> str:
        return html.escape(translate(key, language))

    kind = event.get("kind")
    subject = str(event.get("subject") or "")
    panel = _is_panel_subject(subject)
    label = html.escape(str(event.get("subject_label") or subject or "?"))
    risk = f' <span class="mod">[{html.escape(str(event["risk"]))}]</span>' if event.get("risk") else ""
    quote = ""
    if event.get("warning_text"):
        quote = f'<div class="warn-text">&bdquo;{html.escape(str(event["warning_text"]))}&ldquo;</div>'
    if kind == "restore_point":
        point = {
            "timestamp": event["timestamp"], "created": event["exit_code"] == 0,
            "sequence": event.get("restore_point_sequence"),
            "subject": subject, "subject_label": event.get("subject_label"), "panel": panel,
        }
        body = _restore_point_text(point, language)
        reason = _restore_point_failure_reason(str(event.get("output") or "")) if event["exit_code"] != 0 else ""
        if reason:
            body += f'<div class="warn-text">{html.escape(reason)}</div>'
        # The timestamp is already part of _restore_point_text.
        return f"<li>{body}</li>"
    when = f'<span class="ts">{html.escape(_format_timestamp(event["timestamp"]))}</span>'
    if kind == "restore_point_decision":
        if event.get("decision") == "proceed":
            key = "report_rp_proceeded"
        else:
            key = "report_rp_skipped_panel" if panel else "report_rp_skipped"
        title = t("report_restore_point") + (f" ({label})" if panel else "")
        return f"<li>{when}{title}: {t(key)}</li>"
    if kind == "risk_declined":
        return f"<li>{when}{t('report_declined')}: <strong>{label}</strong>{risk}{quote}</li>"
    if kind == "running_programs_decision":
        # "Ignore" on the "program is still running" warning (G01) - the
        # technician went on anyway; the warning quoted lists the processes.
        return f"<li>{when}{t('report_running_proceeded')}: <strong>{label}</strong>{risk}{quote}</li>"
    if kind == "protected_program":
        # Named by the program itself - "Uninstall refused: Uninstall: X"
        # would say it twice.
        name = subject[len(_UNINSTALL_SUBJECT):] if subject.startswith(_UNINSTALL_SUBJECT) else subject
        reason = _protected_reason(str(event.get("output") or ""), language)
        reason_html = f' <span class="mod">({html.escape(reason)})</span>' if reason else ""
        return f"<li>{when}{t('report_protected_program')}: <strong>{html.escape(name or '?')}</strong>{reason_html}</li>"
    if kind == "hive_backup":
        ok = event["exit_code"] == 0
        state = t("report_hive_saved") if ok else f'<span class="rp-fail">{t("report_hive_failed")}</span>'
        before = ""
        if subject:
            before = f' <span class="mod">{html.escape(translate("report_hive_before", language).format(action=event.get("subject_label") or subject))}</span>'
        # Where it was saved (the folder undo.ps1 and a technician restore
        # from), or why it failed - without main_window's English sentence.
        detail = _strip_prefix(_HIVE_PREFIX_OK if ok else _HIVE_PREFIX_FAILED, str(event.get("output") or ""))
        detail_html = f'<div class="warn-text">{html.escape(detail)}</div>' if detail else ""
        return f"<li>{when}{t('report_hive_backup')}: {state}{before}{detail_html}</li>"
    if kind == "hive_backup_decision":
        key = "report_hive_proceeded" if event.get("decision") == "proceed" else "report_hive_skipped"
        return f"<li>{when}{t('report_hive_backup')}: {t(key)}</li>"
    if kind == "batch_review":
        # The one review screen (G12): the answer, and the pre-flight
        # findings it was given on, in the report's language - the codes in
        # `output` are for the audit log.
        key = {
            "confirmed": "report_review_confirmed",
            "override": "report_review_override",
        }.get(event.get("decision"), "report_review_cancelled")
        css = ' class="rp-fail"' if event.get("decision") == "override" else ""
        hive = ""
        if event.get("decision") in ("confirmed", "override") and _HIVE_REQUESTED in str(event.get("output") or ""):
            hive = f" &mdash; {t('report_review_hive_requested')}"
        return f"<li>{when}{t('report_review')}: <span{css}>{t(key)}</span>{hive}{quote}</li>"
    if kind == "integrity_guard":
        return f"<li>{when}<span class=\"rp-fail\">{t('report_integrity_guard')}</span></li>"
    resume_html = _render_resume_event(event, label, risk, t)
    if resume_html is not None:
        return f"<li>{when}{resume_html}</li>"
    # Unknown/future system event - still show it rather than drop evidence.
    return f"<li>{when}{html.escape(str(kind))}: {html.escape(event.get('output', ''))}</li>"


def _action_list(event: dict) -> str:
    # event.get(): report JSON written before the field existed.
    labels = event.get("action_labels")
    if not isinstance(labels, list):
        return ""
    return html.escape(", ".join(str(item) for item in labels))


def _render_resume_event(event: dict, label: str, risk: str, t) -> str | None:
    """A restart/resume event (research G03) in the report's language, or
    None when the event is not one of them. Falls back to the logged
    sentence when it names no actions the report could read back."""
    kind = event.get("kind")
    output = str(event.get("output") or "")
    actions = _action_list(event) or html.escape(output)
    if kind == "restart_pending":
        state = t("report_restart_immediate" if _RESTART_IMMEDIATE in output else "report_restart_batch_stopped")
        rest = ""
        if _RESTART_SAVED.search(output):
            rest = f'<div class="warn-text">{t("report_restart_saved")}: {actions}</div>'
        elif _RESTART_NOT_SAVED.search(output):
            # Nothing will be offered after the restart - whoever reads the
            # report must know the rest has to be started by hand.
            rest = f'<div class="warn-text"><span class="rp-fail">{t("report_restart_not_saved")}</span>: {actions}</div>'
        return f"{t('report_restart_pending')}: <strong>{label}</strong>{risk} &mdash; {state}{rest}"
    if kind == "resumed_after_reboot":
        after = f" ({t('report_resumed_after')} <strong>{label}</strong>)" if event.get("subject") else ""
        return f"{t('report_resumed')}{after}: {actions}"
    if kind == "resume_skipped":
        return f"<span class=\"rp-fail\">{t('report_resume_skipped')}</span>: {actions}"
    if kind == "resume_declined":
        return f"{t('report_resume_declined')}: {actions}"
    if kind == "resume_hive_backup_missing":
        match = _RESUME_HIVE_MISSING.search(output)
        paths = html.escape(match.group("paths") if match else output)
        return f"<span class=\"rp-fail\">{t('report_resume_hive_missing')}</span>: {paths}"
    return None


def _render_safety_section(events: list[dict], language: str) -> str:
    if not events:
        return ""
    items = "".join(_render_event(e, language) for e in events)
    return (
        f'<section aria-labelledby="pf-h-safety"><h2 id="pf-h-safety">'
        f'{html.escape(translate("report_safety_heading", language))}</h2>'
        f'<ul class="events">{items}</ul></section>'
    )


def _render_branding(brand, language: str) -> str:
    """The technician's logo and company in the report header (G20). The
    logo is checked again here: the page may be re-rendered from a saved
    report.json, which could have been edited by hand."""
    if not isinstance(brand, dict) or not brand:
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    company = str(brand.get("company") or "")
    logo = branding_mod.decode_logo(brand.get("logo"))
    logo_html = ""
    if logo is not None:
        mime, encoded = logo
        logo_html = f'<img class="brand-logo" src="data:{mime};base64,{encoded}" alt="{html.escape(company, quote=True)}">'
    lines = []
    if company:
        lines.append(f"<strong>{html.escape(company)}</strong>")
    if brand.get("company_id"):
        lines.append(f"{t('report_branding_company_id')}: {html.escape(str(brand['company_id']))}")
    if brand.get("contact"):
        lines.append(f'<span class="brand-contact">{html.escape(str(brand["contact"]))}</span>')
    text_html = f'<div class="brand-text">{"<br>".join(lines)}</div>' if lines else ""
    if not logo_html and not text_html:
        return ""
    return f'<header class="brand">{logo_html}{text_html}</header>'


def _recorded_note(timestamp, t) -> str:
    if not timestamp:
        return ""
    when = html.escape(_format_timestamp(timestamp))
    return f'<p class="form-note">{t("report_form_recorded")}: {when}</p>'


def _render_intake(form, language: str) -> str:
    """The intake form (G20): the state the PC was received in."""
    if not isinstance(form, dict) or not form:
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    rows = []

    def row(label_key: str, value: str) -> None:
        if value:
            rows.append(f"<dt>{t(label_key)}</dt><dd>{value}</dd>")

    row("report_intake_problem", html.escape(str(form.get("problem") or "")))
    labels = form.get("condition_labels")
    flags = ", ".join(str(item) for item in labels) if isinstance(labels, list) else ""
    condition = "; ".join(part for part in (flags, str(form.get("condition") or "")) if part)
    row("report_intake_condition", html.escape(condition))
    row("report_intake_accessories", html.escape(str(form.get("accessories") or "")))
    backup = html.escape(str(form.get("backup_label") or ""))
    if backup and form.get("backup") == intake.BACKUP_WAIVER:
        # The client took the risk of data loss - the line the technician
        # may one day have to point at.
        backup = f"<strong>{backup}</strong>"
    row("report_intake_backup", backup)
    row("report_intake_password", html.escape(str(form.get("password_handling_label") or "")))
    if not rows:
        return ""
    return (
        f'<section aria-labelledby="pf-h-intake"><h2 id="pf-h-intake">{t("report_intake_heading")}</h2>'
        f'<dl class="form">{"".join(rows)}</dl>{_recorded_note(form.get("timestamp"), t)}</section>'
    )


def _render_outtake(form, language: str) -> str:
    """The hand-over check (G20): Pass / Fail / N/A per function, and
    who took the PC when."""
    if not isinstance(form, dict) or not form:
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    checks = form.get("checks") if isinstance(form.get("checks"), list) else []
    body = ""
    for check in checks:
        if not isinstance(check, dict):
            continue
        result = str(check.get("result") or "")
        css = f' class="check-{result}"' if result in intake.CHECK_RESULTS else ""
        body += (
            f'<tr><th scope="row">{html.escape(str(check.get("label") or check.get("key") or "?"))}</th>'
            f'<td{css}>{html.escape(str(check.get("result_label") or result))}</td></tr>'
        )
    table = ""
    if body:
        table = (
            '<div class="table-wrap"><table class="summary snapshot"><thead><tr>'
            f'<th scope="col">{t("report_outtake_col_check")}</th><th scope="col">{t("report_outtake_col_result")}</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>"
        )
    handed = ""
    if form.get("handed_to"):
        when = f" &middot; {html.escape(str(form['handed_at']))}" if form.get("handed_at") else ""
        handed = (
            f'<dl class="form"><dt>{t("report_outtake_handed_to")}</dt>'
            f"<dd><strong>{html.escape(str(form['handed_to']))}</strong>{when}</dd></dl>"
        )
    if not table and not handed:
        return ""
    return (
        f'<section aria-labelledby="pf-h-outtake"><h2 id="pf-h-outtake">{t("report_outtake_heading")}</h2>'
        f'{table}{handed}{_recorded_note(form.get("timestamp"), t)}</section>'
    )


def _seconds(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _work_time_text(work, language: str) -> str:
    """The meta line with the run's work time (G20): the sum of its
    batches and, when used, the manual timer - shown apart, since the timer
    usually runs through the batches too."""
    if not isinstance(work, dict) or not work:
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    parts = []
    if _seconds(work.get("batch_count")):
        parts.append(t("report_work_batches").format(time=intake.format_duration(_seconds(work.get("batch_seconds")))))
    if _seconds(work.get("timer_seconds")) or work.get("timer_running"):
        timer = t("report_work_timer").format(time=intake.format_duration(_seconds(work.get("timer_seconds"))))
        if work.get("timer_running"):
            timer += f" ({t('report_work_timer_running')})"
        parts.append(timer)
    if not parts:
        return ""
    return f"{t('report_work_time')}: {' &middot; '.join(parts)}"


def _render_toolbar(language: str) -> str:
    def t(key: str) -> str:
        return html.escape(translate(key, language))

    return (
        '<div class="toolbar no-print" id="pf-toolbar" hidden>'
        f'<div class="seg" role="group" aria-label="{t("report_filter_group")}">'
        f'<button type="button" data-filter="all" aria-pressed="true">{t("report_filter_all")}</button>'
        f'<button type="button" data-filter="fail" aria-pressed="false">{t("report_filter_failed")}</button>'
        f'<button type="button" data-filter="changes" aria-pressed="false">{t("report_filter_changes")}</button>'
        "</div>"
        f'<label for="pf-search" class="sr-only">{t("report_search_label")}</label>'
        f'<input type="search" id="pf-search" placeholder="{t("report_search_placeholder")}" autocomplete="off">'
        f'<button type="button" id="pf-print" class="print-btn">{t("report_print")}</button>'
        "</div>"
    )


def _render_client_summary(summary, language: str) -> str:
    """Research G02: what the client reads first - found, fixed, recommended."""
    if not isinstance(summary, dict):
        return ""

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    def column(key: str, rows: list, none_key: str, text) -> str:
        items = "".join(f"<li>{text(r)}</li>" for r in rows if isinstance(r, dict))
        body = f"<ul>{items}</ul>" if items else f'<p class="empty">{t(none_key)}</p>'
        return f"<div><h3>{t(key)}</h3>{body}</div>"

    def found(row) -> str:
        severity = str(row.get("severity", ""))
        return (f'<span class="sev {html.escape(severity)}">{t("health_state_" + severity) if severity in ("attention", "critical") else ""}</span> '
                f'{html.escape(str(row.get("message", "")))}')

    def action(row) -> str:
        return html.escape(str(row.get("label") or row.get("action_id") or ""))

    return (
        f'<section class="client-summary"><h2>{t("report_client_summary")}</h2><div class="cols">'
        + column("report_summary_found", summary.get("found") or [], "report_summary_none_found", found)
        + column("report_summary_fixed", summary.get("fixed") or [], "report_summary_none_fixed", action)
        + column("report_summary_recommended", summary.get("recommended") or [], "report_summary_none_recommended", action)
        + "</div></section>"
    )


def _render_html(data: dict) -> str:
    language = data.get("language", "sk")

    def t(key: str) -> str:
        return html.escape(translate(key, language))

    actions = data["actions"]
    ok_count = sum(1 for a in actions if a["exit_code"] == 0)
    fail_count = len(actions) - ok_count
    dry_count = sum(1 for a in actions if a["dry_run"])
    cards = "\n".join(_render_action_card(a, language, i) for i, a in enumerate(actions, start=1))
    if not cards:
        cards = f'<p class="empty">{t("report_no_actions")}</p>'
    module_rows = data.get("module_summary")
    if module_rows is None:
        module_rows = _build_module_summary(actions)
    module_section = _render_module_summary(module_rows, language)
    failed_section = _render_failed_list(actions, language)
    snapshot_section = _render_snapshot_table(data.get("snapshot_before"), data.get("snapshot_after"), language)
    toolbar = _render_toolbar(language) if actions else ""

    raw_before = data["snapshot_before"].get("free_gb")
    raw_after = data["snapshot_after"].get("free_gb")
    # None = the snapshot couldn't measure it (snapshot.py never raises).
    free_before = html.escape(str("?" if raw_before is None else raw_before))
    free_after = html.escape(str("?" if raw_after is None else raw_after))
    delta = ""
    if isinstance(raw_before, (int, float)) and isinstance(raw_after, (int, float)):
        diff = round(raw_after - raw_before, 2)
        sign = "+" if diff >= 0 else ""
        delta = f" ({sign}{diff} GB)"

    restart_section = ""
    if data["requires_restart"]:
        items = "".join(f"<li>{html.escape(a['label'])}</li>" for a in data["requires_restart"])
        restart_section = f"<section><h2>{t('report_requires_restart')}</h2><ul>{items}</ul></section>"

    extra_meta = ""
    for point in data.get("restore_points") or []:
        extra_meta += f"<br>\n{_restore_point_text(point, language)}"
    elevated = data.get("elevated")
    if isinstance(elevated, bool):
        extra_meta += f"<br>\n{t('report_elevated')}: {t('report_yes') if elevated else t('report_no')}"
    safety_section = _render_safety_section(data.get("events") or [], language)
    work_time = _work_time_text(data.get("work_time"), language)
    if work_time:
        extra_meta += f"<br>\n{work_time}"
    storage_banner = ""
    if data.get("storage_fallback"):
        # The report lives on the client's disk, in %TEMP% - say so where
        # the technician will actually see it, not only in a startup popup.
        storage_banner = f'<div class="banner" role="note">{t("report_storage_fallback")}</div>'
    if data.get("redacted"):
        # Said on the page itself: a client or a colleague reading "<ip>"
        # must know it was masked on purpose, not lost.
        storage_banner += (
            f'<div class="banner redacted" role="note"><strong>{t("report_redacted")}</strong> &mdash; '
            f'{t("report_redacted_detail")}</div>'
        )

    comparison_section = ""
    comparison = data.get("previous_comparison")
    if comparison:
        cmp_delta = comparison["free_gb_delta"]
        delta_txt = f"{'+' if cmp_delta > 0 else ''}{cmp_delta} GB" if cmp_delta is not None else "?"
        comparison_section = (
            f"<section><h2>{t('report_since_last_visit')}</h2>"
            f"<div class=\"meta\">{t('report_previous_run')} {html.escape(str(comparison['previous_run_id']))} "
            f"({html.escape(_format_timestamp(comparison['previous_generated_at']))})<br>"
            f"{t('report_free_space_change')}: {delta_txt}<br>"
            f"{t('report_actions_then_now')}: {comparison['previous_action_count']} &rarr; {comparison['action_count']}</div></section>"
        )
    new_autostart = data.get("new_autostart")
    if isinstance(new_autostart, dict) and isinstance(new_autostart.get("entries"), list):
        entries = [e for e in new_autostart["entries"] if isinstance(e, str)]
        body = (
            "<ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in entries) + "</ul>" if entries
            else f"<p class=\"empty\">{t('report_new_autostart_none')}</p>"
        )
        comparison_section += f"<section><h2>{t('report_new_autostart')}</h2>{body}</section>"
    drift = [d for d in data.get("drift") or [] if isinstance(d, dict)]
    if drift:
        comparison_section += (
            f"<section><h2>{t('report_drift')}</h2><p>{t('report_drift_intro')}</p><ul>"
            + "".join(f"<li>{html.escape(str(d.get('label', '')))}</li>" for d in drift) + "</ul></section>"
        )

    return f"""<!DOCTYPE html>
<html lang="{html.escape(language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PortableFix report {html.escape(data['run_id'])}</title>
<style>{_CSS}</style></head>
<body><main class="wrap">
{_render_branding(data.get('branding'), language)}
<h1>PortableFix &mdash; {html.escape(data['hostname'])}</h1>
{storage_banner}
{_render_job(data.get('job') or dict(), t, data.get('target_user'))}
<div class="meta">{t('report_run')} {html.escape(data['run_id'])} &middot; {html.escape(data['os'])}<br>
{t('report_generated')}: {html.escape(_format_timestamp(data['generated_at']))}<br>
{t('report_free_space')}: {free_before} GB &rarr; {free_after} GB{delta}{extra_meta}</div>
{_render_client_summary(data.get('client_summary'), language)}
<div class="chips">
<div class="chip"><span class="num">{len(actions)}</span><span class="lbl">{t('report_chip_actions')}</span></div>
<div class="chip ok"><span class="num">{ok_count}</span><span class="lbl">{t('report_chip_ok')}</span></div>
<div class="chip fail"><span class="num">{fail_count}</span><span class="lbl">{t('report_chip_failed')}</span></div>
<div class="chip dry"><span class="num">{dry_count}</span><span class="lbl">{t('report_chip_dry_run')}</span></div>
</div>
{_render_intake(data.get('intake'), language)}
{snapshot_section}
{failed_section}
{safety_section}
{module_section}
{comparison_section}
<section aria-labelledby="pf-h-actions"><h2 id="pf-h-actions">{t('report_actions_heading')}</h2>
{toolbar}
<p class="empty" id="pf-no-match" hidden>{t('report_no_match')}</p>
{cards}
</section>
{restart_section}
{_render_outtake(data.get('outtake'), language)}
</main>
<script>{_JS}</script>
</body></html>
"""


def generate_report(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
    job: dict | None = None,
    storage_fallback: bool = False,
    redact: bool = False,
    branding: dict | None = None,
) -> tuple[Path, Path]:
    data = build_report_data(
        base_dir, run_id, modules, language, snapshot_before, snapshot_after, job,
        storage_fallback=storage_fallback, branding=branding,
    )
    if redact:
        data = redact_report_data(data)
    reports_dir = base_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / f"{data['hostname']}_{run_id}.html"
    json_path = reports_dir / f"{data['hostname']}_{run_id}.json"
    html_path.write_text(_render_html(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return html_path, json_path


def redact_report_data(data: dict, mask: list[str] | None = None) -> dict:
    """The report with personal data masked for the client (research G20):
    user names in paths, IP/MAC addresses, serial numbers, product-key
    fragments and Wi-Fi names. The computer name and the technician /
    client / note entered on purpose stay - they are also never masked
    where the command output happens to repeat them. Only the rendered copy
    changes; the audit log keeps everything.

    `mask`: profile names masked wherever they appear - by default the
    profile folders of this PC, which the report was made on, so a name
    printed without a path after it ("PC\\Jan Novak") is caught too."""
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    keep = [str(data.get("hostname") or ""), *(str(value) for value in job.values())]
    if mask is None:
        mask = redaction.local_profile_names()
    # The target user's account name need not match any profile folder
    # (AzureAD, a renamed account) - masked by name as well.
    target = data.get("target_user") if isinstance(data.get("target_user"), dict) else {}
    mask = [*mask, *redaction.account_names([str(target.get("user") or "")])]
    # The technician's own branding and the name the PC was handed to were
    # entered on purpose, like the job details: they stay as they are (and
    # the logo's base64 must not be touched at all - a collected value
    # between two "/" in it would break the image). The rest of the intake
    # and outtake is free text and is redacted like everything else.
    data = dict(data)
    brand = data.pop("branding", None)
    outtake = data.get("outtake")
    handed_to = outtake.get("handed_to") if isinstance(outtake, dict) else None
    if isinstance(brand, dict):
        keep += [str(value) for key, value in brand.items() if key not in ("logo", "logo_mime")]
    if handed_to:
        keep.append(str(handed_to))
    redacted = redaction.redact_data(data, keep=keep, mask=mask)
    if brand is not None:
        redacted["branding"] = brand
    if handed_to and isinstance(redacted.get("outtake"), dict):
        redacted["outtake"]["handed_to"] = handed_to
    redacted["redacted"] = True
    return redacted


def render_report_html(data: dict) -> str:
    """The HTML page for report data as generate_report writes it (the
    handoff package re-renders a redacted copy from the saved JSON)."""
    return _render_html(data)


class ReportRunner(QThread):
    """generate_report re-reads and re-renders the whole session's audit log
    (it grows with every batch), so on a slow USB stick it runs off the GUI
    thread - otherwise the window froze at every batch end before the
    summary dialog could appear."""

    # (html_path or None, write_failed) - write_failed is the OSError case
    # the GUI reports as disk_write_failed.
    result_ready = Signal(object, bool)

    def __init__(self, base_dir: Path, run_id: str, modules: list[ModuleDef], language: str,
                 snapshot_before: dict, snapshot_after: dict, job: dict | None = None,
                 storage_fallback: bool = False, redact: bool = False, branding: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._args = (base_dir, run_id, modules, language, snapshot_before, snapshot_after)
        self._kwargs = {"job": job, "storage_fallback": storage_fallback, "redact": redact, "branding": branding}
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            html_path, _ = generate_report(*self._args, **self._kwargs)
        except OSError:
            self.result_ready.emit(None, True)
        except Exception:
            # Always answer, or the GUI waits forever with Run disabled -
            # but still surface the bug like an uncaught GUI-thread error.
            sys.excepthook(*sys.exc_info())
            self.result_ready.emit(None, False)
        else:
            self.result_ready.emit(html_path, False)
