# PortableFix — Competitive Feature Gap Analysis

Scope: read-only web + repo research, no files modified. Question: how does
PortableFix's current 108-action catalog compare to well-known Windows
technician tools technicians actually reach for, and which gaps are worth a
solo maintainer's time.

## 0. Baseline — what PortableFix actually has today

Read all 12 `Modules/*/actions.yaml` files in full. Current counts (higher
than the README's "~82+", the catalog has grown since that line was
written):

| Module | Category | Actions | Content |
|---|---|---|---|
| m01_diagnostics | DIAGNOSTICS | 15 | OS/HW/BIOS/CPU/RAM/disk info, Defender status, top-CPU processes, pending reboot, event log errors (7d), BSOD/minidump summary, SMART counters, installed software |
| m02_cleanup | CLEANUP | 21 | temp/system temp, recycle bin, prefetch, WER, CBS logs, thumbnail/font cache, Delivery Optimization, WU cache, component store (+ResetBase), Windows.old, oldest shadow copy, hibernation off, stale profiles, browser cache sweep, upgrade leftovers, GPU driver leftovers, DirectX shader cache, largest-files report |
| m03_disk | REPAIR | 8 | SMART status, volume report, read-only scan, SpotFix, TRIM/defrag, full chkdsk at reboot, dirty-bit check, VSS writers report |
| m04_integrity | REPAIR | 9 | DISM CheckHealth/ScanHealth/RestoreHealth, SFC scan/verify, AppX re-register, WMI verify/backup/salvage (with undo) |
| m05_windows_update | REPAIR | 6 | service check/stop/restart, cache reset (with undo), DLL re-register, trigger detection |
| m06_network | REPAIR | 10 | adapter status, IP config, DNS flush, hosts reset (undo), DHCP renew, Winsock/TCP-IP reset, firewall reset (undo), print spooler reset, adapter power-mgmt disable (undo) |
| m07_autoruns | DIAGNOSTICS | 4 | registry Run/RunOnce, Startup folder, scheduled tasks (read-only), auto-start services — **all read-only, no disable/toggle action** |
| m08_security | SECURITY | 10 | Defender status/update/quickscan, firewall status, UAC status + reset (undo), Defender exclusions list + clear (undo), RDP status, autologon check |
| m09_tuning | REPAIR | 4 | power plan report + High Performance switch (undo), startup apps report, visual effects for performance (undo) |
| m10_drivers | DIAGNOSTICS | 2 | problem devices (with problem code), third-party driver list — **read-only, no update/rollback/reinstall action** |
| m12_online | DIAGNOSTICS | 3 | layered connectivity test, DNS speed comparison, proxy check |
| m13_debloat | CLEANUP | 16 | AppX bloat removal (user + provisioned), telemetry policy, Start suggestions/ads/web-search/widgets/advertising-ID, OneDrive uninstall, Xbox Identity Provider, DiagTrack/CEIP tasks, Fast Startup, Explorer ads, app-reinstall block |

**Total: 108 actions.** Existing prior-art docs in this folder
(`research-repair-additions.md`, `research-security-additions.md`, etc.)
already did category-by-category internal gap analysis against the app's own
schema/constraints. This document takes a different angle: named,
well-known third-party tools technicians already carry on a USB stick, and
what they can do that PortableFix's 108 actions genuinely cannot.

## 1. Competitors researched

- **CCleaner** (Piriform) — registry cleaner (with backup-before-clean),
  browser cleaner, uninstaller, startup manager, disk space analyzer,
  duplicate-file finder, drive wiper; Pro adds Health Check (junk/unused-app/
  tracker sweep in one pass), Software Updater, Driver Updater.
- **BleachBit** — open-source cross-platform cleaner; file/folder shredding
  (overwrite before delete), free-space wiping, per-app cleaners (Chrome,
  Edge, Firefox, VLC, system logs...), preview-before-delete, CLI scripting.
- **Tweaking.com Windows Repair** — 45+ one-click "preset" repair modules
  (registry permissions, IE/WU/firewall resets, service defaults); Pro adds
  drive cleaner, memory cleaner, speed tweaks; free tier is repair-only.
- **Sysinternals Suite** (Microsoft) — ~70 discrete tools; the technician
  staples are **Autoruns** (every autostart surface — Run keys, services,
  tasks, browser helper objects, WMI subscriptions, codecs, printer
  monitors — in one sortable/filterable UI with inline enable/disable),
  **Process Explorer** (live process tree + handle/DLL inspection, "which
  process has this file open"), **TCPView** (live per-process network
  connections), **Autologon**, **PsTools**, **RAMMap**, **Disk2vhd**.
- **O&O ShutUp10++** — single-purpose privacy/telemetry toggle panel;
  per-setting labeled Recommended/Limited/Not-Recommended, creates a system
  restore point before applying, and every individual toggle is
  independently reversible.
- **DISM++** — GUI wrapper around DISM plus its own extras: component-store
  cleanup, enable/disable optional Windows features, bulk uninstall of
  built-in AppX apps, hide/uninstall specific Windows updates, service
  manager, its own backup/restore for changes it makes.
- **Malwarebytes AdwCleaner** — focused adware/PUP/browser-hijacker scanner;
  quarantines (not hard-deletes) findings so removal is reversible; explicit
  browser-settings reset (default search provider, malicious extensions) for
  Chrome/Firefox/Edge/IE.
- **Hiren's BootCD PE** — WinPE-based bootable toolkit (60+ bundled tools):
  MBR/BCD boot repair (BootIce, EasyBCD, BootFix), disk/data recovery
  (TestDisk, Recuva, PhotoRec, DMDE), SMART/disk diagnostics (CrystalDiskInfo,
  HDTune, GSmartControl, Victoria), partition management (AOMEI Partition
  Assistant, MiniTool clones), auto-installed storage/network/display
  drivers for the PE environment itself.
- **WhyNotWin11** — single-purpose, one-shot Windows 11 upgrade-eligibility
  checker: TPM version, Secure Boot state, CPU family/core count, RAM,
  storage, per-item pass/fail.

## 2. Where PortableFix is already ahead or has a distinct angle

Worth stating explicitly before listing gaps, because it's the honest
comparison, not just a wishlist:

- **Undo mechanism (`Backups/<run-id>/undo.ps1`, LIFO)**: none of CCleaner,
  BleachBit, Tweaking.com Windows Repair, or DISM++ ship a single replayable
  undo *script* across a whole batch of actions. CCleaner's registry cleaner
  backs up to one `.reg` file per clean (manual re-import); Tweaking.com
  Windows Repair has no undo at all for its repair presets, only the restore
  point as a safety net; BleachBit's shredding is deliberately irreversible
  by design. PortableFix's per-action `undo_command` chain that unwinds a
  whole session in reverse order is a genuinely distinct mechanism among
  this group — closest analog is O&O ShutUp10++'s per-toggle reversibility,
  but that tool is single-purpose (privacy only), not a full repair/cleanup
  suite.
- **Dry-run / preview mode as a first-class, default-on setting**: BleachBit
  previews file lists before deletion and DISM++ shows sizes before cleanup,
  but neither has a blanket "simulate the entire batch, change nothing" mode
  spanning disk repair, registry, and service actions the way PortableFix's
  `preview_command` + default-on DRY-RUN toggle does. Tweaking.com Windows
  Repair and CCleaner's repair actions have no equivalent — you commit or
  you don't.
- **Per-action risk labeling (SAFE/MODERATE/DESTRUCTIVE/REQUIRES_REBOOT)
  with confirmation gates**: O&O ShutUp10++ has a comparable 3-tier
  labeling (Recommended/Limited/Not-Recommended) but only for privacy
  toggles. CCleaner, BleachBit, and Tweaking.com Windows Repair present
  their options as flat checklists with no risk taxonomy at all — a
  technician can select "Wipe Free Space" and "Registry Cleaner" in the
  same BleachBit/CCleaner pass with identical visual weight, even though
  their blast radius is wildly different.
- **Automatic System Restore Point before the first DESTRUCTIVE/REPAIR/
  SECURITY action per batch**: O&O ShutUp10++ does this too (a real peer),
  but CCleaner and BleachBit do not create restore points at all, and
  Tweaking.com Windows Repair only prompts once, generically, not scoped
  per-batch to what's about to run.
- **Zero-install, single-exe portability with self-signed cert + SHA256SUMS
  + audit log (`Logs/<run-id>/audit.jsonl`) + HTML report per batch**:
  Hiren's BootCD PE and the Sysinternals Suite are also portable/no-install,
  but neither produces a structured per-run audit trail or client-facing
  HTML report — that's closer to what paid MSP RMM tooling does, and it's a
  real differentiator for a technician who has to document what they did on
  a client machine.
- **Bilingual (SK/EN) label/description pairs on every single action**: none
  of the researched tools ship this; it's a narrow but real differentiator
  for the tool's actual (implied Slovak-market) audience.

## 3. Concrete feature gaps

Format: `Gap — competitor precedent — priority — realistic addition`.

---

**Gap: no autostart *editor*, only autostart *reports*.**
M07 (`autoruns_registry_run`, `autoruns_startup_folder`,
`autoruns_scheduled_tasks`, `autoruns_autostart_services`) lists every
autostart surface but has zero disable/remove action anywhere in the
catalog — a technician sees the malicious Run-key entry and can't act on it
without dropping to `regedit`/`taskschd.msc` by hand.
— Competitor: **Sysinternals Autoruns** — inline checkbox
disable/enable per entry across Run keys, services, scheduled tasks, and
browser helper objects, all from one view.
— Priority: **high**.
— Realistic addition: this needs a *parameterized* action (which entry?) —
the YAML schema today is static one-liner-per-action, no per-run user
input. A tractable partial fix without new architecture: add a handful of
**named, common-culprit toggle actions** with fixed identities rather than
a free-form editor — e.g. `autoruns_disable_named_run_key` isn't feasible
generically, but a **safe subset is**: disable a specific well-known
noisy/optional startup entry that's already named elsewhere in the catalog
(nothing new needed) plus a new SAFE **"non-Microsoft Run-key entries only"
filtered report** (`autoruns_registry_run_thirdparty`) that pre-filters out
signed Microsoft paths so the existing full dump is easier to eyeball. True
per-item enable/disable is the "out of realistic scope" item — see §5.

---

**Gap: no driver update, rollback, or reinstall action.**
M10 (`drv_problem_devices`, `drv_third_party_list`) is entirely read-only.
— Competitor: **CCleaner Driver Updater** (Pro) scans and updates outdated
drivers; **Hiren's BootCD PE**'s driver tooling and generic Device Manager
workflows include "roll back driver" as a one-click op via `pnputil`.
— Priority: **medium**.
— Realistic addition: 2 new SAFE/MODERATE actions in M10 — a
`drv_rollback_report` (SAFE: enumerate devices with a rollback driver
available via `Get-PnpDevice` + registry `DriverDateData` comparison, report
only) and `drv_reinstall_problem_devices` (MODERATE: `pnputil` or
`Disable-PnpDevice`/`Enable-PnpDevice` cycle on devices flagged in
`drv_problem_devices` — a real "turn it off and on again" fix for code-43/
code-28 devices). Full driver *downloading* is out of scope (needs internet
+ a driver database PortableFix doesn't have).

---

**Gap: no BitLocker / device encryption or TPM+Secure Boot status check.**
Zero coverage anywhere in the 108 actions.
— Competitor: **WhyNotWin11** (TPM/Secure Boot pass-fail), and BitLocker
status is a standard first check in every enterprise Windows repair
toolkit.
— Priority: **high** (cheap, zero-risk, high diagnostic value — a technician
handing back a machine with an unencrypted disk after a repair, or hitting
a Windows 11 upgrade wall from bad TPM/Secure Boot state, is a real support
scenario).
— Realistic addition: this is **already fully speced** in
`docs/research/research-security-additions.md` (`sec_bitlocker_status`,
`sec_tpm_secureboot_status`) but not yet implemented in
`Modules/m08_security/actions.yaml` — confirmed by reading the current file
(9 actions, neither present). Cheapest gap to close: just land those two
already-written SAFE actions.

---

**Gap: no laptop battery health report.**
M01 has `computer_info` (checks `Win32_Battery` presence/charge only), but
nothing surfaces wear/design-vs-full-charge capacity.
— Competitor: not a dedicated third-party tool so much as a built-in
Windows capability every technician toolkit exposes — `powercfg
/batteryreport` (HTML report with design capacity vs. full charge capacity,
charge cycle history) is table stakes in laptop repair.
— Priority: **high** (one-line SAFE command, zero risk, directly answers
"is this laptop's battery dying").
— Realistic addition: one new SAFE M01 action —
`command: "$p = Join-Path $env:TEMP 'battery-report.html'; powercfg /batteryreport /output $p | Out-Null; Write-Output ('Battery report: ' + $p); if (Test-Path $p) { Start-Process $p }"`.

---

**Gap: no Office (Click-to-Run) quick/online repair action.**
Zero coverage.
— Competitor: Tweaking.com Windows Repair and most MSP toolkits include an
Office repair trigger as a standard preset; it's one of the most common
"Word/Outlook won't open" fixes.
— Priority: **medium**.
— Realistic addition: one new MODERATE M04 (or new small module) action
invoking the already-installed Click-to-Run client, e.g.
`"$c2r = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -EA SilentlyContinue; if ($c2r) { $exe = Join-Path (Get-ItemProperty $c2r.PSPath).ClientFolder 'OfficeC2RClient.exe'; if (Test-Path $exe) { Start-Process $exe -ArgumentList 'scenario=Repair displaylevel=True' } } else { Write-Output 'Click-to-Run Office installation not found (MSI-based Office or not installed).' }"`
— only fires the built-in "Quick Repair" UI, no destructive full-uninstall
path, gracefully no-ops on MSI-based or absent Office installs.

---

**Gap: no browser extension inventory/audit action.**
M02's `browser_cache_sweep` clears cache only; nothing lists installed
extensions.
— Competitor: **AdwCleaner** specifically hunts malicious/unwanted browser
extensions and hijacked settings as its core purpose; CCleaner's browser
cleaner also surfaces installed extensions/toolbars.
— Priority: **medium**.
— Realistic addition: one new SAFE M01 or M08 action reading each browser's
`Extensions` folder / `Secure Preferences`/`Preferences` JSON for Chrome/Edge
and `extensions.json` for Firefox, listing name + enabled state — read-only
inventory, not a remover (matches the existing house convention of
"report first, human decides" for anything ambiguous — same reasoning
already used to reject blind file-association or profile repairs in
`research-repair-additions.md`).

---

**Gap: no "restore default browser homepage/search provider" action.**
— Competitor: **AdwCleaner**'s signature move — undo browser hijacking by
resetting default search engine and homepage per-browser.
— Priority: **low-medium**. Real value, but each browser stores this
differently (Chrome/Edge in `Preferences` JSON with per-profile paths;
Firefox in `prefs.js`), so a blind static one-liner is fragile across
browser versions/profile counts — same class of risk already flagged and
rejected for "file association reset" in prior research. Better suited to
a scripted `.ps1` helper than a single YAML one-liner, which is more
architecture than a solo maintainer should take on for this specific gap
right now.
— Realistic addition: skip for now; revisit only if a per-browser detection
helper already exists for another reason.

---

**Gap: no disk space analyzer / duplicate-file finder.**
M02 has `largest_files_report` (flat top-25 across fixed folders) but
nothing recursive/whole-disk, and nothing for duplicates.
— Competitor: **CCleaner**'s disk space analyzer (treemap-style, whole
drive) and duplicate finder are marquee features.
— Priority: **low** for a full analyzer (this is inherently a rich-UI,
sortable/drillable-tree feature — real architecture, not a YAML action);
**medium** for a cheap partial fix.
— Realistic addition: extend `largest_files_report`'s folder list to be
whole-user-profile recursive with a higher `-First` count, and add a
narrow, genuinely-one-line duplicate finder limited to Downloads
(`Get-ChildItem $env:USERPROFILE\Downloads -Recurse -File | Group-Object Length,{[BitConverter]::ToString((Get-FileHash $_.FullName -Algorithm MD5).Hash)} | Where-Object Count -gt 1`-style,
report-only, no delete). A full treemap-style whole-disk analyzer is out of
scope — see §5.

---

**Gap: no Windows optional-feature (Windows Features) enable/disable.**
Zero coverage — `Dism.exe /Online /Get-Features` / `Enable-WindowsOptionalFeature`
never appears in any catalog.
— Competitor: **DISM++** exposes this directly; it's also just the GUI for
"Turn Windows features on or off" that every technician knows from
`optionalfeatures.exe`.
— Priority: **medium**.
— Realistic addition: one new SAFE M04 report action listing feature
name/state (`Get-WindowsOptionalFeature -Online | Select FeatureName,State`).
Actually toggling a *specific* feature needs a picker (same parameterization
problem as the Autoruns gap above) — safe to add the report now, defer
per-feature toggle actions.

---

**Gap: no Windows Update *hide/uninstall a specific KB* action.**
M05 covers service/cache plumbing only, never a specific update.
— Competitor: **DISM++** and **WuMgr**-style tools let a technician hide or
roll back one problem KB (common after a bad cumulative update breaks
printing/audio/etc.).
— Priority: **low**. Real gap, but "which KB" is inherently a per-incident
human choice — no safe blind default. `wusa /uninstall /kb:<n>` needs a
number nobody can supply statically.
— Realistic addition: skip an uninstall action; add one cheap SAFE report
instead — `wu_recent_kb_list` (`Get-HotFix | Sort InstalledOn -Descending`,
overlaps partially with existing `recent_hotfixes` in M01 but scoped to
KB numbers specifically so a technician can go uninstall the right one by
hand via `wusa`). Marginal value given `recent_hotfixes` already exists;
low priority to actually add.

---

**Gap: no adware/PUP-specific scan distinct from full Defender quick scan.**
M08 has Defender status/update/quickscan only.
— Competitor: **AdwCleaner** is a *second-opinion* scanner precisely because
Defender frequently misses browser hijackers/PUPs that aren't classic
malware.
— Priority: **low**. Bundling or replicating a third-party signature-based
scanner is out of scope for a YAML-action catalog (needs a scan engine,
not a PowerShell one-liner) — see §5. The closest safe substitute
(browser-extension inventory, above) is already proposed as its own gap.

## 4. Category coverage check (explicit answers)

Requested checklist, answered against the actual 108-action catalog:

| Category | Coverage today | Verdict |
|---|---|---|
| Printer troubleshooting | `net_print_spooler_reset` (M06) only | Partial — spooler reset covers the #1 fix; no printer/driver-package removal |
| Office repair | None | **Zero coverage** — gap above |
| Browser deep cleanup/reset | Cache sweep only (M02) | Partial — no extension audit, no homepage/search reset |
| BitLocker/TPM status | None implemented (speced but unlanded) | **Zero coverage today** — gap above |
| Battery health (laptops) | Presence/charge % only (M01 `computer_info`) | Partial — no wear/cycle report — gap above |
| Disk partition management | Volume/health reports only (M01, M03) | **Zero coverage** for resize/create/delete — correctly out of scope, see §5 |
| Boot repair/BCD | None | **Zero coverage**, and intentionally so — flagged and rejected in `research-repair-additions.md` as out-of-scope for a live-Windows (not WinPE) tool |
| Driver rollback | Read-only problem/third-party list (M10) | Partial — gap above |
| Windows feature enable/disable | None | **Zero coverage** — gap above (report is tractable; toggling isn't yet) |
| Scheduled task management beyond autoruns | Read-only full list (M07) + a few named task disables buried in M13 debloat | Partial — no generic disable-by-name; gap noted in §3 (Autoruns editor) |

## 5. Out of realistic scope (name and skip)

These are real, named competitor capabilities that a solo maintainer
extending a static-YAML-action catalog should **not** chase right now —
listed so they're not silently forgotten, not because they're bad ideas:

- **Full Autoruns-style interactive per-item enable/disable across every
  autostart surface.** Needs dynamic, per-run parameterization (which
  registry value/task/service, chosen at runtime) that the current schema
  (`id, command, undo_command` — static strings, no placeholders) can't
  express without a real architecture change (a picker UI + dynamic command
  templating). This is the single biggest genuine capability gap versus
  Sysinternals Autoruns, and also the most expensive to close correctly.
- **Whole-disk treemap-style space analyzer / general duplicate-file
  finder (CCleaner-class).** Needs a rich drillable UI, not a one-shot
  PowerShell report.
- **Partition create/resize/delete (AOMEI/MiniTool/Hiren's-class).**
  Destructive-by-nature, requires an interactive partition picker + free-
  space visualization; a blind static action here is actively dangerous.
- **Boot repair / BCD rebuild (bootrec/bcdboot/EasyBCD-class).** Already
  correctly excluded — this domain assumes a WinPE/offline boot context;
  PortableFix runs from a live, booted Windows session, a fundamentally
  different environment where these tools don't apply the same way.
- **Signature-based malware/adware scanning (AdwCleaner/Malwarebytes-class).**
  Needs a maintained detection database and scan engine; wrapping Defender
  is already done (`sec_defender_quickscan`), and duplicating a dedicated
  AV vendor's engine is not a PowerShell-one-liner problem.
- **Registry cleaner (CCleaner-class "fix registry errors").** Deliberately
  not present today and should stay that way — this is the single most
  reversal-fragile, false-positive-prone feature in the entire competitor
  set (unused-extension/ActiveX/class-ID heuristics routinely break working
  software); its absence is a feature, not a gap.
- **Driver *downloading*/update-from-internet (CCleaner Driver Updater-
  class).** Needs a maintained driver database/vendor feed PortableFix has
  no infrastructure for; the rollback/reinstall subset in §3 is the
  tractable slice of this.

## 6. Top picks (highest value ÷ effort)

Ranked for a solo maintainer, each landable as 1-3 new YAML actions with no
schema/architecture change:

1. **`sec_bitlocker_status` + `sec_tpm_secureboot_status`** (M08) — already
   fully written in `research-security-additions.md`, just needs to be
   copied into `actions.yaml`. Zero new design work.
2. **Battery health report** (`powercfg /batteryreport`, M01) — one line,
   SAFE, directly answers a top laptop-repair question, nothing like it
   exists today.
3. **Office Click-to-Run quick repair** (M04) — one MODERATE action, covers
   a very common "Office is broken" ticket, gracefully no-ops when absent.
4. **Driver rollback-availability report + problem-device reinstall cycle**
   (M10) — turns the module's two read-only reports into an actual fix path
   without needing a driver database.
5. **Windows optional-features report** (M04) — SAFE, one line, closes part
   of the DISM++ gap; toggle-a-specific-feature deferred (parameterization).
6. **Browser extension inventory (read-only)** (M01 or M08) — SAFE report
   answering the AdwCleaner-class "what's installed in my browser" question
   without touching the fragile hijack-reset territory.
7. **Third-party-only filtered Run-key report** (M07) — cheap variant of the
   existing `autoruns_registry_run` that pre-filters signed Microsoft
   entries, narrowing the Autoruns gap without building an editor.
8. **Downloads-folder duplicate-file report** (M02) — narrow, safe slice of
   the CCleaner duplicate-finder gap, report-only.

Items 1-3 are the highest ratio of real-world usefulness to implementation
cost — each is a single self-contained SAFE/MODERATE action with a clear
built-in Windows command backing it, matching the existing catalog's "one
static one-liner, one clear job" convention.

## Sources

- `README.md`, all 12 `Modules/*/actions.yaml` (full contents read)
- `docs/research/research-repair-additions.md`, `research-security-additions.md`
  (prior internal gap analyses, cross-checked for overlap/duplication)
- [Tweaking.Com Windows Repair — Filehippo](https://filehippo.com/download_tweaking-com-windows-repair/)
- [Tweaking Windows Repair Review 2026 — MSPoweruser](https://mspoweruser.com/tweaking-windows-repair-review/)
- [Tweaking.com Windows Repair All-in-One — official features page](https://www.tweaking.com/features/windows-repair-all-in-one/)
- [CCleaner Review 2025 — WindowsTechies](https://windowstechies.com/reviews/ccleaner-review/)
- [CCleaner version history — ccleaner.com](https://www.ccleaner.com/ccleaner/version-history)
- [BleachBit Features — bleachbit.org](https://www.bleachbit.org/features)
- [BleachBit — official site](https://www.bleachbit.org/)
- [Sysinternals Suite — Microsoft Learn](https://learn.microsoft.com/en-us/sysinternals/downloads/sysinternals-suite)
- [Sysinternals Suite 2026.07.05 — Neowin](https://www.neowin.net/software/sysinternals-suite-20260705/)
- [O&O ShutUp10++ — official site](https://www.oo-software.com/en/shutup10)
- [O&O ShutUp10++ — centralized privacy controls — Windows Forum](https://windowsforum.com/threads/o-o-shutup10-centralized-windows-privacy-controls-in-a-portable-tool.391556/)
- [Dism++ — GitHub](https://github.com/DISM-PlusPlus-Windows/)
- [Dism++ — reboottools.com](https://reboottools.com/programs/dism/)
- [Hiren's BootCD PE x64 Tools Overview — Scribd](https://www.scribd.com/document/383771746/Hiren-Boot-CD-Tools)
- [Hiren's BootCD PE ISO — reboottools.com](https://reboottools.com/hirens-bootcd/)
- [AdwCleaner — Malwarebytes official](https://www.malwarebytes.com/adwcleaner)
- [Overview of AdwCleaner features — Malwarebytes Help Center](https://help.malwarebytes.com/hc/en-us/articles/31589287849371-Overview-of-AdwCleaner-features)
- [How to pass the Windows 11 Compatibility Checks — Eleven Forum](https://www.elevenforum.com/t/how-to-pass-the-windows-11-compatibility-checks.163/)
- [Check & Bypass Windows 11 Requirements — WhyNotWin11 — CoSci Blog](https://cosci.de/en/computer-en/check-windows-11-compatibility-with-whynotwin11/)
