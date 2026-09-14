"""Pure, testable logic for the self-improve signal gatherer.

No subprocess calls live here — scripts/self_improve.py wires this to
real pytest/git/archon invocations. Keeping this module subprocess-free
is what makes it testable without spawning real processes.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path


def count_pytest_failures(pytest_stdout: str) -> int:
    """Parses the trailing pytest summary line, e.g. '3 failed, 490 passed
    in 12.3s'. Returns 0 if no 'N failed' substring is present."""
    match = re.search(r"(\d+) failed", pytest_stdout)
    return int(match.group(1)) if match else 0


def parse_fix_commit_files(git_log_output: str) -> list[str]:
    """git_log_output is the output of:
        git log --name-only --pretty=format:%s -N
    Each commit block is a subject line followed by changed file paths;
    blocks are separated by a blank line. Returns the first changed file
    for every commit whose subject starts with 'fix:'."""
    blocks = git_log_output.strip().split("\n\n")
    files: list[str] = []
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        subject, *changed = lines
        if subject.startswith("fix:") and changed:
            files.append(changed[0])
    return files


def find_repeated_fix_file(fix_commit_files: list[str], min_repeats: int = 3) -> str | None:
    """fix_commit_files: output of parse_fix_commit_files. Returns the file
    name that appears at least min_repeats times, or None."""
    counts = Counter(fix_commit_files)
    for file_name, count in counts.most_common():
        if count >= min_repeats:
            return file_name
    return None


def read_new_crash_log_entries(path: Path, last_offset: int) -> tuple[str, int]:
    """Returns (new_text, new_offset). A missing file means no new crashes,
    not an error — returns ("", last_offset) unchanged."""
    if not path.exists():
        return "", last_offset
    with path.open("r", encoding="utf-8") as f:
        f.seek(last_offset)
        new_text = f.read()
        new_offset = f.tell()
    return new_text, new_offset


def has_signal(pytest_fail_count: int, repeated_fix_file: str | None, new_crash_text: str) -> bool:
    return pytest_fail_count > 0 or repeated_fix_file is not None or bool(new_crash_text.strip())


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"crash_log_offset": 0}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def append_soul_entry(path: Path, trigger: str, archon_output_tail: str) -> None:
    """Appends one signal-run entry to docs/SOUL.md. Creates the file with
    a header on first use. Only ever called for a signal-run, never for a
    quiet run (see Global Constraints)."""
    entry = (
        f"\n## {date.today().isoformat()} - self-improve run\n\n"
        f"**Trigger:** {trigger}\n"
        f"**Archon vystup:**\n```\n{archon_output_tail}\n```\n"
        f"**Rozhodnutie:** _(caka na review)_\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# SOUL - self-improve loop log\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)
