import platform
import sys
import traceback
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

BUG_REPORT_URL = "https://github.com/vxkShelby/portableFixer/issues/new"


def crash_log_path(base_dir: Path) -> Path:
    return base_dir / "Logs" / "crash.log"


def install_excepthook(base_dir: Path) -> None:
    """Uncaught exceptions in a --noconsole build vanish silently otherwise -
    this is the only place they'd ever be recorded."""
    previous_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb) -> None:
        try:
            path = crash_log_path(base_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now(timezone.utc).isoformat()} ---\n")
                f.write(f"{platform.platform()}\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                f.write("\n")
        except OSError:
            pass
        previous_hook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def build_bug_report_url(version: str) -> str:
    """GitHub issues are English-only by convention, so the pre-filled
    title/body stay untranslated regardless of the app's UI language."""
    title = f"Bug report (v{version})"
    body = (
        "Please attach the diagnostics zip (Export diagnostics button) "
        f"and describe what happened.\n\nOS: {platform.platform()}"
    )
    query = urlencode({"title": title, "body": body})
    return f"{BUG_REPORT_URL}?{query}"


def export_diagnostics_zip(base_dir: Path, dest_path: Path) -> None:
    """Bundles the audit logs, generated reports and crash log (whatever of
    those exists) into one zip the user can attach to a bug report."""
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in ("Logs", "Reports"):
            src = base_dir / folder
            if not src.is_dir():
                continue
            for file_path in src.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, arcname=str(Path(folder) / file_path.relative_to(src)))
