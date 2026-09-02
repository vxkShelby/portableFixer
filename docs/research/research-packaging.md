# Packaging, Signing & Distribution — Onefile/Auto-update Audit

Scope: `scripts/build.ps1`, `scripts/generate_sha256sums.py`, `portablefix/integrity.py`,
`portablefix/paths.py`, `portablefix/updater.py`, `portablefix/gui/main_window.py`
(update banner/flow), `portablefix/elevation.py`, `portablefix/executor.py`,
`portablefix/restore_point.py`, `README.md`, `.gitignore`, `requirements.txt`,
`requirements-dev.txt`, `pyproject.toml`, `tests/test_paths.py`, `tests/test_updater.py`,
`docs/superpowers/specs/2026-09-02-autoupdate-design.md`,
`docs/superpowers/plans/2026-09-02-autoupdate.md`, plus git history for `README.md`,
`scripts/build.ps1`, and the self-signed cert commit. Also inspected
`Data/PortableFix-SelfSigned.cer` directly (`X509Certificate2` load) for its actual
validity window. Read-only research; no files modified.

---

## Severity summary

- **CRITICAL:** none.
- **HIGH:**
  1. Zero AV/SmartScreen false-positive mitigation or documentation exists beyond
     self-signing, despite a behavior profile (hidden PowerShell spawns + a
     self-replacing `.exe` + registry/service/firewall-touching commands) that is a
     textbook heuristic-detection target.
  2. The `--onefile` switch trades a known, real AV-detection cost (self-extraction to a
     fresh temp path on every launch — a classic packer signature) for update-swap
     correctness, and that trade-off was never evaluated for its AV/SmartScreen impact
     in the design doc.
  3. The full manual release runbook (bump `APP_VERSION`, build, sign, generate
     `Data/SHA256SUMS`, compute `App/PortableFix.exe.sha256`, cut a GitHub Release with
     both assets) exists only in the design spec — the README's "final review" pass
     patched one line (the onefile build-output description) and added nothing about
     auto-update, versioning, or release assets.
  4. `download_update()` silently skips SHA256 verification when a release is missing
     the `.exe.sha256` asset, and the resulting success path is UI-indistinguishable
     from a verified download — a maintainer's one missed upload silently converts an
     entire release into an unverified binary swap for every user.
  5. PyInstaller itself is pinned nowhere (not `requirements.txt`, not
     `requirements-dev.txt`, not `build.ps1`, not README) — a fresh clone cannot follow
     the documented build steps without an undocumented external `pip install
     pyinstaller`, and PyInstaller version drift is a known source of both bootloader
     behavior changes and AV false-positive rate changes between releases.
- **MEDIUM:**
  6. The self-signed cert's 3-year validity window (expires **2026-09-01 → 2029-09-01**,
     confirmed by direct inspection) is recorded nowhere in the repo, and no cert
     regeneration script exists — a future rotation will silently break trust for any
     technician who ran the README's `Import-Certificate` step against the old
     thumbprint.
  7. Auto-update replaces only `App/PortableFix.exe`, by design never touching
     `Modules/*.yaml` — meaning successive releases that add/change catalog actions
     (this session shipped several such commits) leave an auto-updated technician's
     local catalogs stale relative to the new app code, with no mechanism or
     documentation addressing the drift.
  8. If `App/` is writable but its sibling `Data/` is not, the generated swap script's
     `Data/SHA256SUMS` rewrite fails silently (wrapped in `try {} catch {}`), producing
     a false-positive integrity warning on the next launch after a legitimate update.
- **LOW:**
  9. `restore_point.py:11` hardcodes System Restore's target as `-Drive "C:\\"` — not a
     USB-portability bug (System Restore always targets the OS/boot volume, not the
     app's own media), but it is the one other drive-letter-shaped hardcode in the
     codebase and would silently target the wrong volume on a non-`C:`-booted machine.

---

## 1. Antivirus / SmartScreen false-positive risk

### 1.1 The behavior profile is real, not hypothetical
Three independent, now-confirmed code paths combine into exactly the profile the task
describes:

- **Hidden PowerShell spawns for repair actions** — `portablefix/executor.py:115-120`:
  ```python
  process = subprocess.Popen(
      self._plan.argv,
      stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT,
      creationflags=subprocess.CREATE_NO_WINDOW,
  )
  ```
  Every catalog action (registry edits, service stop/start, firewall changes, DISM/SFC)
  runs through this, window-hidden, for every module.
- **A second, detached, hidden PowerShell process that replaces the running `.exe`** —
  `portablefix/updater.py:131-140`:
  ```python
  subprocess.Popen(
      ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
       "-File", str(script_path)],
      creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
      close_fds=True,
  )
  ```
  This is launched from a script that was itself written to a temp `.ps1` file
  moments earlier (`apply_update`, same file, lines 131-135), then that script waits
  for the parent PID to die and does `Move-Item` over the running executable
  (`build_swap_script`, lines 94-128). A parent process writing a script to `%TEMP%`,
  spawning a hidden detached PowerShell to run it, and having that script overwrite the
  parent's own binary is close to a canonical self-updating-malware/dropper pattern as
  far as heuristic engines are concerned — intent is irrelevant to the detector.
- **A "system diagnostic/repair" catalog that legitimately touches registry, services,
  and firewall** — confirmed present today (`Modules/m08_security/actions.yaml`,
  `Modules/m06_network/actions.yaml`, etc., per the prior security audit in
  `docs/research/research-security-audit.md`).

Individually each piece is defensible (hidden windows avoid flashing consoles at a
technician; the detached swap script is the only reliable way to replace a running
`.exe` on Windows). Combined, with a **self-signed, non-CA-trusted** signature
(`README.md:84-91`, `CN=PortableFix Self-Signed`) providing no SmartScreen reputation
build-up, this is a high-probability false-positive profile for both Defender
heuristics and third-party AV, and for SmartScreen's "unrecognized app" warning on
first run of every new release (self-signing does not suppress SmartScreen at all —
only EV-code-signing or accumulated Microsoft reputation does).

### 1.2 No mitigation beyond self-signing, and nothing cheap left on the table unused
`README.md:80-101` ("Manuálne kroky pred distribúciou") covers signing and a VM test
only. There is no:
- Windows Defender Application Control / SmartScreen submission step (submitting the
  binary to Microsoft for reputation/false-positive review is free and commonly
  recommended for exactly this profile — not present anywhere).
- Mention of requesting IT technicians pre-add a Defender exclusion for the USB/App
  path (a one-line ask that would eliminate the runtime-scan-on-every-launch cost
  entirely for the tool's actual target audience — IT techs, who routinely do this for
  other portable tools).
- Any user-facing string warning the technician that Windows/AV may flag the tool
  (checked `portablefix/i18n.py` additions across all four auto-update tasks — no
  `smartscreen`/`antivirus`/`false_positive`-shaped key exists).
- A `.gitignore`/README note that the RFC3161-timestamped signature
  (`README.md:95`, `signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com`)
  is at least correctly used — this part is good practice already in place (see
  Verified Clean), but it isn't called out as *why* it matters for AV posture, so a
  future maintainer could drop the `/tr` flag without realizing the consequence
  (losing "valid signature after cert expiry" for all future builds).

**Severity: HIGH.** **Fix (cheap, no new tooling):** add a short "Known limitations —
Windows may flag this tool" section to README naming SmartScreen/Defender explicitly,
recommending technicians add a Defender path exclusion for the USB root, and — since
it's a zero-cost step already available — submitting each signed release build to
Microsoft's file-submission portal before publishing.

---

## 2. Onefile-specific packaging risk (`--onedir` → `--onefile`)

This is a real, named risk class, not a hypothetical: PyInstaller `--onefile`
bootloaders extract the full payload to a **new** subdirectory under
`%TEMP%` (`_MEIxxxxxx`, PID-suffixed) on every single launch, then execute code from
that temp path. This is functionally identical to what a self-extracting packer does,
and "an executable that unpacks and runs a payload from a freshly-created temp
directory on every run" is one of the most commonly cited heuristic triggers for both
Windows Defender and third-party AV products — independent of what the payload
actually does. `--onedir` does not have this pattern: its files sit statically
next to the launcher at a fixed, inspectable, unchanging path, which AV engines can
build path/hash reputation against over repeated runs; `--onefile`'s temp path and
(in some PyInstaller configurations) temp-file naming churn on every launch, denying
that reputation build-up.

Evidence this session made the switch and the reasoning that drove it — but never
weighed the AV cost:
- `scripts/build.ps1:6-11` (comment added with the switch):
  ```
  # --onefile bundles everything (bootloader + all Python bytecode + deps)
  # into a single .exe. This matters for auto-update: swapping just the
  # .exe is a complete, correct update. With the old --onedir layout the
  # actual app code lived in a separate _internal/PYZ-00.pyz next to a
  # thin bootloader .exe, so swapping only the .exe would have left stale
  # code running.
  ```
- `docs/superpowers/specs/2026-09-02-autoupdate-design.md:25-31` gives the same
  justification, purely about update-swap correctness — the design doc's "Prečo
  onefile" section never discusses runtime scanning behavior or AV posture at all.

Nothing in the codebase mitigates this: no `--runtime-tmpdir` override to a fixed,
non-random extraction path (which at least removes the "new random path every launch"
half of the signature, though not the "self-extracting" half), no mention of the
trade-off in README's known-limitations section, and no fallback plan if this proves to
be a worse false-positive generator in practice than `--onedir` was.

**Severity: HIGH** — this is an inherent, unavoidable cost of the `--onefile` decision
that was made for a legitimate reason (single-file update-swap correctness) but was
never evaluated or documented as a trade-off, and the tool's whole distribution model
(unsigned-by-CA, run-from-USB, touches system internals) is exactly the profile where
this cost bites hardest. **Fix:** document the trade-off explicitly in README/design
docs as a known limitation; consider `--runtime-tmpdir` pinned under the app's own
writable `Data/` or `%TEMP%\PortableFix` (already a known-writable fallback location
per `paths.resolve_writable_base_dir`) instead of PyInstaller's default randomized
`_MEIxxxxxx`, to at least remove path-randomization from the heuristic signature.

---

## 3. USB drive portability — `get_base_dir()` / `resolve_writable_base_dir()`

**This is handled correctly.** Traced the full path:

- `portablefix/paths.py:6-9`:
  ```python
  def get_base_dir() -> Path:
      if getattr(sys, "frozen", False):
          return Path(sys.executable).resolve().parent.parent
      return Path(__file__).resolve().parent.parent
  ```
  In frozen (PyInstaller) mode this derives entirely from `sys.executable` — the
  actual on-disk location of the running `.exe` at the time it launched — with no
  hardcoded drive letter or path anywhere. If the same USB stick enumerates as `D:` on
  one machine and `F:` on another, `sys.executable` reflects whichever letter Windows
  assigned that session, and `get_base_dir()` walks up two levels from wherever that
  actually is (`<root>/App/PortableFix.exe` → `<root>`), correctly, regardless of
  drive letter.
- `tests/test_paths.py:12-16` explicitly proves this is drive-letter-agnostic by
  construction, not just by absence of a counter-example:
  ```python
  def test_get_base_dir_frozen_mode(monkeypatch):
      monkeypatch.setattr(sys, "frozen", True, raising=False)
      monkeypatch.setattr(sys, "executable", str(Path("C:/USB/PortableFix/App/PortableFix.exe")))
      result = get_base_dir()
      assert result == Path("C:/USB/PortableFix")
  ```
  The relative-resolution logic under test is identical whether the mock path starts
  with `C:`, `D:`, or any other letter — the test would pass unchanged if the fixture
  used `E:/...` instead.
- **`--onefile`'s temp-extraction directory does not leak into this logic.** Confirmed
  via `grep -rn "_MEIPASS"` across `portablefix/` — zero matches in source (only the
  PyInstaller-generated build artifacts under `build/`/`App/` reference it, which is
  expected boilerplate, not app code). `sys.executable` in a `--onefile` frozen build
  still points to the real, user-visible `.exe` path on the USB drive, never to the
  bootloader's temp self-extraction directory (that's exclusively reachable via
  `sys._MEIPASS`, which this app never reads) — this matches the design doc's explicit
  claim (`docs/superpowers/specs/2026-09-02-autoupdate-design.md:40-43`) and the claim
  checks out against the actual code.
- The auto-update flow reuses the same safe pattern: `portablefix/gui/main_window.py:498`,
  `current_exe = Path(sys.executable)`, used as the swap target for
  `updater.apply_update(...)` — also drive-letter-safe by the same reasoning.
- `resolve_writable_base_dir()` (`portablefix/paths.py:12-22`) probes writability with
  a real file write/delete and falls back to `%TEMP%\PortableFix` on `OSError` — this
  correctly covers the "USB happens to be locked/read-only on this machine" case
  independent of drive letter, and is exercised by `tests/test_paths.py:19-34`.

**One adjacent, non-drive-letter-portability hardcode worth flagging (LOW):**
`portablefix/restore_point.py:11` —
```python
'Enable-ComputerRestore -Drive "C:\\"; '
```
This targets the *Windows boot/OS volume* for System Restore, not the app's own
portable media, so it is unrelated to the USB-drive-letter question the task asked
about — Windows System Restore points inherently apply to the OS volume, and `C:` is
overwhelmingly the common case. It is flagged only because it's the one other
drive-letter-shaped literal in the codebase: on the rare machine where Windows is
installed on a non-`C:` volume (multi-boot, some enterprise imaging setups), this
would silently attempt to enable/checkpoint a restore point on the wrong (or
nonexistent) drive, likely surfacing as a benign-looking PowerShell error rather than
a clear "wrong drive" message.

---

## 4. Release process gaps

### 4.1 The full runbook never made it into README
The design spec's runbook (`docs/superpowers/specs/2026-09-02-autoupdate-design.md:288-306`)
lists six steps: bump `APP_VERSION`, build, sign, `generate_sha256sums.py`, compute
`App/PortableFix.exe.sha256`, cut a GitHub Release tagged `v<version>` with both
`PortableFix.exe` and `PortableFix.exe.sha256` uploaded as assets.

Confirmed via `git show 1169904 -- README.md` (the commit whose message explicitly
says "fix README (final review)") that this commit's *entire* README change was:
```diff
-Výstup: `App/` (PyInstaller onedir). Po buildе vygeneruj kontrolné
+Výstup: `App/PortableFix.exe` (PyInstaller onefile, jeden spustiteľný
+súbor, žiadny `_internal` podpriečinok). Po buildе vygeneruj kontrolné
 súčty: `python scripts/generate_sha256sums.py`.
```
One line, describing the *build output shape* — nothing about auto-update,
versioning, or release assets. A full-text search of the current `README.md` for
`update|release|GitHub|version|sha256` (case-insensitive) turns up only unrelated
matches (module descriptions mentioning "Windows Update", the existing
`Data/SHA256SUMS` integrity mechanism, and the `signtool` line) — **zero mentions of
the auto-update feature, `APP_VERSION`, GitHub Releases, or the
`App/PortableFix.exe.sha256` sidecar file exist anywhere in README.md.** A maintainer
following only the README — the one place a public GitHub repo's contributors are
expected to look — has no way to discover that: (a) auto-update exists at all, (b) a
version bump is required before each release, or (c) a second, separate `.sha256` file
must be generated and uploaded alongside the `.exe`.

**Severity: HIGH.** **Fix:** port the six-step runbook from the design spec into
README's "Manuálne kroky pred distribúciou" section verbatim (it's already written,
just in the wrong document for a maintainer to find it).

### 4.2 Missing `.sha256` asset degrades to unverified silently
`portablefix/updater.py:68-79`:
```python
def download_update(info: UpdateInfo, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_path = dest_dir / "PortableFix.new.exe"
    urllib.request.urlretrieve(info.download_url, exe_path)
    if info.sha256_url:
        with urllib.request.urlopen(info.sha256_url, timeout=10) as resp:
            expected = resp.read().decode("utf-8").strip().split()[0].lower()
        actual = compute_sha256(exe_path)
        if actual.lower() != expected:
            exe_path.unlink(missing_ok=True)
            raise UpdateVerificationError("Downloaded file does not match expected SHA256.")
    return exe_path
```
When `info.sha256_url` is `None` (i.e., the release has no `PortableFix.exe.sha256`
asset — exactly the artifact a maintainer forgets if they skip runbook step 5), the
function returns the downloaded path with **no verification performed at all**, and no
distinguishing signal is attached to that return value. Tracing the caller
(`UpdateDownloadRunner.run()`, `portablefix/updater.py:165-170`) and the GUI handler
(`portablefix/gui/main_window.py:490-513`, `_on_update_download_finished`): a verified
download and an unverified (no-asset) download both take the exact same
`download_finished.emit(path, "")` → success-banner → "restart and update?" confirm →
`apply_update(...)` path. The only place this distinction *could* surface —
`update_verify_failed`, listed as a planned i18n key in the design spec — is never
actually wired to the "no sha256 asset present" case; that key (per the plan) is only
reachable via the `UpdateVerificationError`-raises-on-*mismatch* path, not the
skipped-entirely path.

**Severity: HIGH.** A single missed upload on one release silently converts every
subsequent auto-update install of that release into "trust GitHub's `browser_download_url`
over HTTPS with zero application-level integrity check," with a UI that looks
identical to a verified update. **Fix:** distinguish "no sha256 asset" from "verified"
in the return/signal shape (e.g., `download_update` returns a tuple or the caller
checks `info.sha256_url is None` before calling `download_update`), and surface a
visibly different, less-reassuring banner state ("update downloaded but could not be
verified — release is missing its checksum file") rather than silence.

### 4.3 Release-to-release catalog drift (adjacent consequence, not asked directly but evidenced)
By design (`docs/superpowers/specs/2026-09-02-autoupdate-design.md:33-49`), auto-update
touches only `App/PortableFix.exe` and never `Modules/`, `Data/`, `Logs/`, `Reports/`,
or `Backups/` — explicitly to protect a technician's own catalog edits. This session's
own git log (`db7e83e`, `699724b`, `a4eb643`, etc.) shows the project actively shipping
new catalog actions into `Modules/*.yaml` release-to-release. Once a technician
auto-updates the `.exe` to a version whose code may assume newer catalog shapes/action
IDs, their local `Modules/*.yaml` is never refreshed by the mechanism — there is no
migration, warning, or README note addressing this drift. **Severity: MEDIUM**,
noted because it's a direct, evidenced consequence of the release process rather than
a hypothetical.

### 4.4 `Data/SHA256SUMS` rewrite can fail independently of the `App/` writability check
The pre-flight check before applying an update (`portablefix/gui/main_window.py:499`)
only verifies `updater.is_writable(current_exe.parent)` — i.e., `App/` itself. The
generated swap script separately rewrites `Data/SHA256SUMS` (`build_swap_script`,
`portablefix/updater.py:109-126`) inside a `try { } catch { }` with
`$ErrorActionPreference = "SilentlyContinue"`, so a failure there (e.g., `Data/` has
different ACLs than `App/` on some locked-down machine) is swallowed silently. The
practical effect: the `.exe` swap still succeeds, but `Data/SHA256SUMS`'s
`App/PortableFix.exe` line stays stale, so `check_integrity()`
(`portablefix/integrity.py:27-37`, invoked unconditionally on every startup via
`main.py:35`) will report a false-positive "files were changed" warning on the very
next launch after a legitimate, successful update. **Severity: MEDIUM** (narrow —
`App/` and `Data/` are sibling directories on the same volume and will almost always
share writability — but there is no test covering this specific split-writability
case, and the failure mode is a confusing false alarm right after the update the user
just asked for).

---

## 5. Build reproducibility

**PyInstaller is pinned nowhere in the repository.**
```
requirements.txt:       PySide6==6.7.2 / pywin32==306 / PyYAML==6.0.2
requirements-dev.txt:   -r requirements.txt / pytest==8.3.2 / pytest-qt==4.4.0
```
Neither file mentions `pyinstaller`. `scripts/build.ps1:12` invokes bare `pyinstaller`
— whatever version happens to be on `PATH` at build time — with no version assertion
or check. `pyproject.toml` contains only `[tool.pytest.ini_options]`, no dependency
list at all. No `.python-version` or `runtime.txt` pins the Python 3.12 patch version
either. There is no CI workflow (`.github/` does not exist in this repo) that would
otherwise pin/record a known-good toolchain.

Two concrete consequences:
1. **README's own documented dev setup doesn't produce a buildable environment.**
   `README.md:105-109` ("Vývoj") instructs `pip install -r requirements.txt -r
   requirements-dev.txt` then references running tests — but `scripts/build.ps1`
   (referenced two sections earlier, `README.md:70-78`) requires `pyinstaller` to be
   importable/on-PATH, which this install command never provides. A maintainer
   following the README verbatim on a fresh machine hits `pyinstaller: command not
   found` with no documented fix.
2. **Rebuild-months-from-now risk is real and specific to this project's AV exposure.**
   PyInstaller bootloader behavior (and its interaction with Windows Defender/AV
   heuristics specifically — see section 2) has measurably changed across minor
   versions historically; an unpinned `pip install pyinstaller` run a year from now
   could silently pick up a materially different bootloader with different AV
   detection characteristics, different `--onefile` extraction behavior, or a
   PySide6-compatibility regression, none of which would be caught by anything in this
   repo (no CI, no version pin, no smoke test gating a release).

**Severity: HIGH.** **Fix:** add `pyinstaller==<current-tested-version>` to
`requirements-dev.txt` (it belongs there, not `requirements.txt`, since it's a build
tool, not a runtime dependency — consistent with how `pytest`/`pytest-qt` are already
split out), and add a one-line note in README's build section that `pip install -r
requirements-dev.txt` is sufficient for both testing and building.

---

## 6. Code-signing certificate lifecycle

Direct inspection of the tracked public certificate confirms the validity window that
was only ever stated informally in a commit message:

```
Subject:     CN=PortableFix Self-Signed
NotBefore:   2026-09-01
NotAfter:    2029-09-01   (3-year validity)
Thumbprint:  F8150F1789A601D7A570A588D7E83CEE9622E518
```

(`Data/PortableFix-SelfSigned.cer`, loaded via
`System.Security.Cryptography.X509Certificates.X509Certificate2`.) This matches commit
`bb00ec7`'s message ("3y validity, CurrentUser\My") but **that expiry date is recorded
nowhere else in the repository** — not in README, not in either design doc. No
`New-SelfSignedCertificate` generation script is checked in either; the cert was
created out-of-band on a local machine's `Cert:\CurrentUser\My` store and only the
exported public `.cer` is tracked (`Data/PortableFix-SelfSigned.cer`, not
`.gitignore`d — confirmed present in the working tree and not excluded by
`.gitignore`'s `Data/SHA256SUMS` / `Data/settings.json` entries).

**What happens to already-distributed copies when the cert expires — verified clean.**
`README.md:95` signs with an RFC3161 timestamp server:
```
signtool sign /fd SHA256 /a /tr http://timestamp.digicert.com /td SHA256 App\PortableFix.exe
```
Authenticode timestamping means Windows continues to treat a signature as valid
*as of the time it was applied* even after the leaf certificate's own `NotAfter`
passes, as long as the timestamp authority itself remains trusted. This is correct,
standard practice and is already in place — binaries signed before 2029-09-01 are not
retroactively "bricked" by the cert lapsing. This also matches the design doc's
explicit statement that the app's SHA256-based update verification is architecturally
independent of code-signing trust (`docs/superpowers/specs/2026-09-02-autoupdate-design.md:355-357`,
"SHA256 overenie je nezávislé od code-signing dôveryhodnosti") — confirmed against the
actual `updater.py` code in section 4.2 above: nothing in the update-check/download/
verify path touches Authenticode signatures at all, so **cert expiry has zero
functional effect on whether auto-update continues to work.**

**What is genuinely a gap: cert *rotation*, not expiry per se.** README's trust-import
instructions (`README.md:87-91`) tell a technician to import today's specific `.cer`
(today's specific public key/thumbprint) into their machine's trusted-root and
trusted-publisher stores. When this cert is eventually replaced — whether because it
expired in 2029 or was rotated earlier for any other reason — a **new** self-signed
cert will necessarily have a different key pair and thumbprint, even with the same
subject name (`CN=PortableFix Self-Signed`). Any technician who did the manual
`Import-Certificate` trust step against the *old* thumbprint gets no benefit from it
against binaries signed with the *new* cert — a future auto-updated `.exe` will read as
untrusted/unrecognized to their machine again, indistinguishable from a first-ever
install. Nothing in the repo documents this rotation-breaks-trust consequence or
provides a plan for it (re-notify technicians to re-import, publish rotation ahead of
time, etc.).

**Severity: MEDIUM.** **Fix:** add the expiry date and a short "cert rotation" note to
README next to the existing trust-import instructions — at minimum, record `NotAfter:
2029-09-01` in the repo (a comment in README or a small `Data/CERT_INFO.txt` next to
the `.cer` would both work) so a future maintainer isn't left re-deriving it from a
binary certificate file the way this research had to.

---

## Verified clean

- `get_base_dir()` (`portablefix/paths.py:6-9`) is fully drive-letter-agnostic in
  frozen mode — derives purely from `sys.executable`, no hardcoded letter anywhere,
  and `tests/test_paths.py:12-16` proves the relative-resolution logic explicitly.
- `--onefile`'s `sys._MEIPASS` temp-extraction path is never referenced by app code
  (`grep -rn "_MEIPASS" portablefix/` — zero hits in source); `sys.executable` in a
  frozen onefile build correctly still points to the real on-disk `.exe`, matching the
  design doc's explicit claim.
- The update-swap's `current_exe = Path(sys.executable)`
  (`portablefix/gui/main_window.py:498`) is drive-letter-safe by the same reasoning.
- `resolve_writable_base_dir()` (`portablefix/paths.py:12-22`) correctly probes and
  falls back to `%TEMP%\PortableFix` on any `OSError`, independent of drive letter;
  covered by `tests/test_paths.py:19-34`.
- RFC3161 timestamping is already correctly used at signing time (`README.md:95`),
  so already-distributed signed binaries remain validly signed after the leaf cert's
  own expiry — the one piece of cert-lifecycle handling that's already right.
- Auto-update's SHA256 verification is architecturally decoupled from code-signing
  trust, exactly as the design doc states and as the actual `updater.py` code
  confirms — cert expiry/rotation cannot break the update mechanism itself, only the
  Windows-trust/SmartScreen experience around it.
- `Data/SHA256SUMS` coordination for the swapped `.exe` is correctly handled in the
  common case: the generated swap script recomputes the hash and rewrites the
  `App/PortableFix.exe` line post-swap (`portablefix/updater.py:109-126`), avoiding
  the false-positive integrity warning that would otherwise fire on the next launch
  (see section 4.4 for the narrow split-writability exception).
- `scripts/generate_sha256sums.py` correctly writes POSIX-style forward-slash relative
  paths (`file_path.relative_to(base_dir).as_posix()`, line 23), matching what the
  swap script's regex (`'App/PortableFix\.exe$'`) expects — no backslash/forward-slash
  mismatch bug.

---

## Files read

- `docs/superpowers/specs/2026-09-02-autoupdate-design.md`
- `docs/superpowers/plans/2026-09-02-autoupdate.md`
- `scripts/build.ps1`
- `scripts/generate_sha256sums.py`
- `portablefix/paths.py`
- `portablefix/integrity.py`
- `portablefix/updater.py`
- `portablefix/gui/main_window.py` (update banner/flow sections)
- `portablefix/elevation.py`
- `portablefix/executor.py` (subprocess spawn section)
- `portablefix/restore_point.py`
- `main.py`
- `README.md`
- `.gitignore`
- `requirements.txt`
- `requirements-dev.txt`
- `pyproject.toml`
- `tests/test_paths.py`
- `Data/PortableFix-SelfSigned.cer` (binary inspection via `X509Certificate2`)
- Git history: `git show 1169904 -- README.md`, `git show bb00ec7 --stat`, `git log`
  for `scripts/build.ps1` and `README.md`
