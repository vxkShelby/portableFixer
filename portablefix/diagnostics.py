import json
import platform
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .audit_log import audit_log_path

BUG_REPORT_URL = "https://github.com/vxkShelby/portableFixer/issues/new"


def crash_log_path(base_dir: Path) -> Path:
    return base_dir / "Logs" / "crash.log"


def install_excepthook(base_dir: Path) -> None:
    """Uncaught exceptions in a --noconsole build vanish silently otherwise -
    this is the only place they'd ever be recorded."""
    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        write_crash_log(base_dir, exc_value, exc_tb)
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def write_crash_log(base_dir: Path, exc: BaseException, tb=None) -> None:
    """Also used for exceptions main() catches itself: those end in a
    "Startup failed" dialog and never reach sys.excepthook, so without this
    the only trace of why the app wouldn't start was that dialog's text."""
    try:
        path = crash_log_path(base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"--- {datetime.now(timezone.utc).isoformat()} ---\n")
            f.write(f"{platform.platform()}\n")
            traceback.print_exception(type(exc), exc, tb or exc.__traceback__, file=f)
            f.write("\n")
    except OSError:
        pass


def build_bug_report_url(version: str) -> str:
    """GitHub issues are English-only by convention, so the pre-filled
    title/body stay untranslated regardless of the app's UI language."""
    title = f"Bug report (v{version})"
    body = (
        "Describe what happened. Do not attach the diagnostics zip here - "
        "this tracker is public and the zip may still hold machine details; "
        f"share it privately if asked.\n\nOS: {platform.platform()}"
    )
    query = urlencode({"title": title, "body": body})
    return f"{BUG_REPORT_URL}?{query}"


_DIAG_README = """PortableFix diagnostics export
==============================
Personal data (user names, addresses, serial numbers, Wi-Fi names) is masked
in every file. The computer name and other machine details are not - share
this zip privately, never on a public issue tracker.

Files:
{members}
"""


def _current_run_id(base_dir: Path) -> str:
    # The newest audit log is the run in progress when the caller does not say.
    try:
        logs = [p for p in (base_dir / "Logs").glob("*.jsonl") if p.is_file()]
    except OSError:
        return ""
    return max(logs, key=lambda p: p.stat().st_mtime).stem if logs else ""


def export_diagnostics_zip(base_dir: Path, dest_path: Path, run_id: str | None = None) -> None:
    """crash.log plus the current run's audit log and report, every file
    redacted (research G20): other clients' runs on the same stick never
    leave it. `run_id` None = the newest audit log."""
    from . import handoff, redaction, report

    run_id = run_id or _current_run_id(base_dir)
    mask = redaction.local_profile_names() + redaction.local_account_names()
    members: list[tuple[str, bytes]] = []
    crash = crash_log_path(base_dir)
    if crash.is_file():
        text = crash.read_text(encoding="utf-8", errors="replace")
        members.append(("Logs/crash.log", redaction.redact_text(text, (), mask).encode("utf-8")))
    audit = audit_log_path(base_dir, run_id) if run_id else None
    if audit is not None and audit.is_file():
        members.append((f"Logs/{audit.name}", handoff._redacted_audit_log(audit, [], mask)))
    for json_path in sorted((base_dir / "Reports").glob(f"*_{run_id}.json")) if run_id else []:
        data = handoff._load_report_json(json_path)
        if data is None:
            continue
        redacted = report.redact_report_data(data, mask)
        members.append((f"Reports/{json_path.name}", json.dumps(redacted, indent=2, ensure_ascii=False).encode("utf-8")))
        if json_path.with_suffix(".html").is_file():
            members.append((f"Reports/{json_path.stem}.html", report.render_report_html(redacted).encode("utf-8")))
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README_DIAGNOSTICS.txt", _DIAG_README.format(members="\n".join(name for name, _ in members)))
        for name, payload in members:
            zf.writestr(name, payload)
