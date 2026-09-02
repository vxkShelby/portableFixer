# Whole-App Error Resilience Audit — Startup, Module Loading, Executor, Elevation, Settings, Auto-Update

Scope: `main.py`, `portablefix/module_engine.py`, `portablefix/paths.py`, `portablefix/integrity.py`,
`portablefix/executor.py`, `portablefix/elevation.py`, `portablefix/settings.py`, `portablefix/updater.py`,
plus `portablefix/gui/main_window.py`, `portablefix/audit_log.py`, `portablefix/report.py`,
`portablefix/undo.py`, `portablefix/restore_point.py`, `portablefix/i18n.py` as needed to trace call sites
and exception propagation. Cross-cutting Python-level robustness only — per-module PowerShell command
correctness was already covered by the earlier M01–M13 catalog audits (`docs/research/research-*-audit.md`)
and is out of scope here. Read-only research; no files modified.

---

## Severity summary

- **CRITICAL:** 3
  1. One malformed/corrupted `Modules/*/actions.yaml` file prevents the **entire app** from starting — no per-module isolation exists.
  2. `powershell.exe` missing from PATH hangs the action executor **silently and forever** — no error, no timeout escape, indistinguishable from a frozen app.
  3. USB drive removed / power lost during the auto-update file swap can leave `App/PortableFix.exe` **deleted with nothing in its place**, after the Python process has already quit — zero recovery, zero error message.
- **HIGH:** 3
  4. Every on-disk write issued from a GUI-thread slot during a batch (audit log, undo script, HTML/JSON report) is completely unguarded — an `OSError` (disk full, USB unplugged) silently truncates that slot's execution with no dialog, no crash, just a stuck UI.
  5. `Data/SHA256SUMS` being unreadable (not just missing) crashes the whole app at startup — the self-integrity *warning* feature becomes a single point of total failure.
  6. A successful self-update can leave a **permanent false-positive tamper warning** on every future launch if the swap script's SHA256SUMS rewrite silently fails.
- **MEDIUM:** 6
  7. `Test-Path $old` in the swap script's "did it work" check is necessary but not sufficient — a locked/still-running old exe can make a **failed** swap look like a success and silently relaunch the un-updated build.
  8. Swap-script paths are interpolated into PowerShell *double-quoted* strings without escaping `$` — a legal NTFS path character that PowerShell treats as variable interpolation.
  9. No post-swap status is ever recorded anywhere; the detached, fire-and-forget swap script gives the app no way, even in principle, to report "your last update failed" on next launch.
  10. `resolve_writable_base_dir`'s `%TEMP%` fallback has no exception handling of its own if `%TEMP%` is also unreachable.
  11. `main.py`'s one broad `try/except` spans startup *and* the entire session *and* final `save_settings()` — a shutdown-time write failure is reported to the user as **"Startup failed"**.
  12. A missing/empty `Modules` folder (bad deployment, partial copy) degrades to a silently empty app window with zero explanation.
- **LOW:** 1
  13. PowerShell execution-policy lockout via GPO fails visibly (not a hang) but gets no friendly interpretation, just raw policy-violation text in the output pane — consistent with the general "no output interpretation" pattern already flagged in the M08 audit.

Plus one **cleanup gap** folded into MEDIUM #9's area: an interrupted update **download** (not swap) leaves a partial `PortableFix.new.exe` undeleted in `%TEMP%\PortableFixUpdate\`.

---

## 1. Missing/broken PowerShell

### 1.1 — CRITICAL: `powershell.exe` not on PATH hangs the executor forever, silently

`portablefix/executor.py:107-121`:

```python
def run(self) -> None:
    if self._plan.mode == "dry_run":
        ...
        return

    process = subprocess.Popen(
        self._plan.argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    self._process = process
    watchdog = threading.Thread(target=self._watchdog, daemon=True)
    watchdog.start()
    try:
        ...
```

The `subprocess.Popen(...)` call itself sits **outside** the `try` block that starts two lines later at
`executor.py:124`. `ActionRunner.run()` is the body of a `QThread` — it executes on a background OS
thread, not the Qt/GUI thread. If `powershell` is not found on PATH (a real scenario on a "severely
broken machine" per the audit brief — corrupted PATH env var, minimal/repair OS image, restricted
environment), `subprocess.Popen` raises `FileNotFoundError`, which propagates out of `run()` uncaught.
Python's default `threading.excepthook` prints a traceback to stderr and the thread simply dies — for a
windowed GUI exe launched from Explorer/USB with no attached console, that stderr output is invisible to
the technician.

Critically, **neither `output_line` nor `finished_with_code` is ever emitted** in this path. The GUI
already set the action row to `"running"` (`main_window.py:670`) and disabled the run button
(`main_window.py:579`) before starting the thread, and nothing ever un-does that: `_on_action_finished`
is only reachable via the `finished_with_code` signal, which never fires. The app is left permanently in
"batch running" state — no error dialog, no timeout message, no way to tell this apart from a genuine
hang. This is exactly the scenario the audit brief calls out ("does the app fail with a clear message or
crash/hang opaquely?") — here it hangs opaquely, with total silence.

**Contrast — the fix pattern already exists elsewhere in this codebase.** `portablefix/restore_point.py:14-18`:

```python
try:
    result = subprocess.run(POWERSHELL_PREFIX + [command], capture_output=True, timeout=120)
    return result.returncode == 0
except (subprocess.SubprocessError, OSError):
    return False
```

`create_restore_point` catches `OSError` (which `FileNotFoundError` subclasses) around its own
PowerShell invocation and degrades to `False` → surfaced to the user as the existing
`restore_point_failed_confirm` dialog ("Could not create a System Restore Point... Continue anyway?").
The identical failure mode against the identical `powershell` binary is handled gracefully in one call
site and not at all in the other.

**Suggested fix:** wrap `subprocess.Popen` in `executor.py:115-120` in the same `try/except OSError` used
in `restore_point.py`, and on failure emit `output_line` with a translated "PowerShell was not found on
this system" message plus `finished_with_code.emit(-1)` (or a new dedicated exit code), so the existing
`_on_action_finished` → `_run_next()` chain still advances instead of stalling.

### 1.2 — Same missing-PowerShell gap in the auto-updater's own swap invocation

`portablefix/updater.py:131-140` (`apply_update`) calls `subprocess.Popen(["powershell", ...])` directly
from `_on_update_download_finished` (`main_window.py:512`), a **GUI-thread** slot, with no
try/except around it either. If `powershell` is missing, this raises synchronously inside a button-click
slot right before `self._quit_app()` on the next line (`main_window.py:513`) — the swap process never
launches, yet the app quits anyway (`_quit_app()` is unconditional and does not check whether
`apply_update()` actually started anything). Net effect: the update silently never applies and the app
just closes, with no indication anything went wrong. Same root cause as 1.1, different call site.

### 1.3 — LOW: Execution-policy GPO lockout is not a hang, but gets no interpretation

If a GPO forces "Turn on Script Execution" off (which overrides the app's own `-ExecutionPolicy Bypass`
flag), `powershell.exe` itself still launches fine — it just refuses to run the command and exits
non-zero with a policy-violation message. That message flows through the normal
`stdout`→`output_line`→audit-log pipeline like any other command failure (`executor.py:127-133`), so this
case is **not** a hang — it fails visibly. It gets zero special-casing/friendly interpretation, but that
is the same "raw unfiltered PowerShell error text" pattern already flagged generally in
`docs/research/research-security-audit.md` section 1/3.3, not a new mechanism gap. Listed here only to
confirm it was checked and does not compound into a hang like 1.1.

---

## 2. Corrupted/malformed YAML

### 2.1 — CRITICAL: One bad `Modules/*/actions.yaml` file kills the entire app, not just that module

`portablefix/module_engine.py:51-52`:

```python
def load_all_modules(modules_dir: Path) -> list[ModuleDef]:
    return [load_module(p) for p in sorted(modules_dir.glob("*/actions.yaml"))]
```

`load_module` (`module_engine.py:14-48`) raises `ModuleLoadError` for a missing `module_id`, an unknown
`category`/`risk` value, or an action missing a required field — and raises a raw `yaml.YAMLError`
(unhandled) for genuinely malformed YAML syntax (bad indentation, unclosed quotes/brackets, tab
characters, etc.). `load_all_modules` has **zero isolation**: a list comprehension with no per-item
try/except means the *first* file that fails to parse aborts the whole list.

The one production call site is `portablefix/gui/main_window.py:75`:

```python
self.modules: list[ModuleDef] = load_all_modules(assets_dir / "Modules")
```

called unconditionally inside `MainWindow.__init__`, itself constructed inside `main.py`'s single
try/except (`main.py:44-56`). The repo currently ships 12 module catalogs
(`Modules/m01_diagnostics` … `Modules/m13_debloat`). Corrupting **any one** of the 12 — a bad USB write,
a technician accidentally saving with the wrong encoding, malware, an interrupted copy — means the
technician cannot launch the tool **at all**, not even to run the 11 unaffected modules, and gets a
generic `QMessageBox.critical(None, "PortableFix", f"Startup failed:\n{exc}")` (`main.py:56`) whose text
is either a terse `ModuleLoadError` string or a multi-line raw PyYAML parser dump, neither of which tells
a technician which file to fix or that the fix is "delete/restore one YAML file."

This is the single most consequential finding in this audit given the deployment model: a portable tool
run from USB drives that get plugged into "genuinely broken machines" is exactly the environment where a
flaky write, an antivirus quarantine action, or a half-finished `git`/file-sync operation on the YAML
catalogs is most likely — and the current design turns any one such incident into a total outage of the
whole tool, not a degraded one.

**Suggested fix:** wrap each `load_module(p)` call in `load_all_modules` in its own try/except, skip and
collect the failing path + error rather than propagating, and return `(modules, load_errors)` (or log the
errors and let `main.py` show a single non-fatal warning listing which module(s) failed to load) — mirrors
exactly how `check_integrity` already tolerates missing/mismatched files without refusing to run.

---

## 3. Disk space exhaustion mid-operation

### 3.1 — HIGH: Every batch-time disk write is unguarded, and PySide6 does not surface the failure

Four write call sites, none wrapped in try/except, all reachable from GUI-thread slots during a running
batch:

- `portablefix/gui/main_window.py:619` — `undo.create_undo_script(...)`, before a DESTRUCTIVE/REPAIR/SECURITY action
- `portablefix/gui/main_window.py:687` — `append_entry(self.state_dir, self.run_id, entry)`, after **every** action
- `portablefix/gui/main_window.py:696` — `undo.create_undo_script(...)` again, after each successful undoable action
- `portablefix/gui/main_window.py:592-599` — `report.generate_report(...)`, at batch end

Underneath, `portablefix/audit_log.py:39-43` (`append_entry`), `portablefix/undo.py:18-21`
(`create_undo_script`), and `portablefix/report.py:187-188` (`html_path.write_text` /
`json_path.write_text`) all perform a bare `.write_text(...)` / `.open("a").write(...)` with no
try/except of their own. If the USB drive (or the `%TEMP%` fallback from `paths.py`) runs out of space or
is physically disconnected at the moment any of these fires, an `OSError` (`ENOSPC` or similar) is raised.

There is no application-level `sys.excepthook` anywhere in the codebase (confirmed by search — no hits
for `excepthook` in `portablefix/`), so PySide6's default unhandled-slot-exception behavior applies
unmitigated: the traceback goes to stderr (invisible for a windowed exe launched off a USB drive) and
execution of that slot stops at the exception point. Concretely for `_on_action_finished`
(`main_window.py:682-697`), whose **last line is `self._run_next()`**: if `append_entry` on line 687
throws, `_run_next()` never runs, the queue never advances, `run_button`/`cancel_button` stay in
"batch active" state forever, and no batch summary or report is ever produced for that run — indistin­guishable
from a hang, with no dialog telling the technician what happened. This is the concrete mechanism behind
the audit brief's "interrupted/partial state" question: the `Logs/{run_id}.jsonl` audit log is left with
whatever entries were successfully appended before the failure — genuinely partial, not falsely
"complete" (no closing/summary marker exists to spoof completeness) — but the app gives the technician no
signal that the run stopped early rather than finished.

**Suggested fix:** wrap the four call sites above (or, more centrally, `append_entry`/`create_undo_script`/
`generate_report` themselves) in try/except `OSError`, surface a translated error via the existing
status-bar/dialog mechanism, and still call `self._run_next()` (or a "batch aborted" path) so the queue
doesn't stall silently.

### 3.2 — Verified partially clean: update download failure is caught, but debris is left behind

`portablefix/updater.py:68-79` (`download_update`):

```python
def download_update(info: UpdateInfo, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    exe_path = dest_dir / "PortableFix.new.exe"
    urllib.request.urlretrieve(info.download_url, exe_path)
    if info.sha256_url:
        ...
        if actual.lower() != expected:
            exe_path.unlink(missing_ok=True)
            raise UpdateVerificationError(...)
    return exe_path
```

`urllib.request.urlretrieve` at line 71 has no try/except of its own, but the *caller*,
`UpdateDownloadRunner.run()` (`updater.py:165-170`), does catch `Exception` broadly and reports failure
via `download_finished.emit(None, str(exc))`, which `main_window.py:495-497` turns into the translated
`update_download_failed` message — this part degrades gracefully, no crash, no hang. However, only the
SHA256-mismatch branch (line 77) deletes the partial file; a disk-full or connection-drop failure during
`urlretrieve` itself leaves a partially-written `PortableFix.new.exe` sitting in
`%TEMP%\PortableFixUpdate\` with nothing to clean it up — wasted space on a machine where disk space may
already be the reason the technician is there. Low-impact (next attempt overwrites the same path) but
worth a one-line `except: exe_path.unlink(missing_ok=True); raise` around the `urlretrieve` call.

---

## 4. Non-English Windows / non-ASCII paths

### 4.1 — Verified clean: command output encoding already fixed at the executor level

`portablefix/executor.py:29-33`:

```python
def build_execution_plan(command: str, dry_run: bool) -> ExecutionPlan:
    if dry_run:
        return ExecutionPlan(mode="dry_run", display_command=command, argv=None)
    utf8_command = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {command}"
    return ExecutionPlan(mode="run", display_command=command, argv=POWERSHELL_PREFIX + [utf8_command])
```

Every real (non-dry-run) command is prefixed with `[Console]::OutputEncoding=[Text.Encoding]::UTF8;`
before execution, and `_iter_output_segments` (`executor.py:40-65`) decodes the raw byte stream with
`.decode("utf-8", errors="replace")`. This is the correct fix for the classic Windows PowerShell 5.1
problem where console output otherwise defaults to the system ANSI codepage (e.g. `cp1250` on Slovak
Windows), which would silently mangle diacritics (á, č, š, ž, …) in both command output and any
non-ASCII text a cmdlet echoes back. Confirmed present and applied uniformly to every action, not
opt-in per module.

### 4.2 — Verified clean (already fixed this session): swap-script file encoding

`portablefix/updater.py:135`: `script_path.write_text(script_text, encoding="utf-8-sig")`. Writing the
generated `.ps1` with a UTF-8 BOM means `powershell.exe -File` reliably detects UTF-8 regardless of the
system's ANSI codepage, instead of misreading any non-ASCII characters embedded in interpolated paths
(a non-English Windows username, a non-ASCII USB drive label, etc.) via the wrong codepage. Confirmed
present in the current `git show 1169904` diff and in the file as read — this was one of the two
previously-identified-and-fixed bugs; verified in place.

### 4.3 — MEDIUM (8, restated with full detail): un-escaped `$` in double-quoted PowerShell path interpolation

`portablefix/updater.py:94-128` (`build_swap_script`) builds the script by interpolating raw Python path
strings directly into PowerShell **double-quoted** literals, e.g. line 105:

```python
f'Move-Item -Path "{old}" -Destination "{old}.old" -Force\n'
```

PowerShell performs variable interpolation *inside* double-quoted strings. `$` is a legal character in
Windows/NTFS file and directory names (e.g. a username, a folder created by some enterprise imaging tool,
an antivirus quarantine path) but is *not* legal inside PowerShell paths themselves. If any component of
`old`/`new`/`sums` (all derived from `sys.executable`'s real path — the actual USB drive path a technician
chose, plus `tempfile.gettempdir()`) contains a literal `$`, PowerShell will attempt to interpret the
following characters as a variable reference (`$Doe` in `C:\Users\Jane$Doe\...`) — most commonly
substituting an empty string for an undefined variable, silently truncating/corrupting the path passed to
`Move-Item`. Combined with `$ErrorActionPreference = "SilentlyContinue"` (line 99) and the fact that this
whole script runs fully detached with no verification (see section 5.3), any resulting failure or
wrong-path move is invisible. Narrow probability (USB drive paths chosen by technicians are usually
simple), but a straightforward fix: use PowerShell single-quoted strings (which don't interpolate) with
`''`-escaping of any embedded single quote, or route the paths through script parameters
(`-Old "..." -New "..."` with `param()`) instead of string-formatting them into the script body.

### 4.4 — Not found: no locale-dependent output *parsing* in the core Python files

The audit specifically asked whether other places assume English-locale PowerShell output (status
strings, dates). Across `executor.py`, `restore_point.py`, `undo.py`, `report.py`, and `audit_log.py`,
command stdout is only ever captured, decoded, displayed verbatim, and hashed — never parsed/matched
against expected English substrings anywhere in these core files. (Any such parsing, if present, would
live in individual `Modules/*/actions.yaml` catalogs, which is explicitly out of scope per the earlier
per-module audits.)

---

## 5. Interrupted/partial state (crash, USB yank mid-batch)

### 5.1 — Restated from 3.1: audit log is genuinely partial, never falsely "complete"

Because `report.generate_report` only ever runs once, at the very end of `_run_next()` when the queue is
empty (`main_window.py:583-601`), an interruption mid-batch (crash, unguarded `OSError` per 3.1, or a
literal USB yank) means **no HTML/JSON report is ever written for that run** — there is no
half-written or falsely-successful report artifact. The `Logs/{run_id}.jsonl` audit log itself
(`audit_log.py:39-43`) is appended one line per completed action, so it stops exactly where the
interruption happened — genuinely partial and readable as such (a technician who opens the `.jsonl` sees
exactly which actions completed and no more). This part of the design is sound: **nothing here looks
falsely complete**. The gap is entirely on the *notification* side (3.1): the app itself doesn't tell the
technician the run was cut short, it just goes quiet.

### 5.2 — CRITICAL: auto-update swap interrupted by USB removal/power loss can delete the app with no recovery

`portablefix/updater.py:94-128`, full script logic:

```
for ($i = 0; $i -lt 30; $i++) { if (-not (Get-Process -Id <pid> ...)) { break }; Start-Sleep -Milliseconds 500 }
Start-Sleep -Milliseconds 300
Move-Item -Path "<old>" -Destination "<old>.old" -Force
Move-Item -Path "<new>" -Destination "<old>" -Force
if (Test-Path "<old>") { Remove-Item -Path "<old>.old" -Force -EA SilentlyContinue }
else { Move-Item -Path "<old>.old" -Destination "<old>" -Force }
try { ...rewrite SHA256SUMS... } catch {}
Start-Process -FilePath "<old>"
```

`apply_update()` (`updater.py:131-140`) spawns this as a **fully detached** process
(`subprocess.DETACHED_PROCESS`) and returns immediately; the caller,
`_on_update_download_finished` (`main_window.py:511-513`), calls `self._quit_app()`
**unconditionally on the very next line**, with no wait for and no signal back from the swap script.
The Python process — the only thing that could have shown an error dialog — is gone before the swap
even starts running against the (already-downloaded, already-verified) new exe.

If the USB drive is physically removed or power is lost in the window between the **first** `Move-Item`
(old → old.old) succeeding and the **second** `Move-Item` (new → old) completing — both paths point at
the USB drive itself, since `old_exe`/`new_exe` derive from `sys.executable`, the app's real path on the
drive the technician is servicing — the result is: `App/PortableFix.exe` no longer exists at all (only
`App/PortableFix.exe.old` does), the new exe download in `%TEMP%` may or may not have been consumed, and
the recently-added recovery branch (`else { Move-Item -Path "<old>.old" -Destination "<old>" -Force }`)
**also targets the now-unreachable drive** and fails silently under the same
`$ErrorActionPreference = SilentlyContinue` that swallows every other error in this script — so the
"restore backup on failure" fix added this session does not help against the specific case of the drive
disappearing mid-swap; it only helps when the *second* move fails while the drive itself is still present
and writable (e.g., a file lock). `Start-Process -FilePath "<old>"` at the end then also fails silently
(no file to start), and the detached script simply exits. Net result: the technician's USB drive now has
no working `PortableFix.exe`, the app that could explain this has already quit, and there is no log, no
marker file, nothing — they discover it only the next time they try to launch the tool and it's gone.

This is precisely the scenario the audit brief calls out by name ("power loss during the Move-Item swap
itself") and rates CRITICAL given the deployment model: technicians who yank USB drives out of
"genuinely broken machines" impatiently are an explicitly named realistic behavior, and the current
design has no defense against it happening during this specific ~1-second window.

**Suggested fix (two independent, complementary changes):** (a) don't quit the Python app until the swap
script signals success — e.g., have the script write a one-line status/marker file
(`Data/last_update_result.txt` or similar) as its very last, unconditional step, and have the *next*
app launch check for and surface that file's content ("last update failed: <reason>, your previous
version has been restored" / "...could not be restored, please re-download PortableFix"); (b) verify the
drive is still present and writable (`Test-Path` on the drive root) before attempting either `Move-Item`,
and if not, abort the whole swap (not just the second half) rather than leaving it half-done.

### 5.3 — MEDIUM (7, restated with full detail): "swap succeeded" check has a false-positive case

The `if (Test-Path "<old>") { Remove-Item "<old>.old" ... } else { restore }` check added this session
(confirmed present, `updater.py:107-108`) verifies *a file exists* at the original path — it does not
verify that file is the *new* one. Consider: the 30×500ms wait loop for the old process to exit
(`updater.py:100-103`) times out after 15 seconds without the process having actually exited (e.g. a
lingering `QThread` delaying shutdown, or the OS being slow to release the file handle on a heavily
loaded/dying machine) — genuinely plausible on "a machine about to fail" per the audit's own framing.
The first `Move-Item` (old → old.old) then fails because the file is still locked, silently swallowed by
`$ErrorActionPreference = SilentlyContinue`. The **old exe was never moved away** — it's still sitting
exactly where it always was. `Test-Path "<old>"` is therefore still `True`, not because the update
succeeded, but because it never started. The script proceeds to (no-op) remove a `.old` backup that
doesn't exist, then `Start-Process -FilePath "<old>"` relaunches the **original, un-updated** exe. The
technician sees the app "restart" and has no way to tell this apart from a genuinely successful update —
no version-mismatch check, no marker, nothing (same root gap as 5.2/9). A more robust check would compare
a hash or file size of the post-swap `<old>` against the already-known-good downloaded file, not merely
test for its existence.

### 5.4 — HIGH (6, restated with full detail): self-update success can produce a permanent false tamper warning

`portablefix/updater.py:109-126`, inside the swap script:

```
try {
    $hash = (Get-FileHash -Path "<old>" -Algorithm SHA256).Hash.ToLower()
    if (Test-Path "<sums>") {
        ...
        Set-Content -Path "<sums>" -Value $newLines -Encoding ASCII
    }
} catch {}
```

The entire SHA256SUMS-rewrite step (meant to keep the self-integrity manifest in sync with the
just-installed new exe) is wrapped in a `try { } catch { }` that silently discards **any** failure —
`Get-FileHash` failing, `Data/SHA256SUMS` being temporarily locked (e.g. antivirus scanning the
freshly-modified file), a disk-full `Set-Content`, or the manifest file having been corrupted for an
unrelated reason. If this step fails, the exe itself has already been successfully swapped to the new
version (that part is *not* inside this try block), but `Data/SHA256SUMS` still records the *old* exe's
hash.

On every subsequent launch, `main.py:35-41` runs `check_integrity(raw_base_dir)`
(`portablefix/integrity.py:27-37`), which will now compute the *new* exe's hash, find it doesn't match
the *stale* recorded hash for `App/PortableFix.exe`, and show the `integrity_warning` dialog — worded as
"Integrity check failed - files were modified" (`i18n.py:14`/`58`) — implying tampering, on every single
launch from then on, for what was actually a completely successful, legitimate self-update. This directly
undermines the self-integrity feature's credibility (a technician who sees this warning repeatedly after
using the built-in updater will reasonably start ignoring integrity warnings altogether, defeating their
purpose for the case where the manifest mismatch reflects *actual* tampering).

**Suggested fix:** make the SHA256SUMS rewrite a required, verified step rather than a best-effort one —
have the swap script exit with a distinguishable status if it fails, and have the app detect "exe version
changed but SHA256SUMS wasn't updated" specifically (e.g. compare `version.py`'s `APP_VERSION` against
what's recorded) and either re-run the rewrite on next launch or word the warning differently for this
specific, benign case.

---

## 6. `settings.json` corruption

### 6.1 — Verified clean: already falls back to defaults correctly

`portablefix/settings.py:18-25`:

```python
def load_settings(base_dir: Path) -> Settings:
    path = settings_path(base_dir)
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return Settings()
    return Settings(...)
```

A malformed/truncated/binary-garbage `settings.json` left behind by a previous crash is handled
correctly: `json.JSONDecodeError`, `OSError` (permission/IO issues), and `UnicodeDecodeError`
(non-UTF-8 content) are all caught and fall back to `Settings()` defaults. The app starts normally
with default language/dry-run settings rather than failing. No corner missed here — this is the pattern
the rest of the codebase (module loading, integrity check) should be following but mostly isn't.

### 6.2 — MEDIUM (11, restated): shutdown-time settings write failure is mislabeled as a startup failure

`portablefix/settings.py:32-35` (`save_settings`) itself has no try/except (unlike `load_settings`), and
its only call site, `main.py:53`, sits inside the *same* outer try/except that also covers
`MainWindow(...)` construction and `app.exec()`:

```python
try:
    ...
    window = MainWindow(...)
    window.show()
    exit_code = app.exec()
    save_settings(base_dir, settings)          # line 53 — unguarded write
    return exit_code
except Exception as exc:
    QMessageBox.critical(None, "PortableFix", f"Startup failed:\n{exc}")
    return 1
```

If the USB drive is removed right as the technician closes the app (a completely plausible end-of-session
action — "I'm done, pull the drive"), `save_settings`'s `path.write_text(...)` raises `OSError` *after* a
fully successful multi-hour session, and the user is shown **"Startup failed:\n[Errno ...]"** — a message
that is factually wrong about what happened and when, and could make a technician think the whole run
(including whatever repairs/reports it produced) is somehow invalid, when in fact only the trivial
language/dry-run preference failed to persist. Low real-world consequence (settings are the least
important state to lose), but the mislabeled error message is a real, easily-triggered UX bug given how
naturally "pull the drive as soon as I'm done" fits this tool's actual usage pattern.

**Suggested fix:** move `save_settings` into its own try/except (or just let it fail silently — losing a
language/dry-run preference is harmless) rather than sharing the "Startup failed" branch with genuine
startup errors.

---

## 7. `Data/SHA256SUMS` missing vs. corrupted

### 7.1 — Verified clean: missing file

`portablefix/integrity.py:27-30`:

```python
def check_integrity(base_dir: Path) -> list[str]:
    sums_path = base_dir / "Data" / "SHA256SUMS"
    if not sums_path.exists():
        return []
```

A missing manifest is handled correctly — the check is simply skipped, no warning shown, app proceeds.
This is the right behavior for, e.g., a from-source dev checkout that never ran the SHA256SUMS generator.

### 7.2 — HIGH: an unreadable/corrupted manifest crashes the entire app, not just the integrity check

If `Data/SHA256SUMS` *exists* but is not valid UTF-8 (binary garbage from an interrupted USB write,
malware overwrite, or a technician opening/re-saving it in the wrong editor/encoding), the failure
happens one call deeper, in `portablefix/integrity.py:15` (`parse_sha256sums`):

```python
def parse_sha256sums(sums_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
```

`.read_text(encoding="utf-8")` has no error handling at all — a non-UTF-8 byte sequence raises
`UnicodeDecodeError`, uncaught, propagating straight through `check_integrity` back to `main.py:35`,
which is inside `main()`'s single outer try/except. The result: `QMessageBox.critical(None, "PortableFix",
"Startup failed:\n'utf-8' codec can't decode byte ...")`, and the app never opens **at all** — for a
failure in what is supposed to be an optional, best-effort tamper-detection feature. The same is true for
a transient `OSError` if the file becomes unreadable mid-read (e.g. the USB connection drops for a moment
during the read — plausible on the "flaky USB connections" scenario named in the audit brief) or is
deleted between the `.exists()` check on line 29 and the read on line 15 (TOCTOU window).

Individual malformed *lines* inside an otherwise-readable file are handled fine —
`parse_sha256sums:19-21` skips any line that doesn't split into exactly two whitespace-separated tokens —
so the gap is specifically "manifest exists but the file itself can't be decoded/read," not "manifest has
bad content." A security-relevant self-check should never be able to take down the whole application;
right now it can.

**Suggested fix:** wrap `check_integrity`'s body (or specifically the `sums_path.read_text(...)` call) in
try/except `(OSError, UnicodeDecodeError)`, treat it the same as "missing" (return `[]`, or better, return
a distinguishable "integrity check itself failed to run" signal so the app can still warn about *that*
without refusing to start).

---

## 8. Deployment/path edge cases

### 8.1 — MEDIUM: `%TEMP%` fallback has no fallback of its own

`portablefix/paths.py:12-22`:

```python
def resolve_writable_base_dir(base_dir: Path) -> tuple[Path, bool]:
    probe = base_dir / ".write_test"
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base_dir, False
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "PortableFix"
        fallback.mkdir(parents=True, exist_ok=True)   # <-- unguarded
        return fallback, True
```

The USB-unwritable path is caught (`except OSError`), correctly falling back to `%TEMP%\PortableFix` —
this is the existing, working fallback the audit brief references. But the fallback's own
`fallback.mkdir(...)` call is not itself guarded. On a machine broken enough that `%TEMP%` is *also*
unwritable/unreachable (a redirected/misconfigured `TEMP` env var pointing at a dead network share or a
permissions-locked profile — an edge case, but exactly the kind of "weird" the audit brief calls out),
this raises `OSError` uncaught, propagating to `main.py`'s outer try/except and producing the generic
"Startup failed" dialog — not a crash, but a dead end with no actionable message (e.g. no suggestion to
check/reset the `TEMP` environment variable). Low likelihood, low-effort fix: catch this second `OSError`
too and either try one more fallback (e.g. the Desktop, or a hardcoded `C:\PortableFix`) or at least
produce a specific, actionable error message rather than a bare exception dump.

### 8.2 — MEDIUM: missing/empty `Modules` folder degrades to a silent, unexplained empty app

`portablefix/module_engine.py:52`: `modules_dir.glob("*/actions.yaml")` on a nonexistent or empty
directory simply yields no matches — no exception, `load_all_modules` returns `[]`. Every consumer of
`self.modules` in `main_window.py` (lines 167, 263, 527, 595, plus the `_build_ui` category/action
rendering loops) just iterates an empty list. There is no check anywhere for `if not self.modules: ...`
(confirmed by search — only the assignment at line 75 and iteration sites exist). A bad deployment (a
technician copies only `App/PortableFix.exe` off the USB without the sibling `Modules/` folder, or an
interrupted file copy drops the whole directory) produces a window that opens fine, shows no error, and
simply has nothing in it — no categories, no actions, no explanation. This is the mirror-image failure
mode of 2.1 (total-failure-on-one-bad-file) and arguably just as confusing in the other direction
(silent-success-with-nothing-to-do). Suggested fix: if `load_all_modules` returns an empty list, show a
warning dialog analogous to the existing `integrity_warning`/`fallback_banner` pattern already used
elsewhere in `main.py`.

---

## Verified clean (checked, no issue found)

- **`settings.json` corruption** (`settings.py:18-25`) — catches `JSONDecodeError`/`OSError`/
  `UnicodeDecodeError`, falls back to defaults. Exactly the pattern the rest of the app should follow.
- **Network failure during update *check*** (`updater.py:42-65`, `check_for_update`) — wrapped in a broad
  `except Exception: return None`; no update banner shown, app proceeds normally. No hang, no crash.
- **Network/disk failure during update *download*** (`updater.py:165-170`, `UpdateDownloadRunner.run`) —
  caught, surfaced via the translated `update_download_failed` message (see 3.2 for the one related but
  minor gap: leftover partial file not cleaned up on this path).
- **`restore_point.create_restore_point`** (`restore_point.py:14-18`) — catches
  `(subprocess.SubprocessError, OSError)` around its own PowerShell invocation, so a missing
  `powershell.exe` degrades gracefully here (contrast with 1.1's `executor.py`, which has the identical
  exposure with no handling at all).
- **Non-English/non-ASCII command *output* decoding** (`executor.py:29-33`, `40-65`) — the
  `[Console]::OutputEncoding=UTF8` prefix plus UTF-8 decoding with `errors="replace"` correctly addresses
  the classic Slovak/non-English-Windows console-codepage mangling problem for every real command.
- **Swap-script file encoding** (`updater.py:135`, `utf-8-sig`) — confirmed present; second of the two
  previously-identified-and-fixed critical bugs (`git show 1169904`) is genuinely in place, not reverted.
- **`i18n.translate`** (`i18n.py:93-95`) — defensive on both axes: unknown language falls back to `"sk"`,
  unknown key falls back to returning the key itself. Never raises, safe to call from any error-handling
  path without risking a secondary exception.
- **Individual malformed lines within an otherwise-readable `SHA256SUMS`** (`integrity.py:19-21`) — lines
  that don't split into exactly two tokens are skipped, not fatal. The gap is specifically an unreadable
  *file*, not a malformed *line* (see 7.2).
- **Audit log never looks falsely "complete" on interruption** (see 5.1) — there's no closing/summary
  record written to `Logs/{run_id}.jsonl`, so a truncated log is honestly readable as truncated, not
  spoofed as a finished run.

---

## Files read

- `main.py`
- `portablefix/module_engine.py`
- `portablefix/paths.py`
- `portablefix/integrity.py`
- `portablefix/executor.py`
- `portablefix/elevation.py`
- `portablefix/settings.py`
- `portablefix/updater.py`
- `portablefix/gui/main_window.py`
- `portablefix/audit_log.py`
- `portablefix/report.py`
- `portablefix/undo.py`
- `portablefix/restore_point.py`
- `portablefix/i18n.py`
- `git show 1169904` (the "harden swap script, close update/batch races" commit) — used to confirm the
  two previously-fixed swap-script bugs are genuinely present in the current working tree, not reverted.
