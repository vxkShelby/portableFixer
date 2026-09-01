# PortableFix — Security Module Research (M08 + Hardening Candidate)

Scope: read-only research, no files modified. Target: family/small-office PC, technician runs this tool locally with owner consent. All commands are built-in Windows tools only (PowerShell 5.1 + WMI/CIM), one static one-liner per action, matching the existing `Modules/*/actions.yaml` schema.

## 0. Method note — what the codebase actually enforces

Before proposing anything I read the schema and the runner logic, not just the YAML, because two constraints in the brief ("SECURITY triggers a restore point", "reversible needs undo_command") are enforced in Python, not YAML:

- **Schema** (`portablefix/models.py`): `ActionDef(id, label_sk, label_en, risk, command, description_sk, description_en, preview_command=None, undo_command=None)`. `RiskLevel` = SAFE/MODERATE/DESTRUCTIVE/REQUIRES_REBOOT. `ModuleCategory` = DIAGNOSTICS/CLEANUP/REPAIR/SECURITY (fixed 4-value enum — no "HARDENING" category exists today).
- **Module discovery** (`portablefix/module_engine.py:52`): `load_all_modules` does `modules_dir.glob("*/actions.yaml")` — any new `Modules/<name>/actions.yaml` is auto-registered with **zero code changes**.
- **Restore point trigger** (`portablefix/gui/main_window.py:372-375`): fires once per batch when `action.risk == DESTRUCTIVE OR module.category in (REPAIR, SECURITY)`. So it's broader than "SECURITY only" — REPAIR does too. Also: this already fires today for the 3 existing SAFE audit actions in M08, because the trigger is category-wide, not per-action-risk. My additions don't change that behavior, just confirming it's pre-existing.
- **GUI grouping** (`main_window.py:186-191`): the UI flattens *all modules sharing a category* into one card. A brand-new module with `category: SECURITY` renders in the exact same "Security" section as M08 — a technician sees no difference between "extend M08" and "new module, same category."
- **Undo convention** (observed across M02/M05/M06/M09/M13): undo is either (a) a fixed inverse value / `Remove-ItemProperty` when the "off" state is an unambiguous Windows default (e.g. `debloat_disable_telemetry`), or (b) a snapshot-to-file-then-restore when the prior value is arbitrary/unknown (e.g. `net_hosts_reset` backs up `hosts` to `hosts.bak` before overwriting). I reused pattern (b) wherever "undo" would otherwise require guessing the machine's prior state — same idea, same file-backup mechanism, no new abstraction invented.
- **Tests**: `tests/test_m08_catalog.py` hard-asserts the *exact* action count (5), exact id set, and exact risk distribution for `m08_security`. Any change to M08 itself requires editing this test. A brand-new module (`m14_...`) needs its own new test file instead and leaves this one untouched.
- Existing catalog convention I matched exactly: Slovak text has **no diacritics** anywhere in any `Modules/*/actions.yaml` (e.g. "nic nemeni", not "nič nemení"). I followed that for every `label_sk`/`description_sk` below.

Current M08 (unchanged, for reference): `sec_defender_status`, `sec_firewall_status`, `sec_defender_update` (MODERATE... actually SAFE), `sec_defender_quickscan` (MODERATE), `sec_uac_status`. All SAFE except quickscan. No undo_command anywhere in M08 today.

---

## Bucket A — AUDIT additions (read-only, land in M08)

All 13 requested items are **ACCEPTED** (0 rejected). All risk = `SAFE` (matches the existing rule: every read-only action in every module in this repo is SAFE, no exceptions found). None get `undo_command` (matches `test_m08_catalog_no_action_has_undo_command`).

Two dedup checks I ran before accepting, worth recording:
- `sec_suspicious_scheduled_tasks` (below) does **not** duplicate `m07_autoruns: autoruns_scheduled_tasks`. M07's action dumps *all* non-disabled tasks unfiltered (TaskName/TaskPath/State only). Mine filters specifically to tasks whose `Execute` path lives under Temp/AppData/Public and also surfaces the actual executable + arguments — a different, narrower, persistence-hunting lens. Complementary, not redundant.
- `sec_windows_update_last` does not duplicate anything in `m05_windows_update` (which only checks/repairs the *service plumbing* — `wu_check_services`, `wu_reset_cache`, etc. — never "when did an update last land," which is the actual security-posture question: is this machine dangerously behind on patches).

### A1 — `sec_bitlocker_status`
- **Risk:** SAFE
- **Label:** SK "Stav BitLocker / sifrovania disku" · EN "BitLocker / disk encryption status"
- **Command:**
  ```
  try { Get-BitLockerVolume -ErrorAction Stop | Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionPercentage | Format-Table -AutoSize } catch { $v = Get-CimInstance -Namespace 'root/cimv2/security/MicrosoftVolumeEncryption' -ClassName Win32_EncryptableVolume -ErrorAction SilentlyContinue; if ($v) { $v | Select-Object DriveLetter,ProtectionStatus,ConversionStatus | Format-Table -AutoSize } else { Write-Output 'BitLocker/Device Encryption not available on this Windows edition.' } }
  ```
- **Value:** Unencrypted disk = total data loss/exposure the moment the machine is lost/stolen/resold. Cheapest possible check for one of the highest-impact gaps.
- **False-positive/compat risk:** `Get-BitLockerVolume` requires the BitLocker PowerShell module, which **does not exist on Windows Home** (verified via search — confirmed Microsoft Q&A). Most family PCs run Home. The `catch` falls back to the raw WMI class (`Win32_EncryptableVolume`), which is present on Home too because consumer "Device Encryption" uses the same underlying volume-encryption subsystem — this is why the fallback is a `try/catch` inside one static one-liner rather than two separate actions. Still report `VolumeStatus`/`ConversionStatus`, not the recovery key, obviously.

### A2 — `sec_tpm_secureboot_status`
- **Risk:** SAFE
- **Label:** SK "Stav TPM a Secure Boot" · EN "TPM and Secure Boot status"
- **Command:**
  ```
  Get-Tpm | Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated | Format-List; try { Write-Output ('SecureBoot enabled: ' + (Confirm-SecureBootUEFI)) } catch { Write-Output 'SecureBoot: not supported (Legacy BIOS/CSM or firmware does not expose this check)' }
  ```
- **Value:** Baseline platform-integrity signal (Windows 11 eligibility, anti-rootkit posture); also explains a lot of "can't upgrade to Win11" tickets for free.
- **False-positive/compat risk:** `Confirm-SecureBootUEFI` throws a terminating, red-text error on legacy BIOS/CSM machines instead of returning `$false` — wrapped in try/catch specifically so the report doesn't look like the tool crashed on older hardware.

### A3 — `sec_smb1_status`
- **Risk:** SAFE
- **Label:** SK "Kontrola protokolu SMBv1" · EN "SMBv1 protocol check"
- **Command:**
  ```
  Write-Output ('SMB1 server protocol enabled: ' + (Get-SmbServerConfiguration).EnableSMB1Protocol); Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol | Select-Object FeatureName,State | Format-List
  ```
- **Value:** SMBv1 is the WannaCry/EternalBlue/NotPetya vector. Off by default since Win10 1709, but downgrade paths, OEM images, and "I turned it on to talk to my old NAS" are common on real machines.
- **False-positive/compat risk:** Reports *two* different things on purpose — the server-side config flag (`Set/Get-SmbServerConfiguration`, no reboot to change) and the optional Windows *feature* (client+server binaries, needs a reboot to fully remove). Technicians conflating these is the single most common SMB1 mistake; the audit output makes the split explicit so the hardening action's exact scope (below) isn't oversold.

### A4 — `sec_rdp_status`
- **Risk:** SAFE
- **Label:** SK "Stav vzdialenej plochy (RDP)" · EN "Remote Desktop (RDP) status"
- **Command:**
  ```
  $v = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections; Write-Output ('RDP denied (fDenyTSConnections, 1=off/0=on): ' + $v); Get-NetFirewallRule -DisplayGroup 'Remote Desktop' | Select-Object DisplayName,Enabled,Direction,Action | Format-Table -AutoSize
  ```
- **Value:** Exposed/brute-forceable RDP is one of the top real-world initial-access vectors for home and small-office ransomware (BlueKeep, credential-stuffing botnets scanning residential ISP ranges). This single read-only check is arguably the highest-leverage new item in bucket A.
- **False-positive/compat risk:** None for the audit itself (read-only). It exists specifically to gate the bucket-B hardening action below — don't disable RDP blind, check first whether it's even on.

### A5 — `sec_local_admins`
- **Risk:** SAFE
- **Label:** SK "Zoznam lokalnych administratorov" · EN "Local administrators list"
- **Command:**
  ```
  Get-LocalGroupMember -Group 'Administrators' | Select-Object Name,PrincipalSource,ObjectClass | Format-Table -AutoSize
  ```
- **Value:** Classic "why is the kid's account admin" / "malware added itself to Administrators" check. Near-zero cost, high technician utility.
- **False-positive/compat risk:** Minimal. On domain-joined machines this can also list AD groups (e.g. "Domain Admins") — expected, not a bug, but worth a technician's second look, not the tool's.

### A6 — `sec_password_never_expires`
- **Risk:** SAFE
- **Label:** SK "Ucty s neexpirujucim heslom" · EN "Accounts with non-expiring passwords"
- **Command:**
  ```
  Get-LocalUser | Where-Object { $_.Enabled -eq $true -and $_.PasswordExpires -eq $null } | Select-Object Name,Enabled,PasswordLastSet | Format-Table -AutoSize
  ```
- **Value:** Surfaces accounts (including a forgotten local admin the OEM/technician left behind) that never force a password change.
- **False-positive/compat risk:** On a single-user home PC this is normal and expected (nobody wants a forced password reset nag on their own PC) — this is descriptive, not prescriptive; frame the report text as "review", not "problem", to avoid crying wolf on the majority-normal case.

### A7 — `sec_autologon_check`
- **Risk:** SAFE
- **Label:** SK "Kontrola automatickeho prihlasenia (plaintext heslo)" · EN "Autologon check (plaintext password)"
- **Command:**
  ```
  $w = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'; $p = Get-ItemProperty -Path $w -ErrorAction SilentlyContinue; [PSCustomObject]@{ AutoAdminLogon = $p.AutoAdminLogon; DefaultUserName = $p.DefaultUserName; PlaintextPasswordStored = [bool]$p.DefaultPassword } | Format-List
  ```
- **Value:** `DefaultPassword` under Winlogon is a genuinely plaintext, HKLM-readable-by-anyone-with-local-access account password. This is a concrete, common (OEMs and "helpful" relatives set autologon constantly), high-severity finding.
- **False-positive/compat risk:** None re: false positives. The important design point is the opposite direction: the command deliberately reports only a **boolean** (`PlaintextPasswordStored`), never the actual password value — because this tool writes an HTML report to disk (`portablefix/report.py`), and echoing the real secret into that command's captured output would leak it into the tool's own report file. Don't let a security *audit* become a new place the secret is stored in plaintext.

### A8 — `sec_recent_local_accounts`
- **Risk:** SAFE
- **Label:** SK "Nedavno vytvorene lokalne ucty" · EN "Recently created local accounts"
- **Command:**
  ```
  Get-LocalUser | Select-Object Name,Enabled,PasswordLastSet | Sort-Object PasswordLastSet -Descending | Format-Table -AutoSize; Write-Output '--- Profile folder creation time (heuristic proxy for account age) ---'; Get-ChildItem "$env:SystemDrive\Users" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin @('Public','Default','Default User','All Users','defaultuser0') } | Select-Object Name,CreationTime | Sort-Object CreationTime -Descending | Format-Table -AutoSize
  ```
- **Value:** A surprise new local/admin account is a classic persistence technique. Cheap to check.
- **False-positive/compat risk:** **Important limitation to state plainly in the tool's description text**: `Get-LocalUser` has no "date created" property at all (verified against the actual cmdlet output shape), and there is no WMI/ADSI equivalent to Active Directory's `whenCreated` for local accounts either. The only built-in proxy is the `C:\Users\<name>` profile folder's `CreationTime`, which really measures "first interactive logon", not "account creation" — a renamed profile, a migrated/re-imaged profile, or roaming-profile setups will all skew it. Filtered out the four standard pseudo-profile folders (`Public`, `Default`, `Default User`, `defaultuser0`) to cut baseline noise. Ship this as a heuristic lead, not a verdict — the description text should say so explicitly (the real authoritative source, Security event ID 4720, needs account-management auditing to have been turned on *before* the account was created, which it usually isn't on a home PC).

### A9 — `sec_hosts_anomaly`
- **Risk:** SAFE
- **Label:** SK "Anomalie v hosts subore" · EN "Hosts file anomaly summary"
- **Command:**
  ```
  $lines = Get-Content "$env:WINDIR\System32\drivers\etc\hosts" | Where-Object { $_ -match '\\S' -and $_ -notmatch '^\\s*#' -and $_ -notmatch '127\\.0\\.0\\.1\\s+localhost' -and $_ -notmatch '::1\\s+localhost' }; Write-Output ('Non-default active hosts entries: ' + $lines.Count); $lines
  ```
  (Note on escaping: this is the YAML-ready, doubly-escaped form. The actual PowerShell regexes after YAML unescaping are `\S`, `^\s*#`, `127\.0\.0\.1\s+localhost`, `::1\s+localhost` — double-backslash is not a typo, YAML's own quoting doubles what PowerShell needs.)
- **Value:** Complements `net_hosts_reset` in M06 (which nukes the file unconditionally) with a non-destructive "how bad is it, actually" count-and-list first — useful when the file has legitimate custom entries (dev boxes, a family Pi-hole-style block list) and a full reset would be overkill or unwelcome.
- **False-positive/compat risk:** A household that intentionally maintains an ad-block hosts file (Steven Black list, self-installed) will show a large but completely benign count. This is exactly why it's a "summary for a human to read," not a scored/auto-flagged result.

### A10 — `sec_suspicious_scheduled_tasks`
- **Risk:** SAFE
- **Label:** SK "Podozrive naplanovane ulohy (Temp/AppData)" · EN "Suspicious scheduled tasks (Temp/AppData)"
- **Command:**
  ```
  Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' } | ForEach-Object { $t = $_; $t.Actions | Where-Object { $_.Execute -match 'Temp\\\\|AppData\\\\|Users\\\\Public\\\\' } | Select-Object @{n='Task';e={$t.TaskName}},@{n='Path';e={$t.TaskPath}},@{n='Execute';e={$_.Execute}},@{n='Arguments';e={$_.Arguments}} } | Format-Table -AutoSize -Wrap
  ```
  (Same note: after YAML unescaping the regex is `Temp\\|AppData\\|Users\\Public\\`.)
- **Value:** Executing from Temp/AppData is one of the most-cited persistence heuristics in incident-response checklists (malware rarely has write access to `Program Files`, but always has write access to its own AppData).
- **False-positive/compat risk:** **This is the noisiest item in bucket A, flag it clearly.** A large number of entirely legitimate auto-updaters live in `%LocalAppData%` by design and register scheduled tasks from there — Google Chrome/Edge Update, Dropbox, Discord, Spotify, Steam client services, OneDrive, etc. Expect this list to be non-empty on a completely clean, healthy PC. Ship it as a *triage starting list* for a human to eyeball task names against, not an infection verdict — do not auto-flag/auto-disable off this heuristic alone.

### A11 — `sec_root_cert_report`
- **Risk:** SAFE
- **Label:** SK "Prehlad korenovych certifikatov" · EN "Root certificate report"
- **Command:**
  ```
  $c = Get-ChildItem Cert:\\LocalMachine\\Root; Write-Output ('Total root CA certificates: ' + $c.Count); $c | Sort-Object NotBefore -Descending | Select-Object -First 15 Subject,NotBefore,Thumbprint | Format-Table -AutoSize -Wrap
  ```
- **Value:** Rogue root CAs are a known real-world malware/adware technique for silent HTTPS MITM (Superfish, eDellRoot-style incidents, some "free VPN"/parental-control/proxy PUAs). A machine with a wildly higher root-cert count than a stock Windows baseline (~40-70) is a real signal.
- **False-positive/compat risk:** Marked "report-only" in the brief for good reason — `NotBefore` is the certificate's own validity-start date (set by the issuing CA), **not** the date it was imported into this machine's store. Windows does not track "date added to store" anywhere accessible without turning on the CAPI2 operational event log (off by default). So this is sorted by the best available proxy, not ground truth — say so in the description text, and lean on the *count* (easy to sanity-check against a known-good baseline) more than the sort order.

### A12 — `sec_defender_exclusions_list`
- **Risk:** SAFE
- **Label:** SK "Vynimky Windows Defender" · EN "Windows Defender exclusions"
- **Command:**
  ```
  Get-MpPreference | Select-Object ExclusionPath,ExclusionExtension,ExclusionProcess,ExclusionIpAddress | Format-List
  ```
- **Value:** As the brief already flags — self-excluding from AV scanning is one of the most common, highest-confidence real-world malware persistence moves technicians actually find in the field. This is likely the single highest-signal item in all of bucket A. Directly pairs with the bucket-B remediation (`hard_defender_clear_exclusions`).
- **False-positive/compat risk:** Legitimate software (game launchers, dev tools, backup agents, some VPNs) also asks users to add AV exclusions for performance — so a non-empty list is not proof of infection by itself, but every entry is now visible to a human for a two-second sanity check, which is the whole point.

### A13 — `sec_windows_update_last`
- **Risk:** SAFE
- **Label:** SK "Datum poslednej aktualizacie Windows" · EN "Last Windows Update install date"
- **Command:**
  ```
  try { $r = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results; Write-Output ('Last successful search: ' + $r.LastSearchSuccessDate); Write-Output ('Last successful install: ' + $r.LastInstallationSuccessDate) } catch { Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID,Description,InstalledOn | Format-Table -AutoSize }
  ```
- **Value:** An unpatched machine is exploitable regardless of every other control on this list. Directly actionable: if this comes back stale, run the existing M05 module (`wu_trigger_detection`, etc.) to fix it.
- **False-positive/compat risk:** Deliberately does **not** use `Get-HotFix`/`Win32_QuickFixEngineering` as the primary source — that's the common naive approach, but it's legacy and silently misses most modern cumulative updates on Win10/11 (they're serviced via component-based servicing, not the classic hotfix installer, so they frequently don't register as a "hotfix" at all). The `Microsoft.Update.AutoUpdate` COM object's `Results.LastInstallationSuccessDate` reliably reflects real Windows Update activity regardless of update type; `Get-HotFix` is kept only as a same-one-liner fallback if the COM object is unavailable.

---

## Bucket B — HARDENING additions (mutating, candidate module)

All 8 requested items are **ACCEPTED** (0 rejected) — but "accepted" is not "recommend running blind on every visit"; three of the eight carry real enough collateral-damage potential on an unknown family/office PC that I'm explicit below about excluding them from the top-5 despite genuine security value, and about how the GUI should gate them.

### Where should these live?

Two options, both zero-code-change (module discovery is a directory glob):

1. **Extend `Modules/m08_security/actions.yaml` in place**, still `category: SECURITY`. Simplest possible diff. Downside: `tests/test_m08_catalog.py` hard-codes "exactly 5 actions, this exact id set, this exact risk distribution" — every one of those assertions has to be rewritten.
2. **New module `Modules/m14_hardening/actions.yaml`** (next free number after `m13`; note `m11` is oddly missing from the sequence — grepped the repo and found no plan/doc reserving it, so I'm not assuming it's spoken for, just picking the safe append-only slot), with `category: SECURITY`.

**Recommendation: option 2.** Because the GUI groups modules by category, not by module file (`main_window.py:186-191`), a new module with `category: SECURITY` renders in the *exact same* "Security" section of the app as extending M08 would — there is zero user-visible difference. But option 2 (a) leaves the already-passing `test_m08_catalog.py` completely untouched, (b) gets its own fresh test file with no pre-existing hard-coded assumptions to fight, and (c) keeps "read-only audit" and "mutates your system" in physically separate files, which matters for anyone (a future contributor, a security reviewer) who wants to grep for "which YAML files can actually change this machine" without reading every action's `risk:` field. `category: SECURITY` is what makes the mandatory pre-batch restore point apply automatically — confirmed from `main_window.py:372-375`, no code change needed, no new `ModuleCategory` enum value needed.

Action id prefix: `hard_` (mirrors the existing `sec_`/`net_`/`tune_`/`debloat_` per-module convention).

### B1 — `hard_disable_smb1`
- **Risk:** MODERATE
- **Label:** SK "Vypnutie SMBv1" · EN "Disable SMBv1"
- **Command:** `Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force; Write-Output 'SMB1 server component disabled (takes effect immediately, no reboot needed for new connections).'`
- **Undo:** `Set-SmbServerConfiguration -EnableSMB1Protocol $true -Force`
- **Value:** Closes the WannaCry/EternalBlue vector. Off by default on any Windows installed since ~2017, so on most machines this is a no-op confirming an already-good state.
- **False-positive/compat risk:** This only flips the **server-side** protocol flag (inbound file sharing). It does not remove the SMB1 *client* driver, so this machine can still connect out to someone else's old SMB1 share — full removal needs the `SMB1Protocol` Windows optional feature disabled, which needs a reboot and is a bigger hammer than most family-PC visits warrant. Deliberately scoped to the safe, instant, reversible half rather than proposing a second REQUIRES_REBOOT action for the full removal — say this scope limit in the description text so nobody oversells it. Real compatibility casualty: very old NAS boxes, ancient network printers/scanners, and pre-2010 game consoles that only speak SMB1 as a *server* will stop being reachable from this PC.

### B2 — `hard_disable_rdp`
- **Risk:** MODERATE
- **Label:** SK "Vypnutie vzdialenej plochy (RDP)" · EN "Disable Remote Desktop (RDP)"
- **Command:** `Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 1 -Type DWord; Disable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue; Write-Output 'Remote Desktop disabled (registry + firewall rule group).'`
- **Undo:** `Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0 -Type DWord; Enable-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue`
- **Value:** Exposed RDP is a top real-world ransomware entry point for exactly this audience (home routers with UPnP-forwarded 3389, small offices with "just RDP in from home" habits).
- **False-positive/compat risk:** **This is the one the brief itself calls out, and it's real.** If the technician (or a family member who manages this PC for a relative) is *connected to this very session over RDP* when this command runs, they lock themselves out the moment the session drops — `fDenyTSConnections` blocks new connections immediately, it doesn't need a reboot to bite. Recommendation beyond the one-liner itself: gate this behind `sec_rdp_status` (require the audit to show it's actually on first) and give it its own standalone confirmation dialog text distinct from the generic MODERATE confirm — something like "If you are remoted into this PC right now, do not run this." Never include in a "select all" bulk-hardening button.

### B3 — `hard_firewall_enable_all`
- **Risk:** MODERATE
- **Label:** SK "Zapnutie firewallu na vsetkych profiloch" · EN "Enable firewall on all profiles"
- **Command:** `$bk = "$env:ProgramData\PortableFix\fw_profile_backup.csv"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; if (-not (Test-Path $bk)) { Get-NetFirewallProfile | Select-Object Name,Enabled | Export-Csv -Path $bk -NoTypeInformation }; Set-NetFirewallProfile -All -Enabled True; Write-Output 'Firewall enabled on all profiles (Domain/Private/Public).'`
- **Undo:** `$bk = "$env:ProgramData\PortableFix\fw_profile_backup.csv"; if (Test-Path $bk) { Import-Csv $bk | ForEach-Object { Set-NetFirewallProfile -Name $_.Name -Enabled ([System.Convert]::ToBoolean($_.Enabled)) } } else { Write-Output 'No backup found; firewall left enabled.' }`
- **Value:** Directly remediates the existing `sec_firewall_status` audit finding. Deliberately only touches `-Enabled`, not `DefaultInboundAction`/`DefaultOutboundAction`, specifically to avoid clobbering a custom policy some other software (VPN client, LAN game, home server) already set up — minimal-diff hardening, not a full firewall reset.
- **False-positive/compat risk:** Low. Backs up per-profile enabled state to a CSV before touching anything (same pattern as `net_hosts_reset`'s `.bak` file), so undo restores the exact prior per-profile state rather than guessing.

### B4 — `hard_uac_restore_default`
- **Risk:** MODERATE
- **Label:** SK "Obnovenie UAC na predvolene nastavenie" · EN "Restore UAC to default"
- **Command:** `$k = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System'; $bk = "$env:ProgramData\PortableFix\uac_backup.json"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; if (-not (Test-Path $bk)) { Get-ItemProperty -Path $k | Select-Object EnableLUA,ConsentPromptBehaviorAdmin,PromptOnSecureDesktop | ConvertTo-Json | Set-Content -Path $bk -Encoding UTF8 }; Set-ItemProperty -Path $k -Name EnableLUA -Value 1 -Type DWord; Set-ItemProperty -Path $k -Name ConsentPromptBehaviorAdmin -Value 5 -Type DWord; Set-ItemProperty -Path $k -Name PromptOnSecureDesktop -Value 1 -Type DWord; Write-Output 'UAC restored to Windows default (takes full effect after sign-out/restart).'`
- **Undo:** `$k = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System'; $bk = "$env:ProgramData\PortableFix\uac_backup.json"; if (Test-Path $bk) { $d = Get-Content $bk -Raw | ConvertFrom-Json; Set-ItemProperty -Path $k -Name EnableLUA -Value $d.EnableLUA -Type DWord; Set-ItemProperty -Path $k -Name ConsentPromptBehaviorAdmin -Value $d.ConsentPromptBehaviorAdmin -Type DWord; Set-ItemProperty -Path $k -Name PromptOnSecureDesktop -Value $d.PromptOnSecureDesktop -Type DWord } else { Write-Output 'No backup found.' }`
- **Value:** UAC fully off is one of the most common "a well-meaning technician (or malware) turned this off to stop the popups" findings, and it quietly weakens the effectiveness of nearly every other control on this machine (silent elevation for anything, including malware). Directly remediates `sec_uac_status`.
- **False-positive/compat risk:** Low. `ConsentPromptBehaviorAdmin=5`/`PromptOnSecureDesktop=1`/`EnableLUA=1` is the stock Windows default that essentially all commercial software already expects and is tested against — software that specifically "needs UAC off to install" is itself a yellow flag more often than a legitimate requirement. `EnableLUA` changes need a sign-out or restart to fully apply, matching the same "takes effect after restart" caveat pattern already used for `debloat_disable_widgets`.

### B5 — `hard_defender_clear_exclusions`
- **Risk:** MODERATE
- **Label:** SK "Odstranenie vynimiek Windows Defender" · EN "Remove Windows Defender exclusions"
- **Command:** `$p = Get-MpPreference; $bk = "$env:ProgramData\PortableFix\defender_exclusions_backup.json"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; [PSCustomObject]@{ Path = $p.ExclusionPath; Extension = $p.ExclusionExtension; Process = $p.ExclusionProcess; IpAddress = $p.ExclusionIpAddress } | ConvertTo-Json | Set-Content -Path $bk -Encoding UTF8; if ($p.ExclusionPath) { Remove-MpPreference -ExclusionPath $p.ExclusionPath }; if ($p.ExclusionExtension) { Remove-MpPreference -ExclusionExtension $p.ExclusionExtension }; if ($p.ExclusionProcess) { Remove-MpPreference -ExclusionProcess $p.ExclusionProcess }; if ($p.ExclusionIpAddress) { Remove-MpPreference -ExclusionIpAddress $p.ExclusionIpAddress }; Write-Output 'Defender exclusions cleared (backup saved).'`
- **Undo:** `$bk = "$env:ProgramData\PortableFix\defender_exclusions_backup.json"; if (Test-Path $bk) { $d = Get-Content $bk -Raw | ConvertFrom-Json; if ($d.Path) { Add-MpPreference -ExclusionPath $d.Path }; if ($d.Extension) { Add-MpPreference -ExclusionExtension $d.Extension }; if ($d.Process) { Add-MpPreference -ExclusionProcess $d.Process }; if ($d.IpAddress) { Add-MpPreference -ExclusionIpAddress $d.IpAddress } } else { Write-Output 'No backup found.' }`
- **Value:** Directly remediates the exact malware-persistence pattern the brief calls out — if malware excluded its own folder from scanning, this removes that exclusion and the next quick scan (`sec_defender_quickscan`, already in M08) can actually see it.
- **False-positive/compat risk:** The main legitimate-collateral scenario is developer/gamer machines (Visual Studio, Docker, Steam/game library folders, some backup software) that were told to add exclusions for disk-scan performance — less common on a pure family PC, more likely on a "small-office" one, which is exactly the audience named in the brief. Fully reversible via the JSON backup, which is why this is MODERATE and not DESTRUCTIVE.

### B6 — `hard_disable_autologon`
- **Risk:** MODERATE
- **Label:** SK "Vypnutie automatickeho prihlasenia" · EN "Disable autologon"
- **Command:** `$w = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'; Set-ItemProperty -Path $w -Name AutoAdminLogon -Value '0' -Type String; Remove-ItemProperty -Path $w -Name DefaultPassword -ErrorAction SilentlyContinue; Write-Output 'Autologon disabled; stored plaintext password removed from the registry.'`
- **Undo:** `Set-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name AutoAdminLogon -Value '1' -Type String; Write-Output 'Autologon re-enabled. The password was NOT restored (by design) - set it again via netplwiz if truly needed.'`
- **Value:** Directly remediates `sec_autologon_check`'s finding — removes a genuinely plaintext, at-rest credential.
- **False-positive/compat risk:** Deliberate, important design choice: **the undo intentionally does not restore the plaintext password anywhere** (not in the registry, not in our own backup file) — only the `AutoAdminLogon` toggle is reversible; the password has to be re-entered by hand (`netplwiz`) if the owner genuinely wants autologon back. Persisting the recovered secret in a "helpful" backup file would just relocate the exact plaintext-secret-at-rest problem this action exists to fix. Separately: many non-technical households deliberately set up autologon for convenience on a single-user home PC and will be confused if it silently stops — pair this with a conversation with the owner, don't bundle it into an unattended bulk-hardening pass.

### B7 — `hard_lsa_protection_enable`
- **Risk:** REQUIRES_REBOOT (per brief)
- **Label:** SK "Zapnutie ochrany LSA (RunAsPPL)" · EN "Enable LSA protection (RunAsPPL)"
- **Command:** `Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name RunAsPPL -Value 1 -Type DWord; Write-Output 'LSA protection (RunAsPPL) enabled. Requires a restart to take effect.'`
- **Undo:** `Remove-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name RunAsPPL -ErrorAction SilentlyContinue`
- **Value:** Runs LSASS as a protected process, raising the bar significantly against Mimikatz-style credential-dumping tools — one of the highest-value single controls against post-compromise lateral movement/credential theft.
- **False-positive/compat risk:** **Deliberately used `Value = 1`, not `2`.** Value `2` UEFI-locks the setting so it survives even registry/offline edits — great for a hardened enterprise fleet, but it would make the `undo_command` a lie (a plain `Remove-ItemProperty` cannot undo the UEFI-locked variant; that needs `bcdedit` / firmware-level changes). Value `1` keeps the promised undo honestly reversible with one registry command, which matters more than maximum lock-in for a repair tool. Real compatibility risk (confirmed via research, this is the most-cited RunAsPPL failure mode): older antivirus drivers, backup agents that inject into LSASS for credential-protected backups, some VPN clients, and unsigned third-party credential providers can fail to load or bluescreen at the *next boot* — which is exactly the moment the technician may no longer be at the machine to fix it. Given the REQUIRES_REBOOT tier already means "you won't see the result immediately," and the target audience (family/small-office PC) is the least likely to have a clean, fully-inventoried software stack, **this is the one item I'd explicitly keep out of any default/bulk selection** despite its genuine textbook value — opt-in only, with its own explicit warning, ideally applied on a schedule where the technician can confirm a clean reboot before leaving.

### B8 — `hard_smartscreen_default`
- **Risk:** MODERATE
- **Label:** SK "Obnovenie SmartScreen na predvolene nastavenie" · EN "Restore SmartScreen to default"
- **Command:** `$k = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer'; $bk = "$env:ProgramData\PortableFix\smartscreen_backup.txt"; New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null; if (-not (Test-Path $bk)) { (Get-ItemProperty -Path $k -Name SmartScreenEnabled -ErrorAction SilentlyContinue).SmartScreenEnabled | Set-Content -Path $bk -Encoding UTF8 }; Set-ItemProperty -Path $k -Name SmartScreenEnabled -Value 'RequireAdmin' -Type String; Write-Output 'SmartScreen restored to the Windows default (RequireAdmin).'`
- **Undo:** `$k = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer'; $bk = "$env:ProgramData\PortableFix\smartscreen_backup.txt"; if (Test-Path $bk) { $v = Get-Content $bk -Raw; Set-ItemProperty -Path $k -Name SmartScreenEnabled -Value $v.Trim() -Type String } else { Write-Output 'No backup found.' }`
- **Value:** Closes a common PUA-installer trick (some bundleware installers flip this to `Off` so subsequent junk installs don't get flagged). Cheap, safe, low blast radius.
- **False-positive/compat risk:** Lowest-urgency item in bucket B — most machines are already at default, and `RequireAdmin` is squarely mainstream (verified as the current default value/name for this key via search, though exact allowed strings have shifted slightly across Windows 10/11 builds — `Off`/`Warn`/`RequireAdmin` historically, some newer docs phrase the middle option as `Prompt`). Worth a quick one-time sanity check against a fresh reference machine if precision matters, rather than trusting this from memory alone.

---

## Top 5 overall (cross-bucket)

Picked for the combination of (a) how often this finding correlates with real compromise on a family/small-office PC, (b) lowest collateral-damage risk, (c) a coherent audit-then-fix story. Explicitly **not** including `hard_lsa_protection_enable` or `hard_disable_rdp` in the top 5 despite high textbook value — see their compatibility-risk notes above for why (reboot-time breakage the technician won't be present for; remote-session self-lockout).

1. **`sec_defender_exclusions_list`** (AUDIT, SAFE) — highest-signal single audit check for active infection; read-only, zero risk.
2. **`hard_defender_clear_exclusions`** (HARDEN, MODERATE) — its direct, backed-up-and-reversible remediation.
3. **`sec_rdp_status`** (AUDIT, SAFE) — flags the #1 real-world home/SMB ransomware network entry vector; read-only, zero risk.
4. **`sec_autologon_check`** (AUDIT, SAFE) — cheap, concrete, catches a genuinely plaintext at-rest credential; careful never to echo the actual secret.
5. **`hard_uac_restore_default`** (HARDEN, MODERATE) — cheap, safe, undoes one of the single most common "someone weakened this on purpose" findings, which quietly undermines every other control on the machine.

Runners-up just outside the 5: `hard_disable_smb1` (excellent value/risk ratio, just slightly lower urgency for a single-PC household with no legacy SMB1 peers) and `hard_firewall_enable_all` (very safe, very cheap, but usually a smaller gap than the other four in practice).
