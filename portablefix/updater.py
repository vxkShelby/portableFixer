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
    package_url: str
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
        zip_asset = next((a for a in assets if a.get("name", "").lower() == "portablefix-portable.zip"), None)
        if not zip_asset:
            return None
        sha_asset = next(
            (a for a in assets if a.get("name", "").lower() == "portablefix-portable.zip.sha256"), None
        )
        return UpdateInfo(
            version=tag.lstrip("vV"),
            package_url=zip_asset["browser_download_url"],
            sha256_url=sha_asset["browser_download_url"] if sha_asset else None,
            notes=data.get("body", ""),
        )
    except Exception:
        return None


def download_update(info: UpdateInfo, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "PortableFix-update.zip"
    try:
        urllib.request.urlretrieve(info.package_url, zip_path)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    if info.sha256_url:
        with urllib.request.urlopen(info.sha256_url, timeout=10) as resp:
            expected = resp.read().decode("utf-8").strip().split()[0].lower()
        actual = compute_sha256(zip_path)
        if actual.lower() != expected:
            zip_path.unlink(missing_ok=True)
            raise UpdateVerificationError("Downloaded package does not match expected SHA256.")
    return zip_path


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


def _ps_quote(value: str) -> str:
    # Single-quoted PowerShell strings never interpolate $variables, unlike
    # the double-quoted strings this previously used - a literal '$' in a
    # path (a legal NTFS character, e.g. a username) would otherwise be
    # misread as a variable reference and silently truncate the path.
    return "'" + value.replace("'", "''") + "'"


def build_swap_script(current_pid: int, install_dir: Path, zip_path: Path) -> str:
    # The downloaded zip's contract (produced by scripts/build_release_zip.ps1):
    # exactly one top-level folder containing App/, Data/, Modules/, PortableFix.cmd.
    app_dir = _ps_quote(str(install_dir / "App"))
    app_bak = _ps_quote(str(install_dir / "App.old"))
    app_exe = _ps_quote(str(install_dir / "App" / "PortableFix.exe"))
    modules_dir = _ps_quote(str(install_dir / "Modules"))
    modules_bak = _ps_quote(str(install_dir / "Modules.old"))
    data_dir = _ps_quote(str(install_dir / "Data"))
    settings_json = _ps_quote(str(install_dir / "Data" / "settings.json"))
    cmd_path = _ps_quote(str(install_dir / "PortableFix.cmd"))
    zip_p = _ps_quote(str(zip_path))
    stage = _ps_quote(str(zip_path.parent / "PortableFixUpdateStage"))
    settings_bak = _ps_quote(str(zip_path.parent / "settings.json.bak"))
    return (
        '$ErrorActionPreference = "SilentlyContinue"\n'
        f"for ($i = 0; $i -lt 30; $i++) {{\n"
        f"    if (-not (Get-Process -Id {current_pid} -EA SilentlyContinue)) {{ break }}\n"
        "    Start-Sleep -Milliseconds 500\n"
        "}\n"
        "Start-Sleep -Milliseconds 300\n"
        f"if (Test-Path {settings_json}) {{ Copy-Item -Path {settings_json} -Destination {settings_bak} -Force }}\n"
        f"Expand-Archive -Path {zip_p} -DestinationPath {stage} -Force\n"
        f"$stagedRoot = (Get-ChildItem -Path {stage} -Directory | Select-Object -First 1).FullName\n"
        f"if (Test-Path {app_dir}) {{ Move-Item -Path {app_dir} -Destination {app_bak} -Force }}\n"
        f"if (Test-Path {modules_dir}) {{ Move-Item -Path {modules_dir} -Destination {modules_bak} -Force }}\n"
        f"Move-Item -Path \"$stagedRoot\\App\" -Destination {app_dir} -Force\n"
        f"Move-Item -Path \"$stagedRoot\\Modules\" -Destination {modules_dir} -Force\n"
        f"Copy-Item -Path \"$stagedRoot\\Data\\*\" -Destination {data_dir} -Recurse -Force\n"
        f"Copy-Item -Path \"$stagedRoot\\PortableFix.cmd\" -Destination {cmd_path} -Force\n"
        f"if (Test-Path {settings_bak}) {{ Copy-Item -Path {settings_bak} -Destination {settings_json} -Force }}\n"
        f"if (Test-Path {app_exe}) {{\n"
        f"    Remove-Item -Path {app_bak} -Recurse -Force -EA SilentlyContinue\n"
        f"    Remove-Item -Path {modules_bak} -Recurse -Force -EA SilentlyContinue\n"
        "} else {\n"
        f"    Remove-Item -Path {app_dir} -Recurse -Force -EA SilentlyContinue\n"
        f"    Remove-Item -Path {modules_dir} -Recurse -Force -EA SilentlyContinue\n"
        f"    if (Test-Path {app_bak}) {{ Move-Item -Path {app_bak} -Destination {app_dir} -Force }}\n"
        f"    if (Test-Path {modules_bak}) {{ Move-Item -Path {modules_bak} -Destination {modules_dir} -Force }}\n"
        "}\n"
        # Relaunch first, then clean up temp files - a freshly-downloaded
        # zip can sit under active AV scanning for many seconds, and that
        # must never delay the user seeing their updated app come back.
        f"Start-Process -FilePath {cmd_path}\n"
        f"Remove-Item -Path {stage} -Recurse -Force -EA SilentlyContinue\n"
        "for ($i = 0; $i -lt 30; $i++) {\n"
        f"    if (-not (Test-Path {zip_p})) {{ break }}\n"
        f"    Remove-Item -Path {zip_p} -Force -EA SilentlyContinue\n"
        "    Start-Sleep -Milliseconds 1000\n"
        "}\n"
        f"Remove-Item -Path {settings_bak} -Force -EA SilentlyContinue\n"
    )


def apply_update(zip_path: Path, install_dir: Path) -> bool:
    if not is_writable(install_dir):
        return False
    current_pid = os.getpid()
    script_text = build_swap_script(current_pid, install_dir, zip_path)
    script_path = Path(tempfile.gettempdir()) / f"portablefix_update_{current_pid}.ps1"
    try:
        script_path.write_text(script_text, encoding="utf-8-sig")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(script_path)],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except OSError:
        return False


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
