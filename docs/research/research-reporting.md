# PortableFix — Report & Audit-Trail Usefulness Research (Handoff/Documentation Lens)

Scope: read-only research into whether PortableFix's generated artifacts — the HTML/JSON
report (`portablefix/report.py`), the JSONL audit log (`portablefix/audit_log.py`), and the
undo script (`portablefix/undo.py`) — are usable as a technician's documentation/handoff
record, not just whether they technically function. Files read: `portablefix/report.py`,
`portablefix/audit_log.py`, `portablefix/undo.py`, `portablefix/restore_point.py`,
`portablefix/paths.py`, `portablefix/models.py`, `portablefix/i18n.py`, `main.py`,
`portablefix/gui/main_window.py` (batch run flow, `_show_batch_summary`, restore-point
gating), `.gitignore`, `tests/test_report.py`, `tests/test_audit_log.py`,
`tests/test_undo.py`, `tests/test_restore_point.py`, plus the five real sample
`Logs/*.jsonl` / `Reports/Norach_IX_*.{html,json}` / `Backups/*/undo.ps1` files that exist
on disk (gitignored per `.gitignore:9-12`, but present locally — not synthetic) and
`docs/superpowers/specs/2026-08-31-f2-cleanup-reporting-design.md` for the original design
intent behind the restore-point/undo feature. No files modified.

The five sample runs (`20cf67538fe0`, `68ee7307d9a4`, `83b086d4642e`, `ac935bde135d`,
`c76ece839272`, all hostname `Norach_IX`) are real output from this exact code, not
hand-written fixtures, and are cited directly below where they demonstrate a gap.

---

## HIGH severity

### F1 — Restore point outcome, description, and ID are never written to the audit log or the report
`create_restore_point()` (`portablefix/restore_point.py:8-18`) runs `Checkpoint-Computer`
and reduces the entire result to a bare `bool` — it doesn't even capture
`result.stdout`/`result.stderr`, which is where the actual restore-point sequence number
would appear. The caller (`main_window.py:617-626`) fires this once per batch, before the
first DESTRUCTIVE/REPAIR/SECURITY action, with description `f"PortableFix {self.run_id}"`
— but that description string, and whether creation succeeded, is **never passed to
`make_entry`/`append_entry`** and never reaches `build_report_data`
(`report.py:33-65`, no `restore_point` key anywhere in the returned dict) or `_render_html`
(`report.py:135-171`, no restore-point section — only a `requires_restart` block exists).

Confirmed against real data: `Reports/Norach_IX_c76ece839272.json` (a run whose module was
`m02_cleanup`, a REPAIR-adjacent category that triggers the restore-point gate) contains no
`restore_point` key at all, and none of the 5 sample `.json` reports do.

**Impact:** this is precisely the safety net the whole DESTRUCTIVE/REPAIR/SECURITY gating
exists for, and its outcome is invisible in every persisted artifact. If a client later asks
"did you actually create a restore point before doing this," the technician has nothing to
point to except their own memory of a modal dialog they may not have read closely.

**Suggested fix:** in `create_restore_point`, return `(bool, str)` (success + the
description actually used, or the captured stderr on failure) instead of a bare bool; in
`main_window.py:617-626`/`630-642`, write one synthetic `AuditEntry` (e.g.
`module_id="_system"`, `action_id="restore_point"`) via the existing `append_entry` call
before/after the attempt; add the resulting description + success flag as one more field in
`build_report_data` and one more line in the `.meta` block of `_render_html`. No new
subsystem — reuses the audit-entry and report-data plumbing that already exists for actions.

### F2 — Risk-warning confirmation dialogs are shown but never logged (no proof the technician was warned)
`_dispatch_action` (`main_window.py:644-663`) shows a `QMessageBox` with
`confirm_destructive_action` or `confirm_risky_action` text (`portablefix/i18n.py:17-18,
61-62`) before running any non-SAFE action, and only proceeds on Yes. That Yes/No answer —
and the fact the warning was shown at all — is discarded; the only thing that eventually
reaches `make_entry`/`append_entry` (`main_window.py:686-687`) is the command, exit code,
output, and `dry_run` flag. `AuditEntry` (`audit_log.py:8-18`) has no `risk` field and no
`warned`/`confirmed` field.

**Impact:** directly undermines the "prove the technician was warned before a
SECURITY/DESTRUCTIVE change" scenario. Today the audit trail can prove an action *ran*, but
not that the mandatory warning copy was displayed and accepted first — a dispute can't be
technically settled by the log as it stands.

**Suggested fix:** thread a `warned: bool` (or the exact dialog text shown) from
`_dispatch_action` through to the `_on_action_finished` → `make_entry` call
(`main_window.py:682-687`); one extra dataclass field on `AuditEntry`, populated from
information `_dispatch_action` already has in hand.

### F3 — "Proceed without a restore point" is a silent, unlogged decision
When `create_restore_point` fails, `_on_restore_point_checked`
(`main_window.py:630-642`) shows `restore_point_failed_confirm`
(`i18n.py:18,62`: *"Could not create a System Restore Point... Continue anyway?"*). Clicking
Yes runs `_dispatch_action` directly with no record written anywhere that the safety net was
known-absent for the action(s) that followed. This is the same missing-persistence pattern
as F1, but specifically the *failure* branch — the exact scenario a client dispute centers
on ("was there a rollback path when you made this change").

**Suggested fix:** same mechanism as F1 — once the synthetic restore-point audit entry
exists, "proceeded after failure" is implicitly provable by the destructive action's
timestamp being later than the failed restore-point entry's timestamp; no separate flag
needed once F1 is fixed.

### F4 — Fallback to the client machine's own `%TEMP%` silently stores all documentation off the technician's drive
`resolve_writable_base_dir` (`portablefix/paths.py:12-22`) falls back to
`Path(tempfile.gettempdir()) / "PortableFix"` on any `OSError` writing to the USB (write
protection, read-only mount, etc.) — i.e. `C:\Users\<client>\AppData\Local\Temp\PortableFix`
**on the machine being serviced**, not the technician's own portable drive.
`get_base_dir()` (`paths.py:6-9`) resolves to the exe's own parent-of-parent directory,
confirming Reports/Logs/Backups are designed to live on the portable medium and accumulate
history across every client machine the technician visits — the fallback breaks that model
entirely for the affected run. `main.py:25-33` shows one `QMessageBox.warning` at startup
using `fallback_banner` (`i18n.py:13,57`: *"USB not writable - using %TEMP%\PortableFix
instead."*) — shown once, not persisted to any file, easy to dismiss without reading.

**Impact:** if this fires, that visit's entire report/audit-log/undo-script/settings history
lives only on the client's disk, in a location routine temp-cleanup (including PortableFix's
own M02 "Temp files" action, if run on a *later* visit) is likely to delete. A technician who
missed the one-time popup has no way to discover after the fact that this happened.

**Suggested fix:** when `used_fallback` is true, stamp that fact into the generated report
itself (one more `build_report_data` field + a visible banner line in `_render_html`) so the
HTML the technician actually opens/emails documents its own atypical, at-risk location —
cheap, since `main_window.py` already receives `state_dir` (the resolved, possibly-fallback
dir) separately from `assets_dir` (the raw USB root) and can compare the two.

### F5 — Audit log entries carry no hostname or run_id inside the record itself
`AuditEntry` (`audit_log.py:8-18`) has fields `timestamp, module_id, action_id, command,
exit_code, output, output_hash, dry_run` — no `hostname`, no `run_id`. Both are only
implied externally: `run_id` by the containing filename (`audit_log_path`,
`audit_log.py:35-36`: `Logs/{run_id}.jsonl`), and hostname by nothing at all in `Logs/` or
`Backups/` — only `report.py:185-186` embeds hostname, and only in the Reports/ filename,
not inside the JSON/JSONL content. Confirmed on disk: `Logs/c76ece839272.jsonl` and
`Backups/c76ece839272/undo.ps1` carry zero hostname anywhere in their content; you'd need to
already know (from the sibling `Reports/Norach_IX_c76ece839272.json`) that this run belongs
to `Norach_IX`.

**Impact:** this is the exact gap that breaks "build tooling around it later" for the
documented use case of one technician's USB drive accumulating history across many different
client machines (confirmed shared-drive design per F4/paths.py). Concatenate or `jq` over
several `Logs/*.jsonl` files — the normal way to build a cross-machine dashboard — and
individual lines carry no self-describing link back to which machine or which run they came
from. That link exists only in the filename, which any copy/rename/archive/consolidation
step can silently drop.

**Suggested fix:** add `hostname` (via `socket.gethostname()`, already imported in
`report.py`) and `run_id` as fields on `AuditEntry`/`make_entry` (`audit_log.py:8-32`) —
both values are already known at the `append_entry` call site in `main_window.py:687`.

---

## MEDIUM severity

### F6 — `run_id` is fully random, so Reports/Logs/Backups filenames don't sort chronologically
`run_id = uuid.uuid4().hex[:12]` (`main.py:43`) has no time component, and it's the sole
sortable element of every generated filename: `Reports/{hostname}_{run_id}.{html,json}`
(`report.py:185-186`), `Logs/{run_id}.jsonl` (`audit_log.py:35-36`), and
`Backups/{run_id}/undo.ps1` (`undo.py:18`). Evidenced directly by the 5 real sample runs —
alphabetical filename order (`20cf67538fe0`, `68ee7307d9a4`, `83b086d4642e`, `ac935bde135d`,
`c76ece839272`) bears no relation to their actual `mtime` order (13:16, 13:32, 16:26, 16:35,
then 00:30 the next day).

**Impact:** for the explicit "same technician, same machine, multiple visits over months"
scenario, opening `Reports/` (sorted by name, the common default in many file pickers/shell
scripts) shows a scramble, not a timeline, and there is no index/manifest file anywhere that
lists runs with human-readable dates. Relying on filesystem "Date modified" is fragile — it's
the first thing many archive/zip/sync/copy operations discard or randomize.

**Suggested fix:** prefix the run identifier with a sortable UTC timestamp, e.g.
`f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"` in `main.py:43` — one
line, and every downstream consumer (`report.py`, `audit_log.py`, `undo.py`) already treats
`run_id` as an opaque string, so nothing else needs to change.

### F7 — No overall report-generation timestamp is shown at a glance in the HTML report
`_render_html` (`report.py:135-171`) puts hostname, run_id, OS, and free-space delta in the
`.meta` block (`report.py:159-161`) but no date/time — the only timestamps anywhere in the
document are buried per-action, inside each `.card` (`report.py:130`, populated from
`entry["timestamp"]`, e.g. `"2026-09-01T14:27:15.925813+00:00"` in the real sample data).

**Impact:** a technician (or client) skimming the top of the report — the normal way anyone
actually reads a report — has no visit date without expanding a card. Combined with F6, this
is the single most useful piece of longitudinal metadata missing from the document.

**Suggested fix:** add a `generated_at` field to `build_report_data`
(`report.py:57-65`, one `datetime.now(timezone.utc).isoformat()` call) and one more `<br>`
line in the `.meta` div (`report.py:160-161`).

### F8 — Elevation state (`is_admin`) is never recorded per run
`is_admin` is computed once at startup and stored on `MainWindow`
(`main_window.py:65,73`), used only for a UI banner and to show/hide the "restart as admin"
button (`main_window.py:124-130`). It never reaches `make_entry`/`append_entry`
(`main_window.py:686-687`) or `AuditEntry` (`audit_log.py:8-18`).

**Impact:** several catalog actions realistically require admin rights and behave
differently — or fail in ways that don't obviously look like failures — when run
non-elevated (a companion finding in `docs/research/research-security-audit.md`'s HIGH #1
documents this for M08 specifically). A report showing `exit 0 / OK` for such an action
can't currently distinguish "ran with full access" from "ran degraded, non-elevated" without
cross-referencing the raw output text by eye.

**Suggested fix:** add `elevated: bool` to `AuditEntry`/`make_entry`, sourced from
`self.is_admin` at the `main_window.py:686` call site.

### F9 — `undo.ps1` has no pointer back to the report/audit log and doesn't flag which irreversible actions ran alongside it
`create_undo_script` (`undo.py:5-21`) writes only a run_id, a generation timestamp, and
either the reversible `steps` or the literal line `"# No reversible changes were made in
this run."` when `steps` is empty. Across the catalog, only 21 of 108 actions
(`Modules/*/actions.yaml`) define `undo_command`; several DESTRUCTIVE actions have none and
say so honestly in their own descriptions — e.g. `component_store_resetbase`
(`Modules/m02_cleanup/actions.yaml:103-110`, description: *"Prevents uninstalling currently
installed updates. Irreversible."*) and `debloat_remove_provisioned`
(`Modules/m13_debloat/actions.yaml:18-24`, description: *"Hard to reverse (requires package
reinstallation)."*). Confirmed on real data: `Backups/c76ece839272/undo.ps1` contains only
the 5-line "no reversible changes" header, even though the paired
`Logs/c76ece839272.jsonl` has 17 executed actions and `Reports/Norach_IX_c76ece839272.json`
is 17.7 KB of batch history.

**Impact:** directly hits the task's own handoff scenario — a technician (or a *different*
technician taking over the case) who finds `Backups/<run_id>/undo.ps1` in isolation a week
later (its entire reason to exist is to be findable and runnable without the original
session) sees a bare "nothing to undo" file with zero indication that 17 other actions ran,
some of them irreversible, and zero pointer to where the full report/audit log actually
live.

**Suggested fix:** append two more comment lines to the header block already built in
`undo.py:7-12`: one linking to the sibling report path
(`<base_dir>/Reports/<hostname>_<run_id>.html`), and — when the run's action list includes
DESTRUCTIVE actions with no `undo_command` — a `# NOT reversible (ran but no rollback
available):` block listing their labels. The caller (`main_window.py`) already has the full
action list and risk tiers in scope at every `undo.create_undo_script(...)` call site
(`main_window.py:619,696`), so this is passing one more argument, not new plumbing.

---

## LOW severity

### F10 — `output_hash` is write-only: computed, stored, and never read back or verified anywhere
`make_entry` (`audit_log.py:20-32`) computes
`hashlib.sha256(output.encode("utf-8")).hexdigest()` and stores it as `output_hash` right
next to the plaintext `output` it was hashed from, in the same JSONL line, in the same file.
Grepping the whole codebase for `output_hash` turns up exactly two other hits: its own field
declaration (`audit_log.py:16`) and one unit test asserting the hash matches
(`tests/test_audit_log.py:9`) — nothing anywhere reads it back, verifies it, or surfaces it
in the report (`build_report_data`, `report.py:45-56`, copies `output` into the report dict
but drops `output_hash` entirely — it isn't even in the JSON export).

**Impact:** low, because it costs nothing today and doesn't actively mislead unless someone
goes looking for it — but the name invites exactly that: a technician citing "it's hashed"
as evidence of tamper-evidence in a dispute would be overstating what this provides. A
same-file, unsigned, unchained hash of adjacent plaintext proves nothing an editor with
enough access to alter `output` couldn't also recompute and overwrite.

**Suggested fix:** either drop the field (simplest — it isn't used) or, if genuine
tamper-evidence is wanted later, chain it (`hash(entry_n) = sha256(entry_n_content +
hash(entry_n-1))`) so altering an old entry breaks every hash after it — out of scope for a
minimal fix, so dropping the unused field is the lazy-correct move today.

### F11 — Risk tier shown in a report is re-derived from the *current* action catalog, not frozen at execution time
`build_report_data` (`report.py:43-56`) looks up `action.risk.value` via `_find_action`
(`report.py:11-18`) against whatever `modules` list is passed in at report-generation time —
loaded fresh from the live YAML catalogs on disk — rather than reading a `risk` value stored
in the JSONL audit entry itself (`AuditEntry`, `audit_log.py:8-18`, has no `risk` field).
Confirmed by `tests/test_report.py:38-43`
(`test_build_report_data_unknown_action_falls_back_to_id`), which shows the risk is entirely
catalog-dependent — an action absent from the passed-in `modules` renders as `"UNKNOWN"`
regardless of what actually ran.

**Impact:** low today because nothing in the codebase currently regenerates an old report
against a newer catalog — but the app is self-updating (`portablefix/updater.py`) and
`generate_report` takes an arbitrary historical `run_id` plus whatever `modules` the caller
loads, so nothing structurally prevents it. If a catalog edit later reclassifies an action's
risk (SAFE→MODERATE, etc.), any future regeneration of an old report would silently show a
different risk tier than what the technician and client actually saw and confirmed on the
day — undermining exactly the "prove what I told the client, and when" use case for archived
visits.

**Suggested fix:** store `risk` (the value shown/confirmed at execution time) directly on
`AuditEntry` via `make_entry`, instead of re-deriving it later from the catalog.

---

## Verified clean

- **Self-contained, portable HTML.** `_render_html` (`report.py:135-171`) inlines all CSS via
  `<style>{_CSS}</style>` (`report.py:76-108,157`) with no `<link>`, `<script src>`, or
  `<img src>` anywhere in the generator. Confirmed against the real sample files too — the
  report can be emailed, copied to a USB, or archived and will render identically on any
  machine with no missing-asset risk. This directly satisfies the "export/portability" ask.

- **HTML output is properly escaped — no XSS/markup-injection risk from command output or
  action IDs.** Every interpolated string in `_render_action_card`/`_render_html` goes
  through `html.escape()` (`report.py:120,126,128-130,152,156,159-160`), and this is
  exercised by `tests/test_report.py::test_render_html_escapes_unsafe_characters`. A
  technician double-clicking this report, or a client opening it from an email attachment,
  won't trigger script execution from unusual command output or a maliciously-named catalog
  entry.

- **Audit log is genuinely append-only, line-delimited JSON — trivially machine-parseable.**
  `append_entry` (`audit_log.py:39-43`) opens in `"a"` mode and writes one
  `json.dumps(...) + "\n"` per call: standard JSONL, parseable with `jq`,
  `pandas.read_json(lines=True)`, or a plain per-line `json.loads` loop, and resilient to a
  crash mid-write (a truncated last line is simply droppable, not file-corrupting). This
  directly answers "is the audit log structured enough to be machine-parseable" — yes, the
  on-disk *shape* is sound; F5 is about what fields are (not) inside each line, a separate
  concern from the format itself.

- **No filename collisions / no silent overwrite of a prior visit's artifacts.**
  `run_id = uuid.uuid4().hex[:12]` (`main.py:43`) gives 48 bits of randomness — collision risk
  is negligible at realistic per-technician volumes, and this is empirically confirmed: 5
  distinct sample runs against the same hostname (`Norach_IX`) sit side-by-side in
  `Reports/`, `Logs/`, and `Backups/` with none overwritten. (The flip side of this same
  design choice is F6 — unique, but not chronologically sortable by filename.)

- **DRY-RUN is honestly and visibly distinguished from a real run.** `dry_run` is a
  first-class field on every `AuditEntry` (`audit_log.py:17`) and rendered distinctly in the
  HTML — a dedicated `.dry-tag` badge per action plus a "Dry-run" chip counter in the summary
  header (`report.py:100,116,139,166`) — so a technician or client skimming a report can't
  mistake a rehearsal run for one that actually changed the machine.

---

## Summary table

| # | Area | Issue | Severity |
|---|------|-------|----------|
| F1 | Report completeness / chain of custody | Restore point description, ID, and success/failure never persisted to audit log or report | HIGH |
| F2 | Chain of custody | DESTRUCTIVE/MODERATE risk-warning dialogs shown but never logged — no proof technician was warned | HIGH |
| F3 | Chain of custody | "Proceed without a restore point" decision silently unlogged | HIGH |
| F4 | Export/portability | USB-write-failure fallback silently strands all documentation in the client machine's own `%TEMP%` | HIGH |
| F5 | Audit log usefulness | `AuditEntry` carries no `hostname`/`run_id` — unusable for cross-machine tooling once files are copied/concatenated | HIGH |
| F6 | Multi-visit tracking | `run_id` is random (no time component) — Reports/Logs/Backups filenames don't sort chronologically | MEDIUM |
| F7 | Report completeness | No overall "generated at" timestamp shown at a glance in the HTML report | MEDIUM |
| F8 | Chain of custody | Elevation state (`is_admin`) never recorded per run | MEDIUM |
| F9 | Undo discoverability | `undo.ps1` has no link back to the report/audit log and doesn't flag irreversible actions that ran alongside it | MEDIUM |
| F10 | Audit log usefulness | `output_hash` is computed and stored but never read/verified anywhere — implies tamper-evidence it doesn't provide | LOW |
| F11 | Report completeness | Risk tier re-derived from the live catalog at report-gen time, not frozen at execution time | LOW |
| — | Export/portability | Self-contained HTML (no external CSS/JS/image refs) — verified clean | INFO |
| — | Export/portability | HTML output properly escaped, XSS-safe, test-covered — verified clean | INFO |
| — | Audit log usefulness | JSONL format itself is sound and trivially machine-parseable — verified clean | INFO |
| — | Multi-visit tracking | No filename collisions / no overwrite of prior visits — verified clean | INFO |
| — | Report completeness | DRY-RUN honestly distinguished from real runs in both log and report | INFO |
