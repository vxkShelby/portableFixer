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
        if not directory.exists():
            return False
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
