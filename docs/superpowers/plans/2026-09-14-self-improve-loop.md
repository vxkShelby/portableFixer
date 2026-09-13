# Self-improve Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dev-side, signal-gated self-improve loop to PortableFix: a git hook detects real problems (failing tests, repeated fix commits, new crash-log entries), hands them to a local Archon CLI workflow that investigates/fixes/opens a PR in its own isolated worktree, and a growing `docs/SOUL.md` log records what happened. Never touches the shipped app; never auto-merges.

**Architecture:** `.githooks/post-commit` fires `scripts/self_improve.py` in the background after every commit. That script is pure signal-detection logic (`scripts/self_improve_lib.py`, unit tested) plus a thin orchestrator that calls `pytest`/`git log`/`archon workflow run` via subprocess. If no signal, it logs quietly and exits. If a signal fires, it invokes the custom `.archon/workflows/self-improve.yaml` workflow (Archon isolates the run into its own git worktree, loops fixes against `pytest` up to 5 iterations, and has the agent open a PR itself via `gh pr create` — Archon never merges).

**Tech Stack:** Python 3.12 (stdlib only: `subprocess`, `json`, `re`, `collections.Counter`, `pathlib`, `datetime`), Archon CLI (external tool, installed once per dev machine), existing `pytest` test suite, `git`, `gh` CLI (already used in this repo for releases).

**Spec:** `docs/superpowers/specs/2026-09-13-self-improve-loop-design.md`

## Global Constraints

- **Dev-side only.** Nothing in this plan touches `portablefix/`, `main.py`, or anything that ships inside `PortableFix.exe`. All new files live under `scripts/`, `.archon/`, `.githooks/`, `docs/`.
- **Never auto-merge.** The `create-pr` workflow node only ever runs `gh pr create`. Never `gh pr merge`, never a push directly to `main`.
- **Max 5 Archon iterations** per signal-run: `max_iterations: 5` combined with `until_bash: "pytest --tb=no -q"` in the workflow YAML (Task 3).
- **`docs/SOUL.md` is written ONLY on a signal-run** (Archon actually got called). A quiet run (no signal found) writes to `Logs/self_improve.log` only, never to `SOUL.md`.
- **The git hook never runs in CI** — it is a local-machine-only convenience. Nothing in this plan touches CI config.
- **No visible windows / no blocking the terminal.** The hook backgrounds `scripts/self_improve.py` so `git commit` returns immediately, and nothing pops a visible console window (matches this project's existing convention — see `Logs/` background-process patterns already in the codebase).
- **Archon's own telemetry is disabled** via `ARCHON_TELEMETRY_DISABLED=1` wherever the hook runs (documented in Task 5's setup note).

---

### Task 1: Verify Archon CLI installation and workflow-run syntax

**Files:**
- Create: `docs/superpowers/specs/2026-09-13-archon-cli-findings.md`

**Interfaces:**
- Consumes: nothing from earlier tasks (this is the first task).
- Produces: a confirmed, working local Archon CLI install, and a findings doc later tasks can cite. No code interfaces — this task is a verification spike embedded in the plan, not a TDD task (there is nothing to unit-test about whether an external CLI is installed).

This task exists because the spec's Archon CLI mechanics section was corrected once already (2026-09-14) based on reading Archon's real documentation, but nobody has actually run `archon doctor` on THIS machine yet. Do that before writing code that assumes it works.

- [ ] **Step 1: Install the Archon CLI**

On Windows, run in PowerShell:

```powershell
irm https://archon.diy/install.ps1 | iex
```

- [ ] **Step 2: Confirm Claude Code is reachable**

The Archon binary does not bundle Claude Code. Confirm `claude` is already on PATH (it is — this repo's own sessions run through it):

```bash
which claude
```

If Archon's own doctor check (next step) can't find it, set `CLAUDE_BIN_PATH` to the path `which claude` printed, or add `assistants.claude.claudeBinaryPath: <path>` to `~/.archon/config.yaml`.

- [ ] **Step 3: Run `archon doctor` from the repo root**

```bash
cd "C:\USB Fixer"
archon doctor
```

Expected: exit code 0, output confirms a Claude provider was detected with no API key required (subscription-based, via the local `claude` binary).

- [ ] **Step 4: Confirm workflow-run syntax with a harmless read-only call**

```bash
archon workflow list
```

Expected: a list of built-in workflow names is printed (confirms the CLI and provider wiring both work end-to-end, without touching any code).

- [ ] **Step 5: Write the findings doc**

Create `docs/superpowers/specs/2026-09-13-archon-cli-findings.md` with this exact structure, filled in with what you actually observed in Steps 1-4:

```markdown
# Archon CLI — verified on this machine (2026-09-14)

**Install method used:** <exact command that worked>
**Archon version:** <output of `archon --version` or equivalent>
**`archon doctor` result:** <exit code, and whether Claude provider was
detected without an API key>
**`archon workflow list` output:** <paste the list, or a summary if long>

## Deviations from the spec's assumptions

<If `archon doctor` or `archon workflow list` behaved differently than
docs/superpowers/specs/2026-09-13-self-improve-loop-design.md's "Archon
CLI — overené fakty" section describes, write exactly what differs
here. If everything matched, write "No deviations — matches the spec
section verbatim.">
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-09-13-archon-cli-findings.md
git commit -m "docs: record verified Archon CLI install and workflow-list output

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

**If Step 5 found deviations:** stop here and flag it — Tasks 3 and 4 below assume the spec's documented `archon workflow run <name> "<message>"` syntax and `.archon/workflows/<name>.yaml` file format are correct. A real deviation means those tasks' code needs to change before implementing them; don't silently implement against the wrong syntax.

---

### Task 2: `scripts/self_improve_lib.py` — pure signal-detection logic

**Files:**
- Create: `scripts/__init__.py` (empty — makes `scripts/` an importable package; check it doesn't already exist first)
- Create: `scripts/self_improve_lib.py`
- Test: `tests/test_self_improve_lib.py`

**Interfaces:**
- Consumes: nothing (pure stdlib logic, no dependency on Task 1).
- Produces (used by Task 4's orchestrator):
  - `count_pytest_failures(pytest_stdout: str) -> int`
  - `parse_fix_commit_files(git_log_output: str) -> list[str]`
  - `find_repeated_fix_file(fix_commit_files: list[str], min_repeats: int = 3) -> str | None`
  - `read_new_crash_log_entries(path: Path, last_offset: int) -> tuple[str, int]`
  - `has_signal(pytest_fail_count: int, repeated_fix_file: str | None, new_crash_text: str) -> bool`
  - `load_state(path: Path) -> dict`
  - `save_state(path: Path, state: dict) -> None`
  - `append_soul_entry(path: Path, trigger: str, archon_output_tail: str) -> None`

- [ ] **Step 1: Check whether `scripts/__init__.py` already exists**

```bash
ls scripts/__init__.py 2>/dev/null || echo "missing"
```

If missing, create it as an empty file.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_self_improve_lib.py`:

```python
from pathlib import Path

from scripts.self_improve_lib import (
    append_soul_entry,
    count_pytest_failures,
    find_repeated_fix_file,
    has_signal,
    load_state,
    parse_fix_commit_files,
    read_new_crash_log_entries,
    save_state,
)


def test_count_pytest_failures_parses_failed_count():
    assert count_pytest_failures("3 failed, 490 passed in 12.3s") == 3


def test_count_pytest_failures_zero_when_all_passed():
    assert count_pytest_failures("493 passed in 10.1s") == 0


def test_parse_fix_commit_files_extracts_first_file_per_fix_commit():
    git_log_output = (
        "fix: race condition in executor\n"
        "portablefix/executor.py\n\n"
        "feat: add button\n"
        "portablefix/gui/main_window.py\n\n"
        "fix: race condition again\n"
        "portablefix/executor.py\n"
    )
    assert parse_fix_commit_files(git_log_output) == [
        "portablefix/executor.py",
        "portablefix/executor.py",
    ]


def test_find_repeated_fix_file_returns_file_at_threshold():
    files = ["a.py", "a.py", "a.py", "b.py"]
    assert find_repeated_fix_file(files, min_repeats=3) == "a.py"


def test_find_repeated_fix_file_returns_none_below_threshold():
    files = ["a.py", "a.py", "b.py"]
    assert find_repeated_fix_file(files, min_repeats=3) is None


def test_read_new_crash_log_entries_returns_only_new_text(tmp_path):
    log = tmp_path / "crash.log"
    log.write_text("first crash\n", encoding="utf-8")
    _, offset_after_first = read_new_crash_log_entries(log, 0)

    with log.open("a", encoding="utf-8") as f:
        f.write("second crash\n")

    new_text, new_offset = read_new_crash_log_entries(log, offset_after_first)

    assert new_text == "second crash\n"
    assert new_offset > offset_after_first


def test_read_new_crash_log_entries_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.log"
    text, offset = read_new_crash_log_entries(missing, 5)
    assert text == ""
    assert offset == 5


def test_has_signal_true_when_any_source_fires():
    assert has_signal(1, None, "") is True
    assert has_signal(0, "a.py", "") is True
    assert has_signal(0, None, "crash!") is True


def test_has_signal_false_when_nothing_fires():
    assert has_signal(0, None, "") is False
    assert has_signal(0, None, "   ") is False


def test_state_round_trip(tmp_path):
    state_file = tmp_path / "state.json"
    save_state(state_file, {"crash_log_offset": 42})
    assert load_state(state_file) == {"crash_log_offset": 42}


def test_load_state_defaults_when_missing(tmp_path):
    state_file = tmp_path / "missing.json"
    assert load_state(state_file) == {"crash_log_offset": 0}


def test_append_soul_entry_creates_file_with_header(tmp_path):
    soul = tmp_path / "SOUL.md"
    append_soul_entry(soul, "2 failing tests", "PR: https://github.com/x/y/pull/1")
    content = soul.read_text(encoding="utf-8")
    assert "self-improve loop log" in content
    assert "2 failing tests" in content
    assert "pull/1" in content


def test_append_soul_entry_appends_not_overwrites(tmp_path):
    soul = tmp_path / "SOUL.md"
    append_soul_entry(soul, "first trigger", "output1")
    append_soul_entry(soul, "second trigger", "output2")
    content = soul.read_text(encoding="utf-8")
    assert "first trigger" in content
    assert "second trigger" in content
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_self_improve_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.self_improve_lib'`

- [ ] **Step 4: Write the implementation**

Create `scripts/self_improve_lib.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_self_improve_lib.py -v`
Expected: PASS (13 tests)

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/self_improve_lib.py tests/test_self_improve_lib.py
git commit -m "feat: add pure signal-detection logic for self-improve loop

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `.archon/workflows/self-improve.yaml` — the Archon workflow

**Files:**
- Create: `.archon/workflows/self-improve.yaml`

**Interfaces:**
- Consumes: Task 1's confirmed Archon CLI syntax (if Task 1 found deviations, adjust this YAML accordingly before writing it verbatim below).
- Produces: the workflow name `self-improve`, invoked by Task 4's orchestrator as `archon workflow run self-improve "<trigger text>"`.

There is no unit test for a YAML file consumed by an external CLI. Verification is manual (Step 2 below).

- [ ] **Step 1: Create the workflow file**

Create `.archon/workflows/self-improve.yaml`:

```yaml
name: self-improve
description: Preskuma nahlaseny signal, opravi ho, overi testami, otvori PR. Nikdy nemerguje.

nodes:
  - id: investigate-and-fix
    loop:
      prompt: |
        Lokalny signal-gatherer skript nasiel tento problem v repo:

        $ARGUMENTS

        Najdi root cause, oprav ho. Po kazdej zmene spusti `pytest`.
        Drz zmeny minimalne, drz sa existujucich konvencii repo (over
        CLAUDE.md alebo existujuci kod pred pisanim noveho stylu).
      max_iterations: 5
      until_bash: "pytest --tb=no -q"
      fresh_context: false

  - id: create-pr
    depends_on: [investigate-and-fix]
    prompt: |
      Commitni zmeny (jednoriadkova imperativna sprava, fix:/feat:
      prefix podla stylu `git log` tohto repo). Pushni branch a otvor
      pull request cez `gh pr create` proti `main`, telo PR nech
      sumarizuje aky signal to spustil a co sa zmenilo. Nemerguj ho -
      merge je vzdy manualny krok cloveka.
```

- [ ] **Step 2: Manually verify Archon picks it up**

```bash
cd "C:\USB Fixer"
archon workflow list
```

Expected: `self-improve` appears in the printed list, with the description above.

- [ ] **Step 3: Commit**

```bash
git add .archon/workflows/self-improve.yaml
git commit -m "feat: add self-improve Archon workflow

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `scripts/self_improve.py` — orchestrator

**Files:**
- Create: `scripts/self_improve.py`
- Test: `tests/test_self_improve_orchestrator.py`
- Modify: `.gitignore` (add `docs/.self_improve_state.json` if not already covered by a broader pattern)

**Interfaces:**
- Consumes: every function from Task 2's `scripts/self_improve_lib.py`; the workflow name `self-improve` from Task 3.
- Produces: `main()` — the entry point Task 5's git hook calls via `python scripts/self_improve.py`.

- [ ] **Step 1: Check `.gitignore` for existing `Logs/` coverage**

```bash
grep -n "^Logs" .gitignore
```

If `Logs/` (or `Logs/*`) is already ignored, nothing to add there. Either way, add this line to `.gitignore` if not already present:

```
docs/.self_improve_state.json
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_self_improve_orchestrator.py`:

```python
from unittest.mock import MagicMock, patch

from scripts import self_improve


def _fake_completed(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


@patch("scripts.self_improve.subprocess.run")
def test_gather_signal_detects_failing_tests(mock_run, tmp_path, monkeypatch):
    monkeypatch.setattr(self_improve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_improve, "CRASH_LOG", tmp_path / "Logs" / "crash.log")
    monkeypatch.setattr(self_improve, "STATE_FILE", tmp_path / "docs" / ".self_improve_state.json")
    mock_run.side_effect = [
        _fake_completed("2 failed, 100 passed in 5.0s"),  # pytest
        _fake_completed(""),  # git log
    ]

    signal_found, trigger, _ = self_improve.gather_signal()

    assert signal_found is True
    assert "2 failing test(s)" in trigger


@patch("scripts.self_improve.subprocess.run")
def test_gather_signal_quiet_when_nothing_found(mock_run, tmp_path, monkeypatch):
    monkeypatch.setattr(self_improve, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(self_improve, "CRASH_LOG", tmp_path / "Logs" / "crash.log")
    monkeypatch.setattr(self_improve, "STATE_FILE", tmp_path / "docs" / ".self_improve_state.json")
    mock_run.side_effect = [
        _fake_completed("100 passed in 5.0s"),
        _fake_completed(""),
    ]

    signal_found, trigger, _ = self_improve.gather_signal()

    assert signal_found is False
    assert trigger == "none"


@patch("scripts.self_improve.save_state")
@patch("scripts.self_improve.append_soul_entry")
@patch("scripts.self_improve.run_archon")
@patch("scripts.self_improve.gather_signal")
def test_main_calls_archon_and_logs_soul_only_on_signal(
    mock_gather, mock_archon, mock_append_soul, mock_save_state
):
    mock_gather.return_value = (True, "2 failing test(s)", 42)
    mock_archon.return_value = "PR: https://github.com/x/y/pull/1"

    self_improve.main()

    mock_archon.assert_called_once_with("2 failing test(s)")
    mock_append_soul.assert_called_once()
    mock_save_state.assert_called_once_with(self_improve.STATE_FILE, {"crash_log_offset": 42})


@patch("scripts.self_improve.append_soul_entry")
@patch("scripts.self_improve.run_archon")
@patch("scripts.self_improve.gather_signal")
def test_main_skips_archon_and_soul_when_quiet(mock_gather, mock_archon, mock_append_soul):
    mock_gather.return_value = (False, "none", 10)

    self_improve.main()

    mock_archon.assert_not_called()
    mock_append_soul.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_self_improve_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.self_improve'`

- [ ] **Step 4: Write the implementation**

Create `scripts/self_improve.py`:

```python
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
        ["pytest", "--tb=no", "-q"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    fail_count = count_pytest_failures(pytest_result.stdout)

    git_result = subprocess.run(
        ["git", "log", "--name-only", "--pretty=format:%s", f"-{FIX_COMMIT_WINDOW}"],
        cwd=REPO_ROOT, capture_output=True, text=True,
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


def run_archon(trigger: str) -> str:
    result = subprocess.run(
        ["archon", "workflow", "run", "self-improve", trigger],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.stdout


def main() -> None:
    signal_found, trigger, new_offset = gather_signal()
    if not signal_found:
        _log("quiet run, no signal")
        return

    _log(f"signal found: {trigger}")
    archon_stdout = run_archon(trigger)

    append_soul_entry(SOUL_FILE, trigger, archon_stdout.strip()[-500:])
    save_state(STATE_FILE, {"crash_log_offset": new_offset})
    _log("done, archon output tail logged to SOUL.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_self_improve_orchestrator.py -v`
Expected: PASS (4 tests)

If imports fail with `ModuleNotFoundError: No module named 'scripts'` when running from repo root, check how `tests/test_diagnostics.py` manages to `import portablefix` successfully (likely a `pyproject.toml`/`pytest.ini` rootdir setting, or an installed editable package) and mirror the same mechanism for `scripts` — do not invent a different import mechanism than what the rest of the repo already uses.

- [ ] **Step 6: Run the full test suite**

Run: `pytest`
Expected: all tests pass, including the new ones, with no regressions elsewhere.

- [ ] **Step 7: Commit**

```bash
git add scripts/self_improve.py tests/test_self_improve_orchestrator.py .gitignore
git commit -m "feat: add self-improve orchestrator wiring signal detection to Archon

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `.githooks/post-commit` hook + setup documentation

**Files:**
- Create: `.githooks/post-commit`
- Modify: `README.md` (add a short "Dev tooling" section — check it exists and read its current structure first; if there's a more appropriate existing docs file for contributor setup steps, e.g. a CONTRIBUTING.md, use that instead)

**Interfaces:**
- Consumes: `scripts/self_improve.py` from Task 4 (calls it as a subprocess).
- Produces: nothing consumed by later tasks — this is the last task.

No automated test is possible for a shell hook that backgrounds a process (there's nothing meaningful to assert against in a unit test — verification is manual per Step 3).

- [ ] **Step 1: Create the hook**

Create `.githooks/post-commit`:

```sh
#!/bin/sh
# Runs the signal-gated self-improve loop in the background so `git commit`
# never waits on it. Output goes to Logs/self_improve.log, not the terminal
# (see scripts/self_improve.py). Never runs in CI - this is a local-only
# convenience hook, activated per-machine via `git config core.hooksPath`.
ARCHON_TELEMETRY_DISABLED=1 python scripts/self_improve.py >> Logs/self_improve.log 2>&1 &
disown
```

Make it executable:

```bash
chmod +x .githooks/post-commit
```

- [ ] **Step 2: Add the setup note**

Read `README.md` first to see its current structure and heading style, then add a short section (matching the existing heading level/style) along these lines:

```markdown
## Dev tooling: self-improve loop

One-time setup per clone, to enable the local self-improve git hook
(see `docs/superpowers/specs/2026-09-13-self-improve-loop-design.md`):

```bash
git config core.hooksPath .githooks
```

After every commit, this checks for real signals (failing tests,
repeated fix commits, new crash.log entries) and — only if it finds
one — calls a local Archon CLI workflow to investigate, fix, and open
a PR. It never merges anything and never runs in CI. Requires the
Archon CLI installed locally (`archon doctor` should pass — see
`docs/superpowers/specs/2026-09-13-archon-cli-findings.md`).
```

- [ ] **Step 3: Manually verify the hook doesn't block or pop a window**

```bash
git config core.hooksPath .githooks
touch /tmp/self_improve_smoke_test
git add /tmp/self_improve_smoke_test 2>/dev/null || echo "smoke test file outside repo, skip add"
```

Simpler manual check: make any trivial commit in the repo (e.g. this task's own commit in Step 4 below) and confirm:
1. `git commit` returns to the prompt immediately (not blocked for however long `pytest` + a possible Archon run would take).
2. No new visible console/terminal window appears.
3. `Logs/self_improve.log` gets a new line appended shortly after.

- [ ] **Step 4: Commit**

```bash
git add .githooks/post-commit README.md
git commit -m "feat: add post-commit hook wiring the self-improve loop

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

At this point, committing this task's own change will be the very first real test of the hook end-to-end (assuming `core.hooksPath` was already set in Step 3) — check `Logs/self_improve.log` afterward to confirm it ran.

---

## Self-Review Notes

- **Spec coverage:** all four spec components (git hook, signal gatherer, Archon workflow YAML, SOUL.md writer) map to Tasks 2-5; Task 1 covers the spec's "Zostávajúce riziká" verification requirement.
- **No auto-merge anywhere:** confirmed in Task 3's workflow YAML (`create-pr` node explicitly says "Nemerguj ho") and Global Constraints.
- **SOUL.md only on signal:** enforced in Task 4's `main()` — `append_soul_entry` is only reachable past the `if not signal_found: return` guard, and the test `test_main_skips_archon_and_soul_when_quiet` locks this in.
- **Type/signature consistency checked:** `append_soul_entry`'s 3-arg signature (path, trigger, archon_output_tail) is identical between Task 2's implementation, Task 2's tests, and Task 4's orchestrator call — no drift between tasks.
