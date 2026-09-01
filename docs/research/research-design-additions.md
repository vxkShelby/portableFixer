# PortableFix — Design/UX Research: What to Add

Scope: read-only review of `portablefix/gui/main_window.py` (451 lines), `portablefix/gui/style.py` (192 lines),
`portablefix/report.py` (189 lines), `portablefix/i18n.py` (59 lines), `portablefix/models.py` (42 lines),
`portablefix/executor.py` (65 lines), `portablefix/settings.py`, `portablefix/audit_log.py`, `portablefix/undo.py`,
`main.py`, and the `Modules/*/actions.yaml` catalog (82 actions across 12 modules / 4 categories:
DIAGNOSTICS 19, CLEANUP 26, REPAIR 32, SECURITY 5). No files were modified.

## Ground truth that shapes the sketches below

- **Backend is not `QProcess`** — it's `ActionRunner(QThread)` wrapping `subprocess.Popen` (`portablefix/executor.py`).
  `finished_with_code`/`output_line` are Qt signals off that thread. Any "cancel" or "duration" work must go through
  this class, not `QProcess`.
- **Action rows have no widget identity.** Each row is a bare `QHBoxLayout` (checkbox + risk `QLabel`) added straight
  into the card's `QVBoxLayout` (`main_window.py:193-204`). There is no per-row container widget and no dict of rows
  by id (only `self._action_checkboxes: dict[str, QCheckBox]`). Anything that needs to show/hide or annotate a row
  (search filtering, status icons) needs the row wrapped in a real `QWidget` first — a small structural change that
  several accepted items below share.
- **`self.statusBar()` is inherited free from `QMainWindow` and is completely unused.** Zero calls to it anywhere.
- **Dark scrollbar styling already exists** (`style.py:68-83`, full vertical `QScrollBar` theme, hover state, hidden
  arrows). **Window icon already exists** (`main.py:20-22`, `app.setWindowIcon()` from `portablefix.ico`, also baked
  into the exe via `scripts/build.ps1 --icon`). Title is already set via i18n. Two brief items are therefore reject-as-done.
- **`ActionDef.preview_command`** already exists and, for some actions (e.g. `m02_cleanup` FontCache clear), computes
  a *real* dynamic size estimate ("Would clear N font cache file(s), X MB") — today it's only substituted in for the
  real command when dry-run is on. This is a ready-made hook for an honest "impact preview," see item 6.
- **`AuditEntry.command` is captured and persisted** to the JSONL audit log (`audit_log.py:13,27`) but
  **`report.py`'s `build_report_data` never reads it back** into the report's per-action dict (`report.py:43-56` pulls
  `timestamp/module_id/action_id/label/risk/exit_code/dry_run/output`, not `command`). The HTML report already has a
  working `<details>/<summary>/<pre>` collapsible pattern for `output` — the same pattern trivially covers `command`
  once it's wired through. Concrete, low-risk gap.
- **`_show_batch_summary`** (`main_window.py:250-291`) builds a plain `QVBoxLayout` of `QLabel` rows with no
  `QScrollArea` — for a large batch (e.g. the 32-action REPAIR category) the non-modal dialog can grow taller than
  the screen with no way to scroll it.
- **`closeEvent`** (`main_window.py:64-69`) sets a `_closed` flag but never checks `self._batch_active` — the window
  can be closed while a batch/subprocess is still running, with no prompt.
- Tests use `pytest-qt` (`qtbot`) already, see `tests/test_gui_main_window.py` — new GUI behavior should get one
  `qtbot`-based test in that file, consistent with the existing convention.

Every implementation sketch below assumes PySide6 widgets + QSS only, per the constraints.

---

## Part 1 — Items from the brief

### 1. Live per-action progress states in cards (queued/running/done/failed)
**ACCEPT — Quick win.**
Value: today the only feedback during a batch is the console text scrolling by; nothing on the card itself says
which of N checked actions is currently running vs. already done. This is the single most standard pattern in
installer/updater UIs (Windows Update, CCleaner) and it's completely absent here.
Sketch: add one `QLabel` (small fixed-width glyph, e.g. `●`/`▶`/`✓`/`✕`) per row next to the risk badge, keyed in a
new `self._status_labels: dict[str, QLabel]` mirroring `_action_checkboxes`. Set it in `_dispatch_action` (running)
and `_on_action_finished` (done/failed); reset to queued/idle at the top of `run_selected_actions`. No animation —
just glyph + `setProperty("state", ...)` + QSS color per state (mirrors the existing `riskBadge[risk=...]` pattern).
~30-40 lines + ~10 lines QSS.
**Qt pitfall:** after `setProperty()` on a live widget, QSS won't repaint until you call
`widget.style().unpolish(widget); widget.style().polish(widget)` — easy to forget, shows up as "the color never
changes." Flag this for whoever implements it; it recurs in items 9 below too.
Priority: **Quick win.**

### 2. Elapsed time per action + total
**ACCEPT — Quick win (rides on item 1's label).**
Value: lets a tech tell "still working" from "stuck" (e.g. `sfc /scannow`, DISM, AppX re-register can run minutes).
Sketch: capture `time.monotonic()` in `_dispatch_action` before `runner.start()`, compute delta in
`_on_action_finished`, append `"(3.2s)"` to the same per-row status label from item 1. For a *live* ticking total,
add one `QTimer(1000ms)` started in `run_selected_actions` and stopped when `_batch_active` goes false — must also be
stopped in `closeEvent` (same lifecycle class as the existing `_closed` guard) so it doesn't fire on a dead window.
~20-25 lines combined with item 1.
Priority: **Quick win.**

### 3. Cancel/abort button for running batch
**ACCEPT — Larger bet (medium/large).**
Value: real gap for a repair tool — several catalog commands (DISM, SFC, AppX re-register) can run for minutes, and
there is currently *no* way to stop a batch short of force-closing the whole app (which doesn't even kill the
child process today, see the `closeEvent` gap noted above).
Sketch: `ActionRunner` needs to keep `self._process` as an instance attribute (it's currently a local in `run()`) and
gain a `stop()` method calling `self._process.kill()`. Because the backend is plain `subprocess.Popen` (not
`QProcess`), `.kill()` is a direct, thread-safe-enough call (Windows `TerminateProcess`) — **do not** reach for
`QThread.terminate()`, which is unsafe mid-execution. Add a "Cancel" `QPushButton` enabled only while
`_batch_active`, wired to: clear `self._queue`, call `self._runner.stop()` if set. ~25-35 lines across
`executor.py` + `main_window.py`.
**Qt/process pitfall:** killing the direct child does not guarantee grandchildren die (a PowerShell one-liner that
itself spawns `Start-Process` can survive) — real limitation, not fixable with a few lines (would need Windows Job
Objects). Worth a one-line code comment (`# ponytail: kills direct child only, detached grandchildren may survive —
Job Object if that's ever reported`) rather than solving it now.
Priority: **Larger bet** — crosses the GUI/executor boundary and has a genuine correctness pitfall, unlike the
mostly single-file quick wins.

### 4. Search/filter box over actions
**ACCEPT — Quick win.**
Value: confirmed by the catalog — 82 actions in only 4 category tabs, REPAIR alone has 32. Hunting for "DNS" or
"sfc" today means eyeballing a long scroll.
Sketch: one `QLineEdit` above the scroll area; `textChanged` → walk `_action_checkboxes`, compare
`action.label(language).lower()` against the query, show/hide the row. Requires wrapping each row's `QHBoxLayout` in
a `QWidget` container first (see "ground truth" above) since a bare layout can't be hidden as a unit — this is the
one shared structural prerequisite for items 1, 4, and 14. Scope to the currently-visible category tab (matches
existing per-category card structure); a global cross-tab search is a bigger change (would need to defeat the
tab-hides-card model) and isn't needed for an 82-item catalog. ~25-30 lines.
Priority: **Quick win.**

### 5. "Recommended preset" one-click profiles
**ACCEPT — Quick win.**
Value: highest-leverage pattern in comparable tools (CCleaner "Analyze", IObit "one-click"). No preset concept
exists anywhere today (checked the YAML schema — no `preset`/`recommended` field in any `actions.yaml`).
Sketch: **don't** touch the YAML schema across 12 files. Hardcode 3 `list[str]` of action ids (Quick Clean / Full
Diagnostic / Privacy Debloat) in a small dict literal, either inline in `main_window.py` or a 15-line new
`presets.py`. A preset button calls `_apply_selection(all_ids, "none")` then `_apply_selection(preset_ids, "all")` —
100% reuse of the selection machinery that already exists for global/per-category select-all/safe/none. ~35-45 lines.
Priority: **Quick win** — high value, almost no new machinery.

### 6. Estimated impact hints on cards ("typical space freed")
**ACCEPT, but scoped down — Medium.**
As literally proposed ("typical space freed" printed on every card) this is **rejected as written**: a static number
would be fabricated per-machine guesswork and actively misleading (a "~50 MB" cache could be 2 MB or 2 GB depending
on the machine). Reframed accept: for the subset of actions that already declare `preview_command` (proven dynamic,
e.g. FontCache clear), add a small "Preview" affordance next to the row that runs the existing `preview_command`
once (reusing `ActionRunner`/`build_execution_plan` in dry-run-style mode) and shows the real number in a tooltip or
one-line label — honest instead of guessed. For actions without a `preview_command`, show nothing rather than a
made-up figure.
Sketch: ~40-50 lines — a per-row "Preview" `QPushButton` (only rendered when `action.preview_command` is set), a
handler that spins up a one-shot `ActionRunner(build_execution_plan(action.preview_command, dry_run=False))` and
routes its `output_line` into a tooltip/label instead of the console.
Priority: **Medium** — worthwhile but not free; this is one of the **top 3 larger bets**.

### 7. Collapsible console with severity coloring
**ACCEPT — Quick win.**
Value: the console is a monochrome dump of raw stdout/stderr; scanning a wall of PowerShell text for the one error
line is real friction today.
Sketch: severity coloring — switch `runner.output_line.connect(self.console.appendPlainText)` to a small formatter
that wraps each line in a colored `<span>` based on keyword sniffing (`error`/`exception`/`fail` → red,
`warning` → amber) and calls `appendHtml`. Collapsible — one toggle `QPushButton` flipping `self.console.setVisible()`
plus an arrow glyph swap. Independent halves, both cheap. ~25-30 lines.
**Qt pitfall:** switching to `appendHtml` means raw command output must be `html.escape()`-d first, or output
containing `<`/`&` (common in PowerShell table output) will corrupt the rendering — easy to miss.
Priority: **Quick win.**

### 8. Toast notifications instead of modal confirmations, batched to "N MODERATE actions — confirm once"
**SPLIT — accept the batching, reject the toast mechanism for this job.**
The brief's underlying complaint is real: `_dispatch_action` currently pops a **separate modal dialog per risky
action** (`main_window.py:404-422`), so selecting 10 MODERATE actions means 10 interleaved "are you sure?" popups.
But a **toast is the wrong control for a yes/no safety gate** — toasts are passive/dismissible; using one to gate a
DESTRUCTIVE action would be a safety regression, not a UX improvement, and this app deliberately treats
confirmation as a blocking decision (see the restore-point-failure prompt using the same pattern).
- **ACCEPT:** batch the *MODERATE* confirmations into one upfront `QMessageBox.question` before the queue starts
  (tally by risk with `collections.Counter`, e.g. "3 MODERATE, 1 DESTRUCTIVE selected — continue?"). Keep the
  per-action DESTRUCTIVE confirmation as a second, explicit belt-and-suspenders prompt since it's irreversible.
  ~20 lines in `run_selected_actions`, no new widget.  **Quick win.**
- **REJECT:** a generic toast-notification *widget/subsystem*. This single-window app has no navigation-away-and
  -come-back use case a toast solves that the console + a status bar (item 10) don't already cover; building a
  positioned, timed, non-blocking overlay widget is real infrastructure for a problem that doesn't exist here yet
  (YAGNI). If a genuine "transient success" need shows up later (e.g. "Report saved"), a one-off `statusBar().showMessage(text, 3000)` (built into `QMainWindow`, free) covers it without a new widget class.

### 9. Dry-run visual mode (cards tinted/badged when dry-run is on)
**ACCEPT, but scoped down — Quick win.**
Dry-run is a genuine safety-relevant default (`Settings.dry_run: bool = True`), and today its only indicator is a
small checkbox in the top bar plus a note that appears *after* the batch finishes. Tinting all ~20-30 visible cards
individually is a much bigger touch-every-row change for marginal extra clarity versus a single strong signal.
Scoped accept: change the **Run button** itself — swap its text to "Run selected (DRY-RUN)" and its accent color to
amber while `dry_run_checkbox` is checked, via the existing `_on_dry_run_toggled` handler plus one new QSS rule
(`QPushButton#runButton[dryrun="true"]`), mirroring the `riskBadge`/`adminPill` property-driven color pattern already
used twice in `style.py`. ~10-15 lines.
**Qt pitfall:** same `unpolish/polish` requirement as item 1.
Priority: **Quick win.**

### 10. Status bar with selection count + estimated total risk
**ACCEPT — Quick win (cheapest item in the whole list).**
`QMainWindow.statusBar()` is a free, already-inherited widget that is 100% unused today.
Sketch: connect each checkbox's `stateChanged` (one extra `.connect()` inside the existing per-action creation loop)
to a `_update_status_bar()` that counts checked ids and finds the max `RiskLevel` among them, then calls
`self.statusBar().showMessage(f"{n} selected · highest risk: {risk}")`. Because `_apply_selection` already toggles
checkboxes one at a time via `.setChecked()`, the existing per-checkbox signal keeps this in sync for free — no
extra hook needed for the select-all/safe/none buttons. ~20 lines, zero new widgets.
Priority: **Quick win.**

### 11. Dark scrollbar styling
**REJECT — already implemented.** `style.py:68-83` fully themes `QScrollBar:vertical` (track, handle, hover, hidden
arrows) matching the palette. No gap here. (The batch summary dialog's *missing* scroll container is a real, related
gap — see item 15.)

### 12. Empty states and first-run hint
**ACCEPT, two small sub-items, sequenced after items 4 and 6.**
- Empty-search-state: once item 4 (search) exists, a "no actions match" `QLabel` shown when every row in the visible
  category is hidden by the filter. Trivial, ~10 lines, but has no host until search exists.
- First-run hint: a one-time dismissible banner pointing at the preset buttons (item 5), gated by a new
  `Settings.first_run_seen: bool` field (the existing JSON load/save round-trip in `settings.py` already handles one
  more field for free). ~20 lines.
Priority: **Quick win**, but explicitly dependent on items 4/5 existing first — list as a follow-on, not standalone.

### 13. Keyboard shortcuts (Ctrl+A select all, F5 run)
**ACCEPT — Quick win, with one guard.**
Sketch: `QShortcut(QKeySequence("F5"), self, activated=self.run_selected_actions)` and a Ctrl+A binding for
select-all — a few lines total using Qt's built-in `QShortcut`, no event filtering needed.
**Qt pitfall:** the default `QShortcut` context (`Qt.WindowShortcut`) fires regardless of which child widget has
focus — so once item 4's search `QLineEdit` exists, pressing Ctrl+A while typing a filter would trigger "select all
actions" instead of the native "select all text in the box." Guard with a focus-widget check
(`if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit)): return`) before running the handler, or
pick a non-colliding binding. ~10-15 lines including the guard.
Priority: **Quick win.**

### 14. Window icon/title polish
**REJECT — already implemented.** `main.py:20-22` sets `app.setWindowIcon()` from `portablefix.ico` (also the exe
icon via `scripts/build.ps1 --icon`); title is set via i18n in `_build_ui`. Optional one-line enhancement (not a
capability gap): append hostname to the title bar, e.g. `f"{title} — {socket.gethostname()}"` — trivial, skip unless
asked.

### 15. Results dialog upgrade (sortable, copy summary button)
**ACCEPT, scoped — reject the literal "sortable table."**
"Sortable" as a `QTableWidget` is over-engineering for what's fundamentally a short pass/fail list (batches here
realistically run up to ~32 actions); adding a model/selection-aware table widget to a lightweight popup is a lot of
new surface for little benefit. Instead:
- **Copy summary button:** build a plain-text summary from `self._batch_results` and
  `QApplication.clipboard().setText(...)`. Directly useful for a technician tool — pasting results into a ticket/chat
  is a real workflow. ~10 lines. **Quick win.**
- **Failed-first ordering:** one `.sort(key=lambda r: r[1] == 0)` before the render loop — gets most of "sortable"'s
  value (see what broke first) without a table widget. ~2 lines. **Quick win.**
- **Scroll container:** wrap the existing `QVBoxLayout` of row labels in a `QScrollArea` (exact same pattern already
  used for the main action list) so a 30-action batch doesn't grow the dialog off-screen — this is the concrete gap
  found while reading `_show_batch_summary`. ~10 lines. **Quick win.**
- **REJECT:** interactive column-sortable `QTableWidget` — real cost (model, delegates, selection handling) for a
  dialog whose job is "glance and close."

### 16. System tray progress
**REJECT.** This is a single-window, portable, foreground tool a technician launches and actively watches — not a
background/always-running service. There's no minimize-to-tray path today (`closeEvent` doesn't intercept minimize),
and adding one raises real process-lifecycle questions this app doesn't currently answer (what happens to a running
subprocess if the window is "closed" but tray-resident?). Items 1/2/3/10 already answer "what's happening" without
needing to alt-tab away. Classic speculative addition for a usage pattern this app doesn't have — skip until asked.

### 17. HTML report enhancements
Three genuinely separate asks bundled together:
- **Collapsible command text — ACCEPT, Quick win.** `AuditEntry.command` is already captured and persisted to the
  JSONL audit log (`audit_log.py:13,27`) but `report.py:build_report_data` never reads it back (only pulls
  `timestamp/module_id/action_id/label/risk/exit_code/dry_run/output`). The `<details>/<summary>/<pre>` collapsible
  pattern already exists for `output` (`report.py:118-121`) — add `"command": entry.get("command", "")` to the dict
  comprehension and a second `<details>` block reusing the same CSS. ~6-8 lines. Concrete, low-risk, good ROI.
- **Per-action duration — ACCEPT, Medium (depends on item 2).** Needs the timing captured in item 2 to be persisted
  into a new `AuditEntry.duration_ms` field, then rendered in `_render_action_card` the same way `risk`/`exit_code`
  are today. This is a real cross-file schema change (`main_window.py` timing → `audit_log.py` field →
  `report.py` render), not a report-only tweak — sizes as one of the **top 3 larger bets** once its dependency (item
  2) lands; the report-side render itself is only ~5 lines.
- **System snapshot header — PARTIALLY ALREADY DONE, accept a small enrichment only.** `build_report_data` already
  captures hostname, OS, and a before/after disk-space snapshot, and `_render_html`'s `.meta` block already renders
  the free-space delta (`report.py:142-148,160-161`) — this *is* a system snapshot header already, just minimal.
  Scoped accept: add 2 fields that already exist elsewhere and require zero new data collection — whether the run
  was elevated (`self.is_admin`) and whether dry-run was active — to the existing `.meta` div. ~10 lines.
  **REJECT** going further into CPU/RAM/installed-software enumeration: that needs a new dependency (`psutil` isn't
  in the project today) and is data-collection scope creep beyond a design/UX pass for a tool that's meant to stay
  lightweight and portable.

---

## Part 2 — Additions found while reading the code (not in the brief)

### 18. Aggregate batch progress bar ("Running action 4 of 12")
**ACCEPT — Quick win.** Distinct from item 1's per-card state: a single `QProgressBar` (top bar or above the
console) showing overall queue progress. The queue length and position are exactly known at all times
(`len(original_queue) - len(self._queue)`), so this is close to free. It's arguably the single most "obviously
missing" pattern for a tool that already sequences a known-length queue — every comparable tool (Windows Update,
CCleaner "Cleaning… 4/12") has this, and the brief's list covers per-card and elapsed-time but not the aggregate bar.
Sketch: `self.batch_progress = QProgressBar()`, `setMaximum(len(queue))` in `run_selected_actions`,
`setValue(...)` + `setFormat("%v / %m actions")` updated in `_on_action_finished`/`_run_next`; `setVisible(self._batch_active)`.
QSS: one `QProgressBar`/`QProgressBar::chunk` rule matching the accent color, same visual family as the existing
`riskBadge`/`runButton` rules. ~20 lines + ~10 lines QSS.
Priority: **Quick win** — candidate for the top-5.

### 19. Confirm before closing during an active batch
**ACCEPT — Quick win.** Found while reading `closeEvent` (`main_window.py:64-69`): it sets `_closed = True` and lets
the window close immediately even if `self._batch_active` is true and a subprocess/thread may still be running —
no prompt, no chance to notice. A tech could accidentally close mid-DISM-scan.
Sketch: in `closeEvent`, `if self._batch_active: ask QMessageBox.question(...); if not confirmed: event.ignore(); return`.
~10 lines. This is more "safety" than "polish," but it's a concrete, code-grounded gap, not speculative.
Priority: **Quick win.**

### 20. Space-freed delta surfaced in the batch summary dialog (not just the HTML report)
**ACCEPT — Quick win.** `_show_batch_summary` already has `self._snapshot_before`/`_snapshot_after` on `self` at the
point it's called, and `report.py`'s `_render_html` already computes the same free-space delta for the HTML report
— but the in-app dialog itself never shows it, only ok/fail counts. Reuse the exact same delta computation already
written in `report.py` and add one `QLabel` to the dialog. ~8-10 lines, no new data.
Priority: **Quick win.**

### 21. "Open Undo Script" button in the batch summary dialog
**ACCEPT — Quick win.** `undo.create_undo_script` already writes a real, path-known undo script
(`state_dir/Backups/<run_id>/undo.ps1`, confirmed in `undo.py:18`) whenever a batch has reversible steps
(`self._undo_steps`), but there is currently **no UI entry point to find it** — the safety net exists but is
invisible. Mirror the exact pattern already used for the "Open report" button
(`QDesktopServices.openUrl(QUrl.fromLocalFile(...))`), shown only when `self._undo_steps` is non-empty.
~10 lines, zero new pattern to learn since it's a copy of the existing Open Report button.
Priority: **Quick win.**
(Note: actually *running* the undo script from the GUI would be a bigger workflow/feature question beyond this
design/UX pass — flagging only the "make the existing artifact discoverable" half, which is pure UI surfacing.)

### 22. Keyboard focus indicators (accessibility)
**ACCEPT — Quick win.** `style.py` defines hover/checked/selected states for buttons, checkboxes, and the category
list, but no `:focus` state anywhere — checked by re-scanning the file. Once keyboard shortcuts (item 13) and a
search box (item 4) make keyboard-only flows more likely, the dark theme currently has no visible focus ring at all
against `#1a1b26`. Add a handful of `QPushButton:focus`, `QListWidget#categoryList::item:focus`-style QSS rules
(2px accent-color outline). Pure QSS, no Python changes. ~10-15 lines.
Priority: **Quick win.**

### 23. Per-category count badges in the sidebar ("3/19 selected")
**REJECT (own idea, rejected on reflection).** `QListWidget` today uses plain text items
(`QListWidgetItem(text)`); a per-item count badge needs a custom `setItemWidget()` per category instead of plain
text, which is a real structural change for a data point the status bar (item 10) already surfaces globally more
cheaply. Not worth the added complexity — good example of scope that doesn't earn its keep here.

---

## Summary counts

- **Accepted (including scoped-down and dependent sub-items): 22**
- **Rejected: 7** — dark scrollbar styling (already done), window icon/title polish (already done), generic toast
  widget, interactive sortable results table, system tray progress, heavier system-info collection in the report,
  per-category sidebar count badges.

## Top 5 quick wins

1. **Status bar: selection count + highest selected risk** — `statusBar()` is free and 100% unused today; ~20 lines.
2. **Aggregate batch progress bar ("4 of 12 actions")** — the most standard missing pattern for a sequential queue
   whose length is always known; ~20 lines + QSS.
3. **Search/filter box over actions** — justified directly by the catalog (82 actions, 32 in REPAIR alone).
4. **One-click recommended presets (Quick Clean / Full Diagnostic / Privacy Debloat)** — reuses 100% of the existing
   select-all/safe/none machinery, zero YAML schema change.
5. **Per-action status icons + elapsed time on cards** — the single most "modern installer" pattern missing; rides
   on one new label per row.

## Top 3 larger bets

1. **Cancel/abort a running batch** — real value (DISM/SFC/AppX actions can run minutes with no escape hatch today),
   real cost: `ActionRunner` needs to expose its `subprocess.Popen` handle for a safe `.kill()`, plus a
   grandchild-process caveat worth documenting rather than solving now.
2. **On-demand "preview impact" affordance** (reframed from the brief's static "space freed" hint) — reuses the
   existing `preview_command` field honestly instead of fabricating numbers, but needs a second "preview run" mode
   through `ActionRunner`/`build_execution_plan`.
3. **Per-action duration threaded end-to-end into the HTML report** — genuinely cross-file (timing capture in
   `main_window.py` → new `AuditEntry.duration_ms` field in `audit_log.py` → render in `report.py`), unlike the
   single-file quick wins above.
