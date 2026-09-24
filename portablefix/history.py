"""Past-run history for the dashboard, read from the JSON reports in Reports/.

Every batch already writes Reports/<hostname>_<run_id>.json (see report.py);
this only reads them back, so history needs no extra state of its own and
survives copying the USB drive around.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# A report is a few KB; anything this large is not one of ours.
_MAX_REPORT_BYTES = 10 * 1024 * 1024


@dataclass
class RunSummary:
    run_id: str
    generated_at: str
    action_count: int
    failed_count: int
    dry_run: bool
    html_path: Path | None

    def display_date(self) -> str:
        try:
            moment = datetime.fromisoformat(self.generated_at).astimezone()
        # OverflowError/OSError: .astimezone() on a corrupt far-past date on
        # Windows - this runs while the window is being built, so never raise.
        except (TypeError, ValueError, OverflowError, OSError):
            return self.generated_at or "?"
        return moment.strftime("%Y-%m-%d %H:%M")


def _summarize(json_path: Path) -> RunSummary | None:
    try:
        if json_path.stat().st_size > _MAX_REPORT_BYTES:
            return None
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    actions = data.get("actions")
    if not isinstance(actions, list):
        return None
    actions = [a for a in actions if isinstance(a, dict)]
    html_path = json_path.with_suffix(".html")
    return RunSummary(
        run_id=str(data.get("run_id", json_path.stem)),
        generated_at=str(data.get("generated_at", "")),
        action_count=len(actions),
        failed_count=sum(1 for a in actions if a.get("exit_code") != 0),
        # A batch is "dry-run" only if every action in it was; an empty
        # report counts as dry-run since it changed nothing either.
        dry_run=all(a.get("dry_run") is True for a in actions),
        html_path=html_path if html_path.exists() else None,
    )


def recent_runs(reports_dir: Path, hostname: str, limit: int = 5, exclude_run_id: str | None = None) -> list[RunSummary]:
    """Newest-first summaries of this machine's past runs.

    Filtered by filename prefix, not glob(f"{hostname}_*") - a Windows
    computer name can contain glob metacharacters. Sorted by filename:
    run_ids start with a UTC timestamp, so names sort chronologically even
    after a copy/restore resets mtimes.
    """
    if limit <= 0 or not reports_dir.is_dir():
        return []
    prefix = f"{hostname}_"
    try:
        candidates = sorted(
            (p for p in reports_dir.glob("*.json") if p.name.startswith(prefix)),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return []
    runs: list[RunSummary] = []
    for path in candidates:
        summary = _summarize(path)
        if summary is None or summary.run_id == exclude_run_id:
            continue
        runs.append(summary)
        if len(runs) >= limit:
            break
    return runs
