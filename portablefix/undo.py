import socket
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import audit_log_path


def create_undo_script(
    base_dir: Path,
    run_id: str,
    steps: list[str] | None = None,
    irreversible: list[str] | None = None,
    hive_backups: list[Path] | None = None,
) -> Path:
    steps = steps or []
    irreversible = irreversible or []
    hive_backups = hive_backups or []
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
    for folder in hive_backups:
        # A hint, never a step: swapping a hive back rolls back EVERY
        # registry change since the backup (not just PortableFix's) and is
        # only possible offline, so running this script must never do it.
        # A line break in the path would end the comment and make the rest
        # executable - replaced, but spaces (Program Files) are kept as-is.
        where = str(folder).replace("\r", " ").replace("\n", " ")
        lines.extend([
            "# FULL REGISTRY HIVE BACKUP - manual restore only, never run by this script:",
            f"#   {where}  (SOFTWARE.hiv, SYSTEM.hiv)",
            "#   Last resort if Windows no longer starts and the restore point cannot help.",
            "#   Offline only (WinRE > Troubleshoot > Command Prompt; drive letters may differ there):",
            "#   rename <Windows>\\System32\\config\\SOFTWARE and SYSTEM (e.g. to *.old), copy SOFTWARE.hiv",
            "#   and SYSTEM.hiv in their place without the .hiv extension, then restart.",
            "#   This undoes every registry change made since the backup, not just PortableFix's.",
            "",
        ])
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
