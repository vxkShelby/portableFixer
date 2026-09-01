# M13 Debloat — What To Add (Research Report)

Scope: compare `Modules/m13_debloat/actions.yaml` against established community debloat
practice (Win11Debloat/Raphire, Chris Titus WinUtil, O&O ShutUp10-style privacy tweaks) and
propose additions. Read-only research; no files modified.

**Current catalog covers:** AppX inventory, aggressive user+provisioned AppX removal,
`AllowTelemetry=0`, ContentDeliveryManager suggestions off, Start web search off, Copilot off,
Widgets off, Advertising ID off, OneDrive uninstall. It does **not** touch any background
service, scheduled task, Explorer/Start "recommendations" surfaces, activity/location
privacy defaults, power settings, or the 2024-2025 Windows-Update-reinstalls-apps-you-just-
removed problem. That gap is the focus below.

## Confidence legend

- **[Verified]** — confirmed via live web search during this research session (2026-09-01).
- **[High]** — not re-verified today, but stable, decade-plus-documented Windows behavior
  (appears in Microsoft's own ADMX templates and/or every major community debloat tool).
- **[Medium]** — plausible and commonly cited, but not independently verified today.
  Test on a real VM before wiring into the default run.

## Standing Home-edition note (applies to nearly every item below)

Windows Home lacks `gpedit.msc` (Local Group Policy Editor) and MDM/CSP push tooling, but
every value under `HKLM|HKCU:\...\Policies\...` in this report is read directly by the OS at
runtime — Home enforces it identically to Pro/Enterprise. The missing GUI only means a Home
user can't set it by hand; a registry one-liner bypasses that entirely. Per-item caveats below
only call out *genuine* exceptions (hardware gating, build-number gating, non-policy paths).

---

## Top 5 by value

Ranked by (impact on the tool's actual audience — technicians servicing old/slow/secondhand
PCs off a USB stick) × (how much of a real gap this is) × (risk-adjusted safety).

| # | id | One-liner (abridged) |
|---|----|----|
| 1 | `debloat_disable_diagtrack_service` | `foreach ($svc in 'DiagTrack','dmwappushservice'){Stop-Service -Name $svc -Force -EA SilentlyContinue; Set-Service -Name $svc -StartupType Disabled -EA SilentlyContinue}` |
| 2 | `debloat_disable_telemetry_tasks` | Disables the 10 stock CEIP/Compat-Appraiser/WER scheduled tasks via `Get-ScheduledTask`/`Disable-ScheduledTask` |
| 3 | `debloat_disable_fast_startup` | `Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' HiberbootEnabled 0` |
| 4 | `debloat_disable_explorer_ads` | Sets `HideRecommendedSection=1` (policy) + `ShowSyncProviderNotifications=0` |
| 5 | `debloat_block_devhome_outlook_reinstall` | Marks the `OutlookUpdate`/`DevHomeUpdate` Windows-Update orchestrator entries as completed |

Why these five specifically, not the more "obvious" privacy toggles (Location, Activity
Feed, Tailored Experiences, etc.): #1/#2 are the single most-implemented, most-expected
action in *every* community debloat tool (Win11Debloat, ShutUp10, WinUtil all lead with
DiagTrack) and this catalog currently has telemetry *policy* but not the *service* —
inconsistent with its own `AllowTelemetry=0` action. #3 is uniquely on-brand for a tool
named "USB Fixer": Fast Startup hybrid-hibernates the boot volume instead of releasing it,
which is a well-known cause of the exact symptoms this tool exists to fix (stale volume
state, dirty bit weirdness on dual-boot/external-drive access after "shutdown"). #4 closes
the most *visible*, most user-recognized "why does my PC have ads" complaint, and is a total
gap today (the existing `debloat_disable_suggestions` only touches Start-menu
ContentDeliveryManager keys, not the 24H2 File-Explorer-native Recommended section). #5
directly patches a hole in the catalog's *own* existing feature: it already removes the
`Microsoft.OutlookForWindows` AppX package, but without this, Windows Update silently
reinstalls it — the existing action is incomplete without this companion.

---

## Accept / reject at a glance

| Candidate (from brief) | Verdict | New action id(s) |
|---|---|---|
| Scheduled-tasks telemetry (Compat Appraiser, CEIP) | **Accept** | `debloat_disable_telemetry_tasks` |
| DiagTrack / dmwappushservice service disable | **Accept** | `debloat_disable_diagtrack_service` |
| Activity History / Timeline off | **Accept** | `debloat_disable_activity_history` |
| Location — global off | **Accept** | `debloat_disable_location` |
| App-permission defaults (per-capability enumeration) | **Reject** | — |
| Cortana leftovers | **Accept (low priority)** | `debloat_disable_cortana_policy` |
| Game Bar / Game DVR off | **Accept** | `debloat_disable_gamedvr` |
| Sticky Keys / accessibility popup nags | **Accept** | `debloat_disable_stickykeys_popup` |
| Explorer ads (sync notifications, Show recommendations) | **Accept** | `debloat_disable_explorer_ads` |
| Start menu web/Bing leftovers (24H2) | **Accept** | `debloat_disable_start_recommendations` |
| Lock screen Spotlight ads | **Accept** | `debloat_disable_lockscreen_spotlight` |
| Tailored experiences | **Accept** | `debloat_disable_tailored_experiences` |
| Feedback frequency | **Accept** | `debloat_disable_feedback` |
| Background apps global toggle | **Reject** | — |
| Teams consumer auto-start leftover (taskbar Chat icon) | **Accept** | `debloat_hide_taskbar_chat` |
| Dev Home / Outlook (new) auto-reinstall prevention | **Accept** | `debloat_block_devhome_outlook_reinstall` |
| Recall (Copilot+ machines) | **Accept** | `debloat_disable_recall` |
| Power-user: Fast Startup off | **Accept** | `debloat_disable_fast_startup` |
| *(own addition)* Windows Welcome/"Suggested settings" nag off | **Accept** | `debloat_disable_welcome_experience` |
| *(own addition)* WER / MRT service disabling | **Reject** | — |

**17 accepted, 3 rejected.**

---

## Accepted proposals (full detail)

### 1. `debloat_disable_diagtrack_service` — Disable DiagTrack + dmwappushservice
- **risk:** MODERATE
- **label_sk:** "Vypnutie sluzieb DiagTrack a dmwappushservice"
- **label_en:** "Disable DiagTrack and dmwappushservice services"
- **command:**
  ```powershell
  foreach ($svc in 'DiagTrack','dmwappushservice') { Stop-Service -Name $svc -Force -EA SilentlyContinue; Set-Service -Name $svc -StartupType Disabled -EA SilentlyContinue }; Write-Output 'DiagTrack and dmwappushservice stopped and disabled.'
  ```
- **undo_command:**
  ```powershell
  foreach ($svc in 'DiagTrack','dmwappushservice') { Set-Service -Name $svc -StartupType Automatic -EA SilentlyContinue; Start-Service -Name $svc -EA SilentlyContinue }; Write-Output 'DiagTrack and dmwappushservice restored to Automatic and started.'
  ```
- **Why it matters:** `AllowTelemetry=0` (already in the catalog) stops what DiagTrack is
  *allowed to send*; it does not stop the "Connected User Experiences and Telemetry"
  service itself from running, holding handles, and waking on its schedule. This is the
  single most iconic line item in Win11Debloat, ShutUp10 and Chris Titus WinUtil — its
  absence here is the most obvious gap in the whole module. [High]
- **Home-edition caveat:** none. Service Control Manager (`sc`/`Set-Service`) behaves
  identically on every SKU.
- **Fidelity note:** DiagTrack's factory startup type is technically "Automatic (Delayed
  Start)"; `Set-Service -StartupType` only supports plain `Automatic`/`Manual`/`Disabled`
  (a PowerShell cmdlet limitation), so the undo restores plain Automatic rather than the
  exact delayed-start flavor. Functionally equivalent (starts a little earlier), cosmetically
  imperfect. If exact fidelity is wanted, undo could shell out to
  `sc.exe config DiagTrack start=delayed-auto` instead.

### 2. `debloat_disable_telemetry_tasks` — Disable CEIP / Compatibility Appraiser scheduled tasks
- **risk:** MODERATE
- **label_sk:** "Vypnutie planovanych uloh telemetrie a CEIP"
- **label_en:** "Disable telemetry and CEIP scheduled tasks"
- **command:**
  ```powershell
  $tasks = @('\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser','\Microsoft\Windows\Application Experience\ProgramDataUpdater','\Microsoft\Windows\Autochk\Proxy','\Microsoft\Windows\Customer Experience Improvement Program\Consolidator','\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask','\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip','\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector','\Microsoft\Windows\Feedback\Siuf\DmClient','\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload','\Microsoft\Windows\Windows Error Reporting\QueueReporting'); $done = 0; foreach ($t in $tasks) { $tp = (Split-Path $t -Parent) + '\'; $tn = Split-Path $t -Leaf; $task = Get-ScheduledTask -TaskName $tn -TaskPath $tp -EA SilentlyContinue; if ($task) { Disable-ScheduledTask -InputObject $task -EA SilentlyContinue | Out-Null; $done++ } }; Write-Output ("Disabled scheduled tasks: " + $done + " of " + $tasks.Count)
  ```
- **undo_command:**
  ```powershell
  $tasks = @('\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser','\Microsoft\Windows\Application Experience\ProgramDataUpdater','\Microsoft\Windows\Autochk\Proxy','\Microsoft\Windows\Customer Experience Improvement Program\Consolidator','\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask','\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip','\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector','\Microsoft\Windows\Feedback\Siuf\DmClient','\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload','\Microsoft\Windows\Windows Error Reporting\QueueReporting'); foreach ($t in $tasks) { $tp = (Split-Path $t -Parent) + '\'; $tn = Split-Path $t -Leaf; $task = Get-ScheduledTask -TaskName $tn -TaskPath $tp -EA SilentlyContinue; if ($task) { Enable-ScheduledTask -InputObject $task -EA SilentlyContinue | Out-Null } }; Write-Output 'Telemetry scheduled tasks re-enabled.'
  ```
- **Why it matters:** Belt-and-suspenders companion to #1 and to the existing
  `AllowTelemetry` action — the policy blocks the upload, this stops the task from even
  running (CPU/disk wake-ups) in the first place. Every task name/path here is [High]
  confidence (documented since Windows 7/8 privacy guides); none were re-verified live today
  because they haven't changed in a decade.
- **Home-edition caveat:** none — Task Scheduler is present on every SKU.
- **Note:** guarded with `Test-Path`-style `-EA SilentlyContinue` per task, so it's a
  harmless no-op for any task name that doesn't exist on a given build (matches this
  catalog's existing tolerant style, e.g. `debloat_remove_promo_apps`).

### 3. `debloat_disable_fast_startup` — Disable Fast Startup (Hiberboot)
- **risk:** MODERATE
- **label_sk:** "Vypnutie rychleho spustenia (Fast Startup)"
- **label_en:** "Disable Fast Startup"
- **command:**
  ```powershell
  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value 0 -Type DWord; Write-Output 'Fast Startup disabled.'
  ```
- **undo_command:**
  ```powershell
  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value 1 -Type DWord; Write-Output 'Fast Startup re-enabled.'
  ```
- **Why it matters:** [High] This is the odd one out — it's not really "privacy debloat,"
  it's a power-management default that every generic debloat script includes anyway (Chris
  Titus WinUtil ships it). It earns a spot specifically *because* this tool is a USB-based
  repair/diagnostic utility: Fast Startup hybrid-hibernates the boot volume on "shutdown"
  instead of fully releasing it, which is a well-documented cause of stale/locked volume
  state, dirty-bit inconsistencies, and confusing behavior on dual-boot or external-drive
  scenarios — precisely the class of symptom this tool's other modules probably chase. No
  data-loss risk; fully reversible; slightly slower cold boot is the only trade-off.
- **Home-edition caveat:** none — plain power-management registry value, identical across
  every SKU.

### 4. `debloat_disable_explorer_ads` — Disable File Explorer "Recommended" section + sync-provider ad notifications
- **risk:** MODERATE
- **label_sk:** "Vypnutie odporucani a reklam v Prieskumnikovi"
- **label_en:** "Disable File Explorer recommendations and ads"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer' -Name HideRecommendedSection -Value 1 -Type DWord; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name ShowSyncProviderNotifications -Value 0 -Type DWord; Write-Output 'File Explorer Recommended section and sync-provider ad notifications disabled (restart Explorer to apply).'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer' -Name HideRecommendedSection -EA SilentlyContinue; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name ShowSyncProviderNotifications -Value 1 -Type DWord -EA SilentlyContinue
  ```
- **Why it matters:** `HideRecommendedSection` **[Verified today]** — confirmed live, added
  in Windows 11 24H2 build 26100.7309+, applies at
  `HKLM:\SOFTWARE\Policies\Microsoft\Windows\Explorer`. `ShowSyncProviderNotifications` is
  [High] (years-old, OneDrive ad-banner-in-Explorer suppressor, present in O&O ShutUp10 and
  most debloat scripts). This is the single most user-recognized "my file manager has ads"
  complaint and the catalog currently has zero coverage of File-Explorer-native surfaces
  (only Start-menu ContentDeliveryManager keys).
- **Home-edition caveat:** none functionally, but `HideRecommendedSection` only has any
  effect on Windows 11 24H2 (build ≥ 26100.7309); on older builds/Windows 10 it's a silent
  no-op, which is fine given the existing codebase's tolerant `-EA SilentlyContinue` style.

### 5. `debloat_block_devhome_outlook_reinstall` — Block Outlook (new) / Dev Home auto-reinstall
- **risk:** DESTRUCTIVE
- **label_sk:** "Zablokovanie automatickej reinstalacie Outlook (novy)/Dev Home"
- **label_en:** "Block auto-reinstall of Outlook (new) / Dev Home"
- **command:**
  ```powershell
  $keys = @('HKLM:\SOFTWARE\Microsoft\WindowsUpdate\Orchestrator\UScheduler_Oobe\OutlookUpdate','HKLM:\SOFTWARE\Microsoft\WindowsUpdate\Orchestrator\UScheduler_Oobe\DevHomeUpdate','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Orchestrator\UScheduler\OutlookUpdate','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Orchestrator\UScheduler\DevHomeUpdate'); $done = 0; foreach ($k in $keys) { if (Test-Path $k) { New-ItemProperty -Path $k -Name workCompleted -Value 1 -PropertyType DWord -Force -EA SilentlyContinue | Out-Null; $done++ } }; Write-Output ("Marked " + $done + " Windows Update orchestrator entries as completed (Outlook/Dev Home auto-install blocked).")
  ```
- **undo_command:**
  ```powershell
  $keys = @('HKLM:\SOFTWARE\Microsoft\WindowsUpdate\Orchestrator\UScheduler_Oobe\OutlookUpdate','HKLM:\SOFTWARE\Microsoft\WindowsUpdate\Orchestrator\UScheduler_Oobe\DevHomeUpdate','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Orchestrator\UScheduler\OutlookUpdate','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Orchestrator\UScheduler\DevHomeUpdate'); foreach ($k in $keys) { Remove-ItemProperty -Path $k -Name workCompleted -EA SilentlyContinue }; Write-Output 'Orchestrator workCompleted markers removed; Windows Update may reinstall Outlook (new)/Dev Home on its normal schedule.'
  ```
- **Why it matters: [Verified today]** — Since 2024-2025, Microsoft's Windows Update
  orchestrator silently *reinstalls* the "Outlook (new)" and "Dev Home" AppX packages even
  after a user (or this catalog's own `debloat_remove_promo_apps`/`debloat_remove_provisioned`
  actions) removes them. This is a direct extension of an existing catalog feature: without
  this action, the OutlookForWindows removal already in the AppX list is incomplete —
  Windows Update just puts it back. Confirmed paths and the `workCompleted=1` marker
  technique via live search today (ElevenForum / tiny11builder GitHub issue #199 threads).
- **Home-edition caveat:** none — this is a Windows Update client-side mechanism, not a
  Group-Policy-gated feature, so it behaves identically on Home, Pro and Enterprise.
- **Caveats / why DESTRUCTIVE, not MODERATE:** this pokes at internal Windows Update
  orchestrator plumbing rather than a documented public policy, and which of the two path
  variants (`Orchestrator\UScheduler_Oobe\...` vs
  `Windows\CurrentVersion\WindowsUpdate\Orchestrator\UScheduler\...`) exists depends on
  build/servicing state — the command is defensive (`Test-Path` guard, `-EA
  SilentlyContinue` throughout) so absence is a harmless no-op, but this is the
  least "officially documented" action in this whole list and deserves a real smoke test on
  a current 24H2 image before it ships enabled by default. Undo is clean (just removes the
  marker value) but does not *guarantee* Windows Update will actually re-offer the apps
  again on the exact original schedule.

---

## Remaining accepted proposals

### 6. `debloat_disable_activity_history` — Disable Activity History / Timeline
- **risk:** MODERATE
- **label_sk:** "Vypnutie histórie aktivit (Timeline)"
- **label_en:** "Disable Activity History"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name EnableActivityFeed -Value 0 -Type DWord; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name PublishUserActivities -Value 0 -Type DWord; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name UploadUserActivities -Value 0 -Type DWord; Write-Output 'Activity History (Timeline) disabled by policy.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name EnableActivityFeed -EA SilentlyContinue; Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name PublishUserActivities -EA SilentlyContinue; Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' -Name UploadUserActivities -EA SilentlyContinue
  ```
- **Why it matters:** [High] Standard GPO-backed triplet (System.admx: "Enable Activity
  Feed" / "Allow publishing of User Activities" / "Allow upload of User Activities").
  Stops local+cloud activity tracking used by Timeline/Cross-device handoff.
- **Home-edition caveat:** none — registry-policy read, not GUI-gated.

### 7. `debloat_disable_location` — Disable location services (global)
- **risk:** MODERATE
- **label_sk:** "Vypnutie sluzieb polohy"
- **label_en:** "Disable location services"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' -Name DisableLocation -Value 1 -Type DWord; Write-Output 'Location services disabled by policy.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' -Name DisableLocation -EA SilentlyContinue
  ```
- **Why it matters:** [High] The "Turn off location" GPO (Sensors.admx). One clean value,
  machine-wide, kills GPS/Wi-Fi geolocation for every app rather than needing per-app
  consent-store entries.
- **Home-edition caveat:** none.
- **Rejected sibling — granular per-app permission defaults:** see Rejections below.

### 8. `debloat_disable_cortana_policy` — Disable legacy Cortana policy
- **risk:** SAFE
- **label_sk:** "Vypnutie zvyskov Cortany"
- **label_en:** "Disable Cortana leftovers"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Name AllowCortana -Value 0 -Type DWord; Write-Output 'Cortana disabled by policy.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Name AllowCortana -EA SilentlyContinue
  ```
- **Why it matters:** [High] but **low priority** — Microsoft retired the standalone
  Cortana consumer app in 2023; on current 23H2/24H2 images this policy has little left to
  do. Cheap to include for older/upgraded 20H2-22H2 images still in the field (this tool's
  actual audience, given its "fix an old USB-installed Windows" premise, plausibly includes
  such machines).
- **Home-edition caveat:** none.

### 9. `debloat_disable_gamedvr` — Disable Game Bar / Game DVR background capture
- **risk:** MODERATE
- **label_sk:** "Vypnutie Game Bar a Game DVR"
- **label_en:** "Disable Game Bar and Game DVR"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Name AllowGameDVR -Value 0 -Type DWord; Set-ItemProperty -Path 'HKCU:\System\GameConfigStore' -Name GameDVR_Enabled -Value 0 -Type DWord; Write-Output 'Game Bar / Game DVR background recording disabled.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Name AllowGameDVR -EA SilentlyContinue; Set-ItemProperty -Path 'HKCU:\System\GameConfigStore' -Name GameDVR_Enabled -Value 1 -Type DWord -EA SilentlyContinue
  ```
- **Why it matters:** [High] Standard tweak in every debloat toolbox; stops the
  always-on background capture buffer, a small but real CPU/RAM/disk saving that matters
  more on the low-end/salvage hardware this tool's audience runs.
- **Home-edition caveat:** none.

### 10. `debloat_disable_stickykeys_popup` — Disable Sticky Keys / Toggle Keys shortcut popups
- **risk:** SAFE
- **label_sk:** "Vypnutie vyskakovacich okien zosilnenych klaves"
- **label_en:** "Disable Sticky/Toggle Keys popup prompts"
- **command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\StickyKeys' -Name Flags -Value '506'; Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\Keyboard Response' -Name Flags -Value '122'; Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\ToggleKeys' -Name Flags -Value '58'; Write-Output 'Sticky/Toggle Keys shortcut popups disabled.'
  ```
- **undo_command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\StickyKeys' -Name Flags -Value '510'; Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\Keyboard Response' -Name Flags -Value '126'; Set-ItemProperty -Path 'HKCU:\Control Panel\Accessibility\ToggleKeys' -Name Flags -Value '62'; Write-Output 'Sticky/Toggle Keys defaults restored.'
  ```
- **Why it matters: [Verified today]** (StickyKeys `506`/ToggleKeys `58` pair confirmed
  live; Keyboard Response value is the third leg of the same well-known triplet used by
  Win11Debloat/WinUtil — [High], not separately re-verified). Purely a UX-annoyance fix
  (the "hold Shift 5x" dialog technicians trigger constantly while typing on flaky/laptop
  keyboards during repairs) with **zero functional/privacy downside**, which is why it's
  rated SAFE rather than MODERATE like the rest of this batch — it doesn't disable a
  feature or telemetry channel, just a confirmation dialog. These are `REG_SZ` string
  values (note the quoted numbers), not DWORDs — easy copy-paste error to avoid.
- **Home-edition caveat:** none — plain per-user Control Panel values.

### 11. `debloat_disable_start_recommendations` — Disable Start menu recommendations / Bing content
- **risk:** MODERATE
- **label_sk:** "Vypnutie odporucani a Bing obsahu v ponuke Start"
- **label_en:** "Disable Start menu recommendations and Bing content"
- **command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name Start_IrisRecommendations -Value 0 -Type DWord; New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Search' -Force | Out-Null; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Search' -Name BingSearchEnabled -Value 0 -Type DWord; Write-Output 'Start menu recommendations and Bing web integration disabled (sign out to fully apply).'
  ```
- **undo_command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name Start_IrisRecommendations -Value 1 -Type DWord -EA SilentlyContinue; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Search' -Name BingSearchEnabled -Value 1 -Type DWord -EA SilentlyContinue
  ```
- **Why it matters:** `Start_IrisRecommendations` **[Verified today]** controls "Show
  recommendations for tips, shortcuts, new apps, and more" in Start menu settings.
  `BingSearchEnabled` is [High] (older but still-honored per-user override). **Distinct
  from the existing `debloat_disable_web_search` action** — that one sets the
  `DisableSearchBoxSuggestions` policy, which stops the search box's live web-suggestion
  dropdown; this targets the Start menu's own "Recommended" tile grid and the legacy Bing
  toggle. Complementary, not a duplicate — worth noting clearly in the YAML description so
  a future maintainer doesn't merge/dedupe them incorrectly.
- **Home-edition caveat:** none.

### 12. `debloat_disable_lockscreen_spotlight` — Disable lock screen Spotlight ads/tips
- **risk:** MODERATE
- **label_sk:** "Vypnutie reklam Windows Spotlight na uvodnej obrazovke"
- **label_en:** "Disable lock screen Spotlight ads"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name DisableWindowsSpotlightFeatures -Value 1 -Type DWord; Write-Output 'Windows Spotlight (lock screen ads/tips) disabled.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name DisableWindowsSpotlightFeatures -EA SilentlyContinue
  ```
- **Why it matters:** [High] CloudContent.admx policy; kills the rotating lock-screen
  "fun facts, tips, and more" and its promotional variants in one shot (broader than just
  fiddling with `ContentDeliveryManager\RotatingLockScreen*` keys, which only cover the
  image rotation, not the tip overlay).
- **Home-edition caveat:** none (this ADMX is nominally "Enterprise/Education" in the GPO
  editor's scope label, but the registry value itself is honored on Home/Pro too since
  Windows just reads the key — same principle as every other Policies-hive entry here).

### 13. `debloat_disable_tailored_experiences` — Disable tailored experiences with diagnostic data
- **risk:** MODERATE
- **label_sk:** "Vypnutie prisposobenych skusenosti na zaklade diagnostickych dat"
- **label_en:** "Disable tailored experiences"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name DisableTailoredExperiencesWithDiagnosticData -Value 1 -Type DWord; Write-Output 'Tailored experiences based on diagnostic data disabled.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent' -Name DisableTailoredExperiencesWithDiagnosticData -EA SilentlyContinue
  ```
- **Why it matters:** [High] Stops Microsoft from using the diagnostic-data stream
  (whatever `AllowTelemetry` level allows) to drive personalized tips/suggestions/ads —
  closes the loop on what the existing telemetry action starts.
- **Home-edition caveat:** none.

### 14. `debloat_disable_feedback` — Set Feedback frequency to Never
- **risk:** SAFE
- **label_sk:** "Vypnutie ziadosti o spatnu vazbu"
- **label_en:** "Disable feedback notifications"
- **command:**
  ```powershell
  New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Force | Out-Null; Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name DoNotShowFeedbackNotifications -Value 1 -Type DWord; Write-Output 'Feedback notifications disabled.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name DoNotShowFeedbackNotifications -EA SilentlyContinue
  ```
- **Why it matters:** [High] Same `DataCollection` key the existing `AllowTelemetry`
  action already touches — trivial, zero-risk addition to a hive this codebase already
  writes to. Rated SAFE (not MODERATE like its sibling) because it only suppresses a
  notification prompt, doesn't change any data-collection behavior itself.
- **Home-edition caveat:** none.

### 15. `debloat_hide_taskbar_chat` — Hide taskbar "Chat"/Teams consumer icon
- **risk:** SAFE
- **label_sk:** "Skrytie ikony Chat/Teams na hlavnom paneli"
- **label_en:** "Hide taskbar Chat/Teams icon"
- **command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name TaskbarMn -Value 0 -Type DWord; Write-Output 'Taskbar Chat/Teams icon hidden.'
  ```
- **undo_command:**
  ```powershell
  Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' -Name TaskbarMn -Value 1 -Type DWord -EA SilentlyContinue
  ```
- **Why it matters:** [High] `TaskbarMn` is the documented Settings-app-backed toggle for
  Personalization > Taskbar > "Chat". Distinct from the existing AppX removal of
  `MicrosoftTeams`/`MSTeams` (which removes the *package*); this hides the taskbar
  *integration point* that can persist as an empty/broken icon on machines where the AppX
  removal partially failed or where a leftover pin survives.
- **Home-edition caveat:** none. **Declining relevance note:** Microsoft dropped the free
  consumer Teams/Chat integration from fresh Windows 11 24H2 installs, so this is
  increasingly only useful for machines upgraded from 22H2/23H2 rather than clean 24H2
  images — still worth keeping given this tool's likely mixed-vintage target fleet.

### 16. `debloat_disable_recall` — Disable Windows Recall (Copilot+ hardware only)
- **risk:** REQUIRES_REBOOT
- **label_sk:** "Vypnutie funkcie Recall"
- **label_en:** "Disable Windows Recall"
- **command:**
  ```powershell
  Disable-WindowsOptionalFeature -Online -FeatureName Recall -NoRestart -EA SilentlyContinue | Out-Null; Write-Output 'Recall optional feature disabled (restart required; no-op if not present on this device).'
  ```
- **undo_command:**
  ```powershell
  Enable-WindowsOptionalFeature -Online -FeatureName Recall -NoRestart -EA SilentlyContinue | Out-Null; Write-Output 'Recall optional feature re-enabled (restart required).'
  ```
- **Why it matters: [Verified today]** — `Disable-WindowsOptionalFeature -Online
  -FeatureName Recall` is Microsoft's own documented mechanism. Deliberately **not** using
  the `-Remove` flag some guides suggest: `-Remove` deletes the feature payload outright and
  can't be cleanly undone without reaching back to Windows Update/an ISO, which conflicts
  with this project's "static undo_command restores default" rule. The plain
  disable/re-enable pair keeps this reversible.
- **Home-edition caveat:** the real gate here is **hardware, not SKU** — Recall only ships
  meaningfully on Copilot+ certified devices (Snapdragon X / Lunar-Lake-class Intel / recent
  Ryzen AI). On the vast majority of older/salvage machines this tool will actually see, the
  feature is simply absent, and the command is a harmless no-op (guarded by
  `-EA SilentlyContinue`). Include it for completeness/future-proofing, but expect it to
  fire on a small minority of target machines.

### 17. `debloat_disable_welcome_experience` — Disable post-update "Welcome/what's new" full-screen nag *(own addition)*
- **risk:** SAFE
- **label_sk:** "Vypnutie uvitacej obrazovky po aktualizaciach"
- **label_en:** "Disable post-update welcome screen"
- **command:**
  ```powershell
  New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement' -Force | Out-Null; Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement' -Name ScoobeSystemSettingEnabled -Value 0 -Type DWord; Write-Output 'Post-update welcome/tips screen disabled.'
  ```
- **undo_command:**
  ```powershell
  Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement' -Name ScoobeSystemSettingEnabled -EA SilentlyContinue
  ```
- **Why it matters:** [Medium confidence — verify before shipping] Not in the brief's
  candidate list; adding it because it's the full-screen "Here's what's new in Windows"
  interstitial that appears after major updates/sign-ins, cited across multiple tweak
  guides as controlled by `ScoobeSystemSettingEnabled`, but I did not independently verify
  this key today (lower confidence than everything else in this report). Cheap, single
  value, trivially reversible — reasonable to include but test on a real post-update
  machine first to confirm the nag actually stops.
- **Home-edition caveat:** none expected — plain per-user value, no policy dependency.

---

## Rejected proposals

### A. Granular per-app permission defaults (camera/mic/location per UWP app)
**Reject.** Windows' per-app consent model stores one entry per app per capability under
`HKCU:\Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\...`
with no single enumerable "restore factory defaults" snapshot — there's nothing to write a
static `undo_command` against, and a blanket `LetAppsAccessCamera=2`/`LetAppsAccessMicrophone=2`
policy (the one static alternative) actively breaks legitimate Store apps (Camera, Skype,
Windows Hello companion UI) rather than just decluttering, which is a worse trade than this
module's other entries. The global `DisableLocation` policy (#7 above) already covers the
one location-specific case that's both simple and safe.

### B. Background apps global toggle
**Reject.** The Windows 10-era key
(`HKCU:\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications\GlobalUserDisabled`)
predates Windows 11's redesign of this surface: Microsoft removed the master switch from
Settings in Windows 11 22H2+ and moved background-app permission fully to a per-app model.
There is no confirmed, currently-functional global registry switch for this on 23H2/24H2 —
shipping a stale Windows-10 key would give a false sense of having done something with no
measurable effect on modern target machines. Not worth the catalog entry.

### C. WER (Windows Error Reporting) / MRT service disabling *(own addition — considered, not in brief)*
**Reject, and flagged deliberately.** Generic debloat scripts often disable `WerSvc` and the
"Windows Malicious Software Removal Tool" scheduled task alongside DiagTrack, since they're
adjacent "background Microsoft service" targets. For *this specific tool* — a
diagnostic/repair utility — that's counter-productive: WER crash-dump data is exactly the
kind of signal a repair technician wants available when a machine is unstable, and MRT is a
baseline malware sweep that costs nothing at rest. Neither is telemetry-for-advertising in
the way everything else in this module is; disabling them would trade the tool's own
diagnostic usefulness for a debloat checkbox. Recommend leaving both alone, and noting this
reasoning in the YAML/docs so a future contributor doesn't add them reflexively because
"every other script has it."

---

## Sources consulted (live web search, 2026-09-01)

- [Prevent installation of Outlook and Dev Home — Windows 11 Forum](https://www.elevenforum.com/t/prevent-installation-of-outlook-and-dev-home.33094/)
- [RegKey's to prevent Outlook and Dev Home reappear — tiny11builder issue #199](https://github.com/ntdevlabs/tiny11builder/issues/199)
- [Enable or Disable Show Recommended Section in File Explorer Home — Windows 11 Forum](https://www.elevenforum.com/t/enable-or-disable-show-recommended-section-in-file-explorer-home-in-windows-11.25224/)
- [Reclaim Your Start Menu: Banishing Windows 11's Recommended Section](https://www.aloneguid.uk/posts/2025/12/win11-remove-recommended/)
- [Disable Recall feature in Windows 11 — kapilarya.com](https://www.kapilarya.com/disable-recall-feature-in-windows-11)
- [Permanently Disable Microsoft Recall with DISM — CSI Specialist](https://csispecialist.com/index.php/2026/02/21/permanently-disable-microsoft-recall-with-dism/)
- [How To Disable Sticky Keys On Windows 10 And 11 (Registry Method) — memstechtips](https://memstechtips.com/disable-sticky-keys-windows-11-regedit/)
- [Enable or Disable Recommended Tips, Shortcuts, New Apps on Start Menu — Windows 11 Forum](https://www.elevenforum.com/t/enable-or-disable-recommended-tips-shortcuts-new-apps-and-more-on-start-menu-in-windows-11.14346/)

Everything else is [High]-confidence long-standing Windows behavior not re-verified live
(see per-item notes) or explicitly flagged [Medium] for pre-ship testing.
