# F2 Cleanup + Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the M02 cleanup module (16 actions, SAFE through DESTRUCTIVE)
with real dry-run previews, a Restore-Point-gated hard confirmation for
DESTRUCTIVE actions, and the M11 reporting module (HTML+JSON per run) to
the F1 skeleton already on `main`.

**Architecture:** Two new pure-logic modules (`restore_point.py`,
`report.py`) follow the existing dependency-free style of `elevation.py`
and `audit_log.py`. `ActionDef`/`module_engine.py` gain one optional field
(`preview_command`) to let dry-run mode show real size/count previews
instead of just echoing the command. `MainWindow` gets its per-action risk
dispatch extended (DESTRUCTIVE gets a Restore Point attempt + a harder
confirmation, on top of F1's existing MODERATE/DESTRUCTIVE gate) and a new
batch-level hook that snapshots free space and generates a report when a
run's action queue drains.

**Tech Stack:** Same as F1 — Python 3.12, PySide6, PyYAML, pytest,
pytest-qt. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-08-31-f2-cleanup-reporting-design.md](../specs/2026-08-31-f2-cleanup-reporting-design.md)

## Global Constraints

(Carried forward from the F1 plan — still binding.)

- PowerShell must always be invoked as: `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <command>` — reuse `portablefix.executor.POWERSHELL_PREFIX`, never redefine it.
- Never hardcode a drive letter; all paths resolve relative to `assets_dir`/`state_dir` already established in F1 (assets = read-only Modules/Data location, state = write-capable Logs/Reports/settings location — these may differ when the USB falls back to `%TEMP%`).
- Risk labels are exactly one of: `SAFE`, `MODERATE`, `DESTRUCTIVE`, `REQUIRES_REBOOT`.
- Dry-run must never execute a destructive command — the `preview_command` mechanism in this plan only ever runs read-only, side-effect-free commands in dry-run mode.
- Prefer `Get-CimInstance`/modern cmdlets over `WMIC` in every bundled command.
- No `undo.ps1` generation in F2 (see spec's "Decisions locked in").
- Restore Point creation is best-effort and must never raise or block the app if it fails.

---

## File Structure

```
portablefix/
├── models.py               # MODIFY: ActionDef gains preview_command
├── module_engine.py         # MODIFY: parse preview_command
├── restore_point.py         # NEW
├── report.py                 # NEW
└── gui/
    └── main_window.py        # MODIFY: preview dispatch, DESTRUCTIVE gate, report trigger
Modules/
└── m02_cleanup/
    └── actions.yaml           # NEW: 16 actions
portablefix/i18n.py            # MODIFY: 2 new keys
tests/
├── test_module_engine.py     # MODIFY: preview_command coverage
├── test_m02_catalog.py        # NEW
├── test_restore_point.py     # NEW
├── test_report.py             # NEW
└── test_gui_main_window.py   # MODIFY: preview dispatch, destructive gate, report trigger tests
```

---

### Task 1: `ActionDef`/`module_engine` — optional `preview_command`

**Files:**
- Modify: `portablefix/models.py:19-20` (insert new field)
- Modify: `portablefix/module_engine.py:36-38` (pass the new field through)
- Modify: `tests/test_module_engine.py` (add coverage)

**Interfaces:**
- Consumes: nothing new
- Produces: `ActionDef.preview_command: str | None` (default `None`), read by `load_module`/`load_all_modules` from an optional `preview_command:` YAML key

- [ ] **Step 1: Write the failing test**

Add to `tests/test_module_engine.py`:

```python
def test_load_module_with_preview_command(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(
        "module_id: m_test\n"
        "actions:\n"
        "  - id: a1\n"
        "    label_sk: \"Akcia 1\"\n"
        "    label_en: \"Action 1\"\n"
        "    risk: SAFE\n"
        "    command: \"Remove-Item foo\"\n"
        "    preview_command: \"Write-Output preview\"\n",
        encoding="utf-8",
    )
    module = load_module(yaml_path)
    assert module.actions[0].preview_command == "Write-Output preview"


def test_load_module_without_preview_command_defaults_to_none(tmp_path):
    yaml_path = tmp_path / "actions.yaml"
    yaml_path.write_text(VALID_YAML, encoding="utf-8")
    module = load_module(yaml_path)
    assert module.actions[0].preview_command is None
```

(`VALID_YAML` already exists at the top of `tests/test_module_engine.py` from F1 — it has no `preview_command` key, which is exactly what the second test needs.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_module_engine.py -v`
Expected: `test_load_module_with_preview_command` FAILS with
`AttributeError: 'ActionDef' object has no attribute 'preview_command'`

- [ ] **Step 3: Write minimal implementation**

In `portablefix/models.py`, change:

```python
@dataclass
class ActionDef:
    id: str
    label_sk: str
    label_en: str
    risk: RiskLevel
    command: str
    description_sk: str = ""
    description_en: str = ""
```

to:

```python
@dataclass
class ActionDef:
    id: str
    label_sk: str
    label_en: str
    risk: RiskLevel
    command: str
    description_sk: str = ""
    description_en: str = ""
    preview_command: str | None = None
```

In `portablefix/module_engine.py`, change the `ActionDef(...)` construction:

```python
        actions.append(
            ActionDef(
                id=raw["id"],
                label_sk=raw["label_sk"],
                label_en=raw["label_en"],
                risk=risk,
                command=raw["command"],
                description_sk=raw.get("description_sk", ""),
                description_en=raw.get("description_en", ""),
            )
        )
```

to:

```python
        actions.append(
            ActionDef(
                id=raw["id"],
                label_sk=raw["label_sk"],
                label_en=raw["label_en"],
                risk=risk,
                command=raw["command"],
                description_sk=raw.get("description_sk", ""),
                description_en=raw.get("description_en", ""),
                preview_command=raw.get("preview_command"),
            )
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_module_engine.py -v`
Expected: PASS (8 tests — the 6 from F1 plus these 2)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `py -m pytest tests/ -v`
Expected: PASS (48 tests — 46 currently on `main` plus these 2; note `-v`, not `-q`, per this project's known sandbox quirk with `-q` around the real-subprocess executor test)

- [ ] **Step 6: Commit**

```bash
git add portablefix/models.py portablefix/module_engine.py tests/test_module_engine.py
git commit -m "feat: optional preview_command field on ActionDef"
```

---

### Task 2: M02 cleanup action catalog

**Files:**
- Create: `Modules/m02_cleanup/actions.yaml`
- Test: `tests/test_m02_catalog.py`

**Interfaces:**
- Consumes: `RiskLevel`, `ActionDef.preview_command` (Task 1)
- Produces: 16 loadable actions under `module_id: m02_cleanup`, all with a non-`None` `preview_command`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_m02_catalog.py
from pathlib import Path

from portablefix.models import RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m02_cleanup" / "actions.yaml"


def test_m02_catalog_loads_16_actions_all_with_preview():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m02_cleanup"
    assert len(module.actions) == 16
    assert all(a.preview_command for a in module.actions)


def test_m02_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert len(by_risk[RiskLevel.SAFE]) == 7
    assert len(by_risk[RiskLevel.MODERATE]) == 5
    assert len(by_risk[RiskLevel.DESTRUCTIVE]) == 4


def test_m02_catalog_no_wmic():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert "wmic" not in action.command.lower()
        assert "wmic" not in (action.preview_command or "").lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_m02_catalog.py -v`
Expected: FAIL — `Modules/m02_cleanup/actions.yaml` does not exist yet (`FileNotFoundError`)

- [ ] **Step 3: Write the catalog**

`Modules/m02_cleanup/actions.yaml`:

```yaml
module_id: m02_cleanup
actions:
  - id: user_temp
    label_sk: "Docasne subory pouzivatela"
    label_en: "User temp files"
    risk: SAFE
    command: "Remove-Item \"$env:TEMP\\*\" -Recurse -Force -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:TEMP\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from $env:TEMP\""
    description_sk: "Vycisti $env:TEMP."
    description_en: "Clears $env:TEMP."

  - id: system_temp
    label_sk: "Systemove docasne subory"
    label_en: "System temp files"
    risk: SAFE
    command: "Remove-Item \"$env:WINDIR\\Temp\\*\" -Recurse -Force -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\Temp\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from $env:WINDIR\\Temp\""
    description_sk: "Vycisti $env:WINDIR\\Temp."
    description_en: "Clears $env:WINDIR\\Temp."

  - id: recycle_bin
    label_sk: "Kos"
    label_en: "Recycle Bin"
    risk: MODERATE
    command: "Clear-RecycleBin -Force -EA SilentlyContinue"
    preview_command: "$sh = New-Object -ComObject Shell.Application; $items = $sh.Namespace(0xA).Items(); Write-Output \"Recycle Bin: $($items.Count) item(s)\""
    description_sk: "Vyprazdni Kos."
    description_en: "Empties the Recycle Bin."

  - id: prefetch
    label_sk: "Prefetch cache"
    label_en: "Prefetch cache"
    risk: SAFE
    command: "Remove-Item \"$env:WINDIR\\Prefetch\\*\" -Force -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\Prefetch\" -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from Prefetch\""
    description_sk: "Vymaze subory Prefetch (docasne spomali dalsi start)."
    description_en: "Removes Prefetch files (temporarily slows next boot)."

  - id: wer_reports
    label_sk: "Hlasenia o chybach (WER)"
    label_en: "Error reports (WER)"
    risk: SAFE
    command: "Remove-Item \"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportQueue\",\"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportArchive\" -Recurse -Force -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportQueue\",\"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportArchive\" -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from WER report queues\""
    description_sk: "Vymaze frontu a archiv hlaseni o chybach."
    description_en: "Clears the WER report queue and archive."

  - id: cbs_logs
    label_sk: "Stare CBS logy"
    label_en: "Old CBS logs"
    risk: SAFE
    command: "Get-ChildItem \"$env:WINDIR\\Logs\\CBS\\CBS*.log\" -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1 | Remove-Item -Force -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\Logs\\CBS\\CBS*.log\" -EA SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -Skip 1; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c old CBS log(s), $mb MB (keeping newest)\""
    description_sk: "Vymaze stare CBS logy, ponecha najnovsi."
    description_en: "Removes old CBS logs, keeps the newest."

  - id: thumbnail_cache
    label_sk: "Cache nahladov"
    label_en: "Thumbnail cache"
    risk: SAFE
    command: "Stop-Process -Name explorer -Force -EA SilentlyContinue; Remove-Item \"$env:LOCALAPPDATA\\Microsoft\\Windows\\Explorer\\thumbcache_*.db\" -Force -EA SilentlyContinue; Start-Process explorer.exe"
    preview_command: "$f = Get-ChildItem \"$env:LOCALAPPDATA\\Microsoft\\Windows\\Explorer\\thumbcache_*.db\" -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c thumbnail cache file(s), $mb MB, and restart Explorer\""
    description_sk: "Vymaze cache nahladov a restartuje Explorer."
    description_en: "Clears the thumbnail cache and restarts Explorer."

  - id: font_cache
    label_sk: "Cache pisiem"
    label_en: "Font cache"
    risk: MODERATE
    command: "Stop-Service FontCache -Force -EA SilentlyContinue; Remove-Item \"$env:WINDIR\\ServiceProfiles\\LocalService\\AppData\\Local\\FontCache\\*\" -Force -EA SilentlyContinue; Start-Service FontCache -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\ServiceProfiles\\LocalService\\AppData\\Local\\FontCache\" -File -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would clear $c font cache file(s), $mb MB, restarting FontCache service\""
    description_sk: "Restartuje sluzbu FontCache a vymaze jej cache."
    description_en: "Restarts the FontCache service and clears its cache."

  - id: delivery_optimization
    label_sk: "Delivery Optimization cache"
    label_en: "Delivery Optimization cache"
    risk: SAFE
    command: "Delete-DeliveryOptimizationCache -Force"
    preview_command: "Write-Output \"Would clear Delivery Optimization cache (size not queryable without deleting)\""
    description_sk: "Vymaze cache Delivery Optimization."
    description_en: "Clears the Delivery Optimization cache."

  - id: windows_update_cache
    label_sk: "Cache Windows Update"
    label_en: "Windows Update cache"
    risk: MODERATE
    command: "Stop-Service wuauserv,bits -Force -EA SilentlyContinue; Remove-Item \"$env:WINDIR\\SoftwareDistribution\\Download\\*\" -Recurse -Force -EA SilentlyContinue; Start-Service wuauserv,bits -EA SilentlyContinue"
    preview_command: "$f = Get-ChildItem \"$env:WINDIR\\SoftwareDistribution\\Download\" -Recurse -File -EA SilentlyContinue; $c = $f.Count; $mb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1MB,2); Write-Output \"Would delete $c files, $mb MB from Windows Update cache\""
    description_sk: "Zastavi sluzby, vymaze stiahnute aktualizacie, spusti sluzby spat."
    description_en: "Stops services, clears downloaded updates, restarts services."

  - id: component_store_cleanup
    label_sk: "Cistenie ulozista komponentov"
    label_en: "Component store cleanup"
    risk: MODERATE
    command: "Dism.exe /Online /Cleanup-Image /StartComponentCleanup"
    preview_command: "Dism.exe /Online /Cleanup-Image /AnalyzeComponentStore"
    description_sk: "Vycisti ulozisko komponentov (bez ResetBase)."
    description_en: "Cleans the component store (no ResetBase)."

  - id: component_store_resetbase
    label_sk: "Ulozisko komponentov - ResetBase"
    label_en: "Component store - ResetBase"
    risk: DESTRUCTIVE
    command: "Dism.exe /Online /Cleanup-Image /StartComponentCleanup /ResetBase"
    preview_command: "Write-Output \"ResetBase permanently removes the ability to uninstall currently installed Windows updates. This cannot be undone.\""
    description_sk: "Znemozni odinstalovanie aktualne nainstalovanych aktualizacii. Nevratne."
    description_en: "Prevents uninstalling currently installed updates. Irreversible."

  - id: windows_old_removal
    label_sk: "Odstranenie Windows.old"
    label_en: "Remove Windows.old"
    risk: DESTRUCTIVE
    command: "takeown /F C:\\Windows.old /R /A | Out-Null; icacls C:\\Windows.old /reset /T /C | Out-Null; Remove-Item C:\\Windows.old -Recurse -Force -EA SilentlyContinue"
    preview_command: "if (Test-Path C:\\Windows.old) { $f = Get-ChildItem C:\\Windows.old -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count; $gb = [math]::Round((($f | Measure-Object Length -Sum).Sum)/1GB,2); Write-Output \"Would delete $c files, $gb GB from C:\\Windows.old\" } else { Write-Output \"C:\\Windows.old not present\" }"
    description_sk: "Odstrani C:\\Windows.old. Nevratne."
    description_en: "Removes C:\\Windows.old. Irreversible."

  - id: shadow_copies_oldest
    label_sk: "Najstarsia shadow copy"
    label_en: "Oldest shadow copy"
    risk: DESTRUCTIVE
    command: "vssadmin delete shadows /for=C: /oldest"
    preview_command: "Get-CimInstance Win32_ShadowCopy | Select-Object ID,InstallDate,VolumeName | Format-Table"
    description_sk: "Zmaze najstarsiu shadow copy na disku C:. Nevratne."
    description_en: "Deletes the oldest shadow copy on drive C:. Irreversible."

  - id: hibernation_off
    label_sk: "Vypnutie hibernacie"
    label_en: "Disable hibernation"
    risk: MODERATE
    command: "powercfg /h off"
    preview_command: "if (Test-Path \"$env:SystemDrive\\hiberfil.sys\") { $mb = [math]::Round((Get-Item \"$env:SystemDrive\\hiberfil.sys\" -Force).Length/1MB,2); Write-Output \"Would free approximately $mb MB by disabling hibernation\" } else { Write-Output \"Hibernation already disabled\" }"
    description_sk: "Vypne hibernaciu a uvolni miesto zabrane hiberfil.sys."
    description_en: "Disables hibernation and frees the space used by hiberfil.sys."

  - id: stale_user_profiles
    label_sk: "Stare pouzivatelske profily"
    label_en: "Stale user profiles"
    risk: DESTRUCTIVE
    command: "Get-CimInstance Win32_UserProfile | Where-Object {-not $_.Special -and $_.LastUseTime -lt (Get-Date).AddDays(-180)} | Remove-CimInstance"
    preview_command: "Get-CimInstance Win32_UserProfile | Where-Object {-not $_.Special -and $_.LastUseTime -lt (Get-Date).AddDays(-180)} | Select-Object LocalPath,LastUseTime | Format-Table"
    description_sk: "Odstrani neaktivne pouzivatelske profily starsie ako 180 dni. Nevratne."
    description_en: "Removes inactive user profiles older than 180 days. Irreversible."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_m02_catalog.py -v`
Expected: PASS (3 tests, all new — this is a new test file) — double-check the risk counts: 7 SAFE
(user_temp, system_temp, prefetch, wer_reports, cbs_logs,
thumbnail_cache, delivery_optimization), 5 MODERATE (recycle_bin,
font_cache, windows_update_cache, component_store_cleanup,
hibernation_off), 4 DESTRUCTIVE (component_store_resetbase,
windows_old_removal, shadow_copies_oldest, stale_user_profiles)

- [ ] **Step 5: Run the full suite**

Run: `py -m pytest tests/ -v`
Expected: PASS (51 tests: 48 from Task 1 plus 3 new)

- [ ] **Step 6: Commit**

```bash
git add Modules/m02_cleanup/actions.yaml tests/test_m02_catalog.py
git commit -m "feat: M02 cleanup action catalog (16 actions, SAFE to DESTRUCTIVE)"
```

---

### Task 3: `restore_point.py`

**Files:**
- Create: `portablefix/restore_point.py`
- Test: `tests/test_restore_point.py`

**Interfaces:**
- Consumes: `portablefix.executor.POWERSHELL_PREFIX`
- Produces: `restore_point.create_restore_point(description: str) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_restore_point.py
import subprocess

from portablefix.executor import POWERSHELL_PREFIX
from portablefix.restore_point import create_restore_point


class _FakeResult:
    def __init__(self, returncode: int):
        self.returncode = returncode


def test_create_restore_point_success(monkeypatch):
    captured = {}

    def fake_run(argv, capture_output, timeout):
        captured["argv"] = argv
        return _FakeResult(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert create_restore_point("test checkpoint") is True
    assert captured["argv"][: len(POWERSHELL_PREFIX)] == POWERSHELL_PREFIX
    command = captured["argv"][-1]
    assert "test checkpoint" in command
    assert "Checkpoint-Computer" in command


def test_create_restore_point_nonzero_returncode_is_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, capture_output, timeout: _FakeResult(1))
    assert create_restore_point("x") is False


def test_create_restore_point_exception_is_false(monkeypatch):
    def raise_error(argv, capture_output, timeout):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "run", raise_error)
    assert create_restore_point("x") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_restore_point.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portablefix.restore_point'`

- [ ] **Step 3: Write minimal implementation**

```python
# portablefix/restore_point.py
import subprocess

from .executor import POWERSHELL_PREFIX


def create_restore_point(description: str) -> bool:
    command = (
        'Enable-ComputerRestore -Drive "C:\\"; '
        f'Checkpoint-Computer -Description "{description}" -RestorePointType MODIFY_SETTINGS'
    )
    try:
        result = subprocess.run(POWERSHELL_PREFIX + [command], capture_output=True, timeout=120)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_restore_point.py -v`
Expected: PASS (3 tests, all new)

- [ ] **Step 5: Run the full suite**

Run: `py -m pytest tests/ -v`
Expected: PASS (54 tests: 51 from Task 2 plus 3 new)

- [ ] **Step 6: Commit**

```bash
git add portablefix/restore_point.py tests/test_restore_point.py
git commit -m "feat: best-effort System Restore Point creation"
```

---

### Task 4: `report.py`

**Files:**
- Create: `portablefix/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `audit_log.audit_log_path(base_dir, run_id) -> Path` (F1), `ModuleDef`/`ActionDef` (Task 1's `preview_command` doesn't matter here, just the existing `label(language)`/`risk` fields)
- Produces: `report.build_report_data(base_dir, run_id, modules, language, snapshot_before, snapshot_after) -> dict`, `report.generate_report(base_dir, run_id, modules, language, snapshot_before, snapshot_after) -> tuple[Path, Path]` (returns `(html_path, json_path)`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
import json

from portablefix.audit_log import append_entry, make_entry
from portablefix.models import ActionDef, ModuleDef, RiskLevel
from portablefix.report import build_report_data, generate_report


def _fixture_modules():
    action = ActionDef(
        id="user_temp",
        label_sk="Docasne subory",
        label_en="Temp files",
        risk=RiskLevel.SAFE,
        command="Remove-Item $env:TEMP",
    )
    return [ModuleDef(module_id="m02_cleanup", actions=[action])]


def test_build_report_data_joins_audit_entries_with_catalog(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "Remove-Item $env:TEMP", 0, "done", False)
    append_entry(tmp_path, "run1", entry)

    data = build_report_data(
        tmp_path, "run1", modules, "en",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 12.0},
    )

    assert data["run_id"] == "run1"
    assert data["snapshot_before"]["free_gb"] == 10.0
    assert data["snapshot_after"]["free_gb"] == 12.0
    assert len(data["actions"]) == 1
    assert data["actions"][0]["label"] == "Temp files"
    assert data["actions"][0]["risk"] == "SAFE"
    assert data["actions"][0]["exit_code"] == 0


def test_build_report_data_unknown_action_falls_back_to_id(tmp_path):
    entry = make_entry("m02_cleanup", "not_in_catalog", "cmd", 0, "out", False)
    append_entry(tmp_path, "run2", entry)
    data = build_report_data(tmp_path, "run2", [], "en", {}, {})
    assert data["actions"][0]["label"] == "not_in_catalog"
    assert data["actions"][0]["risk"] == "UNKNOWN"


def test_generate_report_writes_html_and_json(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "Remove-Item $env:TEMP", 0, "done", False)
    append_entry(tmp_path, "run3", entry)

    html_path, json_path = generate_report(
        tmp_path, "run3", modules, "en",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 12.0},
    )

    assert html_path.exists()
    assert json_path.exists()
    assert html_path.parent == tmp_path / "Reports"
    html_content = html_path.read_text(encoding="utf-8")
    assert "Temp files" in html_content
    assert "SAFE" in html_content

    json_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_data["run_id"] == "run3"
    assert len(json_data["actions"]) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_report.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'portablefix.report'`

- [ ] **Step 3: Write minimal implementation**

```python
# portablefix/report.py
import json
import platform
import socket
from pathlib import Path

from .audit_log import audit_log_path
from .models import ActionDef, ModuleDef


def _find_action(modules: list[ModuleDef], module_id: str, action_id: str) -> ActionDef | None:
    for module in modules:
        if module.module_id != module_id:
            continue
        for action in module.actions:
            if action.id == action_id:
                return action
    return None


def _read_audit_entries(base_dir: Path, run_id: str) -> list[dict]:
    path = audit_log_path(base_dir, run_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def build_report_data(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
) -> dict:
    entries = _read_audit_entries(base_dir, run_id)
    actions = []
    for entry in entries:
        action = _find_action(modules, entry["module_id"], entry["action_id"])
        actions.append(
            {
                "timestamp": entry["timestamp"],
                "module_id": entry["module_id"],
                "action_id": entry["action_id"],
                "label": action.label(language) if action else entry["action_id"],
                "risk": action.risk.value if action else "UNKNOWN",
                "exit_code": entry["exit_code"],
                "dry_run": entry["dry_run"],
            }
        )
    return {
        "run_id": run_id,
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "actions": actions,
        "requires_restart": [a for a in actions if a["risk"] == "REQUIRES_REBOOT"],
    }


def _render_html(data: dict) -> str:
    rows = "\n".join(
        f"<tr><td>{a['module_id']}</td><td>{a['label']}</td><td>{a['risk']}</td>"
        f"<td>{a['exit_code']}</td><td>{a['dry_run']}</td></tr>"
        for a in data["actions"]
    )
    restart_section = ""
    if data["requires_restart"]:
        items = "".join(f"<li>{a['label']}</li>" for a in data["requires_restart"])
        restart_section = f"<h2>Requires restart</h2><ul>{items}</ul>"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>PortableFix report {data['run_id']}</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
th {{ background: #eee; }}
</style></head>
<body>
<h1>PortableFix report - {data['hostname']}</h1>
<p>Run: {data['run_id']}</p>
<p>OS: {data['os']}</p>
<p>Free space before: {data['snapshot_before'].get('free_gb', '?')} GB,
after: {data['snapshot_after'].get('free_gb', '?')} GB</p>
<h2>Actions</h2>
<table>
<tr><th>Module</th><th>Action</th><th>Risk</th><th>Exit code</th><th>Dry run</th></tr>
{rows}
</table>
{restart_section}
</body></html>
"""


def generate_report(
    base_dir: Path,
    run_id: str,
    modules: list[ModuleDef],
    language: str,
    snapshot_before: dict,
    snapshot_after: dict,
) -> tuple[Path, Path]:
    data = build_report_data(base_dir, run_id, modules, language, snapshot_before, snapshot_after)
    reports_dir = base_dir / "Reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_path = reports_dir / f"{data['hostname']}_{run_id}.html"
    json_path = reports_dir / f"{data['hostname']}_{run_id}.json"
    html_path.write_text(_render_html(data), encoding="utf-8")
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return html_path, json_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_report.py -v`
Expected: PASS (3 tests, all new)

- [ ] **Step 5: Run the full suite**

Run: `py -m pytest tests/ -v`
Expected: PASS (57 tests: 54 from Task 3 plus 3 new)

- [ ] **Step 6: Commit**

```bash
git add portablefix/report.py tests/test_report.py
git commit -m "feat: M11 HTML/JSON report generation from the audit log"
```

---

### Task 5: `MainWindow` — preview-command dispatch + DESTRUCTIVE Restore-Point gate

**Files:**
- Modify: `portablefix/gui/main_window.py:1-23` (imports), `:26-46` (`__init__`), `:129-154` (`_run_next`)
- Modify: `portablefix/i18n.py:12` and `:24` (add 2 keys to each language block)
- Modify: `tests/test_gui_main_window.py` (add coverage — this file currently has 9 tests from F1, ending with `test_moderate_risk_action_accepted_runs_and_logs`; add the new tests below after it)

**Interfaces:**
- Consumes: `restore_point.create_restore_point(description: str) -> bool` (Task 3), `ActionDef.preview_command` (Task 1)
- Produces: `MainWindow._restore_point_attempted: bool` (new instance attribute, reset in `run_selected_actions`), `MainWindow._skip_destructive_actions_in_queue() -> None` (new method)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_main_window.py` (this file already has `_make_base_dir`/fixtures from F1 — add a second fixture helper alongside it for a module with a preview command and one with a DESTRUCTIVE action):

```python
DESTRUCTIVE_ACTIONS_YAML = """
module_id: m02_cleanup
actions:
  - id: risky_thing
    label_sk: "Riskantna vec"
    label_en: "Risky thing"
    risk: DESTRUCTIVE
    command: "Write-Output 'destructive-ran'"
    preview_command: "Write-Output 'destructive-preview'"
    description_sk: "Test"
    description_en: "Test"
  - id: safe_thing
    label_sk: "Bezpecna vec"
    label_en: "Safe thing"
    risk: SAFE
    command: "Write-Output 'safe-ran'"
    preview_command: "Write-Output 'safe-preview'"
    description_sk: "Test"
    description_en: "Test"
"""


def _make_destructive_base_dir(tmp_path):
    module_dir = tmp_path / "Modules" / "m02_cleanup"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(DESTRUCTIVE_ACTIONS_YAML, encoding="utf-8")
    return tmp_path


def test_dry_run_with_preview_command_runs_preview_not_real_command(qtbot, tmp_path):
    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=True)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_preview"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    qtbot.waitUntil(lambda: "safe-preview" in window.console.toPlainText(), timeout=10000)
    assert "safe-ran" not in window.console.toPlainText()


def test_destructive_action_declined_at_hard_confirm_is_not_run(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_decline"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_decline")
    qtbot.waitUntil(lambda: log_path.exists() and log_path.read_text(encoding="utf-8").strip() != "", timeout=10000)

    log_content = log_path.read_text(encoding="utf-8")
    assert "risky_thing" not in log_content
    assert "safe_thing" in log_content


def test_destructive_action_accepted_runs_normally(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: True)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.Yes))

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_accept"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_accept")
    qtbot.waitUntil(lambda: log_path.exists() and "risky_thing" in log_path.read_text(encoding="utf-8"), timeout=10000)
    assert "destructive-ran" in window.console.toPlainText()


def test_restore_point_failure_declined_skips_remaining_destructive_but_runs_safe(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from portablefix import restore_point

    monkeypatch.setattr(restore_point, "create_restore_point", lambda description: False)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **kw: QMessageBox.No))

    base_dir = _make_destructive_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_rpfail"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["risky_thing"].setChecked(True)
    window._action_checkboxes["safe_thing"].setChecked(True)

    window.run_selected_actions()

    from portablefix.audit_log import audit_log_path
    log_path = audit_log_path(base_dir, "run_rpfail")
    qtbot.waitUntil(lambda: log_path.exists() and "safe_thing" in log_path.read_text(encoding="utf-8"), timeout=10000)
    assert "risky_thing" not in log_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_gui_main_window.py -v`
Expected: FAIL — `test_dry_run_with_preview_command_runs_preview_not_real_command` fails because dry-run still just echoes `[DRY-RUN] ...` today; the destructive tests fail because `RiskLevel.DESTRUCTIVE` currently goes through the same plain `QMessageBox.question` gate as `MODERATE` (no `restore_point` import exists yet, so `monkeypatch.setattr(restore_point, ...)` would even fail at the `from portablefix import restore_point` line if Task 3 weren't already merged — it is, from Task 3 above)

- [ ] **Step 3: Write minimal implementation**

In `portablefix/i18n.py`, add two keys to the `"sk"` dict (after `"confirm_risky_action"`):

```python
        "confirm_destructive_action": "POZOR: Tato akcia je nevratna a nie je mozne ju vratit spat cez PortableFix. Naozaj pokracovat?",
        "restore_point_failed_confirm": "Nepodarilo sa vytvorit bod obnovenia (Windows to mozno obmedzuje). Pokracovat aj tak?",
```

and the matching two keys to the `"en"` dict (after `"confirm_risky_action"`):

```python
        "confirm_destructive_action": "WARNING: This action is irreversible and cannot be undone through PortableFix. Continue anyway?",
        "restore_point_failed_confirm": "Could not create a System Restore Point (Windows may be limiting this). Continue anyway?",
```

In `portablefix/gui/main_window.py`, change the import block at the top:

```python
from .. import elevation, i18n
```

to:

```python
from .. import elevation, i18n, restore_point
```

In `__init__`, add the new flag after `self._runner: ActionRunner | None = None`:

```python
        self._restore_point_attempted = False
```

Add a new method right after `_find_action`:

```python
    def _skip_destructive_actions_in_queue(self) -> None:
        self._queue = [
            aid for aid in self._queue if self._find_action(aid)[1].risk != RiskLevel.DESTRUCTIVE
        ]
```

Change `run_selected_actions`:

```python
    def run_selected_actions(self) -> None:
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._restore_point_attempted = False
        self._run_next()
```

Replace the whole `_run_next` method with:

```python
    def _run_next(self) -> None:
        if not self._queue:
            return
        action_id = self._queue.pop(0)
        module, action = self._find_action(action_id)

        if action.risk == RiskLevel.DESTRUCTIVE:
            if not self._restore_point_attempted:
                self._restore_point_attempted = True
                if not restore_point.create_restore_point(f"PortableFix cleanup {self.run_id}"):
                    proceed = QMessageBox.warning(
                        self,
                        self._t("app_title"),
                        self._t("restore_point_failed_confirm"),
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if proceed != QMessageBox.Yes:
                        self._skip_destructive_actions_in_queue()
                        self._run_next()
                        return
            confirmed = QMessageBox.warning(
                self,
                self._t("app_title"),
                f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_destructive_action')}",
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirmed != QMessageBox.Yes:
                self._run_next()
                return
        elif action.risk != RiskLevel.SAFE:
            confirmed = QMessageBox.question(
                self,
                self._t("app_title"),
                f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_risky_action')}",
            )
            if confirmed != QMessageBox.Yes:
                self._run_next()
                return

        if self.settings.dry_run and action.preview_command:
            plan = build_execution_plan(action.preview_command, dry_run=False)
        else:
            plan = build_execution_plan(action.command, self.settings.dry_run)

        runner = ActionRunner(plan, parent=self)
        self._runner = runner
        runner.output_line.connect(self.console.appendPlainText)
        runner.finished_with_code.connect(
            lambda code, m=module.module_id, a=action.id, c=action.command, r=runner: self._on_action_finished(
                m, a, c, code, r
            )
        )
        runner.start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_gui_main_window.py -v`
Expected: PASS (14 tests — the 9 already in this file plus these 5)

- [ ] **Step 5: Run the full suite**

Run: `py -m pytest tests/ -v`
Expected: PASS (62 tests: 57 from Task 4 plus 5 new)

- [ ] **Step 6: Commit**

```bash
git add portablefix/i18n.py portablefix/gui/main_window.py tests/test_gui_main_window.py
git commit -m "feat: preview-command dry-run dispatch and DESTRUCTIVE restore-point gate"
```

---

### Task 6: `MainWindow` — free-space snapshot + report generation trigger

**Files:**
- Modify: `portablefix/gui/main_window.py` (imports, `run_selected_actions`, `_run_next`'s empty-queue branch, `__init__`)
- Modify: `tests/test_gui_main_window.py` (add coverage)

**Interfaces:**
- Consumes: `report.generate_report(base_dir, run_id, modules, language, snapshot_before, snapshot_after) -> tuple[Path, Path]` (Task 4)
- Produces: `MainWindow._take_snapshot() -> dict` (new method), `MainWindow._batch_active: bool` (new instance attribute)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_main_window.py`:

```python
def test_running_a_batch_generates_a_report(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)  # from F1: single "hello" SAFE action, no preview_command
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_report"
    )
    qtbot.addWidget(window)
    window._action_checkboxes["hello"].setChecked(True)

    window.run_selected_actions()

    reports_dir = base_dir / "Reports"
    qtbot.waitUntil(lambda: reports_dir.exists() and any(reports_dir.glob("*.html")), timeout=10000)
    assert any(reports_dir.glob("*.json"))


def test_opening_without_running_anything_generates_no_report(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    settings = Settings(language="en", dry_run=False)
    window = MainWindow(
        assets_dir=base_dir, state_dir=base_dir, settings=settings, is_admin=True, run_id="run_none"
    )
    qtbot.addWidget(window)

    window.run_selected_actions()  # nothing checked

    assert not (base_dir / "Reports").exists()
```

(`_make_base_dir` and the `"hello"` action fixture already exist in `tests/test_gui_main_window.py` from F1's Task 9 — reuse them, don't redefine.)

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_gui_main_window.py -v`
Expected: FAIL — `test_running_a_batch_generates_a_report` times out / no `Reports/` dir ever appears, since nothing calls `report.generate_report` yet

- [ ] **Step 3: Write minimal implementation**

In `portablefix/gui/main_window.py`, add to the top of the file (with the other stdlib import):

```python
import shutil
```

Change the import block:

```python
from .. import elevation, i18n, restore_point
```

to:

```python
from .. import elevation, i18n, report, restore_point
```

In `__init__`, add after `self._restore_point_attempted = False`:

```python
        self._batch_active = False
        self._snapshot_before: dict = {}
```

Add a new method after `_skip_destructive_actions_in_queue`:

```python
    def _take_snapshot(self) -> dict:
        usage = shutil.disk_usage(self.state_dir)
        return {
            "free_gb": round(usage.free / (1024**3), 2),
            "total_gb": round(usage.total / (1024**3), 2),
        }
```

Change `run_selected_actions`:

```python
    def run_selected_actions(self) -> None:
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._restore_point_attempted = False
        if self._queue:
            self._batch_active = True
            self._snapshot_before = self._take_snapshot()
        self._run_next()
```

At the very top of `_run_next`, change:

```python
    def _run_next(self) -> None:
        if not self._queue:
            return
```

to:

```python
    def _run_next(self) -> None:
        if not self._queue:
            if self._batch_active:
                self._batch_active = False
                snapshot_after = self._take_snapshot()
                report.generate_report(
                    self.state_dir,
                    self.run_id,
                    self.modules,
                    self.settings.language,
                    self._snapshot_before,
                    snapshot_after,
                )
            return
```

(The rest of `_run_next` — from `action_id = self._queue.pop(0)` onward — is unchanged from Task 5.)

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_gui_main_window.py -v`
Expected: PASS (16 tests — the 14 from Task 5 plus these 2)

- [ ] **Step 5: Run the full suite**

Run: `py -m pytest tests/ -v`
Expected: PASS (64 tests: 62 from Task 5 plus 2 new)

- [ ] **Step 6: Commit**

```bash
git add portablefix/gui/main_window.py tests/test_gui_main_window.py
git commit -m "feat: batch-level free-space snapshot and M11 report generation trigger"
```

---

## Self-Review

**Spec coverage:**
- M02 full 16-action catalog (SAFE through DESTRUCTIVE) → Task 2 ✓
- `preview_command` mechanism (real dry-run previews) → Task 1 (field) + Task 5 (dispatch logic) ✓
- Restore Point before DESTRUCTIVE, best-effort, non-blocking on failure → Task 3 (module) + Task 5 (gate) ✓
- Hard, visually-distinct confirmation for DESTRUCTIVE (separate from F1's generic MODERATE/DESTRUCTIVE gate) → Task 5 ✓
- SAFE/MODERATE items already queued still run after a DESTRUCTIVE decline/restore-point-decline → Task 5, tested explicitly ✓
- M11 HTML+JSON report, pre/post free-space snapshot, requires-restart section (empty for F2, ready for F3) → Task 4 (generation) + Task 6 (trigger) ✓
- No `undo.ps1` subsystem → not built, correctly absent from every task ✓
- Browser cache cleanup, `cleanmgr` automation, large/duplicate file scan → correctly absent, no task touches them ✓

**Placeholder scan:** no TBD/TODO; every step has runnable code, exact YAML content, or a concrete expected test count.

**Type consistency:** `ActionDef.preview_command: str | None` (Task 1) matches its use in Task 5's `_run_next` (`action.preview_command`) and Task 2's catalog (always populated). `restore_point.create_restore_point(description: str) -> bool` (Task 3) matches its call in Task 5 (`restore_point.create_restore_point(f"...")`). `report.generate_report(base_dir, run_id, modules, language, snapshot_before, snapshot_after)` (Task 4) matches its call in Task 6 (`report.generate_report(self.state_dir, self.run_id, self.modules, self.settings.language, self._snapshot_before, snapshot_after)`) — argument order and count match exactly. `MainWindow.state_dir`/`assets_dir` (established in F1) are used consistently: `assets_dir` never appears in Task 5/6's writes, `state_dir` is always the write target (audit log, snapshot, report).
