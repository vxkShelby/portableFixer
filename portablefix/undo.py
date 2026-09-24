import socket
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import audit_log_path


def create_undo_script(
    base_dir: Path,
    run_id: str,
    steps: list[str] | None = None,
    irreversible: list[str] | None = None,
) -> Path:
    steps = steps or []
    irreversible = irreversible or []
    hostname = socket.gethostname()
    # undo.ps1 is meant to be found and run on its own, possibly by a
    # different technician days later - point back to the full record of the
    # run (same naming as report.generate_report / audit_log.audit_log_path)
    # so "nothing to undo" is never mistaken for "nothing happened".
    report_path = base_dir / "Reports" / f"{hostname}_{run_id}.html"
    lines = [
        "# PortableFix undo script",
        f"# run_id: {run_id}",
        f"# computer: {hostname}",
        f"# generated: {datetime.now(timezone.utc).isoformat()}",
        f"# full report: {report_path}",
        f"# audit log:   {audit_log_path(base_dir, run_id)}",
        "",
    ]
    if irreversible:
        # Only comments - these changes have no rollback command, so the
        # script can't undo them; saying so stops anyone assuming it did.
        lines.append("# NOT reversible - ran in this run but has no rollback available:")
        # A line break inside a label would end the comment and turn the
        # rest of it into executable PowerShell - flatten to one line.
        lines.extend(f"#   - {' '.join(str(item).split())}" for item in irreversible)
        lines.append("")
    if steps:
        lines.extend(steps)
    else:
        lines.append("# No reversible changes were made in this run.")

    path = base_dir / "Backups" / run_id / "undo.ps1"
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig (BOM): Windows PowerShell 5.1 reads a BOM-less .ps1 in the
    # ANSI codepage, garbling the Slovak labels above and any non-ASCII in an
    # undo command. The file is always rewritten whole (LIFO puts the newest
    # step on top), so the BOM appears exactly once, at the start.
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return path
