from datetime import datetime, timezone
from pathlib import Path


def create_undo_script(base_dir: Path, run_id: str, steps: list[str] | None = None) -> Path:
    steps = steps or []
    lines = [
        "# PortableFix undo script",
        f"# run_id: {run_id}",
        f"# generated: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    if steps:
        lines.extend(steps)
    else:
        lines.append("# No reversible changes were made in this run.")

    path = base_dir / "Backups" / run_id / "undo.ps1"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
