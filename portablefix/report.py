import html
import json
import platform
import socket
from pathlib import Path

from .audit_log import audit_log_path
from .models import ActionDef, ModuleDef


def _find_action(modules: list[ModuleDef], module_id: str, action_id: str) -> ActionDef | None:
    for module in modules:
        if module.module_id != module_id:
            continue
        for action in module.actions:
            if action.id == action_id:
                return action
    return None


def _read_audit_entries(base_dir: Path, run_id: str) -> list[dict]:
    path = audit_log_path(base_dir, run_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def build_report_data(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
) -> dict:
    entries = _read_audit_entries(base_dir, run_id)
    actions = []
    for entry in entries:
        action = _find_action(modules, entry["module_id"], entry["action_id"])
        actions.append(
            {
                "timestamp": entry["timestamp"],
                "module_id": entry["module_id"],
                "action_id": entry["action_id"],
                "label": action.label(language) if action else entry["action_id"],
                "risk": action.risk.value if action else "UNKNOWN",
                "exit_code": entry["exit_code"],
                "dry_run": entry["dry_run"],
            }
        )
    return {
        "run_id": run_id,
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "actions": actions,
        "requires_restart": [a for a in actions if a["risk"] == "REQUIRES_REBOOT"],
    }


def _render_html(data: dict) -> str:
    rows = "\n".join(
        f"<tr><td>{html.escape(a['module_id'])}</td><td>{html.escape(a['label'])}</td><td>{html.escape(a['risk'])}</td>"
        f"<td>{a['exit_code']}</td><td>{a['dry_run']}</td></tr>"
        for a in data["actions"]
    )
    restart_section = ""
    if data["requires_restart"]:
        items = "".join(f"<li>{html.escape(a['label'])}</li>" for a in data["requires_restart"])
        restart_section = f"<h2>Requires restart</h2><ul>{items}</ul>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PortableFix report {html.escape(data['run_id'])}</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
th {{ background: #eee; }}
</style></head>
<body>
<h1>PortableFix report - {html.escape(data['hostname'])}</h1>
<p>Run: {html.escape(data['run_id'])}</p>
<p>OS: {html.escape(data['os'])}</p>
<p>Free space before: {data['snapshot_before'].get('free_gb', '?')} GB,
after: {data['snapshot_after'].get('free_gb', '?')} GB</p>
<h2>Actions</h2>
<table>
<tr><th>Module</th><th>Action</th><th>Risk</th><th>Exit code</th><th>Dry run</th></tr>
{rows}
</table>
{restart_section}
</body></html>
"""


def generate_report(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
) -> tuple[Path, Path]:
    data = build_report_data(base_dir, run_id, modules, language, snapshot_before, snapshot_after)
    reports_dir = base_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / f"{data['hostname']}_{run_id}.html"
    json_path = reports_dir / f"{data['hostname']}_{run_id}.json"
    html_path.write_text(_render_html(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return html_path, json_path
