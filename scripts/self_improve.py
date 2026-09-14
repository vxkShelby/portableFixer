#!/usr/bin/env python3
"""Signal-gated self-improve loop entry point.

Run manually with `python scripts/self_improve.py`, or automatically via
.githooks/post-commit. Gathers signal from pytest/git/crash.log; if
nothing fired, logs quietly and exits. If a signal fired, calls the
local Archon CLI (.archon/workflows/self-improve.yaml), which does the
actual investigate/fix/PR work in its own isolated git worktree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "Logs"
CRASH_LOG = LOGS_DIR / "crash.log"
STATE_FILE = REPO_ROOT / "docs" / ".self_improve_state.json"
SOUL_FILE = REPO_ROOT / "docs" / "SOUL.md"
SELF_IMPROVE_LOG = LOGS_DIR / "self_improve.log"
FIX_COMMIT_WINDOW = 20
REPEAT_THRESHOLD = 3
PYTEST_TIMEOUT_SEC = 300
# Mirrors the deselect pattern documented in README.md and
# .github/workflows/tests.yml: these files spawn real powershell.exe
# processes and can crash the whole pytest run (STATUS_STACK_BUFFER_OVERRUN),
# which would otherwise silently zero out signal detection.
PYTEST_DESELECT = [
    "--deselect", "tests/test_gui_main_window.py",
    "--deselect", "tests/test_executor.py",
]

sys.path.insert(0, str(REPO_ROOT))
from scripts.self_improve_lib import (  # noqa: E402
    append_soul_entry,
    count_pytest_failures,
    find_repeated_fix_file,
    has_signal,
    load_state,
    parse_fix_commit_files,
    read_new_crash_log_entries,
    save_state,
)


def _log(message: str) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with SELF_IMPROVE_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{message}\n")


def gather_signal() -> tuple[bool, str, int]:
    """Returns (signal_found, trigger_description, new_crash_log_offset)."""
    pytest_result = subprocess.run(
        [sys.executable, "-m", "pytest", "--tb=no", "-q", *PYTEST_DESELECT],
        cwd=REPO_ROOT, capture_output=True, text=True,
        timeout=PYTEST_TIMEOUT_SEC, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    fail_count = count_pytest_failures(pytest_result.stdout)

    git_result = subprocess.run(
        ["git", "log", "--name-only", "--pretty=format:%s", f"-{FIX_COMMIT_WINDOW}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    fix_files = parse_fix_commit_files(git_result.stdout)
    repeated_file = find_repeated_fix_file(fix_files, min_repeats=REPEAT_THRESHOLD)

    state = load_state(STATE_FILE)
    new_crash_text, new_offset = read_new_crash_log_entries(
        CRASH_LOG, state.get("crash_log_offset", 0)
    )

    reasons = []
    if fail_count > 0:
        reasons.append(f"{fail_count} failing test(s)")
    if repeated_file:
        reasons.append(f"repeated fixes in {repeated_file}")
    if new_crash_text.strip():
        reasons.append("new crash.log entry")

    signal_found = has_signal(fail_count, repeated_file, new_crash_text)
    trigger = "; ".join(reasons) if reasons else "none"
    return signal_found, trigger, new_offset


def run_archon(trigger: str) -> tuple[bool, str]:
    """Returns (succeeded, stdout)."""
    result = subprocess.run(
        ["archon", "workflow", "run", "self-improve", trigger],
        cwd=REPO_ROOT, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0, result.stdout


def main() -> None:
    signal_found, trigger, new_offset = gather_signal()
    if not signal_found:
        _log("quiet run, no signal")
        return

    _log(f"signal found: {trigger}")
    archon_ok, archon_stdout = run_archon(trigger)

    if not archon_ok:
        # Don't write a "done" SOUL.md entry for a failed run, and don't
        # advance the crash-log offset - that would permanently lose
        # crash entries that were never actually reviewed by Archon.
        _log("archon run failed, skipping SOUL.md entry and state save")
        return

    append_soul_entry(SOUL_FILE, trigger, archon_stdout.strip()[-500:])
    save_state(STATE_FILE, {"crash_log_offset": new_offset})
    _log("done, archon output tail logged to SOUL.md")


if __name__ == "__main__":
    main()
