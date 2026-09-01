# PortableFix — System Repair Catalog Audit (M03, M04, M05, M06, M09)

Scope: read-only audit of the five REPAIR-category YAML catalogs plus the execution/undo
plumbing they run through (`portablefix/executor.py`, `portablefix/gui/main_window.py`,
`portablefix/undo.py`, `portablefix/elevation.py`). Excludes previously-parked findings
(M05 regsvr32/UsoClient fire-and-forget exit-0 unreliability, stale DLL list, `-EA
SilentlyContinue` hiding error text on compound M05/M06 commands, UsoClient deprecation).

All 5 modules audited share `category: REPAIR`, which matters for one cross-cutting
finding (F15) below.

---

## HIGH severity

### F1 — `m03_disk` / `disk_full_scan_reboot`: chkdsk Y-prompt pipe is unverified in a headless host, and there is no timeout to catch a miss
**Command:** `Write-Output "Y" | chkdsk $env:SystemDrive /f /r`

`chkdsk /f /r` against the system/boot volume always needs exclusive access it can't
get live, so it always emits the "Would you like to schedule this volume to be checked
the next time the system restarts? (Y/N)" prompt and reads a keystroke from stdin.
Piping text into that prompt (`echo Y | chkdsk ...`) is a widely used trick, and
mechanically it should work here too: PowerShell's `X | native.exe` pipeline creates a
real anonymous pipe for the native child's stdin independent of the parent's own
console state. `-NonInteractive` is a red herring — it only governs the PowerShell
*host's* own prompting (e.g. `Read-Host`), not how an external console EXE reads its
own stdin, so it neither helps nor hurts here.

The real risk is environmental: `executor.py` launches `powershell.exe` with
`creationflags=subprocess.CREATE_NO_WINDOW` and no `stdin=` argument at all (so it
inherits whatever stdin handle this GUI process has — typically none/invalid for a
windowed app). Some chkdsk/autochk builds read the Y/N prompt via raw console-input
APIs rather than a buffered stdin read; if that's the case in a fully consoleless
context, the piped "Y" is silently dropped and chkdsk blocks waiting for a keypress
that will never arrive. Because `ActionRunner.run()` has **no timeout** on the
`Popen`/`for raw_line in process.stdout` loop, that failure mode is not a clean error —
it is a permanent hang of the entire action queue, indistinguishable from a frozen app,
requiring the user to kill the process (and losing the run's report/undo bookkeeping
for anything still queued).

**Suggested fix:**
1. Verify empirically once, outside the GUI: run the exact `POWERSHELL_PREFIX` argv
   from a `subprocess.Popen(..., stdin=subprocess.DEVNULL)` (i.e. no inherited console,
   matching the GUI's real launch context) and confirm chkdsk actually schedules
   (`fsutil dirty query` should flip to dirty) rather than hanging.
2. Regardless of (1), add a per-action timeout in `executor.py` (e.g.
   `process.communicate(timeout=...)` / poll loop) so any prompt that isn't answered
   degrades to a reported failure instead of an infinite hang. This is the
   defense-in-depth fix and should happen even if (1) comes back clean, since any
   future catalog command that blocks on input has the same exposure.
3. Cheaper alternative that sidesteps the prompt entirely: `fsutil dirty set
   $env:SystemDrive` marks the volume dirty directly (no chkdsk process, no prompt),
   achieving the same "full check at next boot" outcome that `disk_check_scheduled`
   already verifies via `fsutil dirty query`.

---

### F4 — `m04_integrity` / `dism_scanhealth`, `dism_restorehealth`: DISM's `\r`-only progress is invisible for the full run, not just "hard to read"
**Commands:** `DISM /Online /Cleanup-Image /ScanHealth`, `DISM /Online /Cleanup-Image /RestoreHealth`

This is a concrete, version-independent consequence of how the two pieces of code
interact, not a guess about DISM internals:

- DISM renders its percentage ticker using `\r` (carriage return, **no** trailing
  `\n`) to redraw one line in place.
- `executor.py`'s read loop is `for raw_line in process.stdout:` over a Python
  text-mode pipe. Python's line iteration splits strictly on `\n`. Any `\r`-delimited
  updates with no `\n` are **not** yielded as separate lines — they accumulate in
  Python's internal read buffer and are only flushed once an actual `\n` finally
  appears (a phase boundary, or process exit).

Net effect: `output_line` (wired to `self.console.appendPlainText`) fires nothing for
however long DISM spends ticking percentages — which for `/ScanHealth` and especially
`/RestoreHealth` (pulling files from Windows Update) is commonly several to tens of
minutes — then dumps one large chunk containing embedded `\r` bytes. To a user this is
indistinguishable from a hang, and, same as F1, there is no timeout to bound the wait
if it actually does hang. `dism_checkhealth` is low-impact here since it finishes in
seconds.

**Suggested fix:** stop reading by `\n`-delimited lines; read raw chunks/characters and
split on `[\r\n]`, emitting a signal on either delimiter:
```python
buf = ""
for chunk in iter(lambda: process.stdout.read(1), ""):
    if chunk in "\r\n":
        if buf:
            line = _clean_line(buf)
            self.captured_output.append(line)
            self.output_line.emit(line)
            buf = ""
    else:
        buf += chunk
```
(or use `process.stdout.readline()` isn't enough either — same `\n`-only semantics;
the fix must operate below line-granularity.) This also happens to be the same class
of fix that would make M03's `Optimize-Volume -Verbose` progress visible (F3).

---

### F13 — `m09_tuning` / `tune_visual_effects_performance`: setting `VisualFXSetting` alone likely changes nothing on screen
**Command:** `Set-ItemProperty ...\Explorer\VisualEffects -Name VisualFXSetting -Value 2`

`VisualFXSetting` under `HKCU\...\Explorer\VisualEffects` is the value the System
Properties → Performance Options dialog uses to remember *which radio button* was last
selected (Let Windows choose / Best appearance / Best performance / Custom) — it is
consumed by that dialog's own UI, not read by Explorer/DWM to drive actual effects at
runtime. The effects themselves (menu/combo-box animation, list-view shadows, drag
full windows, minimize/restore animation, etc.) are controlled by a separate set of
keys — chiefly `HKCU\Control Panel\Desktop\UserPreferencesMask` (a binary bitmask) plus
a few standalone values (`DragFullWindows`, `WindowMetrics\MinAnimate`, DWM peek/shadow
keys). Those only get rewritten when the Performance Options dialog's OK/Apply path
runs (which calls `SystemParametersInfo` for each toggle and broadcasts
`WM_SETTINGCHANGE`), not merely by poking `VisualFXSetting`.

If that holds (it is well-corroborated community knowledge but not independently
tested live in this read-only audit), the action's own description — "Sets visual
effects to best performance... takes effect after sign-out/sign-in" — overpromises:
sign-out/sign-in reloads the shell using whatever `UserPreferencesMask` etc. already
contain, which this command never touches, so nothing actually changes. That makes
this a placebo action shipped as a MODERATE-risk "fix."

**Suggested fix:** verify empirically on a real session (check `UserPreferencesMask`
bytes before/after + observe animations after sign-out/sign-in). If confirmed, either
also write the real keys, e.g.:
```powershell
Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name UserPreferencesMask -Type Binary -Value ([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00))
Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name DragFullWindows -Value 0
Set-ItemProperty 'HKCU:\Control Panel\Desktop\WindowMetrics' -Name MinAnimate -Value 0
Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -Name VisualFXSetting -Value 2
```
and update the matching `undo_command` to restore the prior `UserPreferencesMask`
bytes (capture them before overwriting, since "Value 0" alone has the same
does-nothing problem on undo).

---

## MEDIUM severity

### F2 — `m03_disk` / `disk_spotfix`: "online, without a restart" may not hold for the system volume
**Command:** `Repair-Volume -DriveLetter ($env:SystemDrive).TrimEnd(':') -SpotFix`

`Repair-Volume -SpotFix` is designed to fix spot-verifier-flagged NTFS errors without a
full offline chkdsk, but on the boot/system volume it cannot get the exclusive access
some fixes need (it can't dismount the drive Windows is running from). Depending on
what's actually wrong, it either resolves via NTFS self-healing (fine, matches the
description) or reports it could not fully repair online — with nothing in this
action's own output/description telling the user that `disk_full_scan_reboot` is the
next step for whatever SpotFix couldn't reach. Net effect is a possible false sense of
"fixed, no reboot needed" when only partial repair happened.

**Suggested fix:** check the exit code / output text for the "could not be repaired
online" / errors-remaining case and surface a pointer to `disk_full_scan_reboot`
instead of just reporting the action succeeded, e.g. append:
```powershell
$r = Repair-Volume -DriveLetter ($env:SystemDrive).TrimEnd(':') -SpotFix; $r
if ($r.HealthStatus -ne 'Healthy') { Write-Output 'Some errors need a full scan at restart — run "Full disk check at restart".' }
```

### F3 — `m03_disk` / `disk_optimize_volume`: `-Verbose` progress may not surface through the captured pipe
**Command:** `Optimize-Volume -DriveLetter ($env:SystemDrive).TrimEnd(':') -Verbose`

`Optimize-Volume`'s per-pass feedback is driven through the PowerShell Progress stream
(`Write-Progress`), which hosts commonly suppress or render only via
cursor-control/percent updates when output isn't an interactive console — exactly the
case here (`CREATE_NO_WINDOW` + redirected pipe). Combined with F4's read-loop
limitation, a multi-minute HDD defrag pass can show little or no incremental output.
Lower severity than F4 because TRIM on an SSD (the common case on modern hardware)
finishes in seconds, but on a spinning disk this reproduces the "looks hung, no
timeout" risk.

**Suggested fix:** force verbose records into the success stream so they survive a
plain stdout capture: append `4>&1` to the command (`Optimize-Volume ... -Verbose
4>&1`), and apply the same `\r`-aware read loop from F4.

### F5 — `m04_integrity` / `appx_reregister`: null `InstallLocation` produces a red wall of errors while still reporting success
**Command:** `Get-AppXPackage -AllUsers | Foreach {Add-AppxPackage -DisableDevelopmentMode -Register "$($_.InstallLocation)\AppXManifest.xml"}`

Framework/stub AppX package entries commonly have an empty `InstallLocation`, which
this composes into the bare path `\AppXManifest.xml`. `Add-AppxPackage -Register`
against that non-existent path fails per package with a full
`Deployment failed with HRESULT: 0x80073CF6...` block, and `Get-AppXPackage -AllUsers`
routinely returns dozens of such entries — so a normal, "nothing actually wrong" run of
this action floods the console with red errors that look catastrophic. Because these
are non-terminating cmdlet errors inside a `Foreach {}` scriptblock (no `-ErrorAction
Stop`, no `try/catch`), the loop completes and `powershell.exe`'s own exit code stays
`0` — so the GUI will likely record this action as **succeeded** directly underneath a
wall of red text, which is worse for user trust than a clean failure.

**Suggested fix:** filter out packages with no `InstallLocation` before attempting
registration, so the errors never fire for the expected/harmless case:
```powershell
Get-AppXPackage -AllUsers | Where-Object { $_.InstallLocation } | Foreach {
  try { Add-AppxPackage -DisableDevelopmentMode -Register "$($_.InstallLocation)\AppXManifest.xml" -ErrorAction Stop }
  catch { Write-Output "Skipped $($_.Name): $($_.Exception.Message)" }
}
```

### F6 — `m05_windows_update` / `wu_reset_cache`: rename is only existence-guarded, not outcome-verified — and the app's own confirmation-dialog flow creates a real race window
**Command:** guarded `Rename-Item ... -Force -ErrorAction SilentlyContinue` (both folders)

The `if (Test-Path ...)` guard only checks the folder exists *before* attempting the
rename; it never checks the rename actually succeeded. A directory rename fails if any
file inside is open (e.g. `DataStore\DataStore.edb` locked by a live `wuauserv`/BITS
handle), and `-ErrorAction SilentlyContinue` swallows that failure with no exit-code
signal — the action reports success while `SoftwareDistribution` was never touched.

This isn't just a theoretical race: `wu_stop_services` and `wu_reset_cache` are both
`risk: MODERATE`, and `_dispatch_action` in `main_window.py` (lines ~414-422) blocks on
a synchronous `QMessageBox.question` confirmation for *each* of them individually.
Between the user clicking "Yes" on "Stop Windows Update services" and clicking "Yes" on
"Reset Windows Update cache," Windows' own Update Orchestrator scheduled tasks can
restart `wuauserv` in the background (this is normal background behavior, not a
crash-recovery edge case), re-locking files inside `SoftwareDistribution` right before
the rename attempt.

Distinct from the already-known "`-EA SilentlyContinue` hides error text but still
exits 1" issue: this path can leave `powershell.exe` exiting **0** with the cache
folder completely untouched — a silent no-op reported as success, not a suppressed
failure.

**Suggested fix:** verify the post-condition instead of trusting the absence of a
terminating error:
```powershell
Rename-Item -Path "$env:WINDIR\SoftwareDistribution" -NewName "SoftwareDistribution.bak" -Force -ErrorAction SilentlyContinue
if (Test-Path "$env:WINDIR\SoftwareDistribution") { Write-Output "WARNING: SoftwareDistribution still in use, not reset."; exit 1 }
```
(same pattern for `catroot2`).

### F7 — `m05_windows_update` / `wu_stop_services` undo: `Start-Service` failures on a disabled/dependency-broken service are silent
**Undo command:** `Start-Service -Name wuauserv,bits,cryptsvc,msiserver -ErrorAction SilentlyContinue`

Ordering itself is fine: `wuauserv`/`bits`/`cryptsvc`/`msiserver` only formally depend
on `RPCSS` (always running), not on each other, and the LIFO undo-stack mechanism in
`main_window.py`/`undo.py` is correctly implemented and covered by
`tests/test_gui_main_window.py::...run_real_m05_order` (confirmed by reading the test:
it asserts `wu_reset_cache`'s undo text appears before `wu_stop_services`'s undo text
in `undo.ps1`, which is the right order). So "order" is not the real exposure here.

The real gap is `-ErrorAction SilentlyContinue` on the undo itself: if any one of the
four services has been set to `Disabled` startup type (common after third-party
"debloat"/privacy tools touch `wuauserv`), `Start-Service` fails with "service is
disabled" (Win32 1058) for that service, and the flag swallows it with no indication —
the one-click "Undo" silently leaves Windows Update partially stopped while reporting
nothing wrong.

**Suggested fix:** capture and surface per-service outcome instead of blanket
suppression:
```powershell
foreach ($svc in 'wuauserv','bits','cryptsvc','msiserver') {
  try { Start-Service -Name $svc -ErrorAction Stop }
  catch { Write-Output "Could not restart $svc : $($_.Exception.Message)" }
}
```

### F9 — `m06_network` / `net_hosts_reset`: one-time backup guard means a second run's "undo" reverts past the second run, to the very first
**Command:** backs up only `if (-not (Test-Path "$hostsPath.bak"))`; **undo:** restores from `.bak` unconditionally.

By design the `.bak` file is written once, ever (first execution only). If the user
runs "Reset hosts file" in one session, later hand-edits or re-infects the hosts file,
then runs "Reset hosts file" again in a later session, the second run's `undo_command`
restores `.bak` — which is still the *first* run's snapshot, not the state immediately
before the second run. Anything legitimately added between the two tool-runs is
silently discarded by an "Undo" the user reasonably expects to undo only the most
recent action. `wu_reset_cache` (M05) has this exact class of caveat and spells it out
in both `description_sk`/`description_en`; `net_hosts_reset`'s description says nothing
about it.

Existing coverage does not catch this:
`tests/test_m06_catalog.py::test_m06_catalog_hosts_reset_backup_guarded_against_double_run`
only asserts the strings `"-not (Test-Path"` / `"if (Test-Path"` are present in the
command text — it does not exercise two real runs and check the resulting file
contents, so the "guarded against double run" name overstates what's actually verified.

**Suggested fix:** either (a) document the limitation the same way `wu_reset_cache`
does, or (b) make the backup per-run (timestamped) and have undo restore the
most-recent backup rather than a single fixed `.bak`, e.g. back up to
`hosts.bak.<run_id>` and have `undo_command` target that run's own backup instead of a
shared filename.

### F12 — `m09_tuning` / `tune_power_high_performance`: hardcoded GUID may be hidden/absent on Modern Standby devices, with no existence check
**Command:** `powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`

The GUID is correct (this is the standard, well-known `SCHEME_MIN` "High performance"
scheme, and `381b4222-f694-41f0-9685-ff5bb260df2e` is correctly `SCHEME_BALANCED`).
The risk is availability: Microsoft hides the High Performance plan from `powercfg
/list` by default on many modern-standby/Connected-Standby laptops. A hidden-but-still-
provisioned scheme usually still activates fine via `/setactive` even though it's
invisible in `/list`/Control Panel, but on some OEM images the scheme can be absent
from the store entirely, in which case `/setactive` fails with "The specified power
scheme does not exist" and a non-zero exit — surfaced to the user as a raw, unexplained
powercfg error for what's marketed as a simple one-click MODERATE action. (Not
independently verified against a real Modern Standby machine in this audit — flagged
for empirical confirmation.)

**Suggested fix:** check existence first and give a clear message instead of a bare
powercfg failure:
```powershell
if ((powercfg /list) -match '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c') {
  powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
} else {
  Write-Output 'High performance plan is not available on this device (common on Modern Standby laptops).'
  exit 1
}
```

### F15 — Cross-cutting (all 5 modules): restore-point creation is gated on module category, not on the risk of what's actually queued
**File:** `portablefix/gui/main_window.py`, `_run_next()`:
```python
needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
    ModuleCategory.REPAIR, ModuleCategory.SECURITY,
)
```
All five audited modules are `category: REPAIR`. This means a batch consisting purely
of `SAFE` read-only reporting actions — e.g. `disk_smart_status` +
`net_ip_config_report` + `tune_power_plan_report`, none of which change anything —
still triggers a System Restore Point creation attempt on the first action, because
the gate checks the *module's* category rather than whether anything *risk-bearing* is
actually in `self._queue`. Costs 10s of seconds to a minute plus disk space for a batch
that changes nothing. Not one of the five originally-scoped angles, but it materially
affects every module in scope, so it's included here rather than filed separately.

**Suggested fix:** compute `needs_restore_point` from the queue, not the module:
`any(a.risk != RiskLevel.SAFE for _, a in queued_actions)`, evaluated once per batch
before the loop starts (or per-action against remaining risk in queue), rather than
per-category.

---

## LOW severity / verified correct (no action needed)

### F10 — `m06_network` / `net_hosts_reset`: ASCII encoding + CRLF line endings — verified correct, not a bug
`Set-Content ... -Encoding ASCII` with explicit `` `r`n `` line endings matches
Microsoft's own factory-default hosts file byte-for-byte in spirit (same copyright
header text, ASCII, CRLF, no BOM). This is actually the *safer* choice here: Windows
PowerShell 5.1's `-Encoding UTF8` always prepends a BOM (unlike PS7+), and a
BOM-prefixed hosts file risks mis-parsing by naive/legacy consumers. Using `ASCII`
sidesteps that PS5.1-specific landmine correctly. Only forward-looking caveat: `ASCII`
will silently mangle any non-ASCII byte to `?` if a future edit adds e.g. an
IDN/localized comment — low risk given the content is fixed, but worth a comment in
the YAML if anyone touches this string later.

### F11 — `m06_network` / `net_winsock_reset`, `net_tcpip_reset`: no pre-flight elevation check despite `is_admin` being known
The GUI already computes and displays `self.is_admin` (banner + "restart as admin"
button in `main_window.py`), and does not require elevation to launch or to queue
actions. Neither `netsh winsock reset` nor `netsh int ip reset` carry
`-ErrorAction SilentlyContinue`, so a non-elevated failure ("this command requires
elevation...") is not silently hidden and its exit code should propagate normally —
this is not a silent-failure bug. It is, however, a wasted-effort/UX gap: a
non-elevated user pays for a confirmation dialog and (per F15) a restore-point
creation attempt before hitting a raw, untranslated netsh privilege error at the very
end, rather than being told upfront via the `is_admin` flag the app already has.

**Suggested fix (low priority):** if `not self.is_admin`, disable or badge
`REQUIRES_REBOOT`/admin-only actions' checkboxes with a tooltip pointing at "restart as
admin," instead of only discovering the requirement at execution time.

### Undo LIFO ordering (M05/M09 generally) — verified correct
`main_window.py` accumulates `action.undo_command` into `self._undo_steps` in
execution order and writes `undo.ps1` via `undo.create_undo_script(..., steps=
list(reversed(self._undo_steps)))` after every successful action (`undo.py` just joins
the given list with newlines — no reordering of its own). This is genuine LIFO
(last-run action's undo listed first) and is exercised by
`tests/test_gui_main_window.py::...run_real_m05_order`. `tune_power_high_performance`
and `tune_visual_effects_performance`'s undo commands are semantically simple
(single `powercfg /setactive` / single `Set-ItemProperty`) so ordering has no
dependency concerns for M09. The known "restores Balanced, not your actual previous
plan" and "restores automatic mode, not your actual previous custom setting"
limitations are both already disclosed accurately in-catalog
(`description_sk`/`description_en` for `tune_power_high_performance` and
`tune_visual_effects_performance`) — flagged as reviewed, not as a gap.

---

## Summary table

| # | Module | Action ID | Issue | Severity |
|---|--------|-----------|-------|----------|
| F1 | m03_disk | disk_full_scan_reboot | chkdsk Y-prompt pipe unverified headless + no timeout | HIGH |
| F4 | m04_integrity | dism_scanhealth / dism_restorehealth | `\r`-only DISM progress invisible for full run (Python `\n`-only line iteration) | HIGH |
| F13 | m09_tuning | tune_visual_effects_performance | VisualFXSetting alone likely doesn't change any real effect | HIGH |
| F2 | m03_disk | disk_spotfix | "no restart" claim may not hold on system volume; no fallback pointer | MEDIUM |
| F3 | m03_disk | disk_optimize_volume | `-Verbose` progress likely not surfaced through captured pipe | MEDIUM |
| F5 | m04_integrity | appx_reregister | null InstallLocation → red error wall + false-success exit 0 | MEDIUM |
| F6 | m05_windows_update | wu_reset_cache | rename outcome unverified; real race via per-action confirm dialogs | MEDIUM |
| F7 | m05_windows_update | wu_stop_services (undo) | Start-Service failure on disabled service silently swallowed | MEDIUM |
| F9 | m06_network | net_hosts_reset | one-time backup guard → stale undo target on repeat runs; test doesn't catch it | MEDIUM |
| F12 | m09_tuning | tune_power_high_performance | GUID may be hidden/absent on Modern Standby devices, no existence check | MEDIUM |
| F15 | all 5 (cross-cutting) | _run_next() gating | restore point gated on module category, not queued risk | LOW-MEDIUM |
| F11 | m06_network | net_winsock_reset / net_tcpip_reset | no pre-flight elevation check (graceful failure, but wasted steps) | LOW |
| F10 | m06_network | net_hosts_reset | ASCII/CRLF encoding — verified correct | INFO |
| — | m05/m09 | undo LIFO ordering | verified correct and tested | INFO |
