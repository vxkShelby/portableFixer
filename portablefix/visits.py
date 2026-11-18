"""Visits log: one row per past run of every PC the stick has been plugged
into (research N7), for invoicing and "what did we do there last time".

The source is Reports/<hostname>_<run_id>.json - the one file that holds the
computer name, the job details (technician, client), the actions with their
exit codes and the work time. The audit logs in Logs/ have no job details
and are written even for a run that never produced a report, so they would
only add runs with half the columns. The redacted pc_<run_id>_client copies
are skipped (history.CLIENT_SUFFIX). A corrupt or foreign file is skipped.

Pure and Qt-free: the settings menu, the CLI and the tests share it.
"""

import csv
import html
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import redaction
from .history import _MAX_REPORT_BYTES, CLIENT_SUFFIX
from .i18n import translate
from .intake import format_clock

# ';' - Excel with a Slovak (or any comma-decimal) locale splits on it, and
# a ',' would put the whole row into one cell there.
CSV_DELIMITER = ";"
# With "Redact for the client" on, the customer's name is masked too (the
# computer name becomes redaction.PC).
CLIENT_MASK = "<client>"
_COLUMNS = ("date", "computer", "technician", "client", "actions", "failed", "dry_run", "work", "timer", "run_id")


@dataclass
class Visit:
    run_id: str
    started: datetime | None
    hostname: str
    technician: str
    client: str
    actions: int
    failed: int
    dry_run: bool
    work_seconds: int | None
    timer_seconds: int | None

    def date_text(self) -> str:
        return self.started.strftime("%Y-%m-%d %H:%M") if self.started else ""


def _seconds(value) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return int(value)
    return None


def _parse_started(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)).astimezone()
    # OverflowError/OSError: .astimezone() on a corrupt far-past date on Windows.
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _read_visit(path: Path) -> Visit | None:
    try:
        if path.stat().st_size > _MAX_REPORT_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return None
    actions = [a for a in data["actions"] if isinstance(a, dict)]
    # No run_id field: the file is <hostname>_<run_id>, so only the last part is the id (the name before it
    # is the computer name, which redaction must be able to mask).
    run_id = str(data.get("run_id") or path.stem.rsplit("_", 1)[-1])
    hostname = data.get("hostname")
    if not isinstance(hostname, str) or not hostname.strip():
        # No field (an old report): the name before "_<run_id>" in the file name.
        hostname = path.stem.removesuffix(f"_{run_id}") if path.stem.endswith(f"_{run_id}") else ""
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    work = data.get("work_time") if isinstance(data.get("work_time"), dict) else {}
    work_seconds = _seconds(work.get("batch_seconds"))
    if work_seconds is None:
        # No work_time block: add up what the actions themselves timed.
        timed = [s for s in (_seconds(a.get("duration_sec")) for a in actions) if s]
        work_seconds = sum(timed) if timed else None
    return Visit(
        run_id=run_id,
        started=_parse_started(data.get("generated_at")),
        hostname=hostname.strip(),
        technician=str(job.get("technician") or ""),
        client=str(job.get("client") or ""),
        actions=len(actions),
        failed=sum(1 for a in actions if a.get("exit_code") != 0),
        dry_run=all(a.get("dry_run") is True for a in actions),
        work_seconds=work_seconds,
        timer_seconds=_seconds(work.get("timer_seconds")),
    )


def collect_visits(reports_dir: Path, since: date | None = None, redact: bool = False) -> list[Visit]:
    """Newest-first visits of every PC with a report in `reports_dir`.

    `since`: only runs on or after that local date (a run whose date cannot
    be read is then left out). `redact`: computer and customer names masked.
    Sorted by run_id - it starts with a UTC timestamp, so it stays
    chronological across PCs and after a copy that resets mtimes."""
    if not reports_dir.is_dir():
        return []
    try:
        paths = [p for p in reports_dir.glob("*.json") if not p.stem.endswith(CLIENT_SUFFIX)]
    except OSError:
        return []
    rows = [v for v in map(_read_visit, paths) if v is not None]
    if since is not None:
        rows = [v for v in rows if v.started is not None and v.started.date() >= since]
    if redact:
        for v in rows:
            v.hostname = redaction.PC if v.hostname else ""
            v.client = CLIENT_MASK if v.client else ""
    return sorted(rows, key=lambda v: v.run_id, reverse=True)


def _cells(visit: Visit, language: str) -> list[str]:
    def clock(seconds: int | None) -> str:
        return "" if seconds is None else format_clock(seconds)

    yes_no = translate("visits_yes" if visit.dry_run else "visits_no", language)
    return [
        visit.date_text(), visit.hostname, visit.technician, visit.client, str(visit.actions), str(visit.failed),
        yes_no, clock(visit.work_seconds), clock(visit.timer_seconds), visit.run_id,
    ]


def _header(language: str) -> list[str]:
    return [translate(f"visits_col_{name}", language) for name in _COLUMNS]


def _spreadsheet_safe(cell: str) -> str:
    # A customer name starting with = + - @ would run as a formula in Excel.
    return "'" + cell if cell[:1] in ("=", "+", "-", "@", "\t", "\r") else cell


def write_csv(visits: list[Visit], dest: Path, language: str = "en") -> Path:
    """UTF-8 with a BOM (Excel reads it as UTF-8, so "Ján" survives), ';'
    separated, CRLF lines."""
    with dest.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=CSV_DELIMITER, lineterminator="\r\n")
        writer.writerow(_header(language))
        for visit in visits:
            writer.writerow([_spreadsheet_safe(cell) for cell in _cells(visit, language)])
    return dest


_CSS = (
    "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}"
    "table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:4px 10px;text-align:left}"
    "th{background:#f0f0f0}td.n{text-align:right}"
)


def render_html(visits: list[Visit], language: str = "en") -> str:
    title = html.escape(translate("visits_title", language))
    head = "".join(f"<th>{html.escape(name)}</th>" for name in _header(language))
    body = "".join(
        "<tr>" + "".join(
            f'<td class="n">{html.escape(cell)}</td>' if i in (4, 5) else f"<td>{html.escape(cell)}</td>"
            for i, cell in enumerate(_cells(visit, language))
        ) + "</tr>"
        for visit in visits
    )
    return (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><title>{title}</title>'
        f"<style>{_CSS}</style></head><body><h1>{title}</h1>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></body></html>"
    )


def write_html(visits: list[Visit], dest: Path, language: str = "en") -> Path:
    dest.write_text(render_html(visits, language), encoding="utf-8")
    return dest
