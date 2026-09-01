# M02 Cleanup Catalog Audit (PortableFix)

Scope: `Modules/m02_cleanup/actions.yaml` (16 actions) + `portablefix/executor.py`.
Cross-referenced: `portablefix/models.py`, `portablefix/gui/main_window.py`, `Modules/m05_windows_update/actions.yaml`, `tests/test_m02_catalog.py`, git history (`cec59dc`, `2026-08-31-f2-cleanup-reporting.md`), and live read-only checks on the current Win11 machine (no repo files or system state modified; only `Test-Path`/`Get-Command`/`Get-ChildItem`/`Get-CimInstance` queries were run).

Key mechanism confirmed first-hand from commit `cec59dc`'s message and reproduced live in this session: `powershell -NoProfile -NonInteractive -Command "<stmt1>; <stmt2>"` returns **exit code 1** whenever the *last* statement is a cmdlet that hit a non-terminating error, even one fully suppressed by `-EA SilentlyContinue` (it clears `$?` but not the exit code). Separately — confirmed live via `cmd.exe /c exit 3` — when the last statement is a **native** executable, that program's own exit code becomes `powershell.exe`'s exit code directly (`$LASTEXITCODE` passthrough), a *different* failure class. `portablefix/gui/main_window.py:274` treats any non-zero `exit_code` as `status_failed` with no further interpretation, and `portablefix/executor.py` has no subprocess timeout and `main_window.py` has no cancel/abort control anywhere — a hang in any one action blocks the rest of the queue indefinitely with no in-app recovery.

---

## CRITICAL

### 1. `cbs_logs` — missing the ErrorVariable/exit-guard fix applied to its 9 siblings
**Issue:** Commit `cec59dc` added `-ErrorVariable errs` + a trailing `Write-Output` to every other deletion action specifically to stop suppressed locked-file errors from flipping the action to exit 1 with zero explanation (see mechanism above, and `tests/test_m02_catalog.py::test_deletion_actions_report_skipped_locked_items_and_exit_zero`, which lists 9 action ids and conspicuously omits `cbs_logs`). `cbs_logs`'s command is:
```
Get-ChildItem "$env:WINDIR\Logs\CBS\CBS*.log" -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1 | Remove-Item -Force -EA SilentlyContinue
```
`Remove-Item` here is both the last statement *and* receives multiple piped objects when 2+ files match; a single locked file among them clears `$?` for the whole pipeline the same way `user_temp` did pre-fix, and the action reports exit 1 with an empty console — indistinguishable from a real failure. This is a latent regression of the exact class of bug `cec59dc` was written to close.

**Exact suggested fix** (also fixes Finding #8's glob gap in one pass):
```yaml
command: "Get-ChildItem \"$env:WINDIR\\Logs\\CBS\\CBS*.log\" -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1 | Remove-Item -Force -EA SilentlyContinue -ErrorVariable errs; Get-ChildItem \"$env:WINDIR\\Logs\\CBS\\CbsPersist_*.cab\" -EA SilentlyContinue | Remove-Item -Force -EA SilentlyContinue -ErrorVariable +errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
```

### 2. `stale_user_profiles` — same missing guard, on a DESTRUCTIVE action
**Issue:** No `-EA`/`-ErrorVariable` anywhere in:
```
Get-CimInstance Win32_UserProfile | Where-Object {...} | Remove-CimInstance
```
`Remove-CimInstance` failing on one profile (locked hive, permission error, profile dir on a since-removed network path) clears `$?` and the whole DESTRUCTIVE action reports exit 1 with no indication of which profiles (if any) were actually removed. For a DESTRUCTIVE, irreversible, confirmation-gated action this is worse than the SAFE-tier version of the bug: the user sees "FAILED" after approving an irreversible deletion and has no way to tell from the app whether 0, some, or all selected profiles were removed.

**Exact suggested fix:**
```yaml
command: "Get-CimInstance Win32_UserProfile | Where-Object {-not $_.Special -and -not $_.Loaded -and $_.LastUseTime -ne $null -and $_.LastUseTime -lt (Get-Date).AddDays(-180)} | Remove-CimInstance -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
```

### 3. `font_cache` / `windows_update_cache` — service restart failures are completely unmonitored (false *success*)
**Issue:** In both actions, only the middle `Remove-Item` gets `-ErrorVariable errs`; the bookending `Stop-Service`/`Start-Service` calls have `-EA SilentlyContinue` with **no** `-ErrorVariable` and aren't the last statement, so their failures are invisible everywhere — not counted in `$errs`, not in the console, not in the audit log/report, and don't affect the exit code either way. If `Start-Service wuauserv,bits` (a service known to sometimes refuse to (re)start after being stopped mid-update) or `Start-Service FontCache` fails, the action still prints `"Skipped locked/in-use items: 0"` and exits 0 — a confident-looking success message while Windows Update or font rendering may now be silently broken. This is the mirror-image bug of the one `cec59dc` fixed: that fix traded false failures for a new false-success blind spot on the one thing (service health) users would most want to know about.

**Exact suggested fix** (`windows_update_cache`; apply the same shape to `font_cache`):
```yaml
command: "Stop-Service wuauserv,bits -Force -EA SilentlyContinue; Remove-Item \"$env:WINDIR\\SoftwareDistribution\\Download\\*\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; $svcErr = $null; Start-Service wuauserv,bits -EA SilentlyContinue -ErrorVariable svcErr; if ($svcErr) { Write-Output (\"WARNING: Windows Update service failed to restart. Skipped locked/in-use items: \" + $errs.Count) } else { Write-Output (\"Skipped locked/in-use items: \" + $errs.Count) }"
```

### 4. `thumbnail_cache` — unguarded `Start-Process explorer.exe`, and a `Remove-Item` hang leaves the desktop dead with no recovery
**Issue:** `Start-Process explorer.exe` has no `-EA SilentlyContinue`. If it throws (any reason), the trailing `Write-Output` never runs and, more importantly, Explorer is never relaunched. Separately — directly answering the audit's own question — `Remove-Item` sits between `Stop-Process -Name explorer` and `Start-Process explorer.exe` in the same statement chain, so if it hangs (AV real-time-scan lock contention on `thumbcache_*.db`, a stuck profile), `Start-Process explorer.exe` simply never gets reached. `portablefix/executor.py` has **no subprocess timeout** (confirmed by reading the file: `subprocess.Popen(...)` → blocking `for raw_line in process.stdout` → `process.wait()`, no `timeout=`), and `portablefix/gui/main_window.py` has **no cancel/abort control anywhere** (grepped for cancel/abort/terminate/kill — zero matches). Net effect: taskbar and desktop stay gone for as long as the hang lasts, with no in-app way to stop it — most users won't know `Ctrl+Shift+Esc → File → Run new task → explorer.exe` as a manual recovery.

**Exact suggested fix (catalog-level, cheap):**
```yaml
command: "Stop-Process -Name explorer -Force -EA SilentlyContinue; Remove-Item \"$env:LOCALAPPDATA\\Microsoft\\Windows\\Explorer\\thumbcache_*.db\" -Force -EA SilentlyContinue -ErrorVariable errs; Start-Process explorer.exe -EA SilentlyContinue; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
```
**Real fix (executor-level, out of catalog's reach):** add a timeout to `ActionRunner.run()` in `portablefix/executor.py` (e.g. poll with `process.poll()`/`communicate(timeout=...)` and `process.kill()` past a cap) plus a Cancel button in `main_window.py` wired to kill `self._runner`'s subprocess. This affects every module, not just M02, but `thumbnail_cache` is the one action in this catalog where a hang has an immediately visible, high-anxiety symptom (no desktop) rather than just a stuck progress bar.

---

## HIGH

### 5. `component_store_cleanup` / `component_store_resetbase` — DISM exit code 3010 (reboot required) reported as failure
**Issue:** Both run `Dism.exe ...` as the sole, last statement. Per the confirmed native-exit-code-passthrough mechanism, DISM's own exit code becomes the wrapper's exit code directly. DISM returns **3010** ("success, reboot required") extremely commonly after component cleanup — this is standard, documented Windows servicing behavior, not an error — yet `main_window.py:274` treats any nonzero code as `status_failed`. Every successful-but-reboot-pending run of either action shows up as a red "FAILED" row in the batch summary dialog.

**Exact suggested fix:**
```yaml
command: "Dism.exe /Online /Cleanup-Image /StartComponentCleanup; if ($LASTEXITCODE -eq 3010) { Write-Output \"Component store cleaned. A restart is required to finish.\" } elseif ($LASTEXITCODE -ne 0) { Write-Output \"DISM failed with exit code $LASTEXITCODE\" } else { Write-Output \"Component store cleaned.\" }"
```
(mirror for `component_store_resetbase` with `/ResetBase` appended). Ending on `Write-Output` also makes the wrapper exit 0 in the 3010 case without needing to touch `$LASTEXITCODE` itself.

### 6. `shadow_copies_oldest` — vssadmin's "no shadow copies" case is a flat, unexplained failure
**Issue:** `vssadmin delete shadows /for=C: /oldest /quiet` is a native call with no pre-check. When zero shadow copies exist for C: — empirically confirmed on this machine right now (`Get-CimInstance Win32_ShadowCopy` → 0 results) and common on Windows 10/11 since System Protection is off by default on many installs — `vssadmin` prints `Error: No items found that satisfy the query being processed.` and exits non-zero, which becomes the wrapper's exit code via the same native-passthrough mechanism as Finding #5. The action shows "FAILED" for a perfectly normal, expected state (nothing to clean).

**Exact suggested fix:**
```yaml
command: "$vol = (Get-CimInstance Win32_Volume -Filter \"DriveLetter='C:'\" -EA SilentlyContinue).DeviceID; $sc = Get-CimInstance Win32_ShadowCopy -EA SilentlyContinue | Where-Object { $_.VolumeName -eq $vol }; if (-not $sc) { Write-Output \"No shadow copies exist on C: - nothing to delete.\" } else { vssadmin delete shadows /for=C: /oldest /quiet; Write-Output \"Oldest shadow copy on C: deleted.\" }"
```

### 7. `delivery_optimization` — zero error handling on a SAFE-tier action
**Issue:** `Delete-DeliveryOptimizationCache -Force` is the entire command — no `-EA`, no `-ErrorVariable`, no try/catch. Confirmed the cmdlet itself is present on this machine (`Get-Command` → module `DeliveryOptimization` v1.0.3.0, so availability is not the problem), but any runtime hiccup (DoSvc disabled/not running, nothing cached yet) throws straight through as a raw, unexplained non-zero exit for something the catalog itself classifies as SAFE and routine.

**Exact suggested fix:**
```yaml
command: "try { Delete-DeliveryOptimizationCache -Force -EA Stop; Write-Output \"Delivery Optimization cache cleared\" } catch { Write-Output (\"Delivery Optimization cache clear skipped: \" + $_.Exception.Message) }"
```

### 8. `cbs_logs` glob misses the real space consumer, and is a no-op in the common case
**Issue (preview/command accuracy, distinct from Finding #1):** Live-checked on this machine: `$env:WINDIR\Logs\CBS\CBS*.log` matches exactly **1 file** — the active `CBS.log`, currently **≈194 MB** (203,670,913 bytes) — which `Select-Object -Skip 1` deliberately protects ("keeps the newest"). Result: on a typical single-log-file system this action deletes **nothing at all**, while sitting next to the single largest file in that folder without ever touching it by design. Separately, Windows periodically compresses rotated CBS history into `CbsPersist_<timestamp>.cab` archives in the same folder (none present on this particular machine, but well documented elsewhere as the actual multi-hundred-MB accumulation over a system's lifetime) — the glob `CBS*.log` cannot match `.cab` files at all, so even where they do accumulate this action would never reach them.

**Exact suggested fix:** see Finding #1's combined fix, which adds an unconditional `CbsPersist_*.cab` sweep alongside the existing skip-newest `.log` logic.

---

## MEDIUM

### 9. No executor timeout + no cancel control (affects the whole module, sharpest here)
**Issue:** `portablefix/executor.py`'s `ActionRunner.run()` has no timeout on `subprocess.Popen`/`process.wait()`, and `main_window.py` exposes no cancel/abort anywhere. Within M02 this is most dangerous for: `component_store_cleanup`/`resetbase` (DISM can legitimately run 10–60+ minutes, or hang indefinitely on a damaged WinSxS store), `windows_update_cache`/`font_cache` (`Stop-Service` on `wuauserv`/`bits`/`FontCache` has no PowerShell-level timeout and these services are known to occasionally hang on stop), and `windows_old_removal` (`takeown /F ... /R` walking a large `Windows.old` tree can take many minutes on slow/USB-attached storage — relevant for a tool literally named "USB Fixer"). Any one hang blocks every action still queued behind it with zero recourse.
**Suggested fix (executor-level, not catalog):** add a timeout parameter to `ActionRunner`/`build_execution_plan`, and a Cancel button in `main_window.py` that kills `self._runner`'s subprocess (`process.kill()`) and unblocks `_run_next()`.

### 10. Risk-tier inconsistency: `thumbnail_cache` (SAFE) vs `font_cache` (MODERATE)
**Issue:** `thumbnail_cache` kills and restarts **the entire visible desktop shell** (`Stop-Process -Name explorer`) and is tagged SAFE; `font_cache` only stops/restarts a background service (`FontCache`) that most users never interact with directly, and is tagged MODERATE. The ordering is inverted relative to actual user-visible impact/risk. Not necessarily wrong in isolation, but worth reconciling: either both should be SAFE (both self-healing, both routine), or `thumbnail_cache` should be MODERATE given it's the one with a visible, if brief, "my desktop disappeared" moment for the user.
**Suggested fix:** re-tier one of the two for internal consistency — cheapest is bumping nothing and just documenting the rationale (Explorer restart is near-instant and extremely well-trodden vs. a service that can fail to restart per Finding #3), or, if consistency matters more than history, drop `font_cache` to SAFE once Finding #3's warning-on-failure fix lands (since the failure mode becomes visible either way).

### 11. `hibernation_off` (MODERATE) doesn't disclose its Fast Startup side effect
**Issue:** `powercfg /h off` also silently disables Fast Startup/Hybrid Boot (which depends on hibernation). The action's description (`"Disables hibernation and frees the space used by hiberfil.sys."`) doesn't mention this, so a user who only wanted the disk space back gets a behavioral change (different, slightly slower cold-boot path) they weren't told about. The MODERATE risk level itself is reasonable (fully reversible via `powercfg /h on`); this is a disclosure gap, not a risk-tier error.
**Exact suggested fix:**
```yaml
description_en: "Disables hibernation and frees the space used by hiberfil.sys. Also disables Fast Startup, since it depends on hibernation."
description_sk: "Vypne hibernaciu a uvolni miesto zabrane hiberfil.sys. Tiez vypne rychle spustenie (Fast Startup), kedze na hibernacii zavisi."
```

### 12. `windows_update_cache` (M02) vs `wu_reset_cache`/`wu_stop_services`/`wu_restart_services` (M05) overlap
**Issue:** `main_window.py`'s `_action_checkboxes` is a single flat dict built across **all** loaded modules (`self.modules`), and `run_selected_actions()` queues every checked id regardless of which module it came from — so a user can select M02's `windows_update_cache` (self-contained stop→delete→restart) together with M05's `wu_reset_cache` (bare rename, no stop/restart of its own — those are separate M05 steps: `wu_stop_services`/`wu_restart_services`) in one batch. If `windows_update_cache` runs and restarts `wuauserv`/`bits` before `wu_reset_cache`'s `Rename-Item` executes, the rename can fail (folder locked by the now-running service) — a real ordering hazard from two modules independently managing the same service lifecycle, not just harmless redundant cleanup. Separately, `Modules/m05_windows_update/actions.yaml`'s `wu_reset_cache` ends on an `-EA SilentlyContinue` `Rename-Item` with no `-ErrorVariable`/trailing `Write-Output`, i.e. it has the exact same latent exit-1-on-suppressed-error class of bug as pre-fix M02 actions (out of this audit's M02 scope, but worth a pointer since it's the direct interaction partner here).
**Suggested fix:** either (a) document/enforce an ordering constraint (M05's `wu_stop_services` → M02's `windows_update_cache` minus its own service calls → M05's `wu_restart_services`), or (b) make `windows_update_cache` idempotent-safe by checking `Test-Path` before acting (it already implicitly tolerates a missing folder via `-EA SilentlyContinue`, so the main risk is really the lock-during-rename race, best solved by not letting both modules restart the same services independently in one run).

### 13. Preview accuracy gaps
- **`delivery_optimization`**: preview is a canned string (`"...size not queryable without deleting"`) even though the cache lives at a real, measurable path.
  **Fix:** `preview_command: "$f = Get-ChildItem \"$env:WINDIR\\SoftwareDistribution\\DeliveryOptimization\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would clear $c files, $mb MB of Delivery Optimization cache\""`
- **`shadow_copies_oldest`**: preview lists every shadow copy on every volume, unsorted, unfiltered — the user can't tell which single one (oldest, C: only) the real command will actually delete.
  **Fix:** `preview_command: "$vol = (Get-CimInstance Win32_Volume -Filter \"DriveLetter='C:'\" -EA SilentlyContinue).DeviceID; Get-CimInstance Win32_ShadowCopy -EA SilentlyContinue | Where-Object { $_.VolumeName -eq $vol } | Sort-Object InstallDate | Select-Object ID,InstallDate,VolumeName | Format-Table"` (first row = the one that will be deleted).
- **`component_store_resetbase`**: the only DESTRUCTIVE action in the catalog whose preview shows zero concrete data (no size, no count, no table) — just a static warning sentence, unlike `windows_old_removal` (GB), `shadow_copies_oldest` (table), and `stale_user_profiles` (table). Low-cost improvement: run `/AnalyzeComponentStore` (already used by the sibling `component_store_cleanup`) or count superseded packages via `Dism /Online /Get-Packages` to give at least a rough sense of scale before an irreversible action.

---

## LOW / missing hygiene within existing scope

### 14. `wer_reports` only covers the machine-wide path, not per-user WER
**Issue:** Only clears `C:\ProgramData\Microsoft\Windows\WER\ReportQueue`/`ReportArchive`. Per-user application crash reports (which can include attached heap/minidumps) accumulate separately under `%LOCALAPPDATA%\Microsoft\Windows\WER\ReportQueue`/`ReportArchive` and are untouched.
**Exact suggested fix:**
```yaml
command: "Remove-Item \"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportQueue\",\"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportArchive\",\"$env:LOCALAPPDATA\\Microsoft\\Windows\\WER\\ReportQueue\",\"$env:LOCALAPPDATA\\Microsoft\\Windows\\WER\\ReportArchive\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
```
(mirror the added paths into `preview_command`'s `Get-ChildItem` list.)

### 15. No coverage anywhere in the repo for DirectX shader cache or crash-dump artifacts
**Issue:** Repo-wide grep (all `Modules/*/actions.yaml`) for `WinREAgent`, `LiveKernelReports`, `Minidump`, `MEMORY.DMP`, `ShaderCache`/`DirectX` returned zero matches — these are legitimate, commonly-sized, generally-safe cleanup targets that fit M02's existing SAFE/MODERATE pattern exactly and are absent, not just deliberately excluded (the F2 plan's self-review explicitly notes browser caches/cleanmgr/dup-scan were deliberately left out — these were not mentioned at all).
**Exact suggested new entries:**
```yaml
  - id: directx_shader_cache
    label_en: "DirectX shader cache"
    risk: SAFE
    command: "Remove-Item \"$env:LOCALAPPDATA\\D3DSCache\\*\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
    preview_command: "$f = Get-ChildItem \"$env:LOCALAPPDATA\\D3DSCache\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from DirectX shader cache\""
    description_en: "Clears the shared DirectX shader cache (rebuilds automatically when games/apps next run)."
  - id: memory_dumps
    label_en: "Memory dumps (crash dumps)"
    risk: MODERATE
    command: "Remove-Item \"$env:WINDIR\\Minidump\\*\",\"$env:WINDIR\\MEMORY.DMP\",\"$env:WINDIR\\LiveKernelReports\\*\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\Minidump\",\"$env:WINDIR\\LiveKernelReports\" -Recurse -File -Force -EA SilentlyContinue; $dmp = Get-Item \"$env:WINDIR\\MEMORY.DMP\" -EA SilentlyContinue; $c = $f.Count + $(if ($dmp) {1} else {0}); $mb = [math]::Round(((($f | Measure-Object Length -Sum).Sum) + $(if ($dmp) {$dmp.Length} else {0}))/1MB,2); Write-Output \"Would delete $c crash dump file(s), $mb MB\""
    description_en: "Removes system crash dump files. Useful to keep while diagnosing a recent BSOD, otherwise safe to delete."
```
(MODERATE, not SAFE, because deleting these destroys diagnostic evidence a user might still want if they arrived at this tool *because of* a recent crash.) Vendor shader caches (`%LOCALAPPDATA%\NVIDIA\DXCache`, `\NVIDIA\GLCache`, `%LOCALAPPDATA%\AMD\DxCache`) are a reasonable follow-on, not included above to keep the fix minimal.
`$WinREAgent` was deliberately **not** proposed here: it's a staging folder used *during* an active Windows RE update, and safely-removable only when no recovery servicing operation is in progress — that precondition check is more involved than the pattern above and deserves its own careful design rather than a bolted-on line.

### 16. `stale_user_profiles` 180-day cutoff — sane value, one data-quality caveat
**Issue:** 180 days is a reasonable, commonly-used default and the filter already correctly excludes `Special`, `Loaded`, and null-`LastUseTime` profiles (verified against `tests/test_m02_catalog.py::test_stale_user_profiles_excludes_null_lastusetime_and_loaded_profiles`, which passes today). The one caveat: `Win32_UserProfile.LastUseTime` is known to not always update reliably across all login paths (e.g. certain fast-user-switching/roaming-profile scenarios), so a profile could look older than its actual last real use. Not a bug in the code as written — worth a one-line code comment so a future editor doesn't assume `LastUseTime` is as authoritative as, say, a file's `LastWriteTime`.

### 17. `windows_old_removal` preview doesn't warn about `takeown`/`icacls` duration
**Issue:** The preview accurately reports GB via `Get-ChildItem -Recurse`, but the real command's first two steps (`takeown /F C:\Windows.old /R /A /D Y`, `icacls C:\Windows.old /reset /T /C`) walk the entire tree taking ownership/resetting ACLs before any deletion starts, which can take several minutes on a large `Windows.old` (tens of GB, hundreds of thousands of files) especially on slower/USB-attached storage. Combined with Finding #9 (no executor timeout), this reads to the user as a hang rather than expected slowness, even though it usually isn't actually stuck.
**Suggested fix:** add a note to the preview output, e.g. append `" (ownership/ACL reset may take several minutes on large Windows.old folders)"` to the existing `Write-Output` string.

---

## Summary table

| # | Action id | Severity | One-line issue |
|---|---|---|---|
| 1 | cbs_logs | CRITICAL | Missing the ErrorVariable/exit-0 fix; latent false-failure; glob also misses `.cab` archives |
| 2 | stale_user_profiles | CRITICAL | Same missing fix, on a DESTRUCTIVE action |
| 3 | font_cache, windows_update_cache | CRITICAL | Stop/Start-Service failures invisible → false success, service may stay down |
| 4 | thumbnail_cache | CRITICAL | Unguarded Start-Process; hang between kill/restart leaves desktop dead, no cancel exists |
| 5 | component_store_cleanup/resetbase | HIGH | DISM exit 3010 (reboot required) shown as FAILED |
| 6 | shadow_copies_oldest | HIGH | vssadmin "no shadow copies" (confirmed live: 0 exist here) shown as FAILED |
| 7 | delivery_optimization | HIGH | No error handling at all |
| 8 | cbs_logs | HIGH | No-op in practice; misses the actual 194 MB CBS.log's rotated `.cab` siblings |
| 9 | (executor-wide) | MEDIUM | No timeout, no cancel button |
| 10 | thumbnail_cache vs font_cache | MEDIUM | Risk tier inverted vs. user-visible impact |
| 11 | hibernation_off | MEDIUM | Doesn't disclose Fast Startup side effect |
| 12 | windows_update_cache vs M05 | MEDIUM | Cross-module service-lifecycle race if both selected in one run |
| 13 | delivery_optimization, shadow_copies_oldest, component_store_resetbase | MEDIUM | Preview inaccurate/absent |
| 14 | wer_reports | LOW | Missing per-user WER paths |
| 15 | (catalog-wide) | LOW | No shader-cache/crash-dump actions anywhere in the repo |
| 16 | stale_user_profiles | LOW | LastUseTime reliability caveat (doc-only) |
| 17 | windows_old_removal | LOW | Preview doesn't warn about takeown/icacls duration |
