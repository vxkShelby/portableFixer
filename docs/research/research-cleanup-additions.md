# M02 Cleanup — What to Add (disk-space recovery gap analysis)

Scope: `Modules/m02_cleanup/actions.yaml`, read-only research. Goal: evaluate
12(+1) candidate directions for new disk-recovery actions, add my own, and
spec the accepted ones ready to paste into the catalog.

## What's already there (16 actions, confirmed against `tests/test_m02_catalog.py`)

User/system temp, Recycle Bin, Prefetch, WER (ProgramData-only), old CBS
logs, thumbnail cache, font cache, Delivery Optimization cache, WU download
cache, DISM component-store cleanup (+ ResetBase), Windows.old removal,
oldest shadow copy, hibernation off, stale profiles >180d.

Risk split: 7 SAFE / 5 MODERATE / 4 DESTRUCTIVE / 0 REQUIRES_REBOOT. The
test file hard-asserts this distribution and that **every action has a
`preview_command`** — any addition should keep both invariants in mind
(the count assertions will need bumping, that's expected/out of scope for
this read-only pass).

## Conventions to preserve (extracted from the live catalog + `portablefix/models.py`)

- Schema: `id, label_sk, label_en, risk, command, preview_command,
  description_sk, description_en` (+ optional `undo_command`, unused in
  M02). `risk` ∈ `SAFE|MODERATE|DESTRUCTIVE|REQUIRES_REBOOT`
  (`portablefix/models.py:5-9`). Slovak labels are ASCII-only (diacritics
  stripped: "Docasne", "Vycisti").
- Deletion idiom: `Remove-Item <paths> -Recurse -Force -EA SilentlyContinue
  -ErrorVariable errs; Write-Output ("Skipped locked/in-use items: " +
  $errs.Count)` — enforced by `test_deletion_actions_report_skipped_locked_items_and_exit_zero`.
- Preview idiom: `Get-ChildItem <paths> -Recurse -File -Force -EA
  SilentlyContinue; $c=.Count; $mb=[math]::Round((...|Measure-Object Length
  -Sum).Sum/1MB,2); Write-Output "Would delete $c files, $mb MB..."` — GB
  variant for Windows.old-scale items.
- Multi-path actions pass an array to one `Remove-Item`/`Get-ChildItem`
  call (`wer_reports` already does this for 2 paths) — wildcards mid-path
  are already used (`CBS*.log`, `thumbcache_*.db`), so extending to
  `User Data\*\Cache`-style profile globs is consistent, not a new idiom.
- Two removal shapes, both already precedented: `dir\*` (empties an
  actively-managed cache dir the owning app expects to still exist —
  Prefetch, Temp, thumbnail cache) vs. removing the whole `dir` outright
  (one-shot installer/upgrade leftovers nothing re-populates — Windows.old).
  I kept this distinction for the new actions below.
- `-EA SilentlyContinue` on the whole path array means a vendor/app not
  installed (no NVIDIA GPU, no Firefox) makes that one path silently a
  no-op instead of an error — this is what makes multi-vendor path lists
  safe to ship as one static line.
- No action force-kills a user-facing app; `thumbnail_cache`/`font_cache`
  stop OS-owned processes/services (Explorer restarts itself trivially, no
  unsaved state). I did not extend that pattern to browsers/Teams/Discord
  — killing those loses tabs/drafts, so new actions **skip with a message
  if the process is running** instead, or just rely on skip-locked.
- `test_m02_catalog_no_wmic` bans `wmic` outright — irrelevant here since
  none of the candidates need it, but confirms `Get-CimInstance` is the
  house style for anything WMI-shaped.
- Report-only is an already-endorsed *pattern*, just not yet an M02
  *instance*: `component_store_cleanup`'s `preview_command` already runs
  `/AnalyzeComponentStore` as a pure report, and
  `docs/superpowers/specs/2026-08-31-f2-cleanup-reporting-design.md:20-24`
  explicitly parks "large/duplicate-file scanning" as analysis-only,
  deliberately deferred rather than rejected. Same doc (lines 16-19)
  records browser-cache cleanup as **scoped out of F2 for time, not
  vetoed on safety grounds** ("easy to add later as its own
  module-catalog entry") — which is the actual answer to the "argue
  for/against" question below.
- Gap found while reading, not in the candidate list: `wer_reports`
  (line 40-47) only clears the **all-users** WER queue under
  `C:\ProgramData\...`. There's a per-user WER queue under
  `%LOCALAPPDATA%` it misses entirely. Added as its own candidate below.

---

## Evaluation of the 13 given candidates

| # | Direction | Verdict | Why |
|---|---|---|---|
| 1 | Browser caches (Chrome/Edge/Firefox) | **Accept** (scoped) | See below — safe if scoped to `Cache`/`Code Cache`/`GPUCache` only, never touching Cookies/History/Login Data. |
| 2 | DirectX shader cache | **Accept** | Pure regenerable GPU cache, zero real hazard, decent size for gamers. |
| 3 | NVIDIA/AMD driver install leftovers | **Accept** | `C:\NVIDIA`/`C:\AMD` are extraction staging dirs, not read by the running driver (that's DriverStore). Very common multi-GB find. |
| 4 | Memory dumps + LiveKernelReports | **Accept** | Same class as the already-SAFE `wer_reports`: diagnostic exhaust, not live state. |
| 5 | Windows upgrade leftovers ($WINDOWS.~BT, $WinREAgent) | **Accept** | Direct sibling of the existing `windows_old_removal`; same ACL dance, same rollback-window tradeoff. |
| 6 | Downloads aging report (report-only) | **Accept** | Cheap, safe, universally useful; matches the project's own analysis-only precedent. |
| 7 | Per-user Teams/Discord/Spotify caches | **Accept** | Electron/Chromium apps use the same `Cache`/`Code Cache`/`GPUCache` convention as browsers; none hold auth/chat data in those specific folders. |
| 8 | WinSxS analyze-only report | **Reject** | Already exists — it's `component_store_cleanup`'s `preview_command` (`/AnalyzeComponentStore`, line 99). A standalone entry would just duplicate it for no new capability. |
| 9 | iTunes/iOS backups detection (report-only) | **Accept** | Classic hidden multi-GB folder. Report-only is not just "safer," it's the only responsible option — see hazards. |
| 10 | Docker/WSL disk images detection (report-only) | **Accept** | Real and large (vhdx grows, never auto-shrinks). Detection only — reclaiming needs `Optimize-VHD` from the Hyper-V module, which isn't reliably present (Home editions, most consumer machines), so an actual compaction *action* would violate "built-in tools only." |
| 11 | Pagefile size review | **Reject** | Not reclaimable garbage — it's active virtual memory. Resizing is a memory-tuning decision with reboot + stability implications, and belongs in `m09_tuning`, not a cleanup catalog. |
| 12 | Storage Sense config | **Reject** | It's a policy toggle for *future* automated cleanup, not a one-shot recovery action — frees 0 MB at run time, so "expected space recovered" doesn't even apply. Undocumented registry schema, version-fragile. Better as a GUI recommendation banner than a catalog entry. |
| 13 | Largest-files/folders report (report-only top-N) | **Accept** | Explicitly name-checked as an accepted-but-deferred idea in the F2 design doc. High universal value. |

**10 accepted / 3 rejected** from the given list.

### Browser caches — the argue-for/against, in full

*Against (why it was excluded from F2):* per-profile enumeration adds real
scope, and naive "clear the browser's app-data folder" is exactly the kind
of thing that logs users out or wipes bookmarks if scoped wrong. Multiple
Chromium versions have reshuffled on-disk cache layout, so a hardcoded
single path is fragile.

*For:* the design doc's own reasoning is "scoped out for time," not "unsafe
by design," and explicitly invites a later module-catalog entry. Browser
cache is very likely the single largest per-machine recoverable bucket for
non-gamers (multi-GB across 2-3 browsers × several profiles is common).
The scope risk is fully addressable **without per-vendor version-detection
logic**: target only the three folders that are cache-and-only-cache
(`Cache`, `Code Cache`, `GPUCache` / Firefox `cache2`) — Cookies, History,
Login Data, Bookmarks, and Web Data are separate sibling files the wildcard
never reaches. The "logged out" perception risk is real but is a support/UX
question, not a data-safety one.

**Verdict: accept, scoped to cache-only folders, browser-closed check
instead of force-kill.** Full spec below.

---

## Candidates I'm adding

| # | Direction | Verdict | Why |
|---|---|---|---|
| 14 | Per-user WER report queue (`%LOCALAPPDATA%\...\WER\{ReportQueue,ReportArchive}`) | **Accept** | Existing `wer_reports` only covers the all-users ProgramData copy; this is the per-user sibling it misses. Could also just be folded into the existing action's path array instead of a new id — smaller diff, same effect; noted in the spec. |
| 15 | Windows Installer orphaned MSI/MSP cache (`C:\Windows\Installer`) | **Reject (explicit warning)** | This is the single most common "cleanup tool destroys your system" mistake in the wild. The folder holds cached MSI/MSP files that Windows Installer needs for *future* repair/uninstall of currently-installed software. Safely identifying "orphaned" entries requires cross-referencing every cached GUID against installed-product registry keys (PatchCleaner-style logic) — not expressible as a static one-liner, and getting it wrong disables uninstall/repair for real, currently-installed apps. Do not add without that correlation logic. |
| 16 | DriverStore superseded driver packages (`pnputil`) | **Reject for M02** | Real disk hog (5-10GB+ of old `oem##.inf` packages is common), but removing the wrong one breaks a currently-connected or reconnectable device (printers especially). Needs correlation against active hardware, which is `m10_drivers` territory (already a module in this repo) — not a blind-delete cleanup action. |
| 17 | VSS shadow-storage cap resize (`vssadmin resize shadowstorage`) | **Reject** | Resizing the cap can silently purge multiple shadow copies at once to satisfy the new limit, and which ones is not fully predictable from a single line without first inspecting current usage. The catalog's existing "delete the single oldest copy" is the safer, already-field-tested pattern — extend that one instead of adding a cap-resize action. |
| 18 | Windows Search index rebuild/delete | **Reject** | Typically only 1-5GB, and deleting it forces an hours-long CPU-heavy full reindex. Bad size-to-hazard ratio compared to everything else here. |
| 19 | OneDrive Files-On-Demand dehydration | **Reject** | No stable, documented built-in CLI switch for it (the "Free up space" action is GUI/shell-only); would violate "built-in tools only" to fake it. |

**1 accepted / 5 rejected** from my own additions.

**Total: 11 accepted / 8 rejected** across 19 evaluated directions.

---

## Accepted action specs

Risk mix of the 11: 8 SAFE, 2 MODERATE, 1 DESTRUCTIVE, 0 REQUIRES_REBOOT
(none of these need a reboot to take effect — that tier fits driver/network
resets better than disk cleanup).

### ★ TOP 5

#### 1. `browser_cache_sweep` — MODERATE
```yaml
  - id: browser_cache_sweep
    label_sk: "Cache prehliadaca (Chrome/Edge/Firefox)"
    label_en: "Browser cache (Chrome/Edge/Firefox)"
    risk: MODERATE
    command: "$running = @('chrome','msedge','firefox') | Where-Object { Get-Process -Name $_ -EA SilentlyContinue }; if ($running) { Write-Output (\"Skipped: close \" + ($running -join ', ') + \" first\") } else { Remove-Item \"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\Cache\",\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\Code Cache\",\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\GPUCache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\Cache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\Code Cache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\GPUCache\",\"$env:APPDATA\\Mozilla\\Firefox\\Profiles\\*\\cache2\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count) }"
    preview_command: "$f = Get-ChildItem \"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\Cache\",\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\Code Cache\",\"$env:LOCALAPPDATA\\Google\\Chrome\\User Data\\*\\GPUCache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\Cache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\Code Cache\",\"$env:LOCALAPPDATA\\Microsoft\\Edge\\User Data\\*\\GPUCache\",\"$env:APPDATA\\Mozilla\\Firefox\\Profiles\\*\\cache2\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB of browser cache (Chrome/Edge/Firefox, cache only)\""
    description_sk: "Vymaze cache prehliadacov (Cache/Code Cache/GPUCache). Nemaze cookies, hesla ani historiu."
    description_en: "Clears browser cache (Cache/Code Cache/GPUCache). Does not touch cookies, passwords, or history."
```
- **Expected typical recovery:** 500 MB - 8 GB combined, scales with profile count.
- **Hazards:** never touches Cookies/History/Login Data/Bookmarks (separate sibling files); self-skips with a message rather than force-killing browsers (avoids tab/unsaved-form loss); path layout has shifted across Chromium releases before, so the wildcard set is deliberately over-inclusive — a stale path is a silent no-op, not an error; does not cover `Service Worker\CacheStorage` (some PWAs use it for offline function, riskier to classify as blindly safe — left for a v2).

#### 2. `windows_upgrade_leftovers` — DESTRUCTIVE
```yaml
  - id: windows_upgrade_leftovers
    label_sk: "Zvysky po aktualizacii Windows ($WINDOWS.~BT/~WS)"
    label_en: "Windows upgrade leftovers ($WINDOWS.~BT/~WS)"
    risk: DESTRUCTIVE
    command: "foreach ($p in \"C:\\$WINDOWS.~BT\",\"C:\\$WINDOWS.~WS\",\"C:\\$WinREAgent\") { takeown /F $p /R /A /D Y | Out-Null; icacls $p /reset /T /C | Out-Null }; Remove-Item \"C:\\$WINDOWS.~BT\",\"C:\\$WINDOWS.~WS\",\"C:\\$WinREAgent\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
    preview_command: "$paths = \"C:\\$WINDOWS.~BT\",\"C:\\$WINDOWS.~WS\",\"C:\\$WinREAgent\" | Where-Object { Test-Path $_ }; if ($paths) { $f = Get-ChildItem $paths -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $gb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1GB,2); Write-Output \"Would delete $c files, $gb GB from $($paths -join ', ')\" } else { Write-Output \"No Windows upgrade leftover folders present\" }"
    description_sk: "Odstrani zvysky po nadstavbe Windows okrem Windows.old. Znemozni navrat na predoslu verziu. Nevratne."
    description_en: "Removes Windows upgrade leftovers beyond Windows.old. Prevents rolling back to the previous version. Irreversible."
```
- **Expected typical recovery:** 2-8 GB (`$WINDOWS.~BT` alone is commonly 3-5 GB).
- **Hazards:** same rollback-window tradeoff as the existing `windows_old_removal` — these two are the two halves of the same "go back" mechanism, so bundling under DESTRUCTIVE and the same takeown/icacls preamble is consistent, not a new risk class. `$WinREAgent` specifically is lower-risk than the other two (pure WinRE-reconfiguration orphan, no rollback function) if this ever gets split later.

#### 3. `gpu_driver_install_leftovers` — MODERATE
```yaml
  - id: gpu_driver_install_leftovers
    label_sk: "Zvysky instalatorov GPU ovladacov"
    label_en: "Leftover GPU driver installer files"
    risk: MODERATE
    command: "Remove-Item \"C:\\NVIDIA\",\"C:\\AMD\",\"$env:TEMP\\NVIDIA*\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
    preview_command: "$f = Get-ChildItem \"C:\\NVIDIA\",\"C:\\AMD\",\"$env:TEMP\\NVIDIA*\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $gb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1GB,2); Write-Output \"Would delete $c files, $gb GB of leftover GPU driver installer files\""
    description_sk: "Vymaze zvysky rozbalenych instalatorov NVIDIA/AMD ovladacov. Netyka sa aktivneho ovladaca."
    description_en: "Removes leftover extracted NVIDIA/AMD driver installer files. Does not affect the active driver."
```
- **Expected typical recovery:** 1-15 GB after a few driver update cycles (each NVIDIA installer alone extracts 500 MB-1.5 GB and rarely self-cleans).
- **Hazards:** deliberately excludes `%LOCALAPPDATA%\NVIDIA\DisplayDriver`, which the NVIDIA App/GeForce Experience uses for its own "roll back driver" UI — only the plain `C:\NVIDIA`/`C:\AMD` staging dirs are targeted. Doesn't touch DriverStore (the actual active driver). Set MODERATE rather than SAFE only because folder contents/layout vary a lot by installer version.

#### 4. `directx_shader_cache` — SAFE
```yaml
  - id: directx_shader_cache
    label_sk: "Cache shaderov GPU"
    label_en: "GPU shader cache"
    risk: SAFE
    command: "Remove-Item \"$env:LOCALAPPDATA\\D3DSCache\\*\",\"$env:LOCALAPPDATA\\NVIDIA\\DXCache\\*\",\"$env:LOCALAPPDATA\\NVIDIA\\GLCache\\*\",\"$env:LOCALAPPDATA\\AMD\\DxCache\\*\",\"$env:LOCALAPPDATA\\AMD\\VkCache\\*\" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output (\"Skipped locked/in-use items: \" + $errs.Count)"
    preview_command: "$f = Get-ChildItem \"$env:LOCALAPPDATA\\D3DSCache\",\"$env:LOCALAPPDATA\\NVIDIA\\DXCache\",\"$env:LOCALAPPDATA\\NVIDIA\\GLCache\",\"$env:LOCALAPPDATA\\AMD\\DxCache\",\"$env:LOCALAPPDATA\\AMD\\VkCache\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB of GPU shader cache\""
    description_sk: "Vymaze cache prekompilovanych shaderov. Hry si ju pri dalsom spusteni znova vytvoria."
    description_en: "Clears compiled shader cache. Games rebuild it automatically on next launch."
```
- **Expected typical recovery:** 200 MB - 3 GB; heavier gamer libraries can exceed 5 GB.
- **Hazards:** essentially none — next launch of each previously-cached game recompiles its shaders (one-time stutter/longer load), otherwise fully transparent. Missing vendor dirs (no dGPU) are silent no-ops.

#### 5. `largest_files_report` — SAFE (report-only)
```yaml
  - id: largest_files_report
    label_sk: "Report najvacsich suborov (top 25)"
    label_en: "Largest files report (top 25)"
    risk: SAFE
    command: "$f = Get-ChildItem \"$env:USERPROFILE\" -File -Recurse -Force -EA SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 25 FullName,@{N='MB';E={[math]::Round($_.Length/1MB,2)}}; Write-Output ($f | Format-Table -AutoSize | Out-String)"
    preview_command: "$f = Get-ChildItem \"$env:USERPROFILE\" -File -Recurse -Force -EA SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 25 FullName,@{N='MB';E={[math]::Round($_.Length/1MB,2)}}; Write-Output ($f | Format-Table -AutoSize | Out-String)"
    description_sk: "Zobrazi 25 najvacsich suborov v profile pouzivatela. Nic nemaze."
    description_en: "Lists the 25 largest files in the user profile. Deletes nothing."
```
- **Expected typical recovery:** report-only (0 MB automatic); routinely surfaces 5-50 GB of forgotten ISOs/VM disks/videos for the user to act on manually.
- **Hazards:** scoped to `$env:USERPROFILE` only (not the whole drive) for runtime and permissions sanity — a whole-drive variant is possible but slower and noisier; flagged as a deliberate v1 tradeoff, not an oversight. `command` and `preview_command` are identical since the action *is* a dry-run — see schema note below.

---

### Other accepted (not top 5)

#### `nvidia_amd_...` already covered above. Remaining six:

**`crash_dumps` — SAFE**
- `command`: `Remove-Item "$env:WINDIR\Minidump\*.dmp","$env:WINDIR\MEMORY.DMP","$env:WINDIR\LiveKernelReports\*" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output ("Skipped locked/in-use items: " + $errs.Count)`
- `preview_command`: `$f = Get-ChildItem "$env:WINDIR\Minidump\*.dmp","$env:WINDIR\MEMORY.DMP","$env:WINDIR\LiveKernelReports\*" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output "Would delete $c crash dump file(s), $mb MB (Minidump/MEMORY.DMP/LiveKernelReports)"`
- label_sk: "Pady systemu (Minidump/MEMORY.DMP)" / label_en: "Crash dumps (Minidump/MEMORY.DMP)"
- **Typical:** usually tens of MB (minidumps only); occasionally 100 MB-1 GB (kernel dump), up to full RAM size if "complete memory dump" is configured (rare on consumer defaults) — set SAFE for consistency with the existing SAFE `wer_reports` (same "diagnostic exhaust" class), not because the worst case is small.
- **Hazards:** destroys forensic evidence of the most recent BSOD — run after, not before, any support/vendor dump collection. No module in this repo currently reads these paths (checked `m01_diagnostics` — no reference), so no in-app conflict today; flagged for future-you if BSOD analysis ever lands in `m01`.

**`thirdparty_app_caches` — SAFE**
- `command`: `Remove-Item "$env:APPDATA\Microsoft\Teams\Cache","$env:APPDATA\Microsoft\Teams\GPUCache","$env:APPDATA\Microsoft\Teams\Code Cache","$env:LOCALAPPDATA\Packages\MSTeams_8wekyb3d8bbwe\LocalCache\Microsoft\MSTeams\EBWebView\Default\Cache","$env:APPDATA\discord\Cache","$env:APPDATA\discord\Code Cache","$env:APPDATA\discord\GPUCache","$env:LOCALAPPDATA\Spotify\Storage","$env:LOCALAPPDATA\Spotify\Data" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output ("Skipped locked/in-use items: " + $errs.Count)`
- `preview_command`: same path array via `Get-ChildItem ... -File`, `$c`/`$mb` pattern as above.
- label_sk: "Cache aplikacii chatu/media (Teams/Discord/Spotify)" / label_en: "Chat/media app caches (Teams/Discord/Spotify)"
- **Typical:** Discord 200 MB-2 GB, Spotify 500 MB-10 GB+ (heavy offline-download users), classic Teams 100 MB-1 GB.
- **Hazards:** Spotify's `Data` folder caches offline-downloaded tracks — deleting forces re-download next time they're played (bandwidth cost, not data loss). The new-Teams (MSIX) `EBWebView` path is a **medium-confidence best guess** based on it being a standard WebView2 user-data folder name, not verified against a live new-Teams install — verify before shipping. None of these paths hold auth tokens or message history (server-side for both apps), so no login/session risk. No process is killed; locked files during active use are simply skipped, same as every other SAFE deletion in this catalog.

**`downloads_aging_report` — SAFE (report-only)**
- `command`/`preview_command` (identical): `$f = Get-ChildItem "$env:USERPROFILE\Downloads" -File -Recurse -Force -EA SilentlyContinue | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) }; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); $top = $f | Sort-Object Length -Descending | Select-Object -First 10 Name,@{N='MB';E={[math]::Round($_.Length/1MB,2)}},LastWriteTime | Format-Table | Out-String; Write-Output "$c files older than 90 days, $mb MB total.`n$top"`
- label_sk: "Report o starnuti priecinka Downloads" / label_en: "Downloads folder aging report"
- **Typical:** report-only; follow-up manual cleanup commonly recovers 1-20 GB+ since Downloads is rarely curated.
- **Hazards:** none technical (report-only); deep zip-extraction subfolders can make the top-10-by-size list less actionable than a folder-level rollup — noted as a possible v2 refinement, not a blocker.

**`ios_backups_report` — SAFE (report-only)**
- `command`/`preview_command` (identical): `$p = "$env:APPDATA\Apple Computer\MobileSync\Backup"; if (Test-Path $p) { $d = Get-ChildItem $p -Directory -Force -EA SilentlyContinue; $sizes = $d | ForEach-Object { [PSCustomObject]@{ Backup=$_.Name; GB=[math]::Round(((Get-ChildItem $_.FullName -Recurse -File -Force -EA SilentlyContinue | Measure-Object Length -Sum).Sum)/1GB,2) } }; $total = [math]::Round((($sizes | Measure-Object GB -Sum).Sum),2); Write-Output ("$($d.Count) iOS backup(s), $total GB total`n" + ($sizes | Format-Table | Out-String)) } else { Write-Output "No iTunes/iOS backups found" }`
- label_sk: "Zalohy iPhone/iPad (iTunes)" / label_en: "iPhone/iPad backups (iTunes)"
- **Typical:** report-only; each backup is commonly 5-60 GB and users routinely accumulate 2-5 stale ones.
- **Hazards:** must stay report-only, full stop — a backup may be a device's only copy. Explicitly **not** proposing a delete action for this in any risk tier; the description should point the user to iTunes/Finder's own backup manager so they delete with full device/date context.

**`wsl_docker_vhdx_report` — SAFE (report-only)**
- `command`/`preview_command` (identical): `$vhdx = Get-ChildItem "$env:LOCALAPPDATA\Docker\wsl","$env:LOCALAPPDATA\Packages\*\LocalState" -Recurse -Filter *.vhdx -Force -EA SilentlyContinue; $c = $vhdx.Count; $gb = [math]::Round((($vhdx | Measure-Object Length -Sum).Sum)/1GB,2); $list = $vhdx | Select-Object FullName,@{N='GB';E={[math]::Round($_.Length/1GB,2)}} | Format-Table | Out-String; Write-Output "$c WSL/Docker virtual disk(s), $gb GB total`n$list"`
- label_sk: "Virtualne disky WSL/Docker" / label_en: "WSL/Docker virtual disks"
- **Typical:** report-only; Docker Desktop's WSL2 `ext4.vhdx` and per-distro disks commonly balloon to 20-100 GB+ since they grow but never auto-shrink.
- **Hazards:** report-only is not a cop-out here — actual compaction needs `wsl --shutdown` + `Optimize-VHD`, which requires the Hyper-V PowerShell module that is **not installed by default on Windows Home / most consumer machines**, so a compaction action would silently fail "built-in tools only" on a large chunk of the install base. The report's own output text should point users to `wsl --shutdown` + Docker Desktop's "Clean/purge data" UI, or `Optimize-VHD` if Hyper-V tools happen to be present.

**`wer_reports_user` — SAFE**
- `command`: `Remove-Item "$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportQueue","$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportArchive" -Recurse -Force -EA SilentlyContinue -ErrorVariable errs; Write-Output ("Skipped locked/in-use items: " + $errs.Count)`
- `preview_command`: `$f = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportQueue","$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportArchive" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output "Would delete $c files, $mb MB from per-user WER report queue"`
- label_sk: "Hlasenia o chybach (WER) - aktualny pouzivatel" / label_en: "Error reports (WER) - current user"
- **Typical:** usually tens of MB; occasionally large if a user-mode app is crash-looping.
- **Hazards:** minimal, same class as the existing SAFE `wer_reports`. **Simpler alternative:** just add these two paths into the existing `wer_reports` action's path array instead of a new catalog entry — smaller diff, identical effect, and it's really the same feature with a scope bug. Listed separately here only because the candidate list asked for discrete specs.

---

## Rejected — one-line reasoning each

| id (informal) | Reasoning |
|---|---|
| WinSxS analyze-only report | Already shipped as `component_store_cleanup`'s `preview_command`. |
| Pagefile size review | Not garbage (active virtual memory); reboot/stability implications; belongs in `m09_tuning`. |
| Storage Sense config | Policy toggle for future cleanup, frees 0 MB now, undocumented registry schema. |
| Windows Installer orphaned cache | Needs GUID-to-installed-product correlation to be safe; naive delete breaks repair/uninstall of real software. |
| DriverStore superseded packages | Needs correlation against active hardware to avoid breaking reconnectable devices; belongs in `m10_drivers`. |
| VSS shadow-storage cap resize | Unpredictable which/how many shadow copies get purged to satisfy a new cap; existing "delete oldest one" is the safer pattern. |
| Windows Search index rebuild/delete | Small (1-5 GB) for an hours-long CPU-heavy reindex penalty — bad ratio. |
| OneDrive on-demand dehydration | No stable built-in CLI switch; would fake "built-in tools only." |
| iOS backup **deletion** (as opposed to detection) | Explicitly rejected even though detection is accepted — could be someone's only backup. |
| Docker/WSL **compaction** (as opposed to detection) | Requires Hyper-V module not present by default; detection-only keeps the built-in-tools constraint intact. |

---

## Schema/process notes for whoever implements this

1. `test_m02_catalog_loads_16_actions_all_with_preview` and
   `test_m02_catalog_risk_distribution` will need their counts bumped
   (16 → 27, and the SAFE/MODERATE/DESTRUCTIVE tallies) if all 11 are
   added — not done here since this pass is read-only research.
2. Report-only actions are a new *instance* in M02 (the *pattern* already
   exists via `component_store_cleanup`'s preview). There's no
   `is_report_only`/read-only flag on `ActionDef` (`portablefix/models.py`)
   — I set `command` == `preview_command` for all five report-only
   proposals so the existing "every action has a preview" test still
   holds mechanically. Whether the GUI should skip a confirmation dialog
   for a SAFE report-only action is a GUI-layer decision outside this
   YAML file's reach — flagging, not solving.
3. None of the 11 accepted actions need `REQUIRES_REBOOT` — that tier
   fits driver/network resets (see `m06_network`, `m04_integrity`) better
   than disk-space recovery, so its absence here is expected, not a gap.
