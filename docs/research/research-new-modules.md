# PortableFix — New Module Brainstorm (M14+)

Scope: read-only research, no files modified. Task: propose entirely **new**
modules (new `Modules/<name>/actions.yaml` catalogs) — not new actions inside
the 12 modules that already exist. All commands below are PowerShell 5.1 +
built-in Windows cmdlets/binaries only (no external tools), written as
single static one-liners to match the existing schema. Target audience is
unchanged from every other doc in this folder: a technician doing an
on-site/USB-stick repair of a family or small-office PC, not an enterprise
fleet.

Files read before writing anything:
- `Modules/m01_diagnostics/actions.yaml` through `Modules/m13_debloat/actions.yaml`
  (all 12 existing catalogs — `m11` does not exist as a catalog, see below)
- `portablefix/models.py` (`RiskLevel`, `ModuleCategory`, `ActionDef`, `ModuleDef`)
- `portablefix/module_engine.py` (`load_module`, `load_all_modules`)
- `portablefix/executor.py` (`ActionRunner`, watchdog/timeout, exit-code handling)
- `portablefix/gui/main_window.py` (category grouping, restore-point trigger,
  exit-code-to-status mapping)
- `README.md` (module table, safety-mechanism summary)
- `docs/research/research-security-additions.md` and the other five existing
  `docs/research/*.md` reports, to avoid re-proposing ground already covered
  and to match format/tone

## 0. What the codebase actually enforces (recap, confirmed by reading code not just YAML)

- **Schema** (`models.py`): `ActionDef(id, label_sk, label_en, risk, command,
  description_sk="", description_en="", preview_command=None,
  undo_command=None)`. `RiskLevel` = SAFE / MODERATE / DESTRUCTIVE /
  REQUIRES_REBOOT. `ModuleCategory` = DIAGNOSTICS / CLEANUP / REPAIR /
  SECURITY — a **fixed 4-value enum**, no "BACKUP" or "HARDENING" category
  exists, and adding one is a code change, not a YAML change. Every module
  proposed below picks one of the existing four.
- **Zero-code-change discovery** (`module_engine.py:52`,
  `load_all_modules`): `modules_dir.glob("*/actions.yaml")`, sorted by path.
  Any new `Modules/mNN_x/actions.yaml` is auto-registered. `m14`–`m19` sort
  correctly after `m13` with no gap handling needed.
- **Restore point trigger** (`main_window.py:613-624`, and the identical
  check at `main_window.py:536` used to skip queued high-risk actions after
  a failed restore point): fires once per batch when
  `action.risk == DESTRUCTIVE or module.category in (REPAIR, SECURITY)`.
  CLEANUP and DIAGNOSTICS categories do **not** trigger it unless the
  individual action is DESTRUCTIVE.
- **Exit-code handling** (`main_window.py:410-415`, `683-692`): the GUI's
  ok/failed status, and whether an action's `undo_command` gets appended to
  `Backups/<run-id>/undo.ps1`, is driven by a strict `exit_code == 0` check
  on the PowerShell process's return code — nothing fuzzier. Several
  existing actions already normalize a native tool's non-zero-but-successful
  exit code with `if ($LASTEXITCODE -eq 3010) { exit 0 } else { exit
  $LASTEXITCODE }` (DISM's "3010 = restart required" convention in
  `m02_cleanup`/`m04_integrity`) or by pattern-matching stderr text
  (`vssadmin`'s "No items found" in `m02_cleanup:125`). This is a
  **proven, reusable pattern** I lean on below for `robocopy`, which has the
  same "0 isn't the only success code" behavior (0-7 = success, 8+ =
  failure) that DISM has — not a new architecture problem, just a pattern
  every new module touching a native (non-cmdlet) exe needs to repeat.
- **Executor** (`executor.py`): PowerShell 5.1 (`powershell.exe`, not
  `pwsh`), `-NoProfile -NonInteractive`, one process per action, stdout/stderr
  merged and streamed line-by-line. A watchdog added this session now kills
  a hung action after 300s of **no output** (`INACTIVITY_TIMEOUT_SEC`) or a
  hard 7200s ceiling (`HARD_CAP_SEC`) regardless of activity — so a command
  that legitimately goes quiet for >5 minutes without printing anything will
  be killed as "timed out," which matters for a couple of proposals below.
  No per-action GUI beyond the existing checkbox-list + Run/DRY-RUN — no
  text input, no file/folder picker, no dropdown. Every command is a single
  static string baked into YAML at catalog-load time.
- **Numbering gap confirmed, not a theory**: `m11` is not a missing/reserved
  module slot. Per `README.md:29` ("M11 | — | Reporting (HTML report po
  kazdej davke, nie katalog)") and the `docs/superpowers/specs/` design docs,
  M11 is the name for the reporting *engine* (`portablefix/report.py`,
  `Reports/<run-id>/…`), which is Python code, not a `Modules/*/actions.yaml`
  catalog — it was never going to have a directory. `m12_online` and
  `m13_debloat` are already taken, so the next free catalog slot is `m14`.

## 1. Dedup pass — ground already covered elsewhere, deliberately not re-proposed

Two of the areas listed in the brief are **already fully speced** in earlier
research docs (as *additions to an existing module*, not implemented in the
actual `.yaml` files yet, but designed) — re-proposing them here as "new
module" material would just be restating someone else's work under a new
name:

- **BitLocker/TPM/Secure Boot status** — `research-security-additions.md`
  A1 (`sec_bitlocker_status`) and A2 (`sec_tpm_secureboot_status`), designed
  as additions to `m08_security`. I still use this territory below (§2,
  M15) but reframed and merged with boot/BCD diagnostics, because I think
  the *boot-and-platform-trust* angle is a more coherent standalone module
  than bolting three more read-only checks onto M08's *runtime security
  posture* theme (Defender/firewall/UAC/RDP/autologon). See M15 for the
  actual justification — take it as "an alternative home for A1/A2, not a
  duplicate," and note it's an either/or with extending M08, not additive.
- **Malware-adjacent read-only forensics** — `research-security-additions.md`
  Bucket A already covers exactly this brief item in detail: `sec_hosts_anomaly`,
  `sec_suspicious_scheduled_tasks`, `sec_root_cert_report`, `sec_local_admins`,
  `sec_recent_local_accounts`, `sec_windows_update_last`, `sec_password_never_expires`.
  None of those are implemented in `Modules/m08_security/actions.yaml` yet
  (verified — the file currently has only 10 actions:
  `sec_defender_status/firewall_status/defender_update/defender_quickscan/uac_status/
  defender_exclusions_list/rdp_status/autologon_check`,
  `hard_defender_clear_exclusions`, `hard_uac_restore_default`), but they're
  designed, reviewed, and belong in M08 as additions — a brand-new module
  for "suspicious stuff worth a look" would just fragment one coherent
  audit story across two catalogs for no reason. I'm not repeating them.
  The two sub-ideas from the brief that genuinely aren't covered by that
  bucket — "recently-modified system files" and "unusual process/service
  names" — are thin (2 actions, not a module) and are noted as M08
  additions in §4 instead of invented here.

Everything else below is genuinely new ground: no existing module or
research doc touches printers beyond a queue-clear/service-restart, Office,
per-browser extension/policy state, boot/BCD, file-level user backup, or
Windows optional features.

---

## 2. Proposed new modules

### M14 — `m14_printing` — Printer Diagnostics & Repair

**Category: REPAIR** (matches the existing pattern in M03/M06/M09: a mix of
SAFE audits sitting next to MODERATE/DESTRUCTIVE fixes, all under "repair
work a technician does on request").

**Why its own module, not an M06 addition:** M06 already has
`net_print_spooler_reset` — stop spooler, delete the PRINTERS spool
folder, restart spooler. That's a one-shot "unstick today's stuck queue"
action and stays exactly as-is; nothing here duplicates it. What M06 has
zero coverage of is the printer *subsystem* below the queue: which drivers
are installed and whether any are orphaned, which printers are ghost/offline
devices left behind by a swapped-out USB printer, and the "nothing else
worked, wipe every printer and start over" last resort. Printer complaints
are a large, recurring, distinct category of on-site tickets — worth a
dedicated catalog rather than three more actions bolted onto a generically-named
"network repair" module.

- **`print_installed_printers_report`** — SAFE
  ```
  Get-Printer | Select-Object Name,DriverName,PortName,Shared,PrinterStatus | Format-Table -AutoSize; Write-Output '--- Print jobs ---'; Get-Printer | ForEach-Object { Get-PrintJob -PrinterName $_.Name -EA SilentlyContinue } | Select-Object PrinterName,DocumentName,JobStatus,SubmittedTime,Size | Format-Table -AutoSize
  ```
  Value: single-glance inventory before touching anything — what's
  installed, what port/driver it's bound to, what's currently queued.

- **`print_driver_store_report`** — SAFE
  ```
  Get-PrinterDriver | Select-Object Name,Manufacturer,DriverVersion,PrinterEnvironment | Format-Table -AutoSize -Wrap; $inUse = (Get-Printer).DriverName; $orphaned = Get-PrinterDriver | Where-Object { $_.Name -notin $inUse }; Write-Output ('Orphaned driver packages (installed, no printer uses them): ' + $orphaned.Count); $orphaned | Select-Object Name | Format-Table -AutoSize
  ```
  Value: printer driver packages accumulate every time a printer is
  swapped/reinstalled and are rarely cleaned up by anyone; this both sizes
  the problem and feeds the removal action below.

- **`print_offline_ghost_printers_report`** — SAFE
  ```
  Get-Printer | Where-Object { $_.PrinterStatus -eq 'Offline' -or $_.PrinterStatus -eq 'Error' } | Select-Object Name,DriverName,PortName,PrinterStatus | Format-Table -AutoSize
  ```
  Value: flags stale printer objects (old USB printer, removed network
  printer) that clutter the print dialog and confuse non-technical users
  about which one is "the real printer."

- **`print_remove_offline_printers`** — MODERATE
  - preview_command: `$p = Get-Printer | Where-Object { $_.PrinterStatus -in @('Offline','Error') }; Write-Output ('Would remove ' + $p.Count + ' offline/error printer object(s): ' + (($p | Select-Object -ExpandProperty Name) -join ', '))`
  - command:
    ```
    $bk = "$env:ProgramData\PortableFix\printers_removed_backup.json"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; $p = Get-Printer | Where-Object { $_.PrinterStatus -in @('Offline','Error') }; $p | Select-Object Name,DriverName,PortName | ConvertTo-Json | Set-Content -Path $bk -Encoding UTF8; $p | ForEach-Object { Remove-Printer -Name $_.Name -EA SilentlyContinue }; Write-Output ('Removed ' + $p.Count + ' offline/error printer(s), backup saved to ' + $bk)
    ```
  - undo_command:
    ```
    $bk = "$env:ProgramData\PortableFix\printers_removed_backup.json"; if (Test-Path $bk) { Get-Content $bk -Raw | ConvertFrom-Json | ForEach-Object { try { Add-Printer -Name $_.Name -DriverName $_.DriverName -PortName $_.PortName -EA Stop } catch { Write-Output ('Could not recreate ' + $_.Name + ': ' + $_.Exception.Message) } } } else { Write-Output 'No backup found.' }
    ```
  - Design note: no per-action "which printer" input exists in this GUI
    (checkbox + Run only), so this is deliberately a bulk operation on the
    whole offline/error set rather than one printer at a time — same
    solve the codebase already uses elsewhere (`hard_defender_clear_exclusions`
    clears *all* exclusions, not a chosen one).
  - Compat risk: `Add-Printer` recreates the printer *object* (name, port,
    driver binding) but does not reinstall the driver package if it was
    also removed by the action below — undo is "best effort," not a full
    guarantee, and should say so in the UI description text.

- **`print_remove_orphaned_drivers`** — DESTRUCTIVE
  ```
  $inUse = (Get-Printer).DriverName; Get-PrinterDriver | Where-Object { $_.Name -notin $inUse } | ForEach-Object { try { Remove-PrinterDriver -Name $_.Name -EA Stop; Write-Output ('Removed driver: ' + $_.Name) } catch { Write-Output ('Failed to remove ' + $_.Name + ': ' + $_.Exception.Message) } }
  ```
  No undo — reinstalling a removed driver package needs the original INF,
  which the tool doesn't have; matches the existing convention of DESTRUCTIVE
  actions with no `undo_command` (`windows_old_removal`, `component_store_resetbase`).

- **`print_reset_print_system`** — DESTRUCTIVE
  ```
  Stop-Service -Name Spooler -Force -EA SilentlyContinue; Get-Printer | Remove-Printer -EA SilentlyContinue; Remove-Item "$env:WINDIR\System32\spool\PRINTERS\*" -Force -Recurse -EA SilentlyContinue; Start-Service -Name Spooler -EA SilentlyContinue; Write-Output 'All printers removed and spool queue cleared. Every printer must be re-added/re-paired.'
  ```
  Explicit "everything is gone, this is the nuclear option" action, for the
  rare case where the printer subsystem itself is corrupted rather than one
  printer object. Label/description text must say plainly that this removes
  *every* printer, not just broken ones.

Architecture notes for this module: none — `Get-Printer`/`Get-PrinterDriver`/
`Get-PrintJob` are part of the client-side `PrintManagement` module, present
by default on Windows 10/11, no external tool. All six actions fit the
static-one-liner, checkbox+run model with no gaps.

---

### M15 — `m15_boot_platform` — Boot & Platform Integrity

**Category: REPAIR.**

**Why its own module:** this is deliberately the "first sixty seconds
on-site, before touching anything risky" checklist — is the boot chain
sane, is the disk encrypted, is the platform trust hardware (TPM/Secure
Boot) present and on. That's a different lens from M08 (`m08_security`),
which is about *ongoing runtime protection* — Defender, firewall, UAC, RDP
exposure, autologon. TPM/Secure Boot/BitLocker were already proposed as M08
*audit additions* in `research-security-additions.md` (A1/A2) — I'm
proposing them again here, deliberately, as an **alternative home**
alongside genuinely new boot/BCD diagnostics, because "is the boot chain
and platform trust intact" reads to me as a coherent pre-flight-check module
of its own, distinct from "is Defender/UAC/firewall configured correctly."
This is a judgment call, not a hard technical requirement — extending M08
with just A1/A2 remains a perfectly valid alternative; don't build both.

- **`boot_bcd_report`** — SAFE
  ```
  $out = & bcdedit.exe /enum all 2>&1; Write-Output ($out -join "`n")
  ```
  Value: boot entries, default OS, timeout, any safeboot/testsigning/nointegritychecks
  flags left over from a previous troubleshooting session — the single most
  useful "why does this machine boot weird" first look. Needs admin for full
  detail (unelevated `bcdedit /enum` shows a reduced view); the app already
  gates most REPAIR actions behind admin, no new constraint.

- **`boot_tpm_status`** — SAFE (same approach as `research-security-additions.md` A2)
  ```
  Get-Tpm | Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated | Format-List; try { Write-Output ('SecureBoot enabled: ' + (Confirm-SecureBootUEFI)) } catch { Write-Output 'SecureBoot: not supported (Legacy BIOS/CSM or firmware does not expose this check)' }
  ```

- **`boot_bitlocker_status`** — SAFE (same approach as `research-security-additions.md` A1,
  including the Home-edition fallback via the raw WMI encryption class since
  `Get-BitLockerVolume` needs a module that doesn't exist on Windows Home)
  ```
  try { Get-BitLockerVolume -EA Stop | Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionPercentage | Format-Table -AutoSize } catch { $v = Get-CimInstance -Namespace 'root/cimv2/security/MicrosoftVolumeEncryption' -ClassName Win32_EncryptableVolume -EA SilentlyContinue; if ($v) { $v | Select-Object DriveLetter,ProtectionStatus,ConversionStatus | Format-Table -AutoSize } else { Write-Output 'BitLocker/Device Encryption not available on this Windows edition.' } }
  ```

- **`boot_safe_mode_status`** — SAFE
  ```
  $out = & bcdedit.exe /enum '{current}' 2>&1 | Select-String -Pattern 'safeboot'; if ($out) { Write-Output ('Machine is currently configured to boot into SAFE MODE: ' + ($out -join '; ')) } else { Write-Output 'Normal boot (no safeboot flag set).' }
  ```
  Value: "the PC always boots to that blue troubleshooting-looking screen"
  is a real, confusing recurring ticket almost always caused by a leftover
  `msconfig`/`bcdedit /set safeboot` from a previous session that nobody
  cleared. This is the cheap diagnosis; the next action is the fix.

- **`boot_clear_safe_mode_flag`** — MODERATE
  ```
  & bcdedit.exe /deletevalue '{current}' safeboot | Out-Null; if ($LASTEXITCODE -eq 0) { Write-Output 'Safe Mode flag cleared - next restart boots normally.' } else { Write-Output 'Failed to clear safeboot flag (needs administrator).' ; exit 1 }
  ```
  No `undo_command` by design: re-adding `safeboot` needs to know which
  variant (`minimal` vs `network`) the machine was actually in, which isn't
  recoverable after the fact — same reasoning as `hard_disable_autologon`'s
  deliberately one-way undo in `research-security-additions.md` B6. Genuine
  need for staying in Safe Mode long-term is rare enough that a fragile
  guessed undo isn't worth building.

Architecture notes: `bcdedit.exe` is always present and needs no external
tool; all commands are synchronous and return immediately, no watchdog
concerns. No per-action input needed — nothing here operates on
user-chosen "which one," it's whole-machine state.

---

### M16 — `m16_office_repair` — Microsoft Office / M365 Repair & Diagnostics

**Category: REPAIR.**

**Why its own module:** Office breakage (won't open, Outlook won't sync,
"repair Office" button in Control Panel) is one of the single most common
non-OS support tickets a field technician sees, and nothing in the existing
12 modules touches it at all — M02/M13 clean/debloat generic files and
preinstalled apps, not a specific installed productivity suite. This is
squarely new territory.

- **`office_version_channel_report`** — SAFE
  ```
  $c = Get-ItemProperty 'HKCU:\Software\Microsoft\Office\ClickToRun\Configuration' -EA SilentlyContinue; if ($c) { $c | Select-Object VersionToReport,ClientCulture,Platform,UpdateChannel | Format-List } else { Write-Output 'Click-to-Run Office configuration not found (Office not installed, or an MSI-based/volume-license install).' }
  ```

- **`office_addins_report`** — SAFE
  ```
  Get-ItemProperty 'HKCU:\Software\Microsoft\Office\Outlook\Addins\*','HKLM:\Software\Microsoft\Office\Outlook\Addins\*','HKLM:\Software\WOW6432Node\Microsoft\Office\Outlook\Addins\*' -EA SilentlyContinue | Select-Object PSChildName,FriendlyName,Description,LoadBehavior | Format-Table -AutoSize -Wrap
  ```
  Scoped to Outlook specifically (not Word/Excel) since that's where
  add-in-caused crashes/slowness/"disabled add-ins" prompts are the most
  common real-world complaint; say so in the description text.

- **`office_ost_pst_report`** — SAFE
  ```
  Write-Output '--- OST files ---'; Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Outlook" -Filter *.ost -Recurse -EA SilentlyContinue | Select-Object Name,@{N='SizeGB';E={[math]::Round($_.Length/1GB,2)}},LastWriteTime | Format-Table -AutoSize; Write-Output '--- PST files ---'; Get-ChildItem "$env:USERPROFILE\Documents\Outlook Files","$env:LOCALAPPDATA\Microsoft\Outlook" -Filter *.pst -Recurse -EA SilentlyContinue | Select-Object Name,@{N='SizeGB';E={[math]::Round($_.Length/1GB,2)}} | Format-Table -AutoSize
  ```
  Value: an oversized OST/PST is a frequent, concrete cause of a sluggish
  or crash-prone Outlook — cheap to spot, directly actionable advice
  (archive/compact) even though this tool doesn't compact it itself.

- **`office_com_addin_disable_all_thirdparty`** — MODERATE
  ```
  $bk = "$env:ProgramData\PortableFix\office_addins_backup.json"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; $keys = Get-ChildItem 'HKCU:\Software\Microsoft\Office\Outlook\Addins' -EA SilentlyContinue; $state = @(); foreach ($k in $keys) { if ($k.PSChildName -notmatch '^Microsoft\.') { $lb = (Get-ItemProperty $k.PSPath -Name LoadBehavior -EA SilentlyContinue).LoadBehavior; $state += [PSCustomObject]@{ Key = $k.PSChildName; LoadBehavior = $lb }; Set-ItemProperty -Path $k.PSPath -Name LoadBehavior -Value 0 -Type DWord -EA SilentlyContinue } }; $state | ConvertTo-Json | Set-Content -Path $bk -Encoding UTF8; Write-Output ('Disabled ' + $state.Count + ' third-party Outlook add-in(s), backup saved.')
  ```
  undo_command:
  ```
  $bk = "$env:ProgramData\PortableFix\office_addins_backup.json"; if (Test-Path $bk) { Get-Content $bk -Raw | ConvertFrom-Json | ForEach-Object { Set-ItemProperty -Path "HKCU:\Software\Microsoft\Office\Outlook\Addins\$($_.Key)" -Name LoadBehavior -Value $_.LoadBehavior -Type DWord -EA SilentlyContinue } } else { Write-Output 'No backup found.' }
  ```
  Direct fix for the addins_report finding — "Outlook crashes on startup,
  kill every non-Microsoft add-in" is a textbook first troubleshooting step.

- **`office_quick_repair`** — MODERATE
  ```
  $c2r = "$env:ProgramFiles\Common Files\Microsoft Shared\ClickToRun\OfficeC2RClient.exe"; if (Test-Path $c2r) { & $c2r scenario=Repair RepairType=QuickRepair DisplayLevel=False; Write-Output 'Quick Repair started (offline, ~2-5 min).' } else { Write-Output 'Click-to-Run client not found - Office is not installed, or is an MSI-based/volume-license install that needs Control Panel > Programs repair instead.' }
  ```

- **`office_online_repair`** — MODERATE (heaviest of the six — see caveat below)
  ```
  $c2r = "$env:ProgramFiles\Common Files\Microsoft Shared\ClickToRun\OfficeC2RClient.exe"; if (Test-Path $c2r) { & $c2r scenario=Repair RepairType=FullRepair DisplayLevel=False; Write-Output 'Online Repair started (needs internet, re-downloads the Office payload, can take 15-30+ min).' } else { Write-Output 'Click-to-Run client not found.' }
  ```

**Architecture flags specific to this module:**
- Every action is conditional on Office (specifically Click-to-Run Office)
  being installed at all — same shape as the existing Defender-on-Windows-Home
  fallback pattern, not a new problem, just noted for completeness.
- **Worth verifying before shipping, not confidently claimed here:**
  whether `OfficeC2RClient.exe scenario=Repair` truly blocks the calling
  process until the repair finishes (the documented IT-deployment usage
  pattern suggests yes, `$LASTEXITCODE` is meaningful afterward), or whether
  it can detach and hand off to a background installer process while the
  launched exe returns early. If it's the latter, the executor would report
  "success" the moment the repair *starts*, not when it *finishes* — and
  because the command keeps producing no output while backgrounded, the
  300-second inactivity watchdog could also kill the parent PowerShell
  process mid-repair even though the real work is still running unsupervised
  outside it. I did not find a way to confirm this from the repo alone; test
  against a real Office install before trusting the exit code as "done."

---

### M17 — `m17_browser_deep` — Browser Deep Tools (Chrome/Edge/Firefox)

**Category: REPAIR.**

**Why its own module, not an M02 addition:** M02 already has
`browser_cache_sweep` — deletes cache folders for Chrome/Edge/Firefox,
nothing else. That's a disk-space action and stays exactly as-is. This
module is a completely different axis: reading *configuration state*
(extensions, enterprise policy keys, homepage/search hijack) rather than
deleting *files*, plus a profile-reset escape hatch. Browser hijacking
(forced extensions, forced homepage, injected search provider) is a
distinct, common real-world PUA/adware pattern that a cache sweep does
nothing for.

- **`browser_extensions_report`** — SAFE
  ```
  foreach ($b in @(@{N='Chrome';P="$env:LOCALAPPDATA\Google\Chrome\User Data"},@{N='Edge';P="$env:LOCALAPPDATA\Microsoft\Edge\User Data"})) { $pref = Join-Path $b.P 'Default\Secure Preferences'; if (-not (Test-Path $pref)) { $pref = Join-Path $b.P 'Default\Preferences' }; if (Test-Path $pref) { try { $j = Get-Content $pref -Raw | ConvertFrom-Json; $ext = $j.extensions.settings.PSObject.Properties | ForEach-Object { [PSCustomObject]@{ Name = $_.Value.manifest.name; Enabled = ($_.Value.state -eq 1) } }; Write-Output ('--- ' + $b.N + ' (' + $ext.Count + ' extensions) ---'); $ext | Format-Table -AutoSize } catch { Write-Output ($b.N + ': could not parse preferences file.') } } }; $ffProfile = Get-ChildItem "$env:APPDATA\Mozilla\Firefox\Profiles" -Filter *.default* -Directory -EA SilentlyContinue | Select-Object -First 1; if ($ffProfile) { $extJson = Join-Path $ffProfile.FullName 'extensions.json'; if (Test-Path $extJson) { try { $j = Get-Content $extJson -Raw | ConvertFrom-Json; Write-Output ('--- Firefox (' + $j.addons.Count + ' extensions) ---'); $j.addons | Select-Object @{N='Name';E={$_.defaultLocale.name}},active | Format-Table -AutoSize } catch { Write-Output 'Firefox: could not parse extensions.json.' } } }
  ```
  Compat risk: Chrome/Edge's `Preferences`/`Secure Preferences` JSON schema
  has shifted across versions (field names, HMAC wrapper on `Secure
  Preferences`) — verify field paths against a current browser install
  before relying on this; ship it framed as best-effort, matching the
  hedging already used in `research-security-additions.md` for anything
  reading undocumented internal state. Also only reads the `Default`
  profile — a user with multiple named Chrome profiles needs a small loop
  over `Get-ChildItem "...\User Data" -Directory | Where-Object Name -match
  '^(Default|Profile \d+)$'`, noted here rather than in the one-liner to
  keep it readable.

- **`browser_policy_report`** — SAFE
  ```
  Write-Output '--- Chrome policy (HKLM) ---'; Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Google\Chrome' -EA SilentlyContinue | Format-List; Write-Output '--- Chrome policy (HKCU) ---'; Get-ItemProperty 'HKCU:\SOFTWARE\Policies\Google\Chrome' -EA SilentlyContinue | Format-List; Write-Output '--- Edge policy (HKLM) ---'; Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Edge' -EA SilentlyContinue | Format-List; Write-Output '--- Edge policy (HKCU) ---'; Get-ItemProperty 'HKCU:\SOFTWARE\Policies\Microsoft\Edge' -EA SilentlyContinue | Format-List
  ```
  Value: a forced extension, disabled dev tools, or forced homepage set via
  these ADMX-backed policy keys is invisible in the browser's own Settings
  UI (it shows as "managed by your organization" with no way to change it)
  — this is the *only* way to see and fix it, which is exactly why it earns
  a dedicated read.

- **`browser_homepage_search_report`** — SAFE
  ```
  $goodHosts = @('google.com','bing.com','duckduckgo.com','msn.com'); $c = Get-Content "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Preferences" -Raw -EA SilentlyContinue | ConvertFrom-Json -EA SilentlyContinue; if ($c) { $hp = $c.homepage; $sp = $c.default_search_provider.name; Write-Output ("Chrome homepage: $hp"); Write-Output ("Chrome default search: $sp") }; $e = Get-Content "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Preferences" -Raw -EA SilentlyContinue | ConvertFrom-Json -EA SilentlyContinue; if ($e) { Write-Output ("Edge homepage: " + $e.homepage); Write-Output ("Edge default search: " + $e.default_search_provider.name) }
  ```
  Value: classic hijack detector — homepage/search silently redirected to
  an unfamiliar domain is one of the most common "why is my browser weird"
  complaints; comparing against a small known-good list gives the
  technician an instant signal instead of reading raw JSON.

- **`browser_reset_chrome_profile`** — MODERATE
  ```
  Stop-Process -Name chrome -Force -EA SilentlyContinue; Start-Sleep -Seconds 2; $ts = Get-Date -Format 'yyyyMMdd_HHmmss'; $src = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default"; $dst = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default.bak-$ts"; if (Test-Path $src) { Rename-Item -Path $src -NewName (Split-Path $dst -Leaf); Write-Output ('Chrome profile reset. Old profile saved as ' + $dst) } else { Write-Output 'Chrome default profile not found.' }
  ```
  No generic `undo_command`: it's only safely reversible if Chrome hasn't
  been relaunched yet (relaunching creates a brand-new `Default` folder,
  and renaming the backup back on top of it would need to overwrite/merge)
  — say this plainly in the description rather than promising a one-click
  undo that can silently fail. Kills Chrome first, without asking — same
  "closes the app" tradeoff every reset/repair-style action in this tool
  already makes (e.g. `thumbnail_cache` restarts Explorer).

- **`browser_reset_edge_profile`** — MODERATE — identical pattern, targeting
  `$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default`.

- **`browser_clear_policy_keys`** — DESTRUCTIVE
  ```
  $bk = "$env:ProgramData\PortableFix\browser_policy_backup.reg"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; reg export 'HKLM\SOFTWARE\Policies\Google\Chrome' $bk /y 2>$null | Out-Null; reg export 'HKLM\SOFTWARE\Policies\Microsoft\Edge' "$bk.edge" /y 2>$null | Out-Null; Remove-Item 'HKLM:\SOFTWARE\Policies\Google\Chrome' -Recurse -Force -EA SilentlyContinue; Remove-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Edge' -Recurse -Force -EA SilentlyContinue; Write-Output 'Chrome/Edge policy keys removed (backups exported as .reg files).'
  ```
  undo_command: `reg import "$env:ProgramData\PortableFix\browser_policy_backup.reg"; reg import "$env:ProgramData\PortableFix\browser_policy_backup.reg.edge"`
  **DESTRUCTIVE on purpose, and needs the loudest warning in this whole
  module**: on a domain-joined or MDM-managed small-office PC, these keys
  can be a *legitimate* corporate policy (forced password-manager extension,
  required security settings) rather than adware — wiping them can break a
  managed deployment the technician has no visibility into. Description
  text must say explicitly: only run this on a machine that is *not*
  centrally managed.

Architecture notes: `ConvertFrom-Json`/`ConvertTo-Json` are native to
PowerShell 5.1 (since v3), no gap. `reg export`/`reg import` are built-in.
All actions operate on the current user's default profile only, by design,
for the same "no per-action parameter input" reason used throughout —
flagged inline above rather than treated as a blocker.

---

### M18 — `m18_user_backup` — User File Backup (Pre-Risk Snapshot)

**Category: REPAIR** (a safety-net utility run *before* other REPAIR/
DESTRUCTIVE work, same rationale that already puts `wmi_backup` in M04-REPAIR
rather than its own category).

**Why its own module:** nothing in the existing 12 modules does file-level
user-data backup. `restore_point.py` is whole-system System Restore (config/
system files, not user documents — System Restore explicitly excludes
personal files by design). M04's `wmi_backup` backs up the WMI repository
only. Before a technician runs something genuinely risky on a family PC
(full disk repair, a factory-reset-adjacent debloat pass, an Office online
repair), "did anyone back up the kid's school project on the Desktop first"
is a real, distinct, high-value need with no current home.

- **`backup_user_folders`** — MODERATE
  - preview_command:
    ```
    $total = 0; $count = 0; foreach ($f in 'Desktop','Documents','Pictures','Favorites') { $p = Join-Path $env:USERPROFILE $f; if (Test-Path $p) { $items = Get-ChildItem $p -Recurse -File -Force -EA SilentlyContinue; $count += $items.Count; $total += ($items | Measure-Object Length -Sum).Sum } }; Write-Output ('Would back up ' + $count + ' file(s), ' + [math]::Round($total/1GB,2) + ' GB from Desktop/Documents/Pictures/Favorites')
    ```
  - command:
    ```
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'; $dest = "$env:ProgramData\PortableFix\UserFileBackup\$ts"; $failed = $false; foreach ($f in 'Desktop','Documents','Pictures','Favorites') { $src = Join-Path $env:USERPROFILE $f; if (Test-Path $src) { robocopy $src (Join-Path $dest $f) /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null; if ($LASTEXITCODE -ge 8) { $failed = $true } } }; if ($failed) { Write-Output ('Backup completed with errors: ' + $dest); exit 1 } else { Write-Output ('Backup complete: ' + $dest) }
    ```
  - undo_command:
    ```
    $latest = Get-ChildItem "$env:ProgramData\PortableFix\UserFileBackup" -Directory -EA SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1; if ($latest) { $out = Join-Path $env:USERPROFILE ('Desktop\RESTORED_' + $latest.Name); robocopy $latest.FullName $out /E /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null; Write-Output ('Restored to ' + $out + ' (copied next to your files, nothing overwritten).') } else { Write-Output 'No backup found.' }
    ```
  - **Robocopy exit-code gotcha, explicit:** robocopy uses a bitflag scheme
    where 0-7 all mean success (1 = files copied, 2 = extra files at dest,
    4 = mismatched files) and only 8+ is a real failure — a naive `exit
    $LASTEXITCODE` would report a totally successful backup as "FAILED" in
    the GUI the moment robocopy copies even one file (exit code 1). The
    command above normalizes this exactly the way `component_store_cleanup`
    normalizes DISM's 3010 in `m02_cleanup:98` — same established pattern,
    just a different native tool.
  - **Honest limitation to put in the description text**: this backs up to
    `$env:ProgramData` on the *same internal drive* as the original files —
    matching every other backup-before-mutate action already in this
    codebase (`firewall_backup.wfw`, `uac_backup.json`,
    `defender_exclusions_backup.json`, `wmi_backup.bin` are all on `C:`
    too). It protects against *this tool's own mistakes* (a bad repair
    action, an accidental deletion) — it does **not** protect against a
    failing physical disk, which is a real scenario for exactly the kind of
    "why is this PC being repaired" visit that would trigger running it.
    Auto-detecting and preferring an external/removable drive would be a
    real improvement but needs either a drive-picker (a GUI capability this
    tool doesn't have) or a "guess the first non-system fixed/removable
    volume with enough free space" heuristic that can silently pick the
    wrong drive — flagged as a design tradeoff, not solved here.

- **`backup_list_existing`** — SAFE
  ```
  $root = "$env:ProgramData\PortableFix\UserFileBackup"; if (Test-Path $root) { Get-ChildItem $root -Directory | ForEach-Object { $size = (Get-ChildItem $_.FullName -Recurse -File -EA SilentlyContinue | Measure-Object Length -Sum).Sum; [PSCustomObject]@{ Backup = $_.Name; SizeGB = [math]::Round($size/1GB,2) } } | Format-Table -AutoSize } else { Write-Output 'No backups found yet.' }
  ```

- **`backup_restore_latest`** — MODERATE — same body as `backup_user_folders`'s
  `undo_command` above, exposed as its own standalone action so a
  technician can restore from a *previous visit's* backup without needing
  that visit's own `undo.ps1` (which is written per-run and isn't trivially
  re-invoked across separate sessions on a portable USB tool).

Architecture notes: no gaps beyond the robocopy exit-code normalization
already handled inline, and the drive-choice limitation stated plainly
above rather than papered over.

---

### M19 — `m19_win_features` — Windows Optional Feature Management

**Category: REPAIR.** The thinnest of the six proposals — flagged
explicitly rather than padded out.

**Why its own module, not folded into M13 (`debloat`):** M13 removes
*preinstalled UWP apps* (provisioned packages, promo tiles, Xbox identity,
Copilot, widgets). Windows optional features (`Get-WindowsOptionalFeature`)
are a completely different Windows subsystem — DISM-managed OS components
like .NET Framework 3.5, legacy PowerShell v2, Windows Sandbox, SMB1 — with
different tooling, different risk shape (mostly REQUIRES_REBOOT), and a
different technician trigger ("this old app needs .NET 3.5" vs "I don't
want Xbox stuff"). Worth keeping separate for clarity even though it's a
small catalog; I'd rather say that plainly than inflate it with weak
padding actions to look bigger.

- **`feature_list_report`** — SAFE
  ```
  Get-WindowsOptionalFeature -Online | Select-Object FeatureName,State | Sort-Object State -Descending | Format-Table -AutoSize
  ```

- **`feature_legacy_insecure_report`** — SAFE
  ```
  $watch = 'MicrosoftWindowsPowerShellV2Root','MicrosoftWindowsPowerShellV2','SMB1Protocol','TelnetClient','TFTP'; Get-WindowsOptionalFeature -Online | Where-Object { $_.FeatureName -in $watch -and $_.State -eq 'Enabled' } | Select-Object FeatureName,State | Format-Table -AutoSize
  ```
  Broader than the SMB1-only check already proposed for M08
  (`research-security-additions.md` A3) — also flags PowerShell v2 (lacks
  AMSI/ScriptBlock logging, a known downgrade-attack vector) and Telnet/TFTP
  clients (plaintext protocols, rarely needed). Overlaps A3 partially by
  design; not worth trying to avoid since it's one filter list either way.

- **`feature_enable_dotnet35`** — REQUIRES_REBOOT
  ```
  Enable-WindowsOptionalFeature -Online -FeatureName NetFx3 -All -NoRestart
  ```
  Value: extremely common ask — an older LOB or hobby app that only runs
  under .NET 3.5. Real compat risk worth stating: this frequently fails
  with error 0x800f0950 ("source files could not be found") when Windows
  Update-based feature restoration is disabled by policy or there's no
  internet — the technician then needs the original Windows installation
  media as a `-Source` path, which this one-liner can't supply without a
  file picker the GUI doesn't have. Flag this as a known, common failure
  mode in the description text rather than let it look like a bug.

- **`feature_disable_powershell_v2`** — MODERATE
  ```
  Disable-WindowsOptionalFeature -Online -FeatureName MicrosoftWindowsPowerShellV2Root -NoRestart
  ```
  undo_command: `Enable-WindowsOptionalFeature -Online -FeatureName MicrosoftWindowsPowerShellV2Root -All -NoRestart`

- **`feature_enable_sandbox`** — REQUIRES_REBOOT
  ```
  try { Enable-WindowsOptionalFeature -Online -FeatureName 'Containers-DisposableClientVM' -All -NoRestart -EA Stop; Write-Output 'Windows Sandbox enabled - restart required.' } catch { Write-Output ('Could not enable Windows Sandbox: ' + $_.Exception.Message + ' (needs Windows 10/11 Pro or Enterprise, and virtualization enabled in firmware.)') }
  ```
  Genuinely useful technician utility ("let me test this file/installer
  somewhere disposable") but has real prerequisites (Pro/Enterprise edition,
  CPU virtualization enabled in firmware) this tool can't fix or verify
  beyond catching the resulting error — the `try/catch` turns a hard crash
  into a readable message instead, matching the established pattern used
  for `Confirm-SecureBootUEFI` on legacy BIOS.

Architecture notes: `Get/Enable/Disable-WindowsOptionalFeature` are DISM
PowerShell cmdlets, built-in, admin required (already handled by the app).
No per-action input needed — each action targets one specific, named
feature, no "which one" ambiguity.

---

## 3. Candidates considered and explicitly rejected as standalone modules

Per the brief's own list, a few areas don't clear the bar for a *new
module* — either too thin (1-2 actions) or already well-covered elsewhere.
Recording the reasoning so it isn't re-litigated:

- **Battery health** (`powercfg /batteryreport`) — genuinely useful, SAFE,
  cheap, but it's really one action:
  ```
  $path = "$env:ProgramData\PortableFix\battery_report.html"; New-Item -ItemType Directory -Force -Path (Split-Path $path) | Out-Null; powercfg /batteryreport /output $path /duration 14 | Out-Null; if (Test-Path $path) { Write-Output ('Battery report generated: ' + $path) } else { Write-Output 'Could not generate battery report (desktop PC with no battery, or powercfg failed).' }
  ```
  `powercfg` writes an HTML file rather than console output, so the action
  can only point at the file, not display the report inline — that's fine
  for a single SAFE action, not enough substance for its own catalog.
  **Recommendation: add as a new action in `m01_diagnostics`**, next to
  `computer_info` (which already surfaces `Win32_Battery` charge %).

- **Partition/volume management beyond M03** — `m01_diagnostics` already
  has `volumes` (`Get-Volume`) and `m03_disk` already has SMART status,
  volume report, SpotFix, TRIM/optimize, chkdsk-at-reboot. What's left
  (GPT/MBR scheme, hidden EFI/Recovery partition listing, shrink/extend
  headroom via `Get-PartitionSupportedSize`) is 1-2 thin additions that
  overlap significantly with what those two modules already show.
  **Recommendation: fold 1-2 actions into `m03_disk`**, not a new module.

- **Event Viewer deep-dive beyond M01** — `eventlog_critical_7d` (7-day
  System+Application critical/error sweep) and `bsod_summary` (minidumps +
  BugCheck events) already exist in `m01_diagnostics`. Genuinely new ground
  is thin: per-log health/size (`Get-WinEvent -ListLog * | Where-Object
  {$_.IsLogFull}`), and a Security-log logon-failure summary (caveat: only
  useful if Advanced Audit Policy logon-failure auditing was already turned
  on *before* the incident, which it usually isn't on a home PC — same
  "heuristic lead, not ground truth" caveat as A8 in
  `research-security-additions.md`). Two actions, not a module.
  **Recommendation: fold into `m01_diagnostics`.**

- **Malware-adjacent forensics** — see §1; almost entirely already speced
  as M08 additions. The two sub-ideas not already covered there
  ("recently-modified system files," "unusual process/service names") are
  two more thin audit actions, e.g.:
  ```
  Get-ChildItem "$env:WINDIR\System32" -Filter *.exe -EA SilentlyContinue | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-14) } | Select-Object Name,LastWriteTime | Sort-Object LastWriteTime -Descending | Format-Table -AutoSize
  ```
  **Recommendation: fold into `m08_security`** alongside the existing
  Bucket A proposals, not a new module.

---

## 4. Cross-cutting architecture findings

- **No per-action parameter input anywhere in the GUI** (checkbox list +
  Run/DRY-RUN only, confirmed in `main_window.py` and `models.py` — every
  `command` is one static string baked in at YAML-load time). Every
  proposal above that would naturally want a parameter ("which printer,"
  "which folder to restore into," "which add-in") was designed around this
  by using a bulk operation or a fixed/derived convention instead — the
  same solve the existing codebase already uses (`hard_defender_clear_exclusions`
  clears *all* exclusions; backup paths are always fixed to
  `$env:ProgramData\PortableFix\...`, never user-chosen). None of the six
  proposed modules need new GUI capability to work.
- **Native (non-cmdlet) exe exit codes need explicit normalization.**
  Already a solved, precedented pattern in this codebase (DISM's 3010,
  `vssadmin`'s "No items found," `netsh`'s reset) — `robocopy` in M18 needs
  the same treatment (0-7 = success, 8+ = failure) since the GUI's
  ok/failed status and undo.ps1 write both key off a strict
  `exit_code == 0`. Not a new architecture gap, just a pattern every new
  module touching a raw exe must remember to apply.
  **Recommendation**: this is a `robocopy`-shaped foot-gun likely to recur
  in future modules too (any future action that shells out to a native
  tool with non-standard exit-code semantics); worth capturing in the
  test/lint conventions used for the existing catalog test files (I did not
  add a test — this is a research doc — but it's the one thing here I'd
  proactively flag for whoever implements M18).
- **One genuine unresolved question, in M16**: whether
  `OfficeC2RClient.exe scenario=Repair` blocks synchronously under this
  tool's `-NoProfile -NonInteractive` PowerShell launch, or can detach into
  a background installer the 300-second inactivity watchdog might kill
  while real work continues unsupervised. Needs verification against a
  live Office install before shipping `office_quick_repair`/`office_online_repair`
  — every other command in this whole report is a plain synchronous
  cmdlet/exe call with no equivalent ambiguity.
- **No new `ModuleCategory` value needed for anything proposed.** All six
  modules fit inside the existing SAFE/MODERATE/DESTRUCTIVE/REQUIRES_REBOOT
  and DIAGNOSTICS/CLEANUP/REPAIR/SECURITY enums with zero Python changes —
  matches the precedent already established across every other research
  doc in this folder (the m08-hardening proposal in
  `research-security-additions.md` reached the identical conclusion).

---

## 5. Priority ranking

If only building a subset, in order of (a) how often the underlying
complaint shows up on a real family/small-office repair visit, (b) how
little this tool currently does for it, (c) implementation risk:

1. **M14 `m14_printing`** — printer complaints are extremely common,
   completely unaddressed today beyond a one-shot queue reset, and every
   action here is a plain, well-supported cmdlet with no open questions.
2. **M18 `m18_user_backup`** — "back up my stuff before you touch my PC" is
   a first-conversation ask on nearly every repair visit and has zero
   coverage today; the only real caveat (same-drive backup) is honest and
   stated, not a blocker.
3. **M15 `m15_boot_platform`** — cheap, all-SAFE-except-one, high-signal
   pre-flight checklist; the BitLocker/TPM half is already reviewed
   (`research-security-additions.md`), only the boot/BCD half is fully new.
4. **M17 `m17_browser_deep`** — real value (hijack/forced-policy detection
   nothing else in the tool can see), but ships with the most unresolved
   "verify against a current browser build" schema caveats of the six.
5. **M16 `m16_office_repair`** — high value but ship `office_quick_repair`/
   `office_online_repair` only after resolving the synchronous-execution
   question above; the three audit actions are safe to build immediately.
6. **M19 `m19_win_features`** — smallest, most niche audience of the six;
   fine to build last or skip if time is limited.
