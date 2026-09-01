# PortableFix Diagnostics — New Action Proposals (M01 / M07 / M10 / M12)

Scope: read-only research, no files modified. Reviewed the four target catalogs in full before proposing anything:

- `Modules/m01_diagnostics/actions.yaml` — 10 actions: `os_info, computer_info, bios_info, cpu_info, memory_info, volumes, physical_disks, recent_hotfixes, defender_status, top_cpu_processes`
- `Modules/m07_autoruns/actions.yaml` — 4 actions: `autoruns_registry_run, autoruns_startup_folder, autoruns_scheduled_tasks, autoruns_autostart_services`
- `Modules/m10_drivers/actions.yaml` — 2 actions: `drv_problem_devices, drv_third_party_list`
- `Modules/m12_online/actions.yaml` — 3 actions: `online_connectivity_ladder, online_dns_benchmark, online_proxy_check`

Every accepted proposal below is a new action ID (checked against the 19 existing IDs above — no collisions), risk `SAFE` (all four catalogs are 100% SAFE today; nothing proposed here changes that — the one candidate that would have broken the pattern, RAM diagnostic scheduling, is rejected specifically because it doesn't fit a read-only capture-output model), and runs from a single in-box PowerShell one-liner (semicolon-chained multi-statement style, matching the existing `online_connectivity_ladder` convention).

**Totals: 22 evaluated → 20 accepted / 2 rejected.**

---

## Part 1 — Verdicts on the 13 candidate directions given

| # | Direction | Verdict | Module | Notes |
|---|---|---|---|---|
| 1 | Recent BSOD/minidump summary | **Accept** | M01 | Zero crash-analysis capability exists today. Top 5. |
| 2 | Event log critical errors, last 7 days | **Accept** | M01 | No general event-log triage exists at all. Top 5. |
| 3 | Pending reboot detection | **Accept** | M01 | Cheap, prevents wasted re-troubleshooting. Top 5. |
| 4 | Battery health report | **Accept** | M01 | High value but laptop-only (no-op on desktops) — kept out of top 5 for that reason. |
| 5 | Disk SMART detail beyond basic status | **Accept** | M01 | `physical_disks` only exposes Healthy/Warning/Unhealthy. Top 5. |
| 6 | RAM diagnostic scheduling | **Reject** | — | See reasoning below. |
| 7 | License/activation status | **Accept** | M01 | Must use CIM, not naive `slmgr` — see notes. |
| 8 | Restore point listing | **Accept** | M01 | Cheap safety pre-check for other repair modules. |
| 9 | Uptime + Fast Startup status | **Accept** | M01 | Adds computed duration + Fast Startup flag `os_info` doesn't provide. |
| 10 | Windows edition/build + EOL awareness | **Accept** | M01 | Adds UBR/DisplayVersion `os_info`'s CIM query lacks, plus a support-status flag. |
| 11 | Installed software inventory | **Accept** | M01 | Must use registry, not `Win32_Product` — see notes. Top 5. |
| 12 | Network adapter driver versions | **Accept** | M10 | M10 has only 2 actions; fills the #1 real ticket type (Wi-Fi/NIC flakiness). |
| 13 | Listening ports / active connections | **Accept** | M12 | Natural companion to the existing proxy check's threat model. |

### #6 — RAM diagnostic scheduling — rejected

`mdsched.exe` (Windows Memory Diagnostic) has no silent/scriptable mode. Launching it always opens an interactive wizard asking the user to pick "Restart now" or "Check on next restart" — there is no meaningful stdout to capture, just "a window opened," which breaks this tool's run-one-liner/capture-text-output execution model entirely. Reproducing the scheduling silently would mean hand-writing the undocumented registry/BCD state Windows itself uses for the boot-time test — fragile, undocumented, and arguably closer to system-modifying territory than a read-only diagnostic belongs. If this is wanted, it should be a plain "launch external tool" button elsewhere in the UI (like a Device Manager shortcut), not a diagnostics-catalog action with a captured result.

---

## Part 2 — Additional proposals (beyond the given list)

Found while cross-referencing against established field-technician / Sysinternals-Autoruns-style checklists that the four catalogs don't cover at all:

| # | Direction | Verdict | Module |
|---|---|---|---|
| A | BitLocker encryption status | **Accept** | M01 — Top 5 candidate, see reasoning |
| B | TPM status | **Accept** | M01 |
| C | Page file configuration/usage | **Accept** | M01 |
| D | IFEO `Debugger` hijack check (sticky-keys-style backdoor) | **Accept** | M07 |
| E | Unquoted service path vulnerability scan | **Accept** | M07 |
| F | GPU driver info | **Accept** | M10 |
| G | Windows Firewall profile status | **Accept** | M12 |
| H | Hosts file contents (redirect check) | **Accept** | M12 |
| I | WMI event-subscription persistence (`root\Subscription`) | **Reject** | — |

### I — WMI event-subscription persistence — rejected

Real technique (fileless malware/APT persistence via `__EventFilter`/`__EventConsumer`), and it *can* be written as a one-liner scoped to `root\Subscription`. Rejected anyway: legitimate OEM/management-agent software also registers WMI subscriptions there, so raw output needs security-specialist triage to avoid false alarms — a field technician reading an unexplained CIM object dump either panics over a benign entry or shrugs off a real one. Better suited to a dedicated incident-response/forensics tool than a general break-fix diagnostics catalog.

---

## Part 3 — Full specs for all 20 accepted actions

### M01 — diagnostics (13 new actions)

---
**`bsod_summary`** — SAFE
EN: *Recent crash (BSOD) summary* / SK: *Prehľad nedávnych pádov systému (BSOD)*
```powershell
Write-Output '--- Minidump files ---'; Get-ChildItem -Path "$env:SystemRoot\Minidump" -Filter *.dmp -ErrorAction SilentlyContinue | Select-Object Name,LastWriteTime,Length | Format-Table -AutoSize; Write-Output '--- Recent BugCheck/critical System events ---'; Get-WinEvent -FilterHashtable @{LogName='System'; Id=1001,41} -MaxEvents 15 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id,ProviderName,Message | Format-List
```
Why: "it randomly restarts / blue-screens" is one of the most common escalations a field tech faces, and today this toolkit has zero crash-analysis capability. Correlating minidump timestamps with WER (1001) and Kernel-Power (41) events gives a first-pass bugcheck read without needing WhoCrashed or BlueScreenView.

---
**`eventlog_critical_7d`** — SAFE
EN: *Critical/error events, last 7 days* / SK: *Kritické chyby v udalostiach za posledných 7 dní*
```powershell
Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 50 -ErrorAction SilentlyContinue | Select-Object TimeCreated,LogName,ProviderName,Id,LevelDisplayName,Message | Format-Table -AutoSize -Wrap
```
Why: the single most standard event-log-triage workflow in any technician's playbook, and none of the current M01 actions touch the event log at all. Surfaces hardware/driver/application failures without opening `eventvwr.msc` and hand-building a custom filter.

---
**`pending_reboot`** — SAFE
EN: *Pending reboot detection* / SK: *Detekcia čakajúceho reštartu*
```powershell
$pending = @(); if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $pending += 'Component Based Servicing' }; if (Test-Path 'HKLM:\SOFTWARE\Microsoft\WindowsUpdate\Auto Update\RebootRequired') { $pending += 'Windows Update' }; if (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue) { $pending += 'Pending File Rename Operations' }; if ($pending.Count -gt 0) { Write-Output ('REBOOT PENDING: ' + ($pending -join ', ')) } else { Write-Output 'No reboot pending.' }
```
Why: explains "why didn't that fix/update actually take effect" and stops a technician from re-diagnosing an issue that's already fixed pending restart — cheap, high payoff, checked before other repair modules.

---
**`battery_health`** — SAFE
EN: *Battery health report* / SK: *Report o stave batérie*
```powershell
$reportPath = "$env:TEMP\battery-report.html"; powercfg /batteryreport /output $reportPath | Out-Null; Write-Output ('Report saved: ' + $reportPath); Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object Name,EstimatedChargeRemaining,BatteryStatus,DesignCapacity,FullChargeCapacity | Format-List
```
Why: separates "the battery is worn out, replace it" from "power settings are misconfigured" — one of the most common laptop complaints. Creates one temp HTML report file (non-destructive, same category as other report-producing built-ins like `netsh wlan report`); no-op-ish on desktops.

---
**`disk_reliability_counters`** — SAFE
EN: *Disk reliability / SMART counters* / SK: *Spoľahlivostné (SMART) počítadlá diskov*
```powershell
Get-PhysicalDisk | Get-StorageReliabilityCounter | Select-Object DeviceId,Temperature,Wear,ReadErrorsTotal,ReadErrorsUncorrected,WriteErrorsTotal,WriteErrorsUncorrected,PowerOnHours | Format-Table -AutoSize
```
Why: `physical_disks` only shows a binary Healthy/Warning/Unhealthy flag. This exposes actual wear %, uncorrected error counts and power-on hours — a proactive "back this up now" signal *before* HealthStatus flips to Unhealthy and data is gone.

---
**`activation_status`** — SAFE
EN: *Windows activation/license status* / SK: *Stav aktivácie/licencie Windows*
```powershell
Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" | Select-Object Name,Description,LicenseStatus,PartialProductKey | Format-List
```
Why: after a motherboard swap, clean reinstall or disk clone (routine field jobs), confirming activation up front avoids a surprise watermark later and tells the tech whether `slmgr /ato` is worth trying. Deliberately **not** `cscript slmgr.vbs /dli` — running it via the default `wscript` host pops a blocking MessageBox that would hang an unattended one-liner; the CIM approach has no VBS-host quirk at all.

---
**`restore_points`** — SAFE
EN: *System Restore point list* / SK: *Zoznam obnovovacích bodov*
```powershell
Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Select-Object SequenceNumber,CreationTime,Description,RestorePointType | Format-Table -AutoSize
```
Why: before relying on System Restore in any repair step elsewhere in the suite, confirm restore points actually exist — System Protection is silently disabled on a large fraction of real machines.

---
**`uptime_faststartup`** — SAFE
EN: *Uptime and Fast Startup status* / SK: *Doba behu a stav rýchleho spustenia*
```powershell
$os = Get-CimInstance Win32_OperatingSystem; $uptime = (Get-Date) - $os.LastBootUpTime; Write-Output ('Last boot: ' + $os.LastBootUpTime); Write-Output ('Uptime: {0}d {1}h {2}m' -f $uptime.Days,$uptime.Hours,$uptime.Minutes); $fs = Get-ItemPropertyValue -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue; Write-Output ('Fast Startup enabled: ' + [bool]$fs)
```
Why: `os_info` already returns a raw `LastBootUpTime` but makes the technician do the arithmetic, and doesn't surface Fast Startup at all. A hybrid-resume (Fast Startup) is not a real cold boot, which matters when a driver/hardware fix is only confirmed by a genuine restart.

---
**`os_edition_eol`** — SAFE
EN: *Windows edition, build and support status* / SK: *Edícia, build a podpora Windows*
```powershell
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'; Write-Output ($cv.ProductName + ' ' + $cv.DisplayVersion + ' (build ' + $cv.CurrentBuildNumber + '.' + $cv.UBR + ')'); if ([int]$cv.CurrentBuildNumber -lt 22000) { Write-Output 'Windows 10: mainstream support ended 2025-10-14 - verify ESU enrollment or plan upgrade.' }
```
Why: adds the exact patch level (UBR) and marketing version (e.g. "23H2") that `os_info`'s `Win32_OperatingSystem` query cannot expose, plus a one-line flag for Windows-10-past-EOL machines — extremely common in the field as of late 2026. **Caveat noted for the maintainers:** keep the EOL check to this one fixed, already-past historical date; do not grow this into a rolling EOL calendar embedded in a YAML command string — that kind of logic belongs in app config, or it will quietly go stale.

---
**`installed_software`** — SAFE
EN: *Installed software inventory* / SK: *Zoznam nainštalovaného softvéru*
```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object DisplayName | Select-Object DisplayName,DisplayVersion,Publisher,InstallDate | Sort-Object DisplayName | Format-Table -AutoSize
```
Why: a fast "Programs and Features" equivalent for spotting bloatware, duplicate/conflicting antivirus products, or confirming a suspicious app's presence — arguably the single most-used check in any real PC tune-up visit. Deliberately **not** `Get-CimInstance Win32_Product` — that class silently triggers an MSI "reconfigure" pass for every product it enumerates (slow, known side effects, borderline non-SAFE); the registry-based enumeration above is the standard safe replacement.

---
**`bitlocker_status`** — SAFE
EN: *BitLocker encryption status* / SK: *Stav šifrovania BitLocker*
```powershell
Get-BitLockerVolume | Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionPercentage,KeyProtector | Format-List
```
Why: a safety pre-check for the *rest* of the toolkit. Disk repair actions (chkdsk, partition/driver changes) can trigger a BitLocker recovery-key prompt on next boot; knowing protection is ON beforehand tells the technician to retrieve the recovery key first instead of accidentally locking the customer out.

---
**`tpm_status`** — SAFE
EN: *TPM status* / SK: *Stav TPM čipu*
```powershell
Get-Tpm | Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated,ManufacturerVersion | Format-List
```
Why: pairs with `os_edition_eol` — "does this hardware even have a usable TPM 2.0" is the #1 blocker for a Windows 11 upgrade quote, and it also explains BitLocker/Windows Hello availability problems.

---
**`pagefile_info`** — SAFE
EN: *Page file configuration and usage* / SK: *Konfigurácia a využitie odkladacieho súboru*
```powershell
Get-CimInstance Win32_PageFileUsage | Select-Object Name,AllocatedBaseSize,CurrentUsage,PeakUsage | Format-Table -AutoSize; Get-CimInstance Win32_ComputerSystem | Select-Object AutomaticManagedPagefile | Format-List
```
Why: classic instability/"out of memory" triage step, especially on low-RAM machines or ones where a previous technician manually resized or disabled the page file and forgot.

### M07 — autoruns (2 new actions)

---
**`autoruns_ifeo_debuggers`** — SAFE
EN: *IFEO debugger hijacks (backdoor check)* / SK: *Kontrola IFEO Debugger (backdoor)*
```powershell
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options' -ErrorAction SilentlyContinue | ForEach-Object { $d = Get-ItemProperty -Path $_.PSPath -Name Debugger -ErrorAction SilentlyContinue; if ($d) { [PSCustomObject]@{ Executable = $_.PSChildName; Debugger = $d.Debugger } } } | Format-Table -AutoSize
```
Why: detects the classic "sticky keys" backdoor — a `Debugger` value on `sethc.exe`/`utilman.exe`/`osk.exe` that silently launches `cmd.exe` as SYSTEM from the login screen. None of the existing Run-key/Startup-folder/ScheduledTask/Service checks can ever see this, since it lives in a completely different registry branch. Fills a real gap in an "autoruns" module modeled on Sysinternals Autoruns, which has always treated IFEO as its own category.

---
**`autoruns_unquoted_service_paths`** — SAFE
EN: *Unquoted service path vulnerabilities* / SK: *Služby s neuzavretou cestou (zraniteľnosť)*
```powershell
Get-CimInstance Win32_Service | Where-Object { $_.PathName -and $_.PathName -notmatch '^"' -and $_.PathName -match '\s' } | Select-Object Name,DisplayName,StartMode,PathName | Format-Table -AutoSize
```
Why: a textbook local-privilege-escalation vector (third-party service installed with an unquoted path containing spaces) that real machines still have. Natural security-hardening companion to the existing `autoruns_autostart_services` action.

### M10 — drivers (2 new actions)

---
**`drv_network_adapter_versions`** — SAFE
EN: *Network adapter driver versions* / SK: *Verzie ovládačov sieťových adaptérov*
```powershell
Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,LinkSpeed,DriverVersion,DriverDate,DriverProvider | Format-Table -AutoSize
```
Why: M10 currently only flags devices already broken (`Status -ne 'OK'`) or dumps *every* third-party driver with zero context. This targets the #1 real-world driver complaint — Wi-Fi/Ethernet dropouts — with exactly the version/date/provider info needed to compare against the OEM's latest release.

---
**`drv_gpu_info`** — SAFE
EN: *GPU driver information* / SK: *Informácie o ovládači grafickej karty*
```powershell
Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate,AdapterRAM,Status | Format-List
```
Why: display/black-screen/crash-to-desktop tickets are driver-first triage; this is `dxdiag`'s display tab without launching a GUI tool.

### M12 — online (3 new actions)

---
**`online_listening_ports`** — SAFE
EN: *Listening ports and owning processes* / SK: *Otvorené porty a ich procesy*
```powershell
Get-NetTCPConnection -State Listen | Select-Object LocalAddress,LocalPort,@{N='Process';E={(Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName}},OwningProcess | Sort-Object LocalPort | Format-Table -AutoSize
```
Why: a `netstat -ano` replacement with process names pre-resolved. Spots port conflicts ("can't start my server, port already in use") and unexpected listeners — a natural companion to `online_proxy_check`, which already targets the same "hidden network misbehavior" concern.

---
**`online_firewall_status`** — SAFE
EN: *Windows Firewall profile status* / SK: *Stav profilov Windows Firewall*
```powershell
Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction | Format-Table -AutoSize
```
Why: malware and "helpful" third-party installers routinely disable one or more firewall profiles. A one-second check worth running right after any cleanup pass elsewhere in the toolkit to confirm the machine isn't left exposed.

---
**`online_hosts_file`** — SAFE
EN: *Hosts file entries (redirect check)* / SK: *Záznamy v súbore hosts (presmerovania)*
```powershell
Get-Content -Path "$env:SystemRoot\System32\drivers\etc\hosts" -ErrorAction SilentlyContinue | Where-Object { $_ -and $_ -notmatch '^\s*#' }
```
Why: one of the oldest and still most common malware/adware redirection tricks, and a frequent cause of "I can't reach this one website" tickets. `online_proxy_check` already covers proxy-based redirection; this covers the equally common hosts-file vector that nothing today checks.

---

## Top 5 by value

Ranked by universality (applies to virtually every machine), how directly it resolves a top-frequency field complaint, and how much of a genuine gap it closes:

1. **`eventlog_critical_7d`** (M01) — the single most foundational, universal diagnostic step; the catalog currently has no general event-log triage at all.
2. **`bsod_summary`** (M01) — directly answers one of the most dreaded, hard-to-diagnose-without-tools complaints; zero crash-analysis exists today.
3. **`pending_reboot`** (M01) — near-zero cost, prevents misjudging whether any *other* fix in the app actually worked.
4. **`disk_reliability_counters`** (M01) — turns a binary health flag into a proactive, business-critical "back up now" signal.
5. **`installed_software`** (M01) — the classic PC-tune-up/bloatware/PUP check every real technician visit includes; currently absent entirely.

Honorable mentions just outside the top 5: `drv_network_adapter_versions` (M10) and `online_hosts_file` (M12) — both fill real, currently-empty gaps in their modules and are very common ticket types, but are slightly more situational than the universal top 5.
