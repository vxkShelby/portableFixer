# M13 Debloat Catalog Audit — PortableFix

Scope: `Modules/m13_debloat/actions.yaml` (10 actions) + `portablefix/executor.py`.
Target: Windows 11 24H2/25H2, both Home and Pro. Read-only audit, no files modified.

## Findings summary

| # | Severity | Action id | Issue |
|---|----------|-----------|-------|
| F1 | HIGH | `debloat_disable_telemetry` | `AllowTelemetry=0` is ignored/clamped on Home & Pro |
| F2 | HIGH | `debloat_disable_web_search` | Wrong registry key — controls File Explorer, not Start/Bing web search |
| F3 | HIGH | `debloat_remove_promo_apps`, `debloat_remove_provisioned` | List includes `Microsoft.XboxIdentityProvider` — breaks Minecraft/Game Pass sign-in |
| F4 | MEDIUM | `debloat_remove_onedrive` | Doesn't check `%LocalAppData%\Microsoft\OneDrive` (the actually-running, self-updated copy) |
| F5 | MEDIUM | `debloat_disable_copilot` | Registry-only; 24H2+ ships Copilot as an AppX app, policy enforcement reported inconsistent |
| F6 | MEDIUM | executor.py (cross-cutting, exposed by `debloat_remove_onedrive`, `debloat_remove_provisioned`) | No timeout/cancel on blocking `Start-Process -Wait` / DISM calls |
| F7 | LOW | `debloat_disable_widgets` | Policy-only; Widgets AppX package/service keeps running, needs restart |
| F8 | LOW | `debloat_remove_promo_apps`, `debloat_remove_provisioned` | 3 stale package IDs no longer present on clean 24H2/25H2 images |
| F9 | LOW | `debloat_remove_promo_apps`, `debloat_remove_provisioned` | Missing a few current 24H2/25H2 bloat IDs |
| F10 | PASS | both AppX actions | Wildcard list is narrow/safe, no over-match risk |
| F11 | PASS | `debloat_remove_provisioned` | `Where-Object DisplayName -like $a` logic is correct |
| F12 | PASS | 6 registry actions | `undo_command` semantics are correct for each key's real default behavior |
| F13 | PASS | `debloat_remove_promo_apps`/`_provisioned` | `windowscommunicationsapps` / `Microsoft.People` removal — no hazard found |

13 findings: **3 HIGH, 3 MEDIUM, 3 LOW, 4 PASS**.

---

## 1. AppX package list audit

List (identical, reused by both `debloat_remove_promo_apps` [MODERATE] and `debloat_remove_provisioned` [DESTRUCTIVE]):
`Microsoft.549981C3F5F10, Microsoft.BingNews, Microsoft.BingSearch, Microsoft.BingWeather, Microsoft.GetHelp, Microsoft.Getstarted, Microsoft.Microsoft3DViewer, Microsoft.MicrosoftOfficeHub, Microsoft.MicrosoftSolitaireCollection, Microsoft.MixedReality.Portal, Microsoft.People, Microsoft.PowerAutomateDesktop, Microsoft.SkypeApp, Microsoft.Todos, Microsoft.WindowsAlarms, Microsoft.WindowsFeedbackHub, Microsoft.WindowsMaps, Microsoft.YourPhone, Microsoft.ZuneMusic, Microsoft.ZuneVideo, Microsoft.XboxApp, Microsoft.GamingApp, Microsoft.XboxGameOverlay, Microsoft.XboxGamingOverlay, Microsoft.XboxIdentityProvider, Microsoft.XboxSpeechToTextOverlay, Microsoft.Xbox.TCUI, MicrosoftTeams, MSTeams, Microsoft.OutlookForWindows, Clipchamp.Clipchamp, Microsoft.windowscommunicationsapps, king.com*, *CandyCrush*, *Spotify*, *TikTok*, *Facebook*, *Instagram*, *Twitter*, *Netflix*, *Disney*`

### F3 [HIGH] — `Microsoft.XboxIdentityProvider` breaks unrelated sign-ins
**What's wrong:** `Microsoft.XboxIdentityProvider` supplies the Xbox Live authentication/token stack used well beyond the Xbox app: Minecraft (Bedrock, and the Java launcher when linked to a Microsoft account), Game Pass entitlement/activation checks, and other Xbox-Live-integrated PC titles (Forza, Halo MCC, Sea of Thieves, Age of Empires, etc.) authenticate through it. Removing it silently breaks sign-in for those games, not just "Xbox app cleanup" as the action's Slovak/English description implies. Worse, the description promises "reinstallable from Microsoft Store" — but this package is not a normal Store-listed app, so an affected user has no obvious recovery path (in practice it gets silently re-provisioned when Store/Xbox app self-heals, but that's not documented anywhere in the tool).
Same list is reused verbatim in `debloat_remove_provisioned` (DESTRUCTIVE), so the provisioned image loses it for all future users/profiles too.
**Suggested fix:** Remove `Microsoft.XboxIdentityProvider` from both lists. If Xbox-related debloat is still wanted, split it into its own opt-in action (e.g. `debloat_remove_xbox_identity`) risk-tagged DESTRUCTIVE with an explicit description: "Warning: may break sign-in for Minecraft, Game Pass, and other Xbox Live-linked games." Also downgrade-flag `Microsoft.Xbox.TCUI` in the same description (in-game overlay/achievement popups for some Xbox-Live PC titles) — lower severity, but same family of risk, so it belongs in the same warning rather than the silent bulk list.

### F8 [LOW] — stale package IDs
**What's wrong:** Three entries no longer exist on clean Windows 11 24H2/25H2 images (harmless no-ops today via `-EA SilentlyContinue`, but dead weight):
- `Microsoft.Microsoft3DViewer` — 3D Viewer was discontinued/pulled from the Store in 2024.
- `Microsoft.MixedReality.Portal` — Windows Mixed Reality platform was removed from Windows entirely starting with 24H2 (deprecated since late 2023).
- `Microsoft.SkypeApp` — consumer Skype was retired (shut down) in 2025; not part of fresh images anymore.
**Suggested fix:** Prune these three from the `$apps` array in both actions. Purely cosmetic — no behavior change, just removes dead entries that will never match anything on current builds.

### F9 [LOW] — missing current 24H2/25H2 bloat targets
**What's wrong:** The list already covers the highest-value current items (`Microsoft.OutlookForWindows`, `Microsoft.BingSearch`, `MSTeams` alongside legacy `MicrosoftTeams`), but is missing a few common ones:
- `Microsoft.Copilot` — Copilot now ships as a real AppX app on 24H2+ (see F5; `debloat_disable_copilot` only touches a registry policy, never this package).
- `MicrosoftWindows.Client.WebExperience` — the Widgets board app itself (see F7; `debloat_disable_widgets` only sets a policy, doesn't remove the package).
- `MicrosoftCorporationII.MicrosoftFamily` — Family Safety app, commonly preinstalled, commonly included in debloat lists.
**Suggested fix:** Append these three IDs to the `$apps` array (both actions, if provisioned-removal is desired for them too). None are urgent; not adding them is not a bug, just an opportunity.

### F13 [PASS] — `windowscommunicationsapps` / `Microsoft.People` hazard check
**What's wrong:** Nothing. `Microsoft.windowscommunicationsapps` (Mail & Calendar) is being superseded by Microsoft's own new Outlook (already separately listed as `Microsoft.OutlookForWindows`), so removing the legacy app is safe and, if anything, redundant with Microsoft's own direction. `Microsoft.People` has no known dependents in Windows 11; Microsoft itself has been dropping it from newer images. Both are reasonable MODERATE-risk removal candidates as currently classified. No fix needed.

### F10 [PASS] — wildcard over-match check
**What's wrong:** Nothing. `king.com*`, `*CandyCrush*`, `*Spotify*`, `*TikTok*`, `*Facebook*`, `*Instagram*`, `*Twitter*`, `*Netflix*`, `*Disney*` are all narrow, brand-specific substrings. None are generic enough (no bare `*Game*`, `*Media*`, `*App*`, etc.) to risk catching a Microsoft/system component. `*CandyCrush*` is redundant with `king.com*` (King's actual package id is `king.com.CandyCrushSaga`, already matched) but redundant ≠ harmful. No fix needed.

---

## 2. Provisioned-removal variant — `Where-Object DisplayName -like $a` correctness

### F11 [PASS]
**What's wrong:** Nothing — the pattern is correct. `Get-AppxProvisionedPackage -Online | Where-Object { $_.DisplayName -like $a }`:
- For plain entries with no wildcard characters (e.g. `"Microsoft.BingNews"`), `-like` performs a case-insensitive exact match, and `DisplayName` on a provisioned-package object is the package identity name (not the localized Start-menu title), so this correctly matches the same identity `Get-AppxPackage -Name` targets in the per-user variant.
- For true wildcard entries (`king.com*`, `*CandyCrush*`, ...), `-like` interprets `*` exactly as intended.
This is the standard, Microsoft-doc-aligned idiom for provisioned-package cleanup. No fix needed.

---

## 3. Registry tweaks — do they still work on current Win11, and on Home?

Actual Windows-edition nuance: the common belief that "policy" registry keys are ignored on Home is a **misconception** for every key in this catalog except one (F1). `gpedit.msc` (the Local Group Policy Editor GUI) is what's absent on Home — the underlying `HKLM/HKCU\...\Policies\...` registry values are still read directly by the relevant OS component (Explorer, the Widgets/Dsh host, Copilot, DataCollection) regardless of SKU. That's precisely why setting these values via PowerShell/regedit is the standard workaround people use *because* Home lacks gpedit. So, key by key:

### F1 [HIGH] — `debloat_disable_telemetry`: `AllowTelemetry=0`
**What's wrong:** This is the one genuine edition-gated exception. Microsoft's own documentation states the "Security" level (value `0`) for `AllowTelemetry` is available **only on Windows Server, Enterprise, Education, and IoT Enterprise** editions. On Home and Pro — the editions PortableFix is most likely to be run on — the OS clamps the effective minimum to `1` ("Required"/"Basic"); `0` is silently treated as `1`. The action's own success message ("Telemetry policy set to 0 (Security/minimum)") and description ("Sets the telemetry policy to minimum (0)") are therefore inaccurate on Home/Pro — it looks like it worked (exit code 0, value written to the registry) but the OS doesn't honor the value.
**Suggested fix:** Change `-Value 0` to `-Value 1` (the real minimum on Home/Pro) in the `command`, and reword the description to "reduces telemetry to the minimum available on this edition (1 on Home/Pro, 0 on Enterprise/Education)." Optionally detect edition (`(Get-CimInstance Win32_OperatingSystem).OperatingSystemSKU` or `(Get-ItemPropertyValue 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' EditionID)`) and write `0` only on Enterprise/Education/IoT Enterprise, else `1`.

### F2 [HIGH] — `debloat_disable_web_search`: wrong key entirely
**What's wrong:** The action sets `HKCU:\Software\Policies\Microsoft\Windows\Explorer\DisableSearchBoxSuggestions`. That is the policy for **"Turn off display of recent search entries in the File Explorer search box"** — it affects the search box inside File Explorer windows (autocomplete/history), not the Start-menu/taskbar search's Bing web-results integration that the label (`"Disable web results in Start search"`) and description (`"Start search stops sending queries to Bing"`) claim to control. The command runs and returns success, but a user running it will see zero change in Start-menu web search behavior — a functional mismatch between what the action says it does and what it actually does.
**Suggested fix:** Replace with the actual Windows Search web-integration keys:
```powershell
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Force | Out-Null
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Name DisableWebSearch -Value 1 -Type DWord
Set-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search' -Name ConnectedSearchUseWeb -Value 0 -Type DWord
```
and, for the per-user "search highlights" toggle Settings itself uses (no elevation needed, more immediate):
```powershell
Set-ItemProperty -Path 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Search' -Name BingSearchEnabled -Value 0 -Type DWord -EA SilentlyContinue
```
Update `undo_command` to `Remove-ItemProperty` the two `Windows Search` policy values (and set `BingSearchEnabled` back to `1`). The existing `DisableSearchBoxSuggestions` tweak can stay as a *separate, correctly-labeled* action ("disable recent-search suggestions in File Explorer") if that behavior is independently wanted — it's valid, just mislabeled as something else today.

### F5 [MEDIUM] — `debloat_disable_copilot`: `TurnOffWindowsCopilot`
**What's wrong:** `HKCU:\Software\Policies\Microsoft\Windows\WindowsCopilot\TurnOffWindowsCopilot=1` is still a real, currently-documented policy (not deprecated) and is honored on Home and Pro for the same reason as the others (client reads the registry value directly). However, since Windows 11 24H2 Copilot increasingly ships as an actual AppX app (`Microsoft.Copilot`) with deeper OS/taskbar integration rather than purely an Explorer-hosted icon, there have been repeated community reports through 2025 of this policy being inconsistently enforced after some 24H2/25H2 cumulative updates (icon reappearing, or the app being silently re-provisioned by Windows Update even after being removed/blocked). It is not "broken" outright, but registry-only suppression is no longer reliably sufficient by itself on current builds.
**Suggested fix:** Keep the existing registry command, but add a companion line to also remove the AppX package for defense-in-depth:
```powershell
Get-AppxPackage -Name Microsoft.Copilot -AllUsers -EA SilentlyContinue | Remove-AppxPackage -EA SilentlyContinue
```
and note in the description that a future feature update may silently re-enable/reinstall Copilot regardless of this setting (known, currently-unresolved Microsoft behavior — not something PortableFix can fully control).

### F7 [LOW] — `debloat_disable_widgets`: `AllowNewsAndInterests`
**What's wrong:** `HKLM:\SOFTWARE\Policies\Microsoft\Dsh\AllowNewsAndInterests=0` is still valid and still the correct ADMX-backed policy for blocking the Widgets board; the description already honestly discloses it needs a restart. The gap is that this only blocks/hides Widgets by policy — it does not remove the Widgets AppX package (`MicrosoftWindows.Client.WebExperience`) or stop its background service, so the component keeps consuming resources.
**Suggested fix:** No correctness fix needed. Optional improvement: also offer the immediate, no-restart per-user toggle `HKCU:\Software\Microsoft\Windows\CurrentVersion\Feeds\ShellFeedsTaskbarViewMode = 2` (hidden) for instant effect, and/or add `MicrosoftWindows.Client.WebExperience` to the AppX removal list (F9) for users who want the package gone entirely.

### F12 [PASS] — `undo_command` correctness
**What's wrong:** Nothing — each undo uses the semantics appropriate to its key type:
- `debloat_disable_telemetry`, `debloat_disable_web_search`, `debloat_disable_copilot`, `debloat_disable_widgets` all live under `...\Policies\...` and undo via `Remove-ItemProperty`. This is correct: for policy keys, "no value present" *is* the true unmanaged default (the OS/Settings app takes over), so deleting the value is the right undo, not writing some specific "default" number.
- `debloat_disable_suggestions` (ContentDeliveryManager) and `debloat_disable_advertising_id` (AdvertisingInfo) are **not** Policies-namespace keys — they are direct feature-flag/setting values Windows itself writes, whose real factory default is the literal value `1`. Both undo commands correctly restore `1` via `Set-ItemProperty` rather than deleting the value. This is the right approach for these two.
No fix needed. (The one place "undo correctness" doesn't save the action is F2, where the undo is logically correct *for the key it targets* — the problem is the target key itself, not the undo logic.)

**Note on the two AppX actions:** `debloat_remove_promo_apps` and `debloat_remove_provisioned` have no `undo_command` at all. This is a defensible design choice, not a bug — there is no reliable one-line PowerShell "re-add" for a package whose files may already be gone, and both descriptions already document the real recovery path ("reinstallable from Microsoft Store"). Worth only a documentation note, not a code fix.

---

## 4. OneDrive uninstall — `OneDriveSetup.exe /uninstall` from System32/SysWOW64

### F4 [MEDIUM] — `debloat_remove_onedrive`
**What's wrong:** The action checks only `%SystemRoot%\System32\OneDriveSetup.exe` and `%SystemRoot%\SysWOW64\OneDriveSetup.exe` — the original OOBE-installed stub copies (32-bit historically at SysWOW64, with a native 64-bit copy at System32 on machines that got Microsoft's 64-bit OneDrive rollout). In the common case, though, OneDrive self-updates per-user without touching either System32 copy, and the actually-running, current-version uninstaller lives at `%LocalAppData%\Microsoft\OneDrive\OneDriveSetup.exe`. Running the stale System32/SysWOW64 stub's `/uninstall` against a self-updated install can no-op, report success without fully uninstalling, or leave the newer per-user install partially intact — this is a widely-reported real-world gotcha with this exact approach. (A pure Microsoft-Store/MSIX-packaged OneDrive exists only in limited pilot rollouts as of now, not the mainstream consumer path — not the primary concern here.)
**Suggested fix:** Check `%LocalAppData%\Microsoft\OneDrive\OneDriveSetup.exe` first (the per-current-user, actually-running copy), then fall back to System32/SysWOW64:
```powershell
$setup = @("$env:LocalAppData\Microsoft\OneDrive\OneDriveSetup.exe","$env:SystemRoot\SysWOW64\OneDriveSetup.exe","$env:SystemRoot\System32\OneDriveSetup.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
```
If the tool ever needs to clean OneDrive for *all* profiles on the machine (not just the current user), it would need to loop this per user hive under `C:\Users\*\AppData\Local\Microsoft\OneDrive\OneDriveSetup.exe` — out of scope unless multi-profile cleanup becomes a stated goal.

---

## 5. Executor — timeout/cancel exposure (portablefix/executor.py)

### F6 [MEDIUM] — no timeout mechanism, confirmed
**What's wrong:** `ActionRunner.run()` in `portablefix/executor.py` calls `subprocess.Popen(self._plan.argv, stdout=PIPE, stderr=STDOUT, ...)`, then does `for raw_line in process.stdout: ...` followed by `process.wait()` — no `timeout=` argument anywhere, and no watchdog/QTimer kill-after-N-seconds path either (confirmed by reading the full 2 KB file; the only exception handling is a bare `except Exception: process.kill()`, which only fires if `Popen`/iteration itself raises, not on a hang). Every command in every module inherits this, but two M13 actions are the ones most exposed to it actually hanging in practice:
- `debloat_remove_onedrive` uses `Start-Process $setup -ArgumentList '/uninstall' -Wait` — normally silent and fast, but `OneDriveSetup.exe /uninstall` is known to occasionally show a UI prompt (e.g., mid-sync warnings) which would block `-Wait` forever with no way for the user to cancel short of killing the whole PortableFix process.
- `debloat_remove_provisioned` calls `Get-AppxProvisionedPackage -Online` / `Remove-AppxProvisionedPackage -Online`, which are DISM/CBS-backed and documented in the wild to occasionally stall for minutes (rarely indefinitely) when the servicing stack is in a degraded state.
**Suggested fix:** Not fixable at the YAML level — belongs in `executor.py`. Add a timeout to the read loop (e.g., poll `process.poll()` with a `QTimer`, or use a background thread + `process.communicate(timeout=...)` with `process.kill()` on `TimeoutExpired`), surfaced as a cancel button or an auto-kill after a generous ceiling (e.g., 120s) with a clear "action timed out" output line. Flagging here because M13 has two of the more timeout-prone one-liners in the catalog, not because the bug lives in this YAML file.

---

## Existing project boundaries — respected

Confirmed the catalog does not touch Edge, Defender, Store, or Quick Assist, and the OneDrive action is uninstall-only (files remain on disk, per its own description) — consistent with the stated project scope. No findings related to boundary violations.
