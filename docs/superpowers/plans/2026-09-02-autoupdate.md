# Auto-update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PortableFix checks GitHub Releases on startup (silently, non-blocking) and can download + self-apply a newer version via a generated PowerShell swap script, without ever touching Modules/Data/Logs/Reports/Backups.

**Architecture:** New `portablefix/updater.py` module (pure functions + thin `QThread` wrappers, same shape as `portablefix/restore_point.py`), a new `portablefix/version.py` constant, GUI banner in `main_window.py`, and a build-script switch from `--onedir` to `--onefile` (required so a single-file swap is a complete update — see spec).

**Tech Stack:** Python 3.12, PySide6, `urllib.request`/`json` (stdlib only, no new pip dependency), PowerShell 5.1 (generated swap script), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-02-autoupdate-design.md` — this plan argues from that spec; read it first if anything below is ambiguous.

## Global Constraints

- Python 3.12 / PySide6 only — no new pip dependencies (`urllib.request`, `json`, `hashlib` are stdlib, already used elsewhere in the project).
- Windows-only code, no cross-platform guards (matches the rest of the codebase — `executor.py`, `restore_point.py` etc. use Windows-only APIs unconditionally).
- Every new `QThread` wrapper follows the existing pattern in `restore_point.py`: a plain function with the real logic, a thin `QThread` subclass that calls it and emits one result signal, `self.finished.connect(self.deleteLater)` in `__init__`.
- Every new user-facing string goes through `portablefix/i18n.py` with both `sk` and `en` keys (the existing `test_i18n.py::test_sk_and_en_dicts_have_identical_keys` parity test enforces this — do not skip either language).
- No code comments except where a non-obvious WHY needs explaining (project convention, see CLAUDE-level session rules). Commit messages and this plan are in English, matching every commit so far this session.
- This machine has a known transient native crash (`STATUS_STACK_BUFFER_OVERRUN`) when many real-PowerShell-spawning `pytest-qt` tests run in one invocation — **never a real code defect**. Run new/changed GUI and PowerShell-spawning tests individually or in small batches with a retry, not as one giant `pytest tests/` run, exactly as done throughout this session. The non-GUI suite (`pytest tests/ --deselect tests/test_gui_main_window.py --deselect tests/test_executor.py`) is safe to run as one batch.
- Any new PowerShell text this plan generates (the swap script) gets parse-validated via `[scriptblock]::Create($env:PFCMD)` (subprocess, env var, never `Invoke-Expression`) — same pattern used for every YAML catalog command this session — never executed for real in a test.

---

### Task 1: `portablefix/version.py` + core update logic in `portablefix/updater.py`

**Files:**
- Create: `portablefix/version.py`
- Create: `portablefix/updater.py`
- Test: `tests/test_updater.py`

**Interfaces:**
- Produces (consumed by Task 2, Task 3, Task 4):
  - `portablefix/version.py`: `APP_VERSION: str`
  - `portablefix/updater.py`:
    - `GITHUB_API_LATEST_RELEASE: str` (module constant)
    - `class UpdateVerificationError(Exception)`
    - `@dataclass class UpdateInfo: version: str; download_url: str; sha256_url: str | None; notes: str`
    - `def parse_version(v: str) -> tuple[int, ...]`
    - `def is_newer(remote: str, local: str) -> bool`
    - `def check_for_update(current_version: str, timeout: float = 5.0) -> UpdateInfo | None` — never raises, returns `None` on any failure (network, timeout, malformed JSON, missing asset)
    - `def download_update(info: UpdateInfo, dest_dir: Path) -> Path` — raises `UpdateVerificationError` on SHA256 mismatch, returns path to the downloaded exe otherwise
    - `def is_writable(directory: Path) -> bool`
    - `def build_swap_script(current_pid: int, old_exe: Path, new_exe: Path, sums_path: Path) -> str`
    - `def apply_update(new_exe_path: Path, current_exe_path: Path, sums_path: Path) -> None` — launches the detached swap script, does not itself quit the app (caller's job)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_updater.py`:

```python
import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portablefix.updater import (
    UpdateInfo,
    UpdateVerificationError,
    build_swap_script,
    check_for_update,
    download_update,
    is_newer,
    is_writable,
    parse_version,
)


def test_parse_version_strips_v_prefix():
    assert parse_version("v1.2.3") == (1, 2, 3)


def test_parse_version_handles_multi_digit_components():
    assert parse_version("1.10.2") == (1, 10, 2)


def test_is_newer_compares_numerically_not_as_strings():
    assert is_newer("1.10.0", "1.9.0") is True
    assert is_newer("1.9.0", "1.10.0") is False


def test_is_newer_false_when_equal():
    assert is_newer("1.0.0", "1.0.0") is False


def _release_json(tag="v1.1.0", with_sha=True, exe_name="PortableFix.exe"):
    assets = [{"name": exe_name, "browser_download_url": "https://example.com/PortableFix.exe"}]
    if with_sha:
        assets.append({
            "name": "PortableFix.exe.sha256",
            "browser_download_url": "https://example.com/PortableFix.exe.sha256",
        })
    return json.dumps({"tag_name": tag, "assets": assets, "body": "release notes"}).encode("utf-8")


def _mock_response(body: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_check_for_update_returns_none_when_remote_not_newer():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(_release_json(tag="v1.0.0"))):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_info_when_remote_newer():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(_release_json(tag="v1.1.0"))):
        info = check_for_update("1.0.0")
    assert info == UpdateInfo(
        version="1.1.0",
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="release notes",
    )


def test_check_for_update_returns_none_on_network_error():
    with patch("portablefix.updater.urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_on_malformed_json():
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(b"not json")):
        assert check_for_update("1.0.0") is None


def test_check_for_update_returns_none_when_no_exe_asset_present():
    body = _release_json(tag="v1.1.0", exe_name="SomethingElse.exe")
    with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(body)):
        assert check_for_update("1.0.0") is None


def test_download_update_succeeds_when_hash_matches(tmp_path):
    content = b"fake-exe-content"
    expected_hash = hashlib.sha256(content).hexdigest()
    info = UpdateInfo(
        version="1.1.0",
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(content)

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(expected_hash.encode("utf-8"))):
            result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == content


def test_download_update_raises_and_cleans_up_on_hash_mismatch(tmp_path):
    info = UpdateInfo(
        version="1.1.0",
        download_url="https://example.com/PortableFix.exe",
        sha256_url="https://example.com/PortableFix.exe.sha256",
        notes="",
    )
    wrong_hash = "0" * 64

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"fake-exe-content")

    dest = tmp_path / "dest"
    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        with patch("portablefix.updater.urllib.request.urlopen", return_value=_mock_response(wrong_hash.encode("utf-8"))):
            with pytest.raises(UpdateVerificationError):
                download_update(info, dest)

    assert not (dest / "PortableFix.new.exe").exists()


def test_download_update_skips_verification_when_no_sha256_asset(tmp_path):
    info = UpdateInfo(
        version="1.1.0", download_url="https://example.com/PortableFix.exe", sha256_url=None, notes="",
    )

    def fake_urlretrieve(url, path):
        Path(path).write_bytes(b"anything")

    with patch("portablefix.updater.urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        result_path = download_update(info, tmp_path / "dest")

    assert result_path.read_bytes() == b"anything"


def test_is_writable_true_for_writable_directory(tmp_path):
    assert is_writable(tmp_path) is True


def test_is_writable_false_for_missing_directory(tmp_path):
    assert is_writable(tmp_path / "does_not_exist") is False


def test_build_swap_script_parses_as_valid_powershell():
    import os
    import subprocess

    script = build_swap_script(
        current_pid=12345,
        old_exe=Path(r"C:\Users\test\USB Fixer\App\PortableFix.exe"),
        new_exe=Path(r"C:\Users\test\AppData\Local\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\Users\test\USB Fixer\Data\SHA256SUMS"),
    )
    env = os.environ.copy()
    env["PFCMD"] = script
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "[scriptblock]::Create($env:PFCMD) | Out-Null; Write-Output OK"],
        env=env, capture_output=True, text=True,
    )
    assert "OK" in result.stdout, result.stderr


def test_build_swap_script_quotes_paths_with_spaces():
    script = build_swap_script(
        current_pid=1,
        old_exe=Path(r"C:\Users\test\USB Fixer\App\PortableFix.exe"),
        new_exe=Path(r"C:\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\Users\test\USB Fixer\Data\SHA256SUMS"),
    )
    assert '"C:\\Users\\test\\USB Fixer\\App\\PortableFix.exe"' in script


def test_build_swap_script_contains_pid_wait_loop():
    script = build_swap_script(
        current_pid=54321,
        old_exe=Path(r"C:\App\PortableFix.exe"),
        new_exe=Path(r"C:\Temp\PortableFix.new.exe"),
        sums_path=Path(r"C:\App\SHA256SUMS"),
    )
    assert "54321" in script
    assert "Get-Process" in script
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_updater.py -v`
Expected: `ModuleNotFoundError: No module named 'portablefix.updater'` (and `portablefix.version` doesn't exist yet either — the import at the top of the test file fails before any test runs).

- [ ] **Step 3: Write `portablefix/version.py`**

```python
APP_VERSION = "1.0.0"
```

- [ ] **Step 4: Write `portablefix/updater.py`**

```python
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .integrity import compute_sha256

GITHUB_API_LATEST_RELEASE = "https://api.github.com/repos/vxkShelby/portableFixer/releases/latest"


class UpdateVerificationError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    sha256_url: str | None
    notes: str


def parse_version(v: str) -> tuple[int, ...]:
    v = v.lstrip("vV")
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return parse_version(remote) > parse_version(local)


def check_for_update(current_version: str, timeout: float = 5.0) -> UpdateInfo | None:
    try:
        req = urllib.request.Request(
            GITHUB_API_LATEST_RELEASE,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "PortableFix-Updater"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        if not tag or not is_newer(tag, current_version):
            return None
        assets = data.get("assets", [])
        exe_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix.exe"), None)
        if not exe_asset:
            return None
        sha_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix.exe.sha256"), None)
        return UpdateInfo(
            version=tag.lstrip("vV"),
            download_url=exe_asset["browser_download_url"],
            sha256_url=sha_asset["browser_download_url"] if sha_asset else None,
            notes=data.get("body", ""),
        )
    except Exception:
        return None


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


def is_writable(directory: Path) -> bool:
    probe = directory / ".update_write_test"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def build_swap_script(current_pid: int, old_exe: Path, new_exe: Path, sums_path: Path) -> str:
    old = str(old_exe)
    new = str(new_exe)
    sums = str(sums_path)
    return (
        '$ErrorActionPreference = "SilentlyContinue"\n'
        f"for ($i = 0; $i -lt 30; $i++) {{\n"
        f"    if (-not (Get-Process -Id {current_pid} -EA SilentlyContinue)) {{ break }}\n"
        "    Start-Sleep -Milliseconds 500\n"
        "}\n"
        "Start-Sleep -Milliseconds 300\n"
        f'Move-Item -Path "{old}" -Destination "{old}.old" -Force\n'
        f'Move-Item -Path "{new}" -Destination "{old}" -Force\n'
        f'Remove-Item -Path "{old}.old" -Force -EA SilentlyContinue\n'
        "try {\n"
        f'    $hash = (Get-FileHash -Path "{old}" -Algorithm SHA256).Hash.ToLower()\n'
        f'    if (Test-Path "{sums}") {{\n'
        f'        $lines = Get-Content "{sums}"\n'
        "        $newLines = @()\n"
        "        $found = $false\n"
        "        foreach ($line in $lines) {\n"
        "            if ($line -match 'App/PortableFix\\.exe$') {\n"
        '                $newLines += "$hash  App/PortableFix.exe"\n'
        "                $found = $true\n"
        "            } else {\n"
        "                $newLines += $line\n"
        "            }\n"
        "        }\n"
        '        if (-not $found) { $newLines += "$hash  App/PortableFix.exe" }\n'
        f'        Set-Content -Path "{sums}" -Value $newLines -Encoding ASCII\n'
        "    }\n"
        "} catch {}\n"
        f'Start-Process -FilePath "{old}"\n'
    )


def apply_update(new_exe_path: Path, current_exe_path: Path, sums_path: Path) -> None:
    current_pid = os.getpid()
    script_text = build_swap_script(current_pid, current_exe_path, new_exe_path, sums_path)
    script_path = Path(tempfile.gettempdir()) / f"portablefix_update_{current_pid}.ps1"
    script_path.write_text(script_text, encoding="utf-8")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_path)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_updater.py -v`
Expected: all tests PASS (14 tests).

- [ ] **Step 6: Commit**

```bash
git add portablefix/version.py portablefix/updater.py tests/test_updater.py
git commit -m "feat: add version tracking and core auto-update logic"
```

---

### Task 2: `QThread` wrappers — `UpdateCheckRunner`, `UpdateDownloadRunner`

**Files:**
- Modify: `portablefix/updater.py`
- Modify: `tests/test_updater.py`

**Interfaces:**
- Consumes: everything Task 1 produced in `portablefix/updater.py`.
- Produces (consumed by Task 3, Task 4):
  - `class UpdateCheckRunner(QThread)`: `__init__(self, current_version: str, parent=None)`, signal `check_finished = Signal(object)` (payload: `UpdateInfo | None`).
  - `class UpdateDownloadRunner(QThread)`: `__init__(self, info: UpdateInfo, dest_dir: Path, parent=None)`, signal `download_finished = Signal(object, str)` (payload: `(Path | None, error_message: str)` — empty string means no error).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
from portablefix.updater import UpdateCheckRunner, UpdateDownloadRunner


def test_update_check_runner_emits_none_when_no_update(qtbot):
    with patch("portablefix.updater.check_for_update", return_value=None):
        runner = UpdateCheckRunner("1.0.0")
        with qtbot.waitSignal(runner.check_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [None]


def test_update_check_runner_emits_update_info(qtbot):
    info = UpdateInfo(version="1.1.0", download_url="https://x", sha256_url=None, notes="")
    with patch("portablefix.updater.check_for_update", return_value=info):
        runner = UpdateCheckRunner("1.0.0")
        with qtbot.waitSignal(runner.check_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [info]


def test_update_download_runner_emits_path_on_success(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", download_url="https://x", sha256_url=None, notes="")
    fake_path = tmp_path / "PortableFix.new.exe"
    fake_path.write_bytes(b"x")
    with patch("portablefix.updater.download_update", return_value=fake_path):
        runner = UpdateDownloadRunner(info, tmp_path)
        with qtbot.waitSignal(runner.download_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [fake_path, ""]


def test_update_download_runner_emits_error_on_failure(qtbot, tmp_path):
    info = UpdateInfo(version="1.1.0", download_url="https://x", sha256_url=None, notes="")
    with patch("portablefix.updater.download_update", side_effect=UpdateVerificationError("bad hash")):
        runner = UpdateDownloadRunner(info, tmp_path)
        with qtbot.waitSignal(runner.download_finished, timeout=2000) as blocker:
            runner.start()
    assert blocker.args == [None, "bad hash"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_updater.py -k "runner" -v`
Expected: `ImportError: cannot import name 'UpdateCheckRunner'`.

- [ ] **Step 3: Add the runner classes to `portablefix/updater.py`**

Append at the end of `portablefix/updater.py` (after `apply_update`):

```python
from PySide6.QtCore import QThread, Signal


class UpdateCheckRunner(QThread):
    check_finished = Signal(object)

    def __init__(self, current_version: str, parent=None):
        super().__init__(parent)
        self._current_version = current_version
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        info = check_for_update(self._current_version)
        self.check_finished.emit(info)


class UpdateDownloadRunner(QThread):
    download_finished = Signal(object, str)

    def __init__(self, info: UpdateInfo, dest_dir: Path, parent=None):
        super().__init__(parent)
        self._info = info
        self._dest_dir = dest_dir
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            path = download_update(self._info, self._dest_dir)
            self.download_finished.emit(path, "")
        except Exception as exc:
            self.download_finished.emit(None, str(exc))
```

Move the `from PySide6.QtCore import QThread, Signal` import to the top of the file with the other imports instead of leaving it mid-file — final import block at the top of `portablefix/updater.py` should read:

```python
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .integrity import compute_sha256
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_updater.py -v`
Expected: all 18 tests PASS. Run individually if any hang (per Global Constraints, unlikely here since no real subprocess spawns in these specific tests, but stay consistent).

- [ ] **Step 5: Commit**

```bash
git add portablefix/updater.py tests/test_updater.py
git commit -m "feat: add QThread wrappers for update check and download"
```

---

### Task 3: GUI update banner (display only — show/hide/dismiss)

**Files:**
- Modify: `portablefix/gui/main_window.py`
- Modify: `portablefix/gui/style.py`
- Modify: `portablefix/i18n.py`
- Modify: `tests/test_gui_main_window.py`

**Interfaces:**
- Consumes: `portablefix.updater.UpdateInfo`, `portablefix.updater.UpdateCheckRunner` (Task 2), `portablefix.version.APP_VERSION` (Task 1).
- Produces (consumed by Task 4):
  - `MainWindow._pending_update_info: UpdateInfo | None` (instance attribute)
  - `MainWindow._update_check_runner: UpdateCheckRunner | None`, `MainWindow._update_download_runner: UpdateDownloadRunner | None` (instance attributes, `None` until first used)
  - `MainWindow.update_banner: QWidget`, `MainWindow.update_banner_label: QLabel`, `MainWindow.update_button: QPushButton`, `MainWindow.update_dismiss_button: QPushButton`
  - `MainWindow._start_update_check(self) -> None`
  - `MainWindow._on_update_check_finished(self, info) -> None`
  - `MainWindow._on_update_dismiss_clicked(self) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_main_window.py`:

```python
def test_update_banner_hidden_by_default(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_update1")
    qtbot.addWidget(window)
    assert window.update_banner.isVisible() is False


def test_update_banner_shows_when_check_finds_newer_version(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update2")
    qtbot.addWidget(window)

    info = UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes="")
    window._on_update_check_finished(info)

    assert window.update_banner.isVisible() is True
    assert "9.9.9" in window.update_banner_label.text()


def test_update_banner_check_finished_with_none_stays_hidden(qtbot, tmp_path):
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update3")
    qtbot.addWidget(window)

    window._on_update_check_finished(None)

    assert window.update_banner.isVisible() is False


def test_update_banner_dismiss_hides_it(qtbot, tmp_path):
    from portablefix.updater import UpdateInfo

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update4")
    qtbot.addWidget(window)

    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))
    window.update_dismiss_button.click()

    assert window.update_banner.isVisible() is False


def test_update_check_skipped_when_not_frozen(qtbot, tmp_path):
    # pytest never runs as a frozen PyInstaller build, so sys.frozen is
    # always falsy here - this proves _start_update_check's own guard,
    # not a monkeypatched substitute for it.
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(), is_admin=True, run_id="run_update5")
    qtbot.addWidget(window)
    assert window._update_check_runner is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gui_main_window.py -k "update_banner or update_check_skipped" -v`
Expected: `AttributeError: 'MainWindow' object has no attribute 'update_banner'`.

- [ ] **Step 3: Add i18n keys**

In `portablefix/i18n.py`, add to the `"sk"` dict (after `"dry_run_batch_note"`):

```python
        "update_available_banner": "Nova verzia {version} je dostupna",
        "update_button": "Aktualizovat",
        "update_dismiss": "Zavriet",
```

And the matching keys to the `"en"` dict (after its own `"dry_run_batch_note"`):

```python
        "update_available_banner": "Version {version} is available",
        "update_button": "Update",
        "update_dismiss": "Dismiss",
```

(Two more update-related keys, `update_confirm_download`/`update_confirm_restart`/`update_downloading`/`update_download_failed`/`update_not_writable`, are added in Task 4 alongside the code that uses them — keeping each task's i18n additions next to their consumer.)

- [ ] **Step 4: Add the banner widget and wiring to `portablefix/gui/main_window.py`**

Top-level imports — change:
```python
import os
import shutil
import sys
import time
from pathlib import Path
```
to:
```python
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
```

Change:
```python
from . import style
from .. import elevation, i18n, report, restore_point, undo
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..module_engine import load_all_modules
from ..settings import Settings
```
to:
```python
from . import style
from .. import elevation, i18n, paths, report, restore_point, undo, updater
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..module_engine import load_all_modules
from ..settings import Settings
from ..version import APP_VERSION
```

In `MainWindow.__init__`, add near the other new-state initializations (next to `self._cancel_requested = False`):
```python
        self._cancel_requested = False
        self._pending_update_info = None
        self._update_check_runner = None
        self._update_download_runner = None
```

Find the line `self._build_ui()` at the end of `__init__` and change it to:
```python
        self._build_ui()
        self._start_update_check()
```

In `_build_ui`, find `root_layout.addLayout(top_bar)` and insert immediately after it:
```python
        root_layout.addLayout(top_bar)

        self.update_banner = QWidget()
        self.update_banner.setObjectName("updateBanner")
        update_banner_layout = QHBoxLayout(self.update_banner)
        update_banner_layout.setContentsMargins(10, 6, 10, 6)
        self.update_banner_label = QLabel("")
        update_banner_layout.addWidget(self.update_banner_label, 1)
        self.update_button = QPushButton(self._t("update_button"))
        self.update_button.setObjectName("runButton")
        self.update_button.clicked.connect(lambda _checked=False: self._on_update_button_clicked())
        update_banner_layout.addWidget(self.update_button)
        self.update_dismiss_button = QPushButton(self._t("update_dismiss"))
        self.update_dismiss_button.setObjectName("selectionBtn")
        self.update_dismiss_button.clicked.connect(lambda _checked=False: self._on_update_dismiss_clicked())
        update_banner_layout.addWidget(self.update_dismiss_button)
        self.update_banner.setVisible(False)
        root_layout.addWidget(self.update_banner)
```

At the very end of `_build_ui` (find `self._update_status_bar()`, the last line of the method) add right after it:
```python
        self._update_status_bar()
        if self._pending_update_info is not None:
            self.update_banner_label.setText(
                self._t("update_available_banner").format(version=self._pending_update_info.version)
            )
            self.update_banner.setVisible(True)
```
(This restores the banner after a language-toggle rebuild, which calls `_build_ui()` again — `_start_update_check` is only ever called once from `__init__`, never from `_build_ui`, so language toggling never re-triggers a network check.)

Add new methods near `_on_dry_run_toggled`/`_on_toggle_language` (same general area — after `_on_toggle_language`, before `_on_restart_as_admin`):
```python
    def _start_update_check(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        self._update_check_runner = updater.UpdateCheckRunner(APP_VERSION, parent=self)
        self._update_check_runner.check_finished.connect(self._on_update_check_finished)
        self._update_check_runner.start()

    def _on_update_check_finished(self, info) -> None:
        if info is None:
            return
        self._pending_update_info = info
        self.update_banner_label.setText(self._t("update_available_banner").format(version=info.version))
        self.update_banner.setVisible(True)

    def _on_update_dismiss_clicked(self) -> None:
        self.update_banner.setVisible(False)
```

- [ ] **Step 5: Add QSS for the banner in `portablefix/gui/style.py`**

Add after the `QDialog { ... }` block:
```
QWidget#updateBanner {
    background-color: #24283b;
    border: 1px solid #7aa2f7;
    border-radius: 8px;
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run each individually (GUI tests, per Global Constraints):
```bash
python -m pytest tests/test_gui_main_window.py::test_update_banner_hidden_by_default -v
python -m pytest tests/test_gui_main_window.py::test_update_banner_shows_when_check_finds_newer_version -v
python -m pytest tests/test_gui_main_window.py::test_update_banner_check_finished_with_none_stays_hidden -v
python -m pytest tests/test_gui_main_window.py::test_update_banner_dismiss_hides_it -v
python -m pytest tests/test_gui_main_window.py::test_update_check_skipped_when_not_frozen -v
```
Expected: all PASS. Also run `python -m pytest tests/test_i18n.py -v` (key-parity test must still pass with the 3 new keys added to both languages).

Then run the full existing GUI suite individually (same loop pattern used throughout this session) to confirm nothing else broke:
```bash
python -m pytest tests/test_gui_main_window.py --collect-only -q
```
then loop each collected test id through `python -m pytest "<id>" -q`, retrying once on failure (transient native crash, not a real failure, per Global Constraints).

- [ ] **Step 7: Commit**

```bash
git add portablefix/gui/main_window.py portablefix/gui/style.py portablefix/i18n.py tests/test_gui_main_window.py
git commit -m "feat: show a dismissible banner when an update is available"
```

---

### Task 4: GUI update flow — click, confirm, download, confirm, apply, quit

**Files:**
- Modify: `portablefix/gui/main_window.py`
- Modify: `portablefix/i18n.py`
- Modify: `tests/test_gui_main_window.py`

**Interfaces:**
- Consumes: everything Task 3 produced, plus `updater.UpdateDownloadRunner`, `updater.download_update`, `updater.is_writable`, `updater.apply_update` (Task 1/2), `paths.get_base_dir()` (existing).
- Produces:
  - `MainWindow._quit_app(self) -> None` (thin wrapper around `QApplication.instance().quit()`, exists purely so tests can monkeypatch it without touching the real Qt event loop the test session's `qtbot` depends on)
  - `MainWindow._on_update_button_clicked(self) -> None`
  - `MainWindow._on_update_download_finished(self, new_exe_path, error: str) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_main_window.py`:

```python
def test_update_button_click_declined_confirm_does_not_start_download(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))
    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update6")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    assert window._update_download_runner is None


def test_update_button_click_confirmed_downloads_and_applies_update(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: True)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "apply_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update7")
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_quit_app", lambda: applied.setdefault("quit_called", True))
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: applied.get("called") is True, timeout=5000)
    assert applied.get("quit_called") is True


def test_update_download_failure_shows_error_and_reenables_button(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    def raise_it(info, dest):
        raise Exception("boom")

    monkeypatch.setattr(mw_module.updater, "download_update", raise_it)

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update8")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_button.isEnabled() is True, timeout=5000)
    assert window.update_banner_label.text() == "Downloading the update failed. Try again later."


def test_update_not_writable_shows_error_without_applying(qtbot, tmp_path, monkeypatch):
    from portablefix.updater import UpdateInfo
    from portablefix.gui import main_window as mw_module

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    fake_exe = tmp_path / "PortableFix.new.exe"
    fake_exe.write_bytes(b"x")
    monkeypatch.setattr(mw_module.updater, "download_update", lambda info, dest: fake_exe)
    monkeypatch.setattr(mw_module.updater, "is_writable", lambda p: False)
    applied = {}
    monkeypatch.setattr(mw_module.updater, "apply_update", lambda *a, **k: applied.setdefault("called", True))

    base_dir = _make_base_dir(tmp_path)
    window = MainWindow(assets_dir=base_dir, state_dir=base_dir, settings=Settings(language="en"), is_admin=True, run_id="run_update9")
    qtbot.addWidget(window)
    window._on_update_check_finished(UpdateInfo(version="9.9.9", download_url="https://x", sha256_url=None, notes=""))

    window.update_button.click()

    qtbot.waitUntil(lambda: window.update_banner_label.text() == "The app folder is not writable, the update cannot be applied.", timeout=5000)
    assert applied.get("called") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run each individually:
```bash
python -m pytest tests/test_gui_main_window.py::test_update_button_click_declined_confirm_does_not_start_download -v
```
Expected: `AttributeError: 'MainWindow' object has no attribute '_on_update_button_clicked'` (button click connects to nothing yet, or `AttributeError` on `_update_download_runner` access pattern — either way, fails before the assertion).

- [ ] **Step 3: Add the remaining i18n keys**

In `portablefix/i18n.py`, `"sk"` dict, add next to the three keys from Task 3:
```python
        "update_confirm_download": "Stiahnut a pripravit aktualizaciu na verziu {version}?",
        "update_confirm_restart": "Aplikacia sa teraz restartuje a aktualizuje na verziu {version}. Pokracovat?",
        "update_downloading": "Stahujem aktualizaciu...",
        "update_download_failed": "Stiahnutie aktualizacie zlyhalo. Skus znova neskor.",
        "update_not_writable": "Priecinok appky nie je zapisovatelny, aktualizaciu nie je mozne aplikovat.",
```

`"en"` dict:
```python
        "update_confirm_download": "Download and prepare the update to version {version}?",
        "update_confirm_restart": "The app will now restart and update to version {version}. Continue?",
        "update_downloading": "Downloading update...",
        "update_download_failed": "Downloading the update failed. Try again later.",
        "update_not_writable": "The app folder is not writable, the update cannot be applied.",
```

- [ ] **Step 4: Add the click-flow methods to `portablefix/gui/main_window.py`**

Add right after `_on_update_dismiss_clicked` (from Task 3):
```python
    def _quit_app(self) -> None:
        QApplication.instance().quit()

    def _on_update_button_clicked(self) -> None:
        if self._pending_update_info is None:
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_download").format(version=self._pending_update_info.version),
        )
        if confirmed != QMessageBox.Yes:
            return
        dest_dir = Path(tempfile.gettempdir()) / "PortableFixUpdate"
        self.update_banner_label.setText(self._t("update_downloading"))
        self.update_button.setEnabled(False)
        self._update_download_runner = updater.UpdateDownloadRunner(self._pending_update_info, dest_dir, parent=self)
        self._update_download_runner.download_finished.connect(self._on_update_download_finished)
        self._update_download_runner.start()

    def _on_update_download_finished(self, new_exe_path, error: str) -> None:
        self.update_button.setEnabled(True)
        if not new_exe_path:
            self.update_banner_label.setText(self._t("update_download_failed"))
            return
        current_exe = Path(sys.executable)
        if not updater.is_writable(current_exe.parent):
            self.update_banner_label.setText(self._t("update_not_writable"))
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_restart").format(version=self._pending_update_info.version),
        )
        if confirmed != QMessageBox.Yes:
            self.update_banner_label.setText(
                self._t("update_available_banner").format(version=self._pending_update_info.version)
            )
            return
        sums_path = paths.get_base_dir() / "Data" / "SHA256SUMS"
        updater.apply_update(new_exe_path, current_exe, sums_path)
        self._quit_app()
```

`QApplication` needs to be importable in `main_window.py` for `_quit_app` — check the top of the file: if `QApplication` is not already in the `from PySide6.QtWidgets import (...)` block, add it there (alphabetical, matching the existing list style).

- [ ] **Step 5: Run tests to verify they pass**

Run each individually:
```bash
python -m pytest tests/test_gui_main_window.py::test_update_button_click_declined_confirm_does_not_start_download -v
python -m pytest tests/test_gui_main_window.py::test_update_button_click_confirmed_downloads_and_applies_update -v
python -m pytest tests/test_gui_main_window.py::test_update_download_failure_shows_error_and_reenables_button -v
python -m pytest tests/test_gui_main_window.py::test_update_not_writable_shows_error_without_applying -v
```
Expected: all PASS. Then run `python -m pytest tests/test_i18n.py -v` again (parity test, now 8 new keys total).

Then re-run the full GUI suite individually one more time (collect + loop-with-retry, same as Task 3 Step 6) to confirm the whole file is still green end to end — this task's edits touch shared methods (`__init__`, `_build_ui`) that every other GUI test also exercises.

- [ ] **Step 6: Commit**

```bash
git add portablefix/gui/main_window.py portablefix/i18n.py tests/test_gui_main_window.py
git commit -m "feat: wire update button to download, verify, and self-apply flow"
```

---

### Task 5: Switch `scripts/build.ps1` to `--onefile`

**Files:**
- Modify: `scripts/build.ps1`

**Interfaces:**
- Consumes: nothing from Tasks 1-4 (independent of the Python code changes — could technically run in parallel, sequenced last here only because it needs a working build to smoke-test against, and this plan runs tasks in order).
- Produces: `App/PortableFix.exe` as a single self-contained file, no `App/_internal/` folder — this is what `apply_update`'s swap script (Task 1) assumes it's replacing.

- [ ] **Step 1: Edit `scripts/build.ps1`**

Replace the full file content with:
```powershell
# scripts/build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$distStage = "$root\App"

# --onefile bundles everything (bootloader + all Python bytecode + deps)
# into a single .exe. This matters for auto-update: swapping just the
# .exe is a complete, correct update. With the old --onedir layout the
# actual app code lived in a separate _internal/PYZ-00.pyz next to a
# thin bootloader .exe, so swapping only the .exe would have left stale
# code running.
pyinstaller --onefile --noconsole --noconfirm --distpath $distStage --workpath "$root\build" --specpath "$root\build" `
  --add-data "$root\Modules;Modules" `
  --add-data "$root\Data;Data" `
  --add-data "$root\portablefix.ico;." `
  --icon "$root\portablefix.ico" `
  --name PortableFix `
  "$root\main.py"

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

py "$root\scripts\generate_sha256sums.py" "$root"

Write-Host "Build complete. Run PortableFix.cmd from $root to launch."
```

(Removes the entire "flatten nested PortableFix folder" block — `--onefile` writes `PortableFix.exe` directly to `$distStage`, no nested subfolder to flatten.)

- [ ] **Step 2: Verify PortableFix.exe isn't running, then build**

```bash
tasklist //FI "IMAGENAME eq PortableFix.exe"
```
If it shows a running process, stop before continuing (closing it is the user's call if it's their foreground app — ask first, matching how this was handled earlier in the session).

```bash
powershell -ExecutionPolicy Bypass -File ./scripts/build.ps1
```
Expected: build succeeds, no PyInstaller errors.

- [ ] **Step 3: Verify the onefile layout**

```bash
ls "App"
```
Expected: `PortableFix.exe` present, **no** `_internal` directory (onefile has none — everything is inside the single exe).

- [ ] **Step 4: Smoke-test the built exe actually runs**

```powershell
$p = Start-Process -FilePath "App\PortableFix.exe" -PassThru
Start-Sleep -Seconds 5
$stillRunning = Get-Process -Id $p.Id -ErrorAction SilentlyContinue
if ($stillRunning) { Write-Host "OK: still running after 5s" } else { Write-Host "FAIL: exited early" }
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
```
Expected: "OK: still running after 5s" (onefile self-extraction adds a startup delay of roughly 1-3 seconds — this confirms it survives past that and doesn't crash on launch). If it prints "FAIL", do not proceed — investigate before committing (check for a PyInstaller `--onefile` hook issue with PySide6, a known occasional pain point; the existing `hook-PySide6.py` etc. already run during the build and should handle this, but confirm rather than assume).

- [ ] **Step 5: Commit**

```bash
git add scripts/build.ps1
git commit -m "build: switch packaging from --onedir to --onefile for auto-update"
```

Do not sign or release from this task — signing (`signtool`) and cutting a GitHub Release are the manual runbook documented in the spec, run separately whenever the user is ready to ship a version with auto-update live.

---

## Post-plan note (not a task, context for whoever runs this)

After all 5 tasks land, `APP_VERSION` in `portablefix/version.py` is still `"1.0.0"` and no GitHub Release exists yet with that tag — `check_for_update` will find nothing newer than itself and stay silent, which is the correct, safe steady state. The auto-update mechanism only does anything once the user actually cuts a `v1.0.1`-or-later GitHub Release following the runbook in the spec (`docs/superpowers/specs/2026-09-02-autoupdate-design.md`).
