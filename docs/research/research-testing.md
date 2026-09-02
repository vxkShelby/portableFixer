# Test Suite Quality Audit — PortableFix

Scope: `tests/*.py` (26 files, 190 tests), `pyproject.toml`, `requirements-dev.txt`,
`README.md` (dev/testing section), git history (`git log`) for test-relevant commits,
`.github/` (absent), plus the `portablefix/*.py` modules under test where needed to
judge test quality: `executor.py`, `gui/main_window.py`, `updater.py`, `elevation.py`,
`models.py`. Read-only research; no files modified.

Repo is public: `origin` = `https://github.com/vxkShelby/portableFixer.git`.

---

## Severity summary

| Severity | Count | Findings |
|---|---|---|
| HIGH | 2 | F1 (no CI at all), F2 (crash workaround only half-documented) |
| MEDIUM | 3 | F3 (real-subprocess spawns concentrated exactly where crashes happen, incl. one outside the known files), F4 (QThread-destroyed-while-running is a live structural risk, not just bad luck), F5 (M01 catalog has no dedicated test) |
| LOW | 4 | F6 (`models.py` untested directly), F7 (`is_admin()` weak assertion + untested except-branch), F8 (catalog "locked pattern" brittleness), F9 (recurrence risk of a `isVisible()` bug class already fixed once) |
| Verified clean | 4 items | widget cleanup discipline, restore-point/subprocess mocking outside GUI/executor, `run_selected_actions()` always awaited, `isVisible()` fix is complete |

---

## Coverage matrix — `portablefix/*.py` vs `tests/test_*.py`

| Module | Test file | Status |
|---|---|---|
| `elevation.py` | `test_elevation.py` | present, but see F7 (weak) |
| `integrity.py` | `test_integrity.py` | present, solid |
| `paths.py` | `test_paths.py` | present, solid |
| `settings.py` | `test_settings.py` | present, solid |
| `restore_point.py` | `test_restore_point.py` | present, solid, subprocess mocked |
| `undo.py` | `test_undo.py` | present, solid |
| `models.py` | — | **missing**, see F6 |
| `module_engine.py` | `test_module_engine.py` | present, solid |
| `audit_log.py` | `test_audit_log.py` | present, solid |
| `executor.py` | `test_executor.py` | present, see F3/F4 (crash-prone file) |
| `report.py` | `test_report.py` | present, solid (incl. XSS-escaping test) |
| `gui/main_window.py` | `test_gui_main_window.py` | present, see F3/F4/F9 (crash-prone file) |
| `gui/style.py` | — | missing, but pure QSS string/dict constants — not an actual gap |
| `i18n.py` | `test_i18n.py` | present, solid |
| `updater.py` | `test_updater.py` | present, solid, see F3 (one real spawn) |
| `version.py` (1 line) | — | missing, trivial constant — not a gap |
| `scripts/generate_sha256sums.py` | `test_generate_sha256sums.py` | present, solid |
| `Modules/m01_diagnostics/actions.yaml` (15 actions) | — | **missing dedicated catalog test**, see F5 |
| `Modules/m02…m10,m12,m13` (10 catalogs) | `test_m0{2-9}_catalog.py`, `test_m10/12/13_catalog.py` | present, all current, see F8 for brittleness |
| `Modules/m11` (reporting, not a catalog) | — | correctly has no catalog test |

---

## HIGH severity

### F1 — No CI/automation of any kind; tests only run manually, on one machine, with a crash workaround only that operator knows
`.github/` does not exist anywhere in the repo. There is no GitHub Actions workflow, no
other CI config (`azure-pipelines.yml`, `.gitlab-ci.yml`, tox, nox, pre-commit hook
running pytest) anywhere. `git remote -v` confirms this is pushed to a public GitHub
repo (`vxkShelby/portableFixer`), which means:

- Every one of the 190 tests only ever runs when a human (or an interactive agent
  session) types the command by hand, on their own machine, in a state where they
  already know the `test_gui_main_window.py`/`test_executor.py` crash quirk.
- A PR from a first-time contributor, or `git clone` + `pytest tests/` by anyone new,
  gets **zero automated feedback** — not "flaky feedback," literally none. The 5
  recent "add N actions" catalog commits (`a4eb643`, `db7e83e`, `699724b`, `6dd8f81`,
  and the M13 aggressive-preset commits) all happened to land with correctly
  updated locked-pattern assertions (see F8), but nothing enforced that — it worked
  because one careful operator did it by hand every time.
- This compounds every other finding below: F2's documentation gap only matters
  *because* there's no CI to fall back on; F8's brittle assertions are a bigger risk
  precisely because a missed update has no automated safety net catching it before
  merge.

**Fix:** even a minimal `.github/workflows/tests.yml` that runs
`pytest tests/ --ignore=tests/test_gui_main_window.py --ignore=tests/test_executor.py`
on `windows-latest` (the two known-flaky files can be `continue-on-error: true` or
run separately with a retry step, matching the documented manual workaround) would
give every future contributor and PR real, if partial, signal. This project runs
PowerShell-spawning GUI tests, so it specifically needs a Windows runner, not the
GitHub Actions default Linux runner — worth calling out since it's an easy mistake.

### F2 — The crash workaround is documented for one file, not both, and doesn't mention the retry
`README.md:103-114` (Vývoj/Development section):

```
python -m pytest tests/ --ignore=tests/test_gui_main_window.py
python -m pytest tests/test_gui_main_window.py

GUI testy spúšťajú reálne PowerShell procesy; pri behu celého súboru
naraz sa môže objaviť prechodný natívny crash prostredia
(STATUS_STACK_BUFFER_OVERRUN) — nie je to chyba kódu, beh stačí
zopakovať alebo rozdeliť na menšie dávky.
```
(Translation: "GUI tests spawn real PowerShell processes; running the whole file at
once can trigger a transient native environment crash (STATUS_STACK_BUFFER_OVERRUN) —
this is not a code bug, just repeat the run or split it into smaller batches.")

So the README *does* document the crash — better than nothing, and better than what
the task brief assumed going in. But it has two concrete gaps relative to the pattern
this session has actually been using:

1. **`test_executor.py` isn't mentioned at all.** The two-command split only carves
   out `test_gui_main_window.py`. `test_executor.py` spawns real `powershell.exe`
   processes through the exact same `ActionRunner` QThread machinery (see F3) and,
   per the task brief, has needed the identical individual-run-with-retry treatment
   this session — but the README's own instructions would bundle it into the first
   `pytest tests/ --ignore=tests/test_gui_main_window.py` batch along with everything
   else. A contributor following the README to the letter still risks hitting the
   crash, with the text only explaining it for the file they weren't told to isolate.
2. **No mention of "retry once."** The README describes the crash as transient and
   suggests "repeat the run or split into smaller batches," which is directionally
   the same advice, but doesn't state the concrete workflow (run each of the two
   files as its own `pytest` invocation; if either crashes, just rerun that one
   invocation) as plainly as a contributor triaging a fresh STATUS_STACK_BUFFER_OVERRUN
   would need. It reads like a hint, not a runbook entry.

**Fix:** extend the same paragraph to name `test_executor.py` alongside
`test_gui_main_window.py`, and turn "repeat the run or split into smaller batches"
into the literal three-command sequence (main batch minus both files, then each of
the two files individually, retry-once-on-crash) this session has actually been
executing.

---

## MEDIUM severity

### F3 — Real `powershell.exe` spawns are concentrated in exactly the two crash-prone files, plus one more nobody has flagged
Grepping the whole `tests/` tree for real subprocess/PowerShell spawns (as opposed to
mocked ones) turns up exactly four call sites, three of them in the two files this
session already isolates:

- `tests/test_executor.py:45` — `test_action_runner_real_run_executes_powershell`:
  builds a real `ExecutionPlan`, real `ActionRunner(plan)`, `runner.start()` — no
  `subprocess.Popen` patch anywhere in this test.
- `tests/test_executor.py:118-127` — `test_action_runner_cancel_kills_process_and_reports_cancelled_code`:
  runs a real `Start-Sleep -Seconds 30` powershell.exe, then kills it mid-flight from
  the main thread via `runner.cancel()`.
- `tests/test_executor.py:130-139` — `test_action_runner_watchdog_kills_process_after_inactivity_timeout`:
  same real `Start-Sleep -Seconds 30` spawn, this time killed by the watchdog thread
  under a patched (0.2s) inactivity timeout — i.e. deliberately exercises the
  kill-a-running-child-process path under tight timing, repeatedly.
- `tests/test_updater.py:167-184` — `test_build_swap_script_parses_as_valid_powershell`:
  a **fourth, un-flagged** real `subprocess.run(["powershell", ...])` call, outside
  both of the files this session treats as crash-prone. It's a single one-shot spawn
  with no QThread/Qt widget involved (see F4 for why that distinction matters), which
  is presumably why it hasn't been observed to crash — but it means the "only these
  two files spawn real PowerShell" assumption baked into the workaround is not
  strictly true, and a future contributor debugging a flake in `test_updater.py`
  won't find this pattern documented anywhere (not in README, not in a comment on
  that test).

By contrast, `test_restore_point.py` (4 tests) and `test_audit_log.py` (3 tests) mock
`subprocess.run` consistently via `monkeypatch.setattr(subprocess, "run", ...)`
(`tests/test_restore_point.py:19,28,36,47`), and `test_updater.py`'s other 23 tests
mock `urllib.request.urlopen`/`urlretrieve` and `subprocess.Popen` throughout. So the
mocking discipline is **deliberate and consistent everywhere except these four call
sites** — this isn't sloppiness, it's a conscious choice to let `test_executor.py`
and (in one test) `test_updater.py` exercise the real PowerShell path end-to-end,
which is legitimate test design (you do want *something* proving the real subprocess
plumbing works) but it is also exactly the ingredient the crash needs.

**Not proposing removal of the real-spawn tests** — losing end-to-end coverage of
the actual `powershell.exe` invocation would be a real regression in what the suite
verifies (encoding, exit codes, cancel/kill, watchdog timeout all depend on the real
OS process boundary, not just Python-level mocks). Flagging as evidence for the
"why these two files" question, and naming `test_updater.py:167` as a fourth,
currently-invisible location doing the same thing.

### F4 — `ActionRunner` is a QThread parented to the live window with self-deleting cleanup; nothing structurally stops a future test from tearing it down mid-run
Traced in `portablefix/executor.py:68-84` and `portablefix/gui/main_window.py:672-673`:

```python
# executor.py
class ActionRunner(QThread):
    ...
    def __init__(self, plan, parent=None):
        super().__init__(parent)
        ...
        self.finished.connect(self.deleteLater)

# main_window.py
runner = ActionRunner(plan, parent=self)
self._runner = runner
```

`ActionRunner` is a genuine `QThread` subclass (not a plain `threading.Thread`), owned
by the `MainWindow` as a Qt child object, and it schedules its own `deleteLater()`
when it finishes. Additionally, `run()` spawns a *second*, plain `threading.Thread`
watchdog (`executor.py:94-105, 122-123`) that can call `self._process.kill()`
concurrently with the QThread's own run-loop and with `cancel()` being invoked from
the GUI/main thread — three threads (main, ActionRunner's run thread, watchdog
thread) all touching the same `subprocess.Popen` object across a single action.

Qt's own documentation is explicit that destroying a `QThread` while it is still
running is unsafe and can produce native-level failures, not just a Python
exception. I checked whether the test suite ever creates that condition:

- Every test that calls `window.run_selected_actions()` (22 call sites) except one
  (`tests/test_gui_main_window.py:390`, which checks nothing and starts nothing) is
  paired with a `qtbot.waitUntil(...)` or `qtbot.waitSignal(...)` that blocks the
  test function until the action's *observable side effect* (log file written,
  report generated, console text updated) has occurred — which only happens after
  `finished_with_code` has fired. So in the **current** suite, no test lets pytest-qt's
  automatic widget teardown (`qtbot.addWidget` cleanup) run while an `ActionRunner`
  is still mid-flight.
- This is good discipline, verified across the file — but it is discipline, not a
  guard rail. There is no fixture, no assertion helper, no lint rule enforcing "always
  wait for completion before the test ends." A future test that adds a
  `run_selected_actions()` call and forgets the matching `waitUntil` (easy to do —
  the pattern is repeated by hand in every single test, not centralized) would
  silently reintroduce exactly the QThread-destroyed-while-running condition that
  plausibly contributes to the observed `STATUS_STACK_BUFFER_OVERRUN` pattern.

**This is not a claim that this is the root cause** — the task framing is right that
the crash is being treated (correctly, on current evidence) as an external/native
Windows quirk, and nothing here proves otherwise. What this finding adds: the test
suite's *current* structure (real subprocess + real QThread + real Qt widget, dozens
of times per file, always-but-only-by-convention synchronized before teardown) is the
single part of the whole `tests/` tree that combines all three risk ingredients at
volume, which is a plausible explanation for why the crash clusters in exactly these
two files and nowhere else — and a small, cheap mitigation exists: a shared
`run_and_wait(window, qtbot, predicate)` test helper (or a `qtbot`-based
`waitSignal(runner.finished, ...)` captured centrally) would make "always wait before
teardown" structural instead of tribal, reducing (not eliminating) the chance of a
future test accidentally destroying a live QThread.

### F5 — `Modules/m01_diagnostics/actions.yaml` (15 actions) has no dedicated catalog test
Every other module directory (`m02` through `m10`, `m12`, `m13` — 10 of 11 catalog
dirs; `m11` is reporting-only per `README.md:29` and correctly has no catalog test)
has its own `tests/test_m0{N}_catalog.py` asserting an exact action count, exact
risk-level distribution, and module-specific content invariants (no `wmic`, no
password leakage, no interactive prompts, etc. — see F8). `m01_diagnostics` — the
*first* module, 15 actions — has no such file.

The only coverage m01 gets is generic/blanket:
- `tests/test_catalog_descriptions.py` — applies to all 12 loaded modules alike
  (every action has both descriptions, distinct sk/en labels, unique ids across the
  whole catalog set).
- `tests/test_module_engine.py:73-77` — `test_m01_actions_yaml_loads`:
  ```python
  def test_m01_actions_yaml_loads():
      module = load_module(...)
      assert module.module_id == "m01_diagnostics"
      assert len(module.actions) >= 5
      assert all(a.risk == RiskLevel.SAFE for a in module.actions)
  ```
  This is materially weaker than every other module's dedicated test: `>= 5` instead
  of an exact count (m01 actually has 15), no risk-distribution breakdown beyond "all
  SAFE" (true today, but unlike every other module's test this one wouldn't catch a
  count *regression*, e.g. someone accidentally deleting 8 of the 15 actions — the
  test would still pass as long as 5+ SAFE actions remain). It's also misplaced —
  every other module's version of this test lives in its own `test_m0{N}_catalog.py`
  file, this one is buried in `test_module_engine.py` alongside unrelated
  YAML-parsing unit tests, which is where a reviewer would least expect to find "is
  the M01 catalog structurally sound" coverage.

**Fix:** add `tests/test_m01_catalog.py` following the established per-module
template (exact count = 15, risk distribution, any M01-specific content invariants
worth locking — e.g. that diagnostic commands stay read-only/`SAFE` as the file
currently guarantees informally).

---

## LOW severity

### F6 — `portablefix/models.py` has no dedicated test file
`ActionDef.label(language)` and `ActionDef.description(language)`
(`portablefix/models.py:31-35`) contain real (if small) branching logic — a ternary
on `language == "en"` for each. There is no `tests/test_models.py`. This logic is
exercised *indirectly* by GUI tests (`test_action_checkbox_shows_description_as_tooltip`,
`test_language_toggle_flips_language_and_labels` in `test_gui_main_window.py`) and by
one assertion in `test_module_engine.py:30-31` (`action.label("sk")`/`action.label("en")`
on a single fixture action), but there is no direct, isolated unit test of `ActionDef`
itself — e.g. no test of `label()`/`description()` with an unrecognized language code
(falls through to the `sk` branch, unverified in isolation) or of `ModuleDef`'s
default `category`. Low severity because the logic is trivial and is transitively
covered, but it's the one core dataclass in the codebase with zero dedicated test.

### F7 — `is_admin()` assertion is type-only, and its failure branch is untested
`tests/test_elevation.py:6-7`:
```python
def test_is_admin_returns_bool():
    assert isinstance(is_admin(), bool)
```
`portablefix/elevation.py:4-8`:
```python
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False
```
The test only asserts the return *type*, never the actual value under a known
condition — reasonable given `is_admin()` wraps a real Windows API with no injected
seam, and the real answer depends on how the test runner itself is elevated (a
legitimate constraint, not sloppiness). But `relaunch_as_admin` right below it
(`test_elevation.py:10-24`) *does* patch `ctypes.windll.shell32.ShellExecuteW` to
verify behavior — the same technique could patch `IsUserAnAdmin` to assert
`is_admin()` returns `True`/`False` for mocked return values, and specifically to
exercise the `except OSError: return False` fallback branch, which currently has
**zero** test coverage in either direction.

**Fix:** two extra tests mirroring the `relaunch_as_admin` pattern —
`monkeypatch.setattr(ctypes.windll.shell32, "IsUserAnAdmin", lambda: 1)` /
`lambda: 0`, plus one that makes it raise `OSError` to confirm the `except` branch
returns `False`.

### F8 — Catalog "locked pattern" tests are exact-value/golden-file style, proven maintainable so far but with one notably more brittle outlier
Nearly every `test_m0{N}_catalog.py` file (10 of 11) hardcodes an exact action count
(`assert len(module.actions) == N`) and an exact per-risk-level count or full ID set,
e.g. `tests/test_m08_catalog.py:9-13,16-29,58-72` and `tests/test_m02_catalog.py:10-24`.
I cross-checked all 10 files' hardcoded counts against the actual `Modules/*/actions.yaml`
files and every one currently matches — no drift. Git history shows this pattern has
already been exercised for real: `a4eb643`, `db7e83e`, `699724b`, `6dd8f81` (each "add
5 actions to M0N") all landed with correctly updated counts, so in practice this has
not been "annoying," it's been a one-line, obvious, loud-failure touch-up each time.

The one meaningfully more brittle example is `tests/test_m13_catalog.py:16-38`:
```python
def test_m13_catalog_risk_distribution():
    ...
    assert by_risk[RiskLevel.SAFE] == ["debloat_list_installed"]
```
This is a full **ordered list** equality (not a count, not a set) for the SAFE
bucket — adding a second SAFE action to M13 in the "wrong" YAML position would fail
this test even though nothing about the module's actual correctness changed, which
is a stricter check than every other module's version of this same test (which use
`len(...)` or unordered `set(...)` for the equivalent assertion). Low severity, but
worth normalizing to `set(...)` like the rest of the suite for consistency and to
remove the accidental order-dependence.

**Also relevant:** this brittleness is only a *minor* annoyance today specifically
*because* a careful human has been updating both the YAML and the test in the same
commit every time (see F1) — without CI, there's nothing else catching a mismatch
before merge, so the maintenance burden this finding describes is real risk, not
just inconvenience, once more than one person touches these catalogs.

### F9 — A `QWidget.isVisible()` "trivially-passing test" bug class was already found and fixed once; nothing prevents recurrence
Git commit `fe279d6` ("fix: clear pending update on dismiss, fix trivially-passing
visibility tests") added `window.show()` to three tests in `test_gui_main_window.py`
that assert `.isVisible()` on a child widget, with this inline comment:
```python
window.show()  # isVisible() reflects the ancestor chain, so the top-level must be
                # shown (see test_restart_as_admin_button_visibility for the same pattern)
```
Before that fix, `assert window.update_banner.isVisible() is False`
(`test_update_banner_hidden_by_default` etc.) was a tautology: Qt's `isVisible()`
returns `False` for *any* widget whose top-level ancestor was never shown, regardless
of the widget's own visible/hidden state — so the test passed whether or not the
banner's actual show/hide logic worked at all.

I verified the fix's completeness: every current `isVisible()` assertion in the file
(`tests/test_gui_main_window.py:116,123,972,986,998`) is now correctly preceded by a
`.show()` call in the same test — **no live instance of this bug remains today.**
But the only thing preventing recurrence is a code comment pointing at one specific
prior test as precedent; there's no shared assertion helper (e.g. a
`assert_visible(widget)` that calls `.window().show()` first if needed) and no note
in a CONTRIBUTING-style doc warning a future contributor writing a new visibility
test about this exact Qt gotcha. Low severity since it's currently fully fixed, but
worth hardening given it has already bitten this codebase once.

---

## Verified clean

- **Widget cleanup discipline is airtight in `test_gui_main_window.py`:** all 51
  `MainWindow(...)` instantiations are immediately followed by `qtbot.addWidget(...)`
  (51/51, no gaps) — no leaked/unregistered top-level widgets found.
- **`run_selected_actions()` is always awaited before test end**, with one harmless
  exception (`tests/test_gui_main_window.py:390`, which checks nothing and starts no
  async work) — see F4 for why this matters and why it's still worth hardening
  structurally rather than trusting it to hold forever.
- **`restore_point.py` and `audit_log.py` tests mock `subprocess`/file I/O
  consistently** (`tests/test_restore_point.py:19,28,36,47`) — no real
  `Checkpoint-Computer` calls anywhere in the suite.
- **The `isVisible()` bug class (F9) has zero live instances** as of the current
  `test_gui_main_window.py` — the fix in `fe279d6` covers every current assertion
  site.
- **No `assert True`/no-op/smoke-only tests found** anywhere in `tests/*.py` — grepped
  for common weak-assertion idioms (`assert True`, `assert 1`, bare `pass` after a
  call, "doesn't crash"/"no exception" comments) across all 26 files, found none.
  Assert-to-test ratios are consistently 1.3–3 asserts/test across the suite (see
  raw counts below), with no file standing out as unusually thin except the two
  already covered by F5/F7.
- **`pytest==8.3.2` / `pytest-qt==4.4.0`** (`requirements-dev.txt`) against
  **`PySide6==6.7.2`** (`requirements.txt`) — current, compatible versions; not a
  stale-dependency contributor to the crash.

Raw per-file test/assert counts (for reference, no file flagged beyond F5/F7):
`test_audit_log.py` 3/9, `test_catalog_descriptions.py` 4/6, `test_elevation.py` 2/6,
`test_executor.py` 11/20, `test_generate_sha256sums.py` 2/3, `test_gui_main_window.py`
50/102, `test_i18n.py` 5/6, `test_integrity.py` 6/6, `test_m02_catalog.py` 7/18,
`test_m03_catalog.py` 4/11, `test_m04_catalog.py` 5/14, `test_m05_catalog.py` 4/15,
`test_m06_catalog.py` 4/11, `test_m07_catalog.py` 4/9, `test_m08_catalog.py` 5/13,
`test_m09_catalog.py` 4/12, `test_m10_catalog.py` 3/6, `test_m12_catalog.py` 3/6,
`test_m13_catalog.py` 5/10, `test_module_engine.py` 14/17, `test_paths.py` 4/6,
`test_report.py` 6/24, `test_restore_point.py` 4/8, `test_settings.py` 4/4,
`test_undo.py` 3/5, `test_updater.py` 24/28.

---

## pytest configuration

`pyproject.toml` in full:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```
No `pytest.ini`, no `conftest.py` anywhere in the repo. `pythonpath = ["."]` is the
only configuration — no markers, no `--timeout`, no `-p no:cacheprovider`, no warning
filters, no `addopts`. `qtbot`/`qapp` fixtures come entirely from `pytest-qt`'s own
plugin defaults (a single session-scoped `QApplication`), which is standard and not
itself a problem, but it also means there is no repo-level hook available to add
crash-mitigation tooling (e.g. `pytest-timeout` to fail a hung test instead of
blocking forever, or `pytest-forked`/`pytest-xdist --forked` to isolate each test's
native crash from the rest of the run) — neither package is in `requirements-dev.txt`.
Not raising this as its own finding since no test currently hangs, but it's a cheap,
available lever if the `STATUS_STACK_BUFFER_OVERRUN` pattern needs a more automated
mitigation than "run these two files separately by hand."

---

## Files read

- `tests/test_audit_log.py`, `test_catalog_descriptions.py`, `test_elevation.py`,
  `test_executor.py`, `test_generate_sha256sums.py`, `test_gui_main_window.py`
  (in full, 1170 lines), `test_i18n.py`, `test_integrity.py`, `test_m02_catalog.py`
  through `test_m10_catalog.py`, `test_m12_catalog.py`, `test_m13_catalog.py`,
  `test_module_engine.py`, `test_paths.py`, `test_report.py`, `test_restore_point.py`,
  `test_settings.py`, `test_undo.py`, `test_updater.py`
- `portablefix/executor.py`, `portablefix/gui/main_window.py` (targeted sections),
  `portablefix/gui/style.py` (header), `portablefix/models.py`, `portablefix/elevation.py`,
  `portablefix/updater.py`, `portablefix/module_engine.py` (via its tests)
- `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `README.md`
- `Modules/*/actions.yaml` (action counts only, via `grep -c`)
- `git log`, `git show fe279d6`, `git remote -v` (history/provenance checks, no
  working-tree changes)
- Confirmed absent: `.github/` (any file), `pytest.ini`, `tests/conftest.py`,
  `CONTRIBUTING.md`
