import html
import json
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import audit_log_path
from .i18n import translate
from .models import ActionDef, ModuleDef


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
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
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


_MAX_PREVIOUS_REPORT_BYTES = 10 * 1024 * 1024


def _find_previous_report(reports_dir: Path, hostname: str) -> dict | None:
    if not reports_dir.exists():
        return None
    # Filtered by prefix rather than glob(f"{hostname}_*.json") - a Windows
    # computer name can itself contain glob metacharacters like [ or ].
    # Sorted by filename, not mtime: run_id starts with a UTC timestamp (see
    # main.py's run_id format), so filenames already sort chronologically -
    # and stay correct after a USB copy/backup/restore resets mtimes.
    prefix = f"{hostname}_"
    candidates = sorted(
        (p for p in reports_dir.glob("*.json") if p.name.startswith(prefix)),
        key=lambda p: p.name,
    )
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        if latest.stat().st_size > _MAX_PREVIOUS_REPORT_BYTES:
            return None
        parsed = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # A prior report that's syntactically valid JSON but not the shape this
    # tool itself ever writes (corrupted, hand-edited, or from a future/past
    # schema) must degrade to "no previous report" rather than let a
    # malformed field crash the comparison a few lines later.
    return parsed if isinstance(parsed, dict) else None


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


def build_report_data(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
    job: dict | None = None,
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
                "category": _module_category(modules, entry["module_id"]),
            }
        )
    hostname = socket.gethostname()
    previous = _find_previous_report(base_dir / "Reports", hostname)
    return {
        "run_id": run_id,
        "language": language,
        "hostname": hostname,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "actions": actions,
        "requires_restart": [a for a in actions if a["risk"] == "REQUIRES_REBOOT"],
        "previous_comparison": _build_comparison(previous, actions, snapshot_after),
        "module_summary": _build_module_summary(actions),
        "job": _clean_job(job),
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


def _render_job(job: dict, t) -> str:
    if not job:
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
    return f'<div class="job">{line}{note}</div>'


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
@media print {
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
    exit_note = "" if ok else f'<span class="mod">{t("report_exit").format(code=html.escape(str(a["exit_code"])))}</span>'
    search_text = f"{a['label']} {a['module_id']} {a['action_id']}".lower()
    return (
        f'<div class="card {status_cls}" id="action-{index}" data-status="{status_cls}" '
        f'data-dry="{1 if a["dry_run"] else 0}" data-search="{html.escape(search_text, quote=True)}">'
        f'<div class="row">'
        f'<span class="status {status_cls}">{status_txt}</span>'
        f'<span class="label">{html.escape(a["label"])}</span>'
        f"{dry_tag}{exit_note}"
        f'<span class="badge" style="background:{badge_color}">{html.escape(a["risk"])}</span>'
        f'<span class="mod">{html.escape(a["module_id"])}</span>'
        f'<span class="ts">{html.escape(_format_timestamp(a["timestamp"]))}</span>'
        f"</div>{output_block}</div>"
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


def _render_failed_list(actions: list[dict], language: str) -> str:
    failed = [(i, a) for i, a in enumerate(actions, start=1) if a["exit_code"] != 0]
    if not failed:
        return ""
    items = "".join(
        f'<li><a href="#action-{i}">{html.escape(a["label"])}</a>'
        f'<span class="mod">{html.escape(a["module_id"])} &middot; '
        f'{html.escape(translate("report_exit", language).format(code=a["exit_code"]))}</span></li>'
        for i, a in failed
    )
    return (
        f'<section aria-labelledby="pf-h-failed"><h2 id="pf-h-failed">{html.escape(translate("report_failed_actions", language))}'
        f" ({len(failed)})</h2><ol class=\"failed-list\">{items}</ol></section>"
    )


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
    toolbar = _render_toolbar(language) if actions else ""

    free_before = html.escape(str(data["snapshot_before"].get("free_gb", "?")))
    free_after = html.escape(str(data["snapshot_after"].get("free_gb", "?")))
    raw_before = data["snapshot_before"].get("free_gb")
    raw_after = data["snapshot_after"].get("free_gb")
    delta = ""
    if isinstance(raw_before, (int, float)) and isinstance(raw_after, (int, float)):
        diff = round(raw_after - raw_before, 2)
        sign = "+" if diff >= 0 else ""
        delta = f" ({sign}{diff} GB)"

    restart_section = ""
    if data["requires_restart"]:
        items = "".join(f"<li>{html.escape(a['label'])}</li>" for a in data["requires_restart"])
        restart_section = f"<section><h2>{t('report_requires_restart')}</h2><ul>{items}</ul></section>"

    comparison_section = ""
    comparison = data.get("previous_comparison")
    if comparison:
        cmp_delta = comparison["free_gb_delta"]
        delta_txt = f"{'+' if cmp_delta > 0 else ''}{cmp_delta} GB" if cmp_delta is not None else "?"
        comparison_section = (
            f"<section><h2>{t('report_since_last_visit')}</h2>"
            f"<div class=\"meta\">{t('report_previous_run')} {html.escape(str(comparison['previous_run_id']))} "
            f"({html.escape(str(comparison['previous_generated_at']))})<br>"
            f"{t('report_free_space_change')}: {delta_txt}<br>"
            f"{t('report_actions_then_now')}: {comparison['previous_action_count']} &rarr; {comparison['action_count']}</div></section>"
        )

    return f"""<!DOCTYPE html>
<html lang="{html.escape(language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PortableFix report {html.escape(data['run_id'])}</title>
<style>{_CSS}</style></head>
<body><main class="wrap">
<h1>PortableFix &mdash; {html.escape(data['hostname'])}</h1>
{_render_job(data.get('job') or dict(), t)}
<div class="meta">{t('report_run')} {html.escape(data['run_id'])} &middot; {html.escape(data['os'])}<br>
{t('report_generated')}: {html.escape(_format_timestamp(data['generated_at']))}<br>
{t('report_free_space')}: {free_before} GB &rarr; {free_after} GB{delta}</div>
<div class="chips">
<div class="chip"><span class="num">{len(actions)}</span><span class="lbl">{t('report_chip_actions')}</span></div>
<div class="chip ok"><span class="num">{ok_count}</span><span class="lbl">{t('report_chip_ok')}</span></div>
<div class="chip fail"><span class="num">{fail_count}</span><span class="lbl">{t('report_chip_failed')}</span></div>
<div class="chip dry"><span class="num">{dry_count}</span><span class="lbl">{t('report_chip_dry_run')}</span></div>
</div>
{failed_section}
{module_section}
{comparison_section}
<section aria-labelledby="pf-h-actions"><h2 id="pf-h-actions">{t('report_actions_heading')}</h2>
{toolbar}
<p class="empty" id="pf-no-match" hidden>{t('report_no_match')}</p>
{cards}
</section>
{restart_section}
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
) -> tuple[Path, Path]:
    data = build_report_data(base_dir, run_id, modules, language, snapshot_before, snapshot_after, job)
    reports_dir = base_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / f"{data['hostname']}_{run_id}.html"
    json_path = reports_dir / f"{data['hostname']}_{run_id}.json"
    html_path.write_text(_render_html(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return html_path, json_path
