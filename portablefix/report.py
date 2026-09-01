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
                "output": entry.get("output", ""),
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


_RISK_COLORS = {
    "SAFE": "#9ece6a",
    "MODERATE": "#e0af68",
    "DESTRUCTIVE": "#f7768e",
    "REQUIRES_REBOOT": "#bb9af7",
    "UNKNOWN": "#565f89",
}

_CSS = """
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', sans-serif; background: #1a1b26; color: #c0caf5;
       margin: 0; padding: 32px; font-size: 14px; }
.wrap { max-width: 900px; margin: 0 auto; }
h1 { color: #7aa2f7; font-size: 22px; margin: 0 0 4px 0; }
.meta { color: #565f89; margin-bottom: 20px; line-height: 1.6; }
.chips { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 24px; }
.chip { background: #24283b; border-radius: 8px; padding: 10px 18px; text-align: center; }
.chip .num { font-size: 20px; font-weight: bold; display: block; }
.chip.ok .num { color: #9ece6a; }
.chip.fail .num { color: #f7768e; }
.chip.dry .num { color: #e0af68; }
.chip .lbl { font-size: 11px; color: #565f89; text-transform: uppercase; }
.card { background: #24283b; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; }
.card.fail { border-left: 3px solid #f7768e; }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status { font-weight: bold; font-size: 12px; border-radius: 8px; padding: 1px 9px; color: #1a1b26; }
.status.ok { background: #9ece6a; }
.status.fail { background: #f7768e; }
.label { font-weight: 600; flex: 1; }
.badge { font-size: 10px; font-weight: bold; border-radius: 7px; padding: 1px 8px; color: #1a1b26; }
.mod { color: #565f89; font-size: 12px; }
.ts { color: #565f89; font-size: 11px; }
.dry-tag { color: #e0af68; font-size: 11px; font-weight: bold; }
details { margin-top: 8px; }
summary { color: #7aa2f7; cursor: pointer; font-size: 12px; }
pre { background: #16161e; border-radius: 6px; padding: 10px; overflow-x: auto;
      font-family: 'Cascadia Mono', Consolas, monospace; font-size: 12px; color: #a9b1d6;
      white-space: pre-wrap; word-break: break-word; }
h2 { color: #bb9af7; font-size: 16px; margin-top: 28px; }
ul { color: #c0caf5; }
"""


def _render_action_card(a: dict) -> str:
    ok = a["exit_code"] == 0
    status_cls = "ok" if ok else "fail"
    status_txt = "OK" if ok else "FAILED"
    badge_color = _RISK_COLORS.get(a["risk"], _RISK_COLORS["UNKNOWN"])
    dry_tag = '<span class="dry-tag">DRY-RUN</span>' if a["dry_run"] else ""
    output_block = ""
    if a.get("output"):
        output_block = (
            f"<details><summary>Output</summary><pre>{html.escape(a['output'])}</pre></details>"
        )
    exit_note = "" if ok else f'<span class="mod">exit {a["exit_code"]}</span>'
    return (
        f'<div class="card {status_cls}"><div class="row">'
        f'<span class="status {status_cls}">{status_txt}</span>'
        f'<span class="label">{html.escape(a["label"])}</span>'
        f"{dry_tag}{exit_note}"
        f'<span class="badge" style="background:{badge_color}">{html.escape(a["risk"])}</span>'
        f'<span class="mod">{html.escape(a["module_id"])}</span>'
        f'<span class="ts">{html.escape(str(a["timestamp"]))}</span>'
        f"</div>{output_block}</div>"
    )


def _render_html(data: dict) -> str:
    actions = data["actions"]
    ok_count = sum(1 for a in actions if a["exit_code"] == 0)
    fail_count = len(actions) - ok_count
    dry_count = sum(1 for a in actions if a["dry_run"])
    cards = "\n".join(_render_action_card(a) for a in actions)

    free_before = data["snapshot_before"].get("free_gb", "?")
    free_after = data["snapshot_after"].get("free_gb", "?")
    delta = ""
    if isinstance(free_before, (int, float)) and isinstance(free_after, (int, float)):
        diff = round(free_after - free_before, 2)
        sign = "+" if diff >= 0 else ""
        delta = f" ({sign}{diff} GB)"

    restart_section = ""
    if data["requires_restart"]:
        items = "".join(f"<li>{html.escape(a['label'])}</li>" for a in data["requires_restart"])
        restart_section = f"<h2>Requires restart</h2><ul>{items}</ul>"

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PortableFix report {html.escape(data['run_id'])}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>PortableFix &mdash; {html.escape(data['hostname'])}</h1>
<div class="meta">Run {html.escape(data['run_id'])} &middot; {html.escape(data['os'])}<br>
Free space: {free_before} GB &rarr; {free_after} GB{delta}</div>
<div class="chips">
<div class="chip"><span class="num">{len(actions)}</span><span class="lbl">Actions</span></div>
<div class="chip ok"><span class="num">{ok_count}</span><span class="lbl">OK</span></div>
<div class="chip fail"><span class="num">{fail_count}</span><span class="lbl">Failed</span></div>
<div class="chip dry"><span class="num">{dry_count}</span><span class="lbl">Dry-run</span></div>
</div>
{cards}
{restart_section}
</div></body></html>
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
