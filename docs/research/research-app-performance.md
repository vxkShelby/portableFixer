# PortableFix — Own-App Performance Audit (startup, memory, I/O)

Scope: `main.py`, `portablefix/module_engine.py`, `portablefix/paths.py`,
`portablefix/integrity.py`, `portablefix/gui/main_window.py`, `portablefix/executor.py`,
`portablefix/updater.py`, plus `portablefix/audit_log.py`, `portablefix/undo.py`,
`portablefix/report.py`, `portablefix/restore_point.py`, `portablefix/settings.py`,
`portablefix/elevation.py`, `portablefix/i18n.py`, `portablefix/models.py`,
`build/PortableFix.spec`, `scripts/generate_sha256sums.py`, `Data/SHA256SUMS` as needed
to trace behavior. This audits the app's own speed/footprint as software, not the
diagnostics it runs against the target machine. Read-only research; no files modified.

Two numbers ground everything below, measured on the actual build artifact in this
checkout (`App/PortableFix.exe`, a PyInstaller `--onefile` build per `build/PortableFix.spec`):

- **File size: 48,303,384 bytes (~46 MB).**
- **SHA-256 of that file, on this dev machine's fast local disk: ~363 ms** (`Get-FileHash`,
  3-run average not needed — single measurement was stable). On a real USB 2.0 flash
  drive at a realistic 15–30 MB/s sequential-read speed (cheap sticks are often at the
  low end of that, sometimes below it), reading the same 46 MB takes on the order of
  **1.5–3+ seconds by I/O alone**, before any hashing CPU time — and the CPU side gets
  worse too on the old/slow machines this tool is meant to run from.
- `powershell.exe` (Windows PowerShell 5.1, what this app shells out to) cold-starts in
  **~190 ms average** on this dev machine (3-run `Measure-Command` of a no-op
  `-NoProfile -NonInteractive -Command "1"`). Old target hardware will be slower.

---

## Severity counts

| Severity | Count |
|---|---|
| CRITICAL | 0 |
| HIGH | 2 |
| MEDIUM | 3 |
| LOW | 3 |
| **Total actionable findings** | **8** |

(Plus 9 "verified clean" confirmations at the end.)

---

## 1. Startup cost — what blocks the window from appearing

`main.py:16-54` runs everything below **synchronously, in order, before `window.show()`
on line 51**. Nothing here is threaded except the update check, which is fired from
inside `MainWindow.__init__` (after the window object exists, but its background thread
doesn't block `show()` either way — see Verified Clean #7).

```
main.py:18   QApplication(sys.argv)                       — onefile bootloader cost lives here
main.py:25   resolve_writable_base_dir(raw_base_dir)       — trivial (mkdir + probe write + delete)
main.py:26   load_settings(base_dir)                       — trivial (one small JSON read)
main.py:35   check_integrity(raw_base_dir)                 — HIGH, see 1.1
main.py:44   MainWindow(...)  -> load_all_modules(...)     — trivial, see Verified Clean #1
main.py:51   window.show()
```

### 1.1 — HIGH: `check_integrity` does a full 46 MB SHA-256 pass, synchronously, before the window exists

`main.py:35` — `mismatches = check_integrity(raw_base_dir)` runs on the GUI thread with
no threading, no progress indication, and nothing rendered yet (this call happens 9
lines before `MainWindow(...)` is even constructed).

`portablefix/integrity.py:27-37` (`check_integrity`) reads `Data/SHA256SUMS`
(`scripts/generate_sha256sums.py` generates it) and calls `compute_sha256` (lines 5-10:
a plain chunked `f.read(65536)` loop) on **every** listed file, unconditionally, every
launch — no mtime/size short-circuit, no caching.

`Data/SHA256SUMS` currently lists 13 files: 12 tiny `Modules/*/actions.yaml` (~92 KB
total — trivial) and **one** `App/PortableFix.exe` at 46 MB, which dominates the whole
check by a wide margin (>99.8% of the bytes hashed). So in practice "hash every file in
the manifest" is really "re-read the app's own 46 MB onefile binary in full, from
whatever medium it's running on."

This is not "hundreds of small files" (that fear from the task brief doesn't hold for
the current manifest) — it's worse in a different way: it's one large, unavoidable,
sequential read of the exact file the OS/PyInstaller bootloader just finished reading
moments earlier to self-extract (see §2). Combined with §2's already-measured
1–3 s onefile overhead, a technician on a cheap USB 2.0 stick plugged into an old
machine can plausibly wait **3–7+ seconds of blank/no window** before anything is on
screen — for a tool whose whole pitch is "plug in and go" on hardware that's already
struggling.

**Fix (minimal, mirrors a pattern already in this codebase):** move `check_integrity`
off the synchronous path. `portablefix/updater.py`'s `UpdateCheckRunner(QThread)`
already does exactly this shape of thing correctly (background thread, signal back to
the GUI, `main_window.py:452-461`) — reuse that pattern: show the window immediately,
run the hash check on a `QThread` after `window.show()`, and pop the existing
`integrity_warning` `QMessageBox` when/if it finishes with mismatches. The catalog is
still usable (and if the exe were tampered with, it already ran — a synchronous
pre-launch check doesn't prevent execution, it only delays the window), so nothing
about the security value of the check is lost, only its place in the critical path.

### 1.2 — Module YAML loading: not a real cost (contrast case)

`portablefix/module_engine.py:14-52` (`load_module` / `load_all_modules`) parses 12
`actions.yaml` files (~92 KB total, 108 actions) with `yaml.safe_load` on plain
`read_text`. This is genuinely O(n) in file count/size like the integrity check, but n
is tiny — this is **not** a candidate for backgrounding; flagged only to make explicit
that the "is YAML loading a startup risk" question in the task brief is answered no,
for the same underlying reason `check_integrity` is answered yes (bytes moved, not
algorithmic complexity). See Verified Clean #1.

---

## 2. Onefile PyInstaller overhead — is it paid more than once per session?

`build/PortableFix.spec:19-38` confirms this is a `--onefile`-shaped build
(`EXE(pyz, a.scripts, a.binaries, a.datas, ..., runtime_tmpdir=None, ...)` — binaries
and datas baked directly into the single EXE, self-extracted to a temp dir at launch).
The task's own smoke-test already puts this at ~1–3 s per cold start; this audit's job
was to find out where that cost gets paid more than once.

### 2.1 — LOW: the onefile bundle embeds a redundant, unused copy of `Modules/` and `Data/`

`build/PortableFix.spec:8` — `datas=[('...\\Modules', 'Modules'), ('...\\Data', 'Data'),
('...\\portablefix.ico', '.')]` bundles `Modules/` and `Data/` **inside** the exe.

But `portablefix/paths.py:6-9` (`get_base_dir`) resolves paths for a frozen build as
`Path(sys.executable).resolve().parent.parent` — the real drive location next to
`App/` — and `main_window.py:75` loads modules from `assets_dir / "Modules"`, i.e. the
external drive path. Nothing in the codebase ever reads `sys._MEIPASS` (confirmed: no
match for `_MEIPASS` anywhere in `portablefix/`). So the `Modules`/`Data` copy embedded
in the onefile archive is dead weight — it gets self-extracted to the temp dir on every
launch and then never read from there.

In absolute terms this is small today (~94 KB, per `du`), so it's not meaningfully
adding to the 1–3 s onefile cost by itself — flagged as LOW because it's pure waste
that will silently get worse if `Modules/` grows (more actions, more modules) and
because it's a one-line fix: **drop `Modules` and `Data` from `datas=[...]` in the
`.spec` file** — they're already shipped as sibling folders on the USB drive and are
never read from the bundle.

### 2.2 — LOW: two self-relaunch paths pay the full onefile + integrity cost a second time in one session

Two flows spawn a **new cold process** of the same onefile exe rather than mutating the
running one:

- **Restart-as-admin:** `main_window.py:515-524` (`_on_restart_as_admin`) calls
  `elevation.relaunch_as_admin(sys.executable)` (`portablefix/elevation.py:11-13`,
  `ShellExecuteW(..., "runas", executable, ...)`), then `self.close()`. This is the only
  way to elevate on Windows (a running process can't elevate itself in place), so it's
  not a bug — but it means a technician who clicks "restart as admin" pays the full
  self-extraction **and** (today, per §1.1) the blocking 46 MB integrity hash **again**,
  back-to-back with the first launch.
- **Post-update relaunch:** `portablefix/updater.py:94-128` (`build_swap_script`) writes
  a PowerShell script that waits for the current PID to exit, swaps the exe, and ends
  with `Start-Process -FilePath "{old}"` — another cold onefile launch, but this is a
  once-per-update event, not a per-session one, so it matters far less.

Not proposing a mechanism change to elevation (Windows doesn't offer one) — noting this
because it's the clearest illustration of why §1.1's fix (background the integrity
check) has compounding value: it's not just one launch that gets faster, it's every
launch in a session that involves an elevation restart.

---

## 3. Per-action subprocess overhead — one `powershell.exe` per action, always serial

`portablefix/executor.py:9` — `POWERSHELL_PREFIX = ["powershell", "-NoProfile",
"-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]`. `ActionRunner.run()`
(`executor.py:107-120`) calls `subprocess.Popen(self._plan.argv, ...)` once per action —
no process reuse, no pooling.

Dispatch is strictly serial: `main_window.py:583-628` (`_run_next` /
`_dispatch_action`) pops one queued action, runs it via a single `self._runner`, and
only calls `_run_next()` again from `_on_action_finished` once the previous action's
`finished_with_code` signal has fired (`main_window.py:675-680`, `697`). Actions never
run concurrently.

### 3.1 — MEDIUM: no batching option for read-only SAFE actions costs real seconds on multi-action runs

Measured on this machine, a bare `powershell.exe -NoProfile -NonInteractive -Command
"1"` averages **~190 ms** cold-start overhead before any actual work happens (3-run
`Measure-Command`, PowerShell 5.1 — CLR/assembly load dominates this, `-NoProfile`
doesn't touch it). This is paid **on top of** whatever the action's own command takes.

The built-in `full_diagnostic` preset (`main_window.py:43-49`, `PRESETS` dict) queues
**17 actions**, and every one of them is `risk: SAFE` (verified against the YAML: all 17
IDs — `os_info` through `sec_uac_status` — resolve to `risk: SAFE` in
`Modules/m01_diagnostics/actions.yaml` and `Modules/m08_security/actions.yaml`). Clicking
that one preset button spawns **17 separate `powershell.exe` processes**, serially. At
this machine's measured ~190 ms/spawn that's ~3.2 s of pure process-spawn overhead
before counting any cmdlet work; on an old technician-target machine (slower disk,
slower CLR JIT, more AV-scan-on-exec interference) 400 ms–1 s per spawn is plausible,
putting pure overhead at **7–17 seconds** for one preset click.

**Fix, scoped to avoid a rearchitecture:** this doesn't need a general batching engine —
the executor's per-action live-streaming, per-action exit code, and per-action audit-log
entry are all real, used features that a naive "concatenate every command into one
PowerShell invocation" approach would break. A minimal, additive version: when a queued
run is entirely `SAFE` actions (the common "run a diagnostic sweep" case, and exactly
what `full_diagnostic` is), wrap the batch in one `powershell.exe` invocation that runs
each command in sequence separated by a `Write-Output "---PF:{action_id}---"` delimiter,
split the captured output back into per-action segments client-side, and still emit one
`finished_with_code`/audit-log entry per action from that split. Leave MODERATE/
DESTRUCTIVE/REQUIRES_REBOOT actions on today's one-process-per-action path unchanged,
since those already show confirmation dialogs and restore-point gating between them
(`main_window.py:613-628`) where per-action isolation matters more than speed.

---

## 4. Memory over a long, multi-batch session

The app is meant to stay open across many batches on one machine (diagnose, fix, verify,
repeat) — `run_id` is generated once per launch (`main.py:43`) and reused for every
batch until the app closes, so anything keyed by "for the life of this window" rather
than "for this batch" is worth checking for growth.

### 4.1 — HIGH: the console output widget is never capped or cleared — unbounded growth for the whole session

`main_window.py:309-312` constructs `self.console = QPlainTextEdit()` with no
`setMaximumBlockCount(...)` call anywhere in the file (confirmed: no match for that
method, nor for `console.clear()`, anywhere in `main_window.py`). `runner.output_line`
is wired straight to `self.console.appendPlainText` (`main_window.py:674`) for **every**
action, in **every** batch, for the life of the window — never trimmed.

This matters concretely for this app because several modules run commands that are
genuinely verbose (SFC/DISM-style scans live in `m04_integrity`, event-log dumps like
`eventlog_critical_7d` live in `m01_diagnostics`, driver/autoruns enumeration in
`m07_autoruns`/`m10_drivers`) — exactly the kind of multi-thousand-line output a
technician doing repeated repair passes across a long session will accumulate, with no
mechanism to release it short of closing the app. On the RAM-constrained old machines
this tool targets, an ever-growing `QPlainTextEdit` document is real, avoidable memory
pressure, and it also makes the widget itself slower to scroll/repaint as block count
grows.

**Fix:** one line, Qt-native, no custom logic needed —
`self.console.setMaximumBlockCount(5000)` (or similar) right after construction at
`main_window.py:311`. `QPlainTextEdit` drops old blocks from the top automatically once
the cap is set; full output for any individual action is still captured and preserved
separately in `runner.captured_output` → the audit log/report (`main_window.py:685-687`),
so nothing is lost from the parts of the app a technician would actually go back to —
only the live scrolling console (which nobody re-reads once an action is long done) gets
bounded.

### 4.2 — MEDIUM: `_undo_steps` accumulates for the whole app run, and every append triggers a full-file rewrite of the growing list

`main_window.py:87` — `self._undo_steps: list[str] = []` is set once in `__init__` and
**never reset** in `run_selected_actions()` (`main_window.py:567-581` resets `_queue`,
`_batch_results`, `_restore_point_attempted`, `_cancel_requested` — not `_undo_steps`).
So it grows across every batch in the session, matching the known design note in the
task brief.

The problem isn't the list itself (a few hundred short PowerShell one-liners is nothing
in memory) — it's that **every single successful undoable action rewrites the entire
file from scratch** with the whole accumulated-so-far list:
`main_window.py:695-696` — `self._undo_steps.append(action.undo_command)` immediately
followed by `undo.create_undo_script(self.state_dir, self.run_id,
steps=list(reversed(self._undo_steps)))`, and `undo.py:5-21` (`create_undo_script`) does
`path.write_text(...)` — a full overwrite, not an append, of `Backups/{run_id}/undo.ps1`.

Contrast with `audit_log.append_entry` (`portablefix/audit_log.py:39-43`), which opens
in `"a"` mode and writes exactly the one new line — the *correct* O(1)-per-entry
pattern, sitting right next to the O(n)-per-entry one. Action *k* in a session writes
O(k) bytes to `undo.ps1`; summed over a session of *n* undoable actions that's O(n²)
total bytes physically written to (typically) the USB drive. In absolute terms this
stays small even at the extreme (each step is one short PS line, so even 200 accumulated
steps means the last rewrite is only a few KB), so it won't meaningfully wear a modern
flash stick — flagged MEDIUM because it's a real, easily-avoidable quadratic pattern
sitting directly next to the linear pattern that does the same job correctly one
function away.

**Fix:** append the single new step to `undo.ps1` directly (open in `"a"` mode,
matching `audit_log.append_entry`'s pattern) instead of rewriting the full reversed list
every time; only the final ordering (steps applied in reverse) needs the full list, and
that could be handled by prepending. If `undo.py`'s reversed-order file format is worth
keeping simple, the smaller fix is just to stop calling `create_undo_script` on every
action and instead call it once, when the batch actually finishes (mirroring where
`report.generate_report` is already invoked, `main_window.py:592`).

### 4.3 — MEDIUM: `report.generate_report` re-reads and re-renders the *entire* session's audit history on every batch completion, synchronously on the GUI thread

`main_window.py:591-601` calls `report.generate_report(...)` synchronously, right on the
GUI thread, every time a batch's queue empties (`main_window.py:583-589`,
`_batch_active` goes false) — this blocks the UI (and delays the summary dialog) for as
long as this call takes.

`report.py:21-30` (`_read_audit_entries`) reads and JSON-parses **every line** of
`Logs/{run_id}.jsonl` — and since `run_id` is one value for the whole session
(`main.py:43`), that log accumulates every action from every batch run so far, including
each action's full captured stdout (`audit_log.py` stores `output` verbatim). Then
`build_report_data` (`report.py:33-65`) rebuilds the full `actions` list from scratch,
and `generate_report` (`report.py:174-189`) re-renders the whole HTML page
(`_render_html`, `report.py:135-171`, one `<div class="card">` per action ever run this
session) and re-serializes the whole thing to `json.dumps(data, indent=2)` — both
written to the **same** `{hostname}_{run_id}.html`/`.json` filenames every time (so it's
not accumulating extra files, just redoing growing work).

For a technician who runs several small batches in one sitting (a common real pattern:
check a few boxes, click Run, look at results, check a few more, click Run again), batch
*k*'s report-generation cost is proportional to the sum of all actions run in batches
1..*k*, not just batch *k*'s own actions — the same quadratic-total shape as §4.2, and
here it's compounded by also being a synchronous GUI-thread stall rather than a tiny
background file write.

**Fix:** two independent, small changes cover this — (a) move the
`report.generate_report` call onto a `QThread` (same pattern as `RestorePointRunner`/
`UpdateCheckRunner`) so a growing report doesn't stall the summary dialog from
appearing; (b) have `build_report_data` accept and append only the newly-finished
batch's entries instead of re-reading the whole `Logs/{run_id}.jsonl` file every time —
`main_window.py` already tracks `self._batch_results` per batch (`main_window.py:571`,
`688`), which is enough to avoid the full re-read.

---

## 5. USB-drive-specific I/O patterns

`Logs/`, `Reports/`, and `Backups/` all resolve under `state_dir`, which is the drive
root next to `App/` unless the write-probe in `paths.resolve_writable_base_dir`
(`portablefix/paths.py:12-23`) fails, in which case everything falls back to
`%TEMP%\PortableFix` instead (`paths.py:19-22`) — so by default, yes, all of this app's
own log/report/backup writes land on the (possibly slow, possibly wear-sensitive) USB
medium being carried around.

The two genuinely wasteful, evidence-backed write patterns are §4.2 (`undo.ps1`
full-rewrite-per-action) and §4.3 (HTML+JSON report full-rewrite-per-batch) — both
already covered above rather than repeated here, since they're squarely both a
CPU/responsiveness issue *and* a "more bytes hit the drive than necessary" issue. Beyond
those two:

- `audit_log.append_entry` (§4.2's contrast case) is the right pattern already — O(1)
  append per action, no rewriting. Nothing to fix here.
- `settings.save_settings` is called exactly once, at process exit
  (`main.py:53`, after `app.exec()` returns) — not on every toggle. Nothing to fix here.
- No finding here rises to "will measurably shorten a cheap stick's lifespan over months
  of use" — the absolute byte volumes in §4.2/§4.3, even at their quadratic worst case
  for one long session, are realistically in the tens-to-low-hundreds of KB to a few MB
  range (short PS one-liners; HTML/JSON reports for a few dozen actions), not the
  sustained-high-volume writes (video, disk images, database WAL) that actually matter
  for flash wear-leveling budgets. Flagging §4.2/§4.3 as CPU-time/responsiveness issues
  with a secondary I/O-volume angle is the accurate framing — not as a drive-lifespan
  risk in its own right.

---

## Verified clean (checked, no issue found)

1. **Module YAML loading is not a startup risk.** `module_engine.load_all_modules`
   (`portablefix/module_engine.py:51-52`) parses 12 files, ~92 KB total, 108 actions —
   negligible next to `check_integrity`'s 46 MB read of the same drive. See §1.2.
2. **`i18n.translate` is pure in-memory lookup.** `portablefix/i18n.py:1` defines
   `_STRINGS` as a module-level dict literal; `translate()` (`i18n.py:93-95`) never
   touches disk. Called dozens of times during `_build_ui()` at zero real cost.
3. **`audit_log.append_entry` uses the correct O(1)-per-entry append pattern**
   (`portablefix/audit_log.py:39-43`, opens `"a"`, writes one line) — the pattern §4.2's
   `undo.create_undo_script` should have used instead of a full rewrite.
4. **All four `QThread` workers clean up correctly.** `ActionRunner`
   (`executor.py:75-84`), `RestorePointRunner` (`restore_point.py:21-27`),
   `UpdateCheckRunner`/`UpdateDownloadRunner` (`updater.py:143-163`) all wire
   `self.finished.connect(self.deleteLater)` in `__init__` — no dangling thread objects
   accumulate across a long session.
5. **Widget dicts (`_action_checkboxes`/`_action_rows`/`_action_status_labels`) don't
   grow.** They're keyed by the fixed set of 108 action IDs; on language toggle
   (`main_window.py:441-447`) `_build_ui()` re-runs and overwrites every key with a
   fresh widget, and the previous `central` widget (parent of the old widget tree) gets
   `.deleteLater()`'d (`main_window.py:447`), which cascades through Qt's parent-child
   ownership to release the old checkboxes/labels/rows. Steady-state footprint, not
   unbounded.
6. **`_batch_results` and `_action_start_times` don't leak.** `_batch_results` is reset
   to `[]` at the start of every batch (`main_window.py:571`); `_action_start_times`
   entries are `.pop()`'d off in `_on_action_finished` (`main_window.py:689`) as each
   action completes.
7. **The update check is already correctly backgrounded and doesn't block startup.**
   `MainWindow.__init__` calls `_start_update_check()` (`main_window.py:97`) after the
   window object and its UI are built; `UpdateCheckRunner.start()`
   (`main_window.py:452-454`) runs the network call on a `QThread`, not the GUI thread —
   this is the exact pattern §1.1 recommends copying for `check_integrity`.
8. **Restore-point creation is already backgrounded and deduplicated per batch**, not
   per action. `RestorePointRunner` runs on a `QThread` (`restore_point.py:21-31`), and
   `_restore_point_attempted` (`main_window.py:83`, `617-618`) ensures at most one
   restore-point PowerShell invocation per batch regardless of how many
   REPAIR/SECURITY/DESTRUCTIVE actions are queued.
9. **No polling/timer accumulation anywhere.** No `QTimer`, `startTimer`, or
   `singleShot` usage exists anywhere under `portablefix/` — there's no periodic
   background work that could compound over a long session beyond what's already
   covered in §4.

---

## Files read

- `main.py`
- `portablefix/module_engine.py`
- `portablefix/paths.py`
- `portablefix/integrity.py`
- `portablefix/gui/main_window.py`
- `portablefix/executor.py`
- `portablefix/updater.py`
- `portablefix/audit_log.py`
- `portablefix/undo.py`
- `portablefix/report.py`
- `portablefix/restore_point.py`
- `portablefix/settings.py`
- `portablefix/elevation.py`
- `portablefix/i18n.py` (existence/shape of `_STRINGS`/`translate` only)
- `portablefix/models.py`
- `build/PortableFix.spec`
- `scripts/generate_sha256sums.py`
- `Data/SHA256SUMS` (content, to confirm what `check_integrity` actually hashes today)
- `Modules/*/actions.yaml` (grepped for `risk:` values of the `full_diagnostic` preset only)

Measurements taken on this dev machine via PowerShell (`Get-FileHash`,
`Measure-Command`) against `App/PortableFix.exe` as it exists in this checkout —
cited inline as directional evidence, not a benchmark of target hardware.
