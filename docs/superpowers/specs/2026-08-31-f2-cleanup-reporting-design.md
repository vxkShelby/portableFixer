# PortableFix — F2 Design: Cleanup (M02) + Reporting (M11)

Scope: Phase F2 from `PortableFix_SPEC.md`'s phasing table — the cleanup
module and the reporting module, built on top of the F1 skeleton (module
engine, executor, audit log, GUI shell, elevation, i18n, all already
merged to `main`). F3+ (repair modules, restore-point-and-undo
infrastructure for *reversible* changes, autoruns/security audit, tuning,
drivers, online extensions) are separate future cycles.

## Decisions locked in during brainstorming

- **M02 scope is the full spec table**, including the four `DESTRUCTIVE`
  items (Windows.old removal, component-store `ResetBase`, oldest shadow
  copy deletion, stale user-profile removal) — pulled forward from F3 at
  explicit request, rather than the safer SAFE+MODERATE-only subset.
- **Browser cache cleanup is explicitly out of scope for F2** — it wasn't
  part of the chosen option and adds real scope (per-browser profile
  enumeration, opt-in UX) with no dependency from anything else in F2. Easy
  to add later as its own module-catalog entry.
- **Disk Cleanup automation (`cleanmgr /sageset` + `/sagerun` +
  `StateFlags0001` registry profile) is out of scope** — redundant with the
  manual per-target list already in scope, and more fragile across Windows
  builds. Large/duplicate-file scanning is explicitly analysis-only in the
  master spec, not cleanup, and stays out of F2 too.
- **No `undo.ps1` subsystem yet.** Pulling DESTRUCTIVE items forward means
  Restore Point + a harder confirmation dialog, not full undo generation.
  M02's destructive actions delete things outright (files, profiles,
  update-rollback capability) — there is nothing there for an undo script
  to meaningfully reverse. `undo.ps1` becomes real once F3's repair
  actions start touching registry/services/network settings.
- **Restore Point is best-effort, not blocking.** Windows throttles
  `Checkpoint-Computer` (default: one per 24h) and restore points may be
  disabled entirely (some SKUs, some IT policies). If creation fails,
  PortableFix shows a clear warning and still lets the user proceed after
  an explicit acknowledgment — refusing to run cleanup because Windows
  itself won't cooperate would violate the "offline-first, never block"
  principle for something outside the tool's control.

## M02 action catalog

Same `Modules/<id>/actions.yaml` schema as M01, with one addition: an
optional `preview_command`. Full list below, `[P]` marks entries with a
preview command (see next section for why and how).

| id | risk | command source |
|---|---|---|
| user_temp | SAFE [P] | `Remove-Item "$env:TEMP\*" -Recurse -Force -EA SilentlyContinue` |
| system_temp | SAFE [P] | `Remove-Item "$env:WINDIR\Temp\*" -Recurse -Force -EA SilentlyContinue` |
| recycle_bin | MODERATE [P] | `Clear-RecycleBin -Force -EA SilentlyContinue` |
| prefetch | SAFE [P] | `Remove-Item "$env:WINDIR\Prefetch\*" -Force -EA SilentlyContinue` |
| wer_reports | SAFE [P] | clears `ReportQueue`/`ReportArchive` under `C:\ProgramData\Microsoft\Windows\WER` |
| cbs_logs | SAFE [P] | removes `CBS*.log` under `$env:WINDIR\Logs\CBS` except the newest |
| thumbnail_cache | SAFE [P] | clears `%LOCALAPPDATA%\Microsoft\Windows\Explorer\*.db`, restarts `explorer.exe` |
| font_cache | MODERATE [P] | stops `FontCache` service, clears its cache dir, restarts the service |
| delivery_optimization | SAFE [P] | `Delete-DeliveryOptimizationCache -Force` |
| windows_update_cache | MODERATE [P] | stops `wuauserv`+`bits`, clears `SoftwareDistribution\Download`, restarts both |
| component_store_cleanup | MODERATE [P] | `Dism /Online /Cleanup-Image /StartComponentCleanup` |
| component_store_resetbase | DESTRUCTIVE [P] | `Dism /Online /Cleanup-Image /StartComponentCleanup /ResetBase` |
| windows_old_removal | DESTRUCTIVE [P] | `takeown` + `icacls` + `Remove-Item C:\Windows.old -Recurse -Force` |
| shadow_copies_oldest | DESTRUCTIVE [P] | `vssadmin delete shadows /for=C: /oldest` |
| hibernation_off | MODERATE [P] | `powercfg /h off` |
| stale_user_profiles | DESTRUCTIVE [P] | `Get-CimInstance Win32_UserProfile \| Where {...} \| Remove-CimInstance` |

`component_store_cleanup`/`component_store_resetbase` both preview via
`Dism /Online /Cleanup-Image /AnalyzeComponentStore` (this is literally
the master spec's own suggested pre-cleanup analysis command).

## The `preview_command` mechanism

The master spec requires dry-run mode to show "exactly what would be
deleted/changed, including sizes and file counts" — not just echo the
command text back, which is all F1's dry-run does today (adequate for
M01's read-only diagnostics, where dry-run and real-run are identical
anyway).

**Design:** `ActionDef` gains an optional `preview_command: str | None`
field. Dispatch logic in `MainWindow` (not a change to
`executor.build_execution_plan`, which keeps its existing two-argument
signature and behavior unchanged) decides which command string to hand
the executor:

- **Dry-run ON, action has a `preview_command`:** run the preview command
  for real (`build_execution_plan(action.preview_command, dry_run=False)`)
  — it's inherently read-only (sizing/counting/analysis), so there's
  nothing unsafe about actually executing it. Its output (e.g. "Would
  delete 1,204 files, 340 MB from %TEMP%") streams to the console exactly
  like a real run would, satisfying the spec's requirement.
- **Dry-run ON, no `preview_command`:** unchanged F1 behavior — print
  `[DRY-RUN] <command>` without executing anything (this remains M01's
  path, and covers any M02 action where a numeric preview doesn't apply,
  e.g. `component_store_resetbase`'s preview is a static warning string
  since Dism has no "what would ResetBase do" dry-run mode).
- **Dry-run OFF:** unchanged — run `action.command` for real.

The audit log still records `dry_run=True` for the preview-command path
(nothing destructive happened), distinguishing it from a real cleanup run
in the JSONL trail and the eventual report.

## Restore Point + hard confirmation for DESTRUCTIVE actions

New module `portablefix/restore_point.py`:
`create_restore_point(description: str) -> bool` — runs
`Enable-ComputerRestore -Drive "C:\"` then
`Checkpoint-Computer -Description <description> -RestorePointType MODIFY_SETTINGS`,
returns `True`/`False` based on exit code (best-effort, never raises).

In `MainWindow`, before running the *first* `DESTRUCTIVE`-risk action in a
queued batch (checked once per run, not once per action):
1. Attempt `create_restore_point(...)`. If it fails, show a
   `QMessageBox.warning` explaining a restore point could not be created
   (Windows-imposed limit or disabled feature) and ask whether to proceed
   anyway — Yes continues, No cancels just the remaining DESTRUCTIVE items
   in the queue (SAFE/MODERATE items already queued still run).
2. Regardless of the restore-point outcome (once accepted), show a second,
   visually distinct confirmation (`QMessageBox.warning` icon, not the
   plain `question` icon F1's generic MODERATE/DESTRUCTIVE gate uses) for
   *each* DESTRUCTIVE action specifically, naming the action and stating
   plainly that this change cannot be undone through PortableFix. This
   replaces the generic gate for DESTRUCTIVE risk only — SAFE/MODERATE
   actions keep using F1's existing single gate.

## M11 reporting

New module `portablefix/report.py`:
`generate_report(base_dir: Path, run_id: str, modules: list[ModuleDef], language: str, snapshot_before: dict, snapshot_after: dict) -> tuple[Path, Path]`
— reads `Logs/<run_id>.jsonl` back, joins each entry against the already-
loaded `modules` list (by `module_id`/`action_id`) to recover the human
label and risk for display (no schema change to `AuditEntry` — report
generation is a pure read-side join over data the GUI already has in
memory), and writes:

- `Reports/<hostname>_<run_id>.html` — self-contained, inline CSS, no
  external assets (works fully offline). Sections: machine identification
  (hostname, OS via Python's stdlib `platform`/`socket` — not another
  PowerShell round-trip, since this is just for the report header), free
  space before/after (`shutil.disk_usage`, taken at batch start and batch
  end), the action list with exit codes/risk/dry-run flag, and a
  "Requires restart" section. For F2, no action in the M02 catalog is
  classified `REQUIRES_REBOOT` (that risk level exists for F3's `chkdsk
  /f /r` etc.), so this section renders as empty/absent for now — the
  report template supports it so F3 doesn't need a report-format change.
  A findings section for M08's security results is likewise present in
  the template but empty until M08 exists.
- `Reports/<hostname>_<run_id>.json` — the same data, machine-readable.

**Trigger:** `MainWindow.run_selected_actions()` takes the pre-run
snapshot when the queue is non-empty; when `_run_next()` drains the queue
to empty *after having run at least one action this batch*, it takes the
post-run snapshot and calls `generate_report(...)` once. Opening the app
and closing it without running anything produces no report.

## Testing

Same TDD discipline as F1: every new pure-logic module (`restore_point.py`
best-effort wrapper, `report.py`'s HTML/JSON generation, the
`preview_command` dispatch logic) gets unit tests against real file I/O
(no mocking the thing under test). The DESTRUCTIVE-confirmation and
restore-point-failure UI paths get `pytest-qt` tests monkeypatching
`QMessageBox`/`create_restore_point` the same way F1's Task 9 fix wave
tested the restart-as-admin failure path.

## Out of scope for F2 (explicitly deferred)

- Browser cache cleanup (opt-in, per-profile enumeration).
- `cleanmgr`/`StateFlags` automation, large/duplicate file scanning.
- `undo.ps1` generation subsystem (F3, once there's something reversible
  to generate it for).
- Any M02 action requiring `REQUIRES_REBOOT` classification (none in this
  catalog) — the report template supports the section, no action uses it
  yet.
