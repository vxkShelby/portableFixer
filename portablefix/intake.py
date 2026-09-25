"""Intake and outtake forms and work time (research G20).

Intake: what the client reported, the state the PC came in (proof that
"the scratch was already there"), the accessories received, the answer to
a data backup (requested, or a waiver - the client declines it and takes
the risk) and how the password was handled. Outtake: a Pass / Fail / N/A
function check at hand-over, who took the PC and when.

Both are stored as `_system` audit events whose output is the form as JSON
- the audit log stays the one record of the run, and each save is kept
there; the report shows the latest one. There is deliberately no field
for a password value: only *how* it was handled (not needed / given by the
client / reset), and from_dict drops any key it does not know, so not even
a hand-edited log carries one into the report.

Work time: every batch logs how long it ran (batch_duration), and the
optional manual timer on the Job details logs its start and stop
(work_timer) - kept in the run's audit log, so it survives a restart of
PortableFix within the same run.

Qt-free on purpose: the report, the GUI and the tests share it.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

INTAKE_EVENT = "intake"
OUTTAKE_EVENT = "outtake"
WORK_TIMER_EVENT = "work_timer"
BATCH_DURATION_EVENT = "batch_duration"
# _system events that are not safety facts - report.py lists them in their
# own sections, not in the safety log.
EVENT_KINDS = frozenset({INTAKE_EVENT, OUTTAKE_EVENT, WORK_TIMER_EVENT, BATCH_DURATION_EVENT})

TIMER_START = "start"
TIMER_STOP = "stop"

CONDITION_FLAGS = ("scratches", "cracked_screen", "missing_keys", "liquid_damage")
BACKUP_REQUESTED = "requested"
BACKUP_WAIVER = "waiver"
BACKUP_CHOICES = (BACKUP_REQUESTED, BACKUP_WAIVER)
PASSWORD_CHOICES = ("not_needed", "given_by_client", "reset")
OUTTAKE_CHECKS = ("wifi", "sound", "camera", "keyboard", "usb", "charging", "display")
CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_NA = "na"
CHECK_RESULTS = (CHECK_PASS, CHECK_FAIL, CHECK_NA)

MAX_PROBLEM_LENGTH = 2000
MAX_CONDITION_LENGTH = 1000
MAX_ACCESSORIES_LENGTH = 500
MAX_NAME_LENGTH = 120
MAX_HANDED_AT_LENGTH = 32
# One batch longer than this is a clock jump or a corrupted entry, not work.
_MAX_BATCH_SECONDS = 7 * 24 * 3600


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _choice(value, choices: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in choices else ""


@dataclass
class IntakeForm:
    problem: str = ""
    condition: str = ""
    condition_flags: list[str] = field(default_factory=list)
    accessories: str = ""
    backup: str = ""  # "" / BACKUP_CHOICES
    password_handling: str = ""  # "" / PASSWORD_CHOICES - never the password

    @classmethod
    def from_dict(cls, raw) -> "IntakeForm":
        if not isinstance(raw, dict):
            return cls()
        flags = raw.get("condition_flags")
        flags = flags if isinstance(flags, list) else []
        return cls(
            problem=_text(raw.get("problem"), MAX_PROBLEM_LENGTH),
            condition=_text(raw.get("condition"), MAX_CONDITION_LENGTH),
            # In the catalog's order, each once.
            condition_flags=[f for f in CONDITION_FLAGS if f in flags],
            accessories=_text(raw.get("accessories"), MAX_ACCESSORIES_LENGTH),
            backup=_choice(raw.get("backup"), BACKUP_CHOICES),
            password_handling=_choice(raw.get("password_handling"), PASSWORD_CHOICES),
        )

    def to_dict(self) -> dict:
        # Through from_dict, so what is stored is always the cleaned form.
        clean = IntakeForm.from_dict({
            "problem": self.problem, "condition": self.condition,
            "condition_flags": list(self.condition_flags), "accessories": self.accessories,
            "backup": self.backup, "password_handling": self.password_handling,
        })
        return {
            "problem": clean.problem, "condition": clean.condition,
            "condition_flags": clean.condition_flags, "accessories": clean.accessories,
            "backup": clean.backup, "password_handling": clean.password_handling,
        }

    def is_empty(self) -> bool:
        return not any(self.to_dict().values())


@dataclass
class OuttakeForm:
    # check -> CHECK_RESULTS; a check not tested is simply missing.
    checks: dict[str, str] = field(default_factory=dict)
    handed_to: str = ""
    # Local time as the technician set it ("YYYY-MM-DD HH:MM"), only with
    # handed_to - a time without anyone to hand the PC to means nothing.
    handed_at: str = ""

    @classmethod
    def from_dict(cls, raw) -> "OuttakeForm":
        if not isinstance(raw, dict):
            return cls()
        checks = raw.get("checks")
        checks = checks if isinstance(checks, dict) else {}
        handed_to = _text(raw.get("handed_to"), MAX_NAME_LENGTH)
        return cls(
            checks={key: checks[key] for key in OUTTAKE_CHECKS if _choice(checks.get(key), CHECK_RESULTS)},
            handed_to=handed_to,
            handed_at=_text(raw.get("handed_at"), MAX_HANDED_AT_LENGTH) if handed_to else "",
        )

    def to_dict(self) -> dict:
        clean = OuttakeForm.from_dict({"checks": dict(self.checks), "handed_to": self.handed_to, "handed_at": self.handed_at})
        return {"checks": clean.checks, "handed_to": clean.handed_to, "handed_at": clean.handed_at}

    def is_empty(self) -> bool:
        clean = self.to_dict()
        return not clean["checks"] and not clean["handed_to"]


def form_to_output(form: IntakeForm | OuttakeForm) -> str:
    """The audit event's output: the cleaned form as one line of JSON."""
    return json.dumps(form.to_dict(), ensure_ascii=False, sort_keys=True)


def _parse_output(output) -> dict | None:
    if not isinstance(output, str):
        return None
    try:
        parsed = json.loads(output)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _system_events(entries: list[dict], kind: str) -> list[dict]:
    return [e for e in entries if e.get("module_id") == "_system" and e.get("action_id") == kind]


def latest_intake(entries: list[dict]) -> tuple[IntakeForm, str] | None:
    """(form, timestamp) of the last intake saved in the run, or None -
    also when the last save cleared the form."""
    for entry in reversed(_system_events(entries, INTAKE_EVENT)):
        raw = _parse_output(entry.get("output"))
        if raw is None:
            continue  # A torn line - an earlier save still counts.
        form = IntakeForm.from_dict(raw)
        return None if form.is_empty() else (form, str(entry.get("timestamp") or ""))
    return None


def latest_outtake(entries: list[dict]) -> tuple[OuttakeForm, str] | None:
    for entry in reversed(_system_events(entries, OUTTAKE_EVENT)):
        raw = _parse_output(entry.get("output"))
        if raw is None:
            continue
        form = OuttakeForm.from_dict(raw)
        return None if form.is_empty() else (form, str(entry.get("timestamp") or ""))
    return None


def batch_duration_output(seconds: float) -> str:
    return json.dumps({"seconds": max(0, int(round(seconds)))})


def batch_seconds(entries: list[dict]) -> tuple[int, int]:
    """(total seconds, number of logged batch segments) of the run."""
    total = 0
    count = 0
    for entry in _system_events(entries, BATCH_DURATION_EVENT):
        raw = _parse_output(entry.get("output")) or {}
        seconds = raw.get("seconds")
        if isinstance(seconds, int) and not isinstance(seconds, bool) and 0 <= seconds <= _MAX_BATCH_SECONDS:
            total += seconds
            count += 1
    return total, count


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # Audit timestamps are UTC with an offset; a naive one is taken as UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class TimerState:
    # Closed start-stop intervals only.
    stopped_seconds: int = 0
    running_since: datetime | None = None

    def total_seconds(self, now: datetime | None = None) -> int:
        if self.running_since is None:
            return self.stopped_seconds
        now = now or datetime.now(timezone.utc)
        return self.stopped_seconds + max(0, int((now - self.running_since).total_seconds()))


def timer_state(entries: list[dict]) -> TimerState:
    """The manual timer as the run's audit log leaves it. A second start
    while running and a stop while stopped are ignored (a double click, a
    torn write); an interval running backwards (the clock was changed)
    counts as zero."""
    state = TimerState()
    for entry in _system_events(entries, WORK_TIMER_EVENT):
        when = _parse_time(entry.get("timestamp"))
        if when is None:
            continue
        decision = entry.get("decision")
        if decision == TIMER_START and state.running_since is None:
            state.running_since = when
        elif decision == TIMER_STOP and state.running_since is not None:
            state.stopped_seconds += max(0, int((when - state.running_since).total_seconds()))
            state.running_since = None
    return state


def format_duration(seconds: int) -> str:
    """"1 h 05 min", "12 min", "45 s" - the same in Slovak and English."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def format_clock(seconds: int) -> str:
    """"0:12:34" for the running timer in the GUI."""
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
