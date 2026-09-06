# PortableFix

*[Slovenská verzia / Slovak version: README.md](README.md)*

A portable diagnostic and repair tool for Windows 10/11, meant to run
from a USB stick. Python 3.12 + PySide6 GUI, actions run through
PowerShell.

## Quick start

1. Copy the whole folder to a USB stick (or run directly from disk).
2. Run `PortableFix.cmd` (or `python main.py` in dev).
3. Without admin rights the app runs in diagnostics-only mode; the
   **Restart as Administrator** button unlocks full access.
4. Check the actions you want, verify in **DRY-RUN** mode (on by
   default), then turn DRY-RUN off and run for real.

## Modules

| Module | Category | Contents |
|---|---|---|
| M01 | Diagnostics | System info (OS, HW, disks, processes...) |
| M02 | Cleanup | Temp files, cache, recycle bin, Windows Update cache... |
| M03 | Repair | Disk: SMART, NTFS scan/SpotFix, TRIM, chkdsk on restart |
| M04 | Repair | System integrity: DISM, SFC, AppX, WMI |
| M05 | Repair | Windows Update: service/cache reset, DLL re-registration, detection |
| M06 | Repair | Network: DNS, hosts, DHCP, Winsock, TCP/IP |
| M07 | Diagnostics | Autostart: Run registry keys, Startup, tasks, services |
| M08 | Security | Defender, firewall, UAC audit + quick scan, WPBT disable |
| M09 | Repair | Tuning: power plan, visual effects, End Task, Sticky Keys, classic context menu |
| M10 | Diagnostics | Drivers: problem devices, third-party drivers |
| M11 | — | Reporting (HTML report after every batch, not a catalog) |
| M12 | Diagnostics | Online: layered connectivity test, DNS, proxy |
| M13 | Cleanup | Debloat: telemetry, scheduled tasks, Fast Startup, Explorer ads, Recall/Click to Do |
| M14 | Repair | Printing: printers, drivers, offline/ghost printers, spooler reset |
| M15 | Repair | Boot/platform: BCD, TPM, Secure Boot, BitLocker, Safe Mode, F8 recovery |
| M16 | Repair | Office: version/channel, Outlook add-ins, OST/PST, quick/full repair |
| M17 | Repair | Browsers: extensions, policy, homepage hijack, profile reset |
| M18 | Repair | Back up user folders (Desktop/Documents/Pictures/Favorites) |
| M19 | Repair | Windows optional features: overview, .NET 3.5, PowerShell v2, Sandbox |
| M20 | Repair | Software updates via winget: list, outdated software, update all |
| M21 | Repair | Hardware sensors: PawnIO status/install (CPU temp/clock via LibreHardwareMonitor) |
| M22 | Cleanup | Deep cleanup: orphaned uninstall entries, duplicate files, broken shortcuts (.lnk), secure free-space wipe |

## Safety mechanisms

- **Risk levels:** every action is tagged SAFE / MODERATE / DESTRUCTIVE /
  REQUIRES_REBOOT. MODERATE and above need confirmation; DESTRUCTIVE
  gets an extra irreversibility warning.
- **DRY-RUN:** on by default — actions only print (or run a read-only
  preview), nothing changes.
- **Restore point:** before the first DESTRUCTIVE action, or any action
  from the Repair/Security category, a System Restore Point is created
  once per batch (best-effort; on failure the app asks whether to
  continue anyway).
- **undo.ps1:** actions with a reversible effect (e.g. resetting the
  hosts file, stopping services, changing the power plan) append their
  undo command to `Backups/<run-id>/undo.ps1` as they run - in reverse
  (LIFO) order, so the script can be run as a whole. It's written after
  every successful action, so even if the app crashes the file reflects
  real state.
- **Audit log + report:** every action is written to
  `Logs/<run-id>/audit.jsonl`, and an HTML report is generated to
  `Reports/` after each batch.
- **Auto-update:** when running as a packaged `.exe`, the app silently
  checks GitHub Releases (`vxkShelby/portableFixer`) at startup; if a
  newer version exists, it shows a dismissible banner offering to
  download and apply it. The download runs in the background and
  replaces the whole package (`App/`, `Modules/`, `Vendor/`,
  `PortableFix.cmd`) - `Data/settings.json` (language, dry-run) is kept.
  On failure (offline, timeout) it stays silent - nothing is shown.
- **Self-delete protection:** the actions that wipe `%TEMP%` and
  `%WINDIR%\Temp` (`user_temp`, `system_temp`) detect if the app is
  running from inside that folder and exclude it - if that can't be
  determined safely (the app's own folder IS `%TEMP%`, or it's
  redirected via a junction/symlink), the action refuses to run at all
  and tells the user instead of guessing. The app also logs its own
  resolved paths at startup, and checks mid-batch whether its own
  folder has disappeared - if so, it stops the batch immediately
  instead of silently continuing.

## Folder layout

```
PortableFix/
  PortableFix.cmd        launcher
  main.py                entry point
  portablefix/           application code
  Modules/<id>/actions.yaml   declarative action catalogs
  Vendor/                 LibreHardwareMonitorLib (optional HW sensors)
  Data/                  settings.json, SHA256SUMS (runtime)
  Logs/                  audit logs (runtime)
  Reports/               HTML reports (runtime)
  Backups/               undo.ps1 scripts (runtime)
  scripts/build.ps1      PyInstaller build
```

If the USB stick isn't writable, runtime folders move to
`%TEMP%\PortableFix` (the app announces this with a banner).

## Build

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

Output: `App/PortableFix.exe` (PyInstaller onefile, a single
executable, no `_internal` subfolder). The script also automatically
regenerates checksums, packages the portable ZIP, and compiles the
installer (if `ISCC.exe` is found) - no extra manual step needed.

## Manual steps before distribution

These steps need resources outside the repo and are done by hand:

1. **Code signing** — `App\PortableFix.exe` is signed with a
   self-signed certificate (`CN=PortableFix Self-Signed`, public part in
   `Data\PortableFix-SelfSigned.cer`). On a target machine the signature
   can be trusted by importing it (admin PowerShell):
   ```powershell
   Import-Certificate -FilePath Data\PortableFix-SelfSigned.cer -CertStoreLocation Cert:\LocalMachine\Root
   Import-Certificate -FilePath Data\PortableFix-SelfSigned.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher
   ```
   For warning-free distribution on other machines you need a
   commercial certificate (OV/EV); then re-sign:
   ```powershell
   signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 App\PortableFix.exe
   ```
   Regenerate SHA256SUMS after every signing pass
   (`python scripts/generate_sha256sums.py .`).
2. **VM test** — test on a clean Windows 10 and 11 install (both
   without and with admin rights): app startup, a DRY-RUN batch, a real
   SAFE batch, and check the generated report and undo.ps1.

## Release process (a new version with auto-update)

Manual process, none of this is automated:

1. Bump `APP_VERSION` in `portablefix/version.py` **and**
   `MyAppVersion` in `installer/PortableFix.iss` (they must match).
2. `powershell -ExecutionPolicy Bypass -File scripts\build.ps1` →
   `App/PortableFix.exe` (onefile), `Output/PortableFix-Portable.zip`
   (+ `.sha256`), and if Inno Setup is installed (ISCC.exe,
   [jrsoftware.org](https://jrsoftware.org/isinfo.php)) also
   `Output/PortableFix-Setup.exe`.
3. Sign `App/PortableFix.exe` (`signtool sign ...`, see above) **before**
   step 2, or re-run `scripts\build_release_zip.ps1` after signing, so
   the signed exe ends up in the zip too.
4. `python scripts/generate_sha256sums.py .` — updates
   `Data/SHA256SUMS` (the zip's contents must have the up-to-date
   SHA256SUMS, run this before steps 2/3 in the order above).
5. Create a GitHub Release tagged `v<version>` (e.g. `v1.1.0`), upload
   **three** files as assets, with exactly these names (auto-update and
   the installer both look them up by a fixed name, not by version):
   - `PortableFix-Portable.zip` — this is what the auto-update mechanism downloads
   - `PortableFix-Portable.zip.sha256`
   - `PortableFix-Setup.exe` — the installer for regular users

**Important:** if a release is created without the `.sha256` asset,
auto-update won't notice and the downloaded package gets applied
**without hash verification** (no warning in the UI) - never skip step 5.

Since this version, auto-update downloads the **whole package** (exe +
Data + Modules), not just the `.exe` - this way already-installed
copies also get new/changed modules, not just Python code changes.
`Data/settings.json` (language, dry-run) is kept across an update;
everything else in `App/`, `Modules/` and `PortableFix.cmd` is replaced.

## Development

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ --deselect tests/test_gui_main_window.py --deselect tests/test_executor.py
python -m pytest tests/test_gui_main_window.py
python -m pytest tests/test_executor.py
python -m pytest tests/test_updater.py
```

`tests/test_gui_main_window.py`, `tests/test_executor.py` and
`tests/test_updater.py` (its `UpdateCheckRunner`/`UpdateDownloadRunner`
tests) spawn real PowerShell processes through the same `QThread`
mechanism (`portablefix/executor.py`); running several such files at
once in one pytest session can occasionally trigger a transient native
environment crash (STATUS_STACK_BUFFER_OVERRUN) - not a code bug. If
this happens, run the affected tests individually
(`python -m pytest tests/test_gui_main_window.py::test_name`) with one
retry on failure, instead of the whole file at once.

## Known limitations

- Undo only covers actions with a static reversible command; DISM/SFC/chkdsk
  repairs are inherently irreversible (covered by the restore point instead).
- Undo for combined actions (e.g. stopping 4 services at once) is only
  written on full success of the action.
- A restore point is created even for purely diagnostic actions in the
  Repair/Security categories (deliberate, conservative behavior).
- `regsvr32`/`UsoClient` steps in M05 report success even on a silent
  failure (non-blocking processes).
