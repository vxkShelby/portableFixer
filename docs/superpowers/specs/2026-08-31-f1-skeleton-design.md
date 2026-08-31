# PortableFix — F1 Skeleton Design

Scope: Phase F1 only from `PortableFix_SPEC.md` — launcher, elevation, module
engine, logging, dry-run plumbing, GUI skeleton, M01 diagnostics module.
F2–F6 (cleanup, repairs, autoruns/security audit, tuning/drivers, online
extensions, polish) are separate design/plan cycles built on top of this
skeleton.

## Tech stack

Python 3.12 + PySide6, packaged with PyInstaller `--onedir`. Backend uses
`subprocess` + `pywin32`, shelling out to Windows PowerShell 5.1 via
`powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command`
(Bypass scoped to the child process only, never changes system policy).

## Directory layout (F1 subset)

```
PortableFix/
├── PortableFix.cmd            # launcher: admin check → elevate attempt → run exe
├── App/                       # PyInstaller onedir build
│   ├── PortableFix.exe
│   └── _internal/
├── Modules/
│   └── m01_diagnostics/
│       └── actions.yaml       # declarative action catalog for this module
├── Data/
│   ├── SHA256SUMS             # basic self-check input (no signing yet — F6)
│   └── settings.json          # UI language + last dry-run toggle, lives on USB
├── Logs/                      # per-run JSONL audit logs
└── Reports/                   # created empty; populated starting F2 (M11)
```

`Tools/` (Sysinternals, smartmontools, 7zip) and `Backups/` (quarantine,
registry exports, undo scripts) are not created in F1 — nothing bundled or
mutated yet that needs them. They get added when M02/M07/M08 land.

## Elevation model

**Deviation from spec §7:** the spec calls for an exe manifest with
`requestedExecutionLevel="requireAdministrator"`. That conflicts with
acceptance criterion #2 ("without admin rights, starts and offers read-only
diagnostics only, no errors") — a `requireAdministrator` manifest makes
Windows force a UAC prompt on every launch, and Cancel exits the process
entirely, so a read-only mode without admin becomes unreachable.

F1 instead uses:

- Exe manifest: `asInvoker` (no forced elevation).
- `PortableFix.cmd`: checks admin via `net session` or equivalent; if not
  admin, attempts `Start-Process -Verb RunAs` to relaunch elevated. If the
  UAC prompt is cancelled, falls through to launching the exe unelevated
  instead of aborting.
- App startup: calls `IsUserAnAdmin()` (pywin32/`ctypes.windll.shell32`).
  If not admin, GUI runs in **read-only diagnostic mode** — M01 actions
  only, all other module categories disabled/greyed with an explanation —
  plus a visible "Restart as Administrator" button that re-launches the exe
  via `ShellExecute(..., "runas", ...)`.

## Module engine

Each module is a folder under `Modules/` containing `actions.yaml`:

```yaml
module_id: m01_diagnostics
actions:
  - id: os_info
    label_sk: "Informácie o systéme"
    label_en: "System information"
    risk: SAFE
    command: "Get-ComputerInfo"
    description_sk: "Základné info o OS, verzii, builde."
    description_en: "Basic OS/version/build info."
```

- Engine scans `Modules/*/actions.yaml` at startup, validates required
  fields, and populates the GUI action list per module/category.
- Execution: each run action is dispatched to a `QThread` worker running
  `subprocess.run([...])` (or `Popen` for streaming), never on the GUI
  thread. Stdout/stderr are streamed line-by-line back via Qt signals into
  the live console panel.
- Risk labels (`SAFE` / `MODERATE` / `DESTRUCTIVE` / `REQUIRES_REBOOT`) are
  read from the action definition and drive UI color-coding. F1 only ships
  `SAFE` actions (M01 is read-only), but the confirmation-gating logic for
  `MODERATE`/`DESTRUCTIVE` is built now since M02 needs it immediately
  after and retrofitting it later would touch every module.
- Global **DRY-RUN** toggle: when on, the engine prints the exact command
  it would run instead of executing it. M01 has no destructive actions, so
  in F1 this is pure plumbing validated end-to-end, ready for M02+.

## Logging

One JSONL audit file per run: `Logs/<run-id>.jsonl`. One line per executed
action with: timestamp, module id, action id, exact command, exit code,
stdout/stderr hash, dry-run flag. The live console is a separate
human-readable view of the same stream — no HTML/JSON report generation in
F1 (that's M11, F2+).

## Path resolution & portable fallback

`BASE_DIR` is computed from `Path(sys.executable).parent` when frozen
(PyInstaller), else `Path(__file__).parent` in dev — the USB drive letter
is never hardcoded anywhere. On startup, the app test-writes a temp file
under `BASE_DIR`; if that fails (read-only media, full disk), it falls back
to `%TEMP%\PortableFix` and shows a persistent warning banner in the GUI
explaining the fallback and that residue will be left on the PC (spec
guardrail: normally no residue outside USB).

## GUI shell

- **Top bar:** hostname, OS build, admin yes/no, online/offline (stubbed
  false for F1 — connectivity check is M12), free space, global DRY-RUN
  toggle.
- **Left panel:** module categories — just "Diagnostika" in F1.
- **Center panel:** M01 actions as checkboxes with risk tag + one-line
  description (SK/EN per active language).
- **Bottom/right panel:** live console streaming subprocess output.
- **Threading:** all subprocess execution in worker threads; GUI thread
  never blocks.
- Dark mode, tested down to 1366×768 per spec §6.

## i18n

Small dict-based SK/EN lookup module. Default SK. Toggle persisted in
`Data/settings.json` (lives on the USB, not the target PC — doesn't violate
the no-residue guardrail).

## Out of scope for F1 (explicitly deferred)

- Self-integrity signing infrastructure (F6) — F1 only reads
  `Data/SHA256SUMS` if present and warns on mismatch; generation/signing
  tooling comes later.
- System Restore Point creation (needed starting F3/M04 repairs).
- Quarantine, undo.ps1 generation, HTML/JSON reporting (M11), all cleanup
  (M02) and security audit (M08) actions.
- Online connectivity detection (M12) — top bar shows a stubbed offline
  state.

## Testing

- Launch from FAT32 and exFAT USB with drive letter changed between runs —
  verify no hardcoded path breaks.
- Launch without admin — verify read-only mode, no errors, "Restart as
  Administrator" works.
- Toggle DRY-RUN — verify M01 actions still just execute (read-only either
  way) but the plumbing path (print-would-run) is exercised via a temporary
  test action or unit test on the engine, since no destructive action
  exists yet to visibly prove it.
- Corrupt one byte in a file under `Data/SHA256SUMS`-covered set — verify
  self-check warns.
