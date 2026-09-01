# PortableFix — System Repair Catalog Gap Analysis

Scope: M03 disk, M04 integrity, M05 windows_update, M06 network, M09 tuning.
Angle: what repair actions field technicians reach for that these five catalogs are currently missing.

Read-only research. No files were modified.

## 0. What currently exists (baseline, so nothing below duplicates it)

| Module | Actions today |
|---|---|
| m03_disk (7) | disk_smart_status, disk_volume_report, disk_scan_readonly, disk_spotfix, disk_optimize_volume, disk_full_scan_reboot, disk_check_scheduled |
| m04_integrity (8) | dism_checkhealth, dism_scanhealth, dism_restorehealth, sfc_scannow, sfc_verifyonly, appx_reregister, wmi_verify, wmi_salvage |
| m05_windows_update (6) | wu_check_services, wu_stop_services, wu_reset_cache, wu_restart_services, wu_reregister_dlls, wu_trigger_detection |
| m06_network (7) | net_adapter_status, net_ip_config_report, net_flush_dns, net_hosts_reset, net_dhcp_renew, net_winsock_reset, net_tcpip_reset |
| m09_tuning (4) | tune_power_plan_report, tune_power_high_performance, tune_startup_apps_report, tune_visual_effects_performance |

I also grepped all 12 module catalogs (m01–m13, excluding a missing m11) for every keyword in the candidate list (spooler, wsreset, SearchIndexer, w32tm, advfirewall, lodctr, secedit, reagentc, PolicyDefinitions, BthServ/Bluetooth, AudioSrv, indexer, DPI, ProfileSvc, PATH). None of the candidate directions exist anywhere in the repo today. The one near-hit: `m08_security` has `sec_firewall_status` (read-only `Get-NetFirewallProfile` report) but **no firewall reset/repair action anywhere** — confirming that gap is real, not a duplicate.

## 1. Schema and mechanism facts that drove the judgment calls

Confirmed from `portablefix/models.py` and `portablefix/module_engine.py`:

- `RiskLevel`: exactly `SAFE | MODERATE | DESTRUCTIVE | REQUIRES_REBOOT`.
- `ActionDef` fields: `id, label_sk, label_en, risk, command, description_sk, description_en, preview_command (optional), undo_command (optional)`. Required: `id, label_sk, label_en, risk, command`.
- `category` is set once per **module file** (all five target files declare `category: REPAIR`), not per action.
- Observed risk convention (not enforced by code, but consistent across all 32 existing actions): **SAFE** = read-only reports, or "start something back up" actions. **MODERATE** = stopping a service, renaming/deleting files, changing a persistent setting — especially when a working `undo_command` is supplied. **DESTRUCTIVE** = real chance of unrecoverable loss even with care (only `wmi_salvage` today, and it currently has **no** `undo_command` at all). **REQUIRES_REBOOT** = needs a restart to take effect.
- Execution (`portablefix/executor.py`): every `command` and every `undo_command` is run as its own independent, non-interactive `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "..."` process. **There is no shared PowerShell session between a command and its undo.** Any state an undo needs must be persisted to disk/registry by the command itself — exactly the pattern already used by `wu_reset_cache` (rename to `.bak`, undo renames back) and `net_hosts_reset` (copy to `.bak`, undo copies back). This is the load-bearing constraint for judging every "export-then-reset, undo-imports" proposal below.
- Elevation (`portablefix/elevation.py`, `gui/main_window.py`) is whole-app (one admin relaunch, banner + button), not per-action — so any command needing admin rights is fine, same as existing DISM/sfc/winmgmt actions.
- No per-action timeout is enforced by the executor, so long-running operations (DISM RestoreHealth already does this) are acceptable.
- Undo accumulation is LIFO into `undo.ps1`: successful actions' `undo_command`s are collected in completion order and unwound in reverse. So an undo that depends on another action's side effect (e.g., a backup file) only works if that action ran and completed first in the same batch — a soft ordering dependency that must be spelled out in the label/description text, since there's no per-item dynamic UI to enforce it.

## 2. Verdicts on the given candidate directions

### ACCEPT — Windows Search index rebuild
Real and common ("0 results" search, broken index after malware/corruption). Stop the service, delete the corrupted index DB, restart — Windows rebuilds it automatically in the background. No safe static undo exists (nothing to restore to), which matches existing no-undo precedent (`dism_scanhealth`, `disk_optimize_volume`). → **M04 integrity** (same "corrupted built-in database, delete + rebuild" shape as `wmi_salvage`).

### SPLIT — Windows Store repair
- `wsreset.exe` (clear Store cache): **ACCEPT**. Distinct, lighter-weight, and lower-blast-radius than the existing `appx_reregister`, which many technicians won't reach for first. Caveat: it briefly flashes the Store window, so it needs an interactive desktop — acceptable since this is a GUI tool run by a technician at the console.
- "Re-register the Store app" as its own action: **REJECT** — this is just `appx_reregister` (already exists in M04) filtered to one package; adding a near-duplicate narrows nothing and adds catalog clutter.

### ACCEPT — Print spooler reset
Textbook field fix ("nothing will print, spooler is jammed"). Stop `Spooler`, delete only the stuck `.SPL`/`.SHD` job files, start `Spooler`. Printers/drivers/queue config are untouched. No undo needed — deleted files are already-undeliverable transient jobs, same class as other no-undo cleanup actions. → **M06 network** (closest existing sibling pattern: protocol/service-stack resets like `net_winsock_reset`); M09 tuning would also be a defensible home if the maintainer prefers a "services" bucket.

### ACCEPT — Time sync repair
Clock drift silently breaks Kerberos/TLS/cert validation — a very common and non-obvious root cause. Re-enable + start `W32Time`, force a resync. No meaningful undo (you don't "undo" a corrected clock). → **M06 network** (w32tm is an NTP network client; borderline system/network either way).

### REJECT — File association reset guidance
There is no safe built-in one-liner for this. The only OS mechanism is `Dism /Online /Import-DefaultAppAssociations:<xml>`, which needs a pre-built XML exported from a known-good reference machine — not obtainable at repair time, and not a static command. Blanket-deleting `HKCU\...\FileExts` entries is unpredictable per-machine and would nuke legitimate custom associations for every app, not just broken ones. Better left as static help text in the app than a catalog action.

### ACCEPT — Environment PATH sanity report
Zero risk, real diagnostic value ("command not found" after a bad installer/uninstaller, or a silently-truncated >2047-char PATH). Pure read-only report. → **M09 tuning** (same shape as the existing `tune_startup_apps_report`).

### SPLIT — Profile service repair hints
A blind "repair" is unsafe: fixing a corrupted/temporary profile means editing a *specific* SID's key under `ProfileList` (State bit, stray `.bak` duplicate), and there is no way to pick the right SID without a human looking at the machine — touching the wrong one on a shared machine breaks a different user's profile. **REJECT** the blind repair. **ACCEPT** a SAFE read-only report listing every SID/path/State/RefCount so the technician can see which profile is flagged, then act by hand. → **M04 integrity**.

### ACCEPT (strong) — WMI repository backup before salvage
Exactly the gap the prompt flagged: `wmi_salvage` is today the **only** DESTRUCTIVE action in the entire repo with no `undo_command` at all. `winmgmt /backup <file>` is SAFE, doesn't touch the live repository, and — because the backup path is a fixed, static convention (not a runtime capture) — it can be referenced by a static `undo_command` on `wmi_salvage` itself. This is two proposals: a new `wmi_backup` action, and an **edit to the existing `wmi_salvage` entry** to add `undo_command`. → **M04 integrity**. See §4 for exact strings.

### ACCEPT — Performance counters rebuild
`lodctr /R` (rebuild counter definitions from the registry backup INI) plus `winmgmt /resyncperf` (resync WMI's perf classes) is the standard fix for "Performance Monitor / SCCM / monitoring agent counters missing or corrupt." No undo — rebuilding from OS-shipped definitions isn't reversible and doesn't need to be (there's no better "previous" state). → **M04 integrity** (sits with `wmi_verify`/`wmi_salvage`; perf counters are WMI-adjacent).

### ACCEPT (strong) — Windows Firewall reset to defaults, with export/import undo
The prompt's own prior-art hint (export-then-reset, undo-imports) applies cleanly: `netsh advfirewall export` before `netsh advfirewall reset`, undo does `netsh advfirewall import` guarded by `Test-Path`. This is a lossless, Microsoft-documented round-trip — more reliable than the WMI case. "Can't reach the internet / a program is blocked" after a bad rule, leftover third-party firewall product, or malware cleanup is a very common ticket. → **M06 network** (an argument also exists for M08 security, next to the existing `sec_firewall_status`; noting both). See §4.

### ACCEPT (strong) — Network adapter power management disable
"Wi-Fi/Ethernet randomly drops, especially after sleep" is one of the most common connectivity tickets, and the fix (stop Windows from powering adapters off to save energy) is a single built-in cmdlet already from the same module (`NetAdapter`) as the existing `net_adapter_status`/`Get-NetAdapter`. Fully and cleanly reversible. → **M06 network**. See §4.

### REJECT — SetDPI/scaling fixes
Inherently per-monitor, per-user, resolution-dependent — there is no single "correct" value to force blindly. Forcing e.g. 100% scaling on a 4K panel makes the UI unreadably small on the very machine being fixed, only applies HKCU (so "system-wide fix" doesn't make sense anyway), and only takes effect after logoff. This needs a human choosing a value, not a static one-liner.

### REJECT — Startup repair scheduling (reagentc)
`reagentc /boottore` schedules a boot straight into Windows RE — this is WinRE-adjacent tooling in the same family as `bootrec`/`bcdboot`, which the design explicitly excludes. Out of scope by the stated design constraint, not by risk.

### REJECT — System file ownership fixes
The prompt is right that `sfc_scannow` already covers file *content* corruption — but ACL/ownership corruption is a genuinely different problem. The gap is real, but there's no safe *blind, static, unattended* one-liner for it: a recursive `takeown`/`icacls /reset` broad enough to matter (e.g. all of `C:\Windows`) is slow (can run for hours), and can strip deliberately-customized ACLs set by legitimate AV/EDR/enterprise tooling. The risk-to-value ratio at the scope required to be useful is too high for a one-click unattended catalog action.

### SPLIT — Group policy reset
- Full reset via `secedit /configure /cfg defltbase.inf`: **REJECT**. Feasible to build with the export/undo pattern (export current policy first, undo re-imports it) — but the failure mode undo can't fix is self-lockout: if the reset changes logon rights/account-lockout policy in a way that stops the technician from authenticating, they can no longer *run* the undo. Undo only helps when you can still get back in to trigger it.
- `RD PolicyDefinitions` (delete local ADMX central-store folder): **REJECT**. This folk-fix targets a fairly narrow domain-joined SYSVOL central-store corruption scenario; deleting it on a machine where that's *not* the problem breaks `gpedit.msc` policy display with no cheap undo (it's a whole tree, not a single file). Low value for what looks like a general consumer/field tool, given the rest of the catalog's audience.
- Safer substitute — **ACCEPT** a plain `secedit /export` snapshot (SAFE, no state change, just a backup file a technician can reference before making manual policy changes by hand). → **M04 integrity**. See §4 and §3.

### SPLIT — Bluetooth/audio service resets
Both are real, common, and both are just stop/start on named services — the exact pattern already proven by `wu_stop_services`/`wu_restart_services`. **ACCEPT** both, as two separate single-purpose actions (matches the house style of narrow actions rather than one bundled one). → **M09 tuning** (general "stuck peripheral service" bucket; M06 is a reasonable alternate home for Bluetooth specifically since it's a connectivity adapter).

### ACCEPT — Camera/mic privacy service reset
Modern, frequently-reported issue: an app can't get camera/mic frames even though Settings → Privacy looks correct, because the shared `FrameServer`/`FrameServerMonitor` service is wedged. Same stop/start shape as Bluetooth/audio. → **M09 tuning**.

## 3. Additional proposals (my own, not in the candidate list)

### ACCEPT (strong) — VSS writer status report
`vssadmin list writers` is one of the single most reached-for built-in diagnostics in real repair work — it's the standard first check when System Restore, Backup, or any shadow-copy-based operation fails ("writer X is in a Failed state"). Completely absent from M03 today despite the module already owning `disk_volume_report`, its natural sibling. SAFE, zero risk, built-in, huge diagnostic-value-to-effort ratio. → **M03 disk**.

### ACCEPT — DNS Client service restart
Distinct from the existing `net_flush_dns` (which only clears the resolver cache): if the `Dnscache` *service* itself is wedged rather than just the cache being stale, flushing does nothing and the service needs a real stop/start. Same shape as the Bluetooth/audio/camera service resets. → **M06 network**.

### REJECT (own caution) — Force network connection category to Private
Considered and rejected: `Set-NetConnectionProfile -NetworkCategory Private` would fix "can't see other PCs / can't share files" on a home network misclassified as Public — but blindly forcing Private on a network that's genuinely public (coffee shop, hotel) is a security regression, not a repair. This is a judgment call the technician must make per-network, not a safe blind default.

### REJECT (own caution) — DISM RestoreHealth with an offline `/Source`
The real fix for "RestoreHealth fails because Windows Update itself is broken" is pointing DISM at a source WIM from install media — but the media's path is different on every job and can't be hardcoded into a static one-liner. Blocked purely by the static-command/no-dynamic-input constraint, not by risk; worth revisiting if the app ever gets a file-picker for action parameters.

## 4. Full spec for every accepted proposal

Format: `id` / module / risk / command / undo_command / labels / why.

---
**wmi_backup** — new — M04 integrity — SAFE
```
command: "New-Item -ItemType Directory -Force -Path \"$env:ProgramData\\PortableFix\" | Out-Null; winmgmt /backup \"$env:ProgramData\\PortableFix\\wmi_backup.bin\""
```
undo_command: none (a backup has nothing to undo)
label_sk: "Zaloha WMI repozitara" / label_en: "Backup WMI repository"
Why: gives `wmi_salvage` a real safety net for the first time; SAFE, instant, no side effects.

---
**wmi_salvage** — *edit existing action* — M04 integrity — DESTRUCTIVE (unchanged)
Add:
```
undo_command: "if (Test-Path \"$env:ProgramData\\PortableFix\\wmi_backup.bin\") { winmgmt /restore \"$env:ProgramData\\PortableFix\\wmi_backup.bin\" 1 }"
```
Why: turns the catalog's only undo-less DESTRUCTIVE action into a recoverable one — but only if `wmi_backup` ran first in the same session. Update `description_sk`/`description_en` to say so explicitly, since there's no automatic ordering enforcement. If no backup file exists, the `Test-Path` guard makes the undo a safe no-op, matching the existing `wu_reset_cache`/`net_hosts_reset` pattern.

---
**net_firewall_reset** — new — M06 network — MODERATE
```
command: "New-Item -ItemType Directory -Force -Path \"$env:ProgramData\\PortableFix\" | Out-Null; netsh advfirewall export \"$env:ProgramData\\PortableFix\\firewall_backup.wfw\" | Out-Null; netsh advfirewall reset"
undo_command: "if (Test-Path \"$env:ProgramData\\PortableFix\\firewall_backup.wfw\") { netsh advfirewall import \"$env:ProgramData\\PortableFix\\firewall_backup.wfw\" }"
```
label_sk: "Reset Windows Firewall na predvolene nastavenia" / label_en: "Reset Windows Firewall to defaults"
Why: "no internet / app is blocked" after a bad rule, orphaned third-party firewall product, or malware. Export/import is a lossless, fully static, Microsoft-documented round trip.

---
**net_print_spooler_reset** — new — M06 network — MODERATE
```
command: "Stop-Service -Name Spooler -Force -ErrorAction SilentlyContinue; Remove-Item -Path \"$env:WINDIR\\System32\\spool\\PRINTERS\\*\" -Force -ErrorAction SilentlyContinue; Start-Service -Name Spooler"
```
undo_command: none (deleted files are already-stuck transient jobs)
label_sk: "Reset tlacoveho fronty (Print Spooler)" / label_en: "Reset print spooler queue"
Why: the single most common "nothing prints" fix technicians know; printers/drivers untouched.

---
**net_adapter_power_disable** — new — M06 network — MODERATE
```
command: "Get-NetAdapter | Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice Disabled -ErrorAction SilentlyContinue"
undo_command: "Get-NetAdapter | Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice Enabled -ErrorAction SilentlyContinue"
```
label_sk: "Zakazat uspavanie sietovych adapterov" / label_en: "Disable network adapter sleep/power-saving"
Why: classic fix for random Wi-Fi/LAN drops after sleep; cleanly reversible built-in cmdlet from the same `NetAdapter` module already used by `net_adapter_status`.

---
**net_time_sync_repair** — new — M06 network — MODERATE
```
command: "Set-Service -Name W32Time -StartupType Automatic -ErrorAction SilentlyContinue; Start-Service -Name W32Time -ErrorAction SilentlyContinue; w32tm /resync /force"
```
undo_command: none (a corrected clock isn't something to undo)
label_sk: "Oprava synchronizacie casu" / label_en: "Repair time synchronization"
Why: clock drift is a non-obvious root cause of certificate/TLS and sign-in failures.

---
**net_dns_client_restart** — new (own idea) — M06 network — MODERATE
```
command: "Stop-Service -Name Dnscache -Force -ErrorAction SilentlyContinue; Start-Service -Name Dnscache -ErrorAction SilentlyContinue"
```
undo_command: none
label_sk: "Restart sluzby DNS Client" / label_en: "Restart DNS Client service"
Why: covers the case where the DNS Client *service* is wedged, not just the cache — `net_flush_dns` alone doesn't fix that.

---
**search_index_rebuild** — new — M04 integrity — MODERATE
```
command: "Stop-Service -Name WSearch -Force -ErrorAction SilentlyContinue; Remove-Item -Path \"$env:ProgramData\\Microsoft\\Search\\Data\\Applications\\Windows\\Windows.edb\" -Force -ErrorAction SilentlyContinue; Start-Service -Name WSearch"
```
undo_command: none
label_sk: "Znovu-vytvorenie indexu vyhladavania" / label_en: "Rebuild Windows Search index"
Why: standard fix for "search finds nothing" / corrupted index.

---
**store_cache_reset** — new — M04 integrity — MODERATE
```
command: "wsreset.exe"
```
undo_command: none
label_sk: "Vycistenie cache Microsoft Store" / label_en: "Clear Microsoft Store cache"
Why: lighter, more targeted first step than the existing full `appx_reregister`. Note: briefly shows/hides the Store window (needs an interactive desktop, which this GUI tool already has).

---
**perf_counters_rebuild** — new — M04 integrity — MODERATE
```
command: "lodctr /R; winmgmt /resyncperf"
```
undo_command: none
label_sk: "Znovu-vytvorenie pocitadiel vykonu" / label_en: "Rebuild performance counters"
Why: fixes missing/corrupt Performance Monitor / WMI perf-class counters, a common cause of broken monitoring agents.

---
**profile_list_report** — new — M04 integrity — SAFE
```
command: "Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\ProfileList' | ForEach-Object { $p = Get-ItemProperty $_.PSPath; [PSCustomObject]@{SID=$_.PSChildName; Path=$p.ProfileImagePath; State=$p.State; RefCount=$p.RefCount} } | Format-Table -AutoSize"
```
undo_command: none (read-only)
label_sk: "Kontrola zoznamu profilov (ProfileList)" / label_en: "Profile list sanity check"
Why: surfaces which SID is flagged/corrupted so a technician can fix *that specific* profile by hand — deliberately not an automated repair (see §2 rejection reasoning).

---
**secpol_export_snapshot** — new — M04 integrity — SAFE
```
command: "New-Item -ItemType Directory -Force -Path \"$env:ProgramData\\PortableFix\" | Out-Null; secedit /export /cfg \"$env:ProgramData\\PortableFix\\secpol_backup.inf\" /quiet"
```
undo_command: none (nothing changed; this only writes a backup file)
label_sk: "Export miestnej bezpecnostnej politiky" / label_en: "Export local security policy"
Why: safe substitute for a full group-policy reset (see §2) — a snapshot a technician can consult or manually restore from before hand-editing policy.

---
**tune_path_sanity_report** — new — M09 tuning — SAFE
```
command: "$m=[Environment]::GetEnvironmentVariable('Path','Machine') -split ';' | Where-Object { $_ }; $u=[Environment]::GetEnvironmentVariable('Path','User') -split ';' | Where-Object { $_ }; Write-Output \"Machine entries: $($m.Count), User entries: $($u.Count), Total length: $((($m+$u) -join ';').Length) chars\"; ($m+$u) | Select-Object -Unique | ForEach-Object { if (-not (Test-Path $_)) { Write-Output \"MISSING: $_\" } }"
```
undo_command: none (read-only)
label_sk: "Kontrola premennej PATH" / label_en: "PATH environment variable sanity check"
Why: catches "command not found" caused by a bad installer/uninstaller, or a silently-truncated (>2047 char) PATH.

---
**tune_bluetooth_service_restart** — new — M09 tuning — MODERATE
```
command: "Stop-Service -Name bthserv -Force -ErrorAction SilentlyContinue; Start-Service -Name bthserv -ErrorAction SilentlyContinue"
```
undo_command: none
label_sk: "Restart sluzby Bluetooth" / label_en: "Restart Bluetooth service"
Why: common fix when Bluetooth devices won't pair/connect.

---
**tune_audio_service_restart** — new — M09 tuning — MODERATE
```
command: "Stop-Service -Name AudioEndpointBuilder,Audiosrv -Force -ErrorAction SilentlyContinue; Start-Service -Name AudioEndpointBuilder,Audiosrv -ErrorAction SilentlyContinue"
```
undo_command: none
label_sk: "Restart zvukovej sluzby" / label_en: "Restart audio service"
Why: common fix for "no sound" / audio device errors.

---
**tune_camera_service_restart** — new — M09 tuning — MODERATE
```
command: "Stop-Service -Name FrameServer,FrameServerMonitor -Force -ErrorAction SilentlyContinue; Start-Service -Name FrameServer,FrameServerMonitor -ErrorAction SilentlyContinue"
```
undo_command: none
label_sk: "Restart sluzby kamery (Frame Server)" / label_en: "Restart camera service (Frame Server)"
Why: fixes apps unable to get camera frames despite correct privacy permissions.

## 5. Top 5

Ranked by real-world frequency × safety/reversibility × how cleanly it fills a genuine, currently-empty gap:

1. **wmi_backup + wmi_salvage undo retrofit** (M04) — turns the catalog's only unprotected DESTRUCTIVE action into a recoverable one.
2. **net_firewall_reset** (M06) — extremely common ticket, lossless export/import undo, matches the prompt's own prior-art pattern.
3. **net_print_spooler_reset** (M06) — the single most iconic "just restart the spooler" field fix, currently 100% absent from the tool.
4. **net_adapter_power_disable** (M06) — very common silent connectivity-drop fix, clean built-in reversible cmdlet.
5. **disk_vss_writers_report** (M03, own addition) — zero risk, one line, and the standard first check for any backup/restore-point failure; fills a real hole next to `disk_volume_report`.

## Sources consulted

- `Modules/m03_disk/actions.yaml`, `m04_integrity/actions.yaml`, `m05_windows_update/actions.yaml`, `m06_network/actions.yaml`, `m09_tuning/actions.yaml` (full contents)
- `Modules/m01_diagnostics`, `m02_cleanup`, `m07_autoruns`, `m08_security`, `m10_drivers`, `m12_online`, `m13_debloat` (`actions.yaml`, grepped for keyword overlap / duplication check)
- `portablefix/models.py` (`ActionDef`, `RiskLevel`, `ModuleCategory`)
- `portablefix/module_engine.py` (YAML loading, required fields, risk validation)
- `portablefix/executor.py` (PowerShell invocation prefix, per-process execution model)
- `portablefix/elevation.py`, `portablefix/restore_point.py` (elevation model, timeout precedent)
- Session-memory plan snippet describing `MainWindow._undo_steps` / `undo.create_undo_script` LIFO accumulation
