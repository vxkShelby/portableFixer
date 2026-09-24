"""A PyInstaller-built app updates itself from v1 to v2, and v2 comes back up.

The only test that runs the update the way users do: a real onefile exe
(tests/frozen/probe_app.py) stages a release-shaped zip, hands off to the
swap, exits; the swap waits for it AND its bootloader parent, replaces
App\\, and relaunches the new exe, which must start with a fresh _MEI
folder (not the dead v1's, which "Failed to load Python DLL") in the
install root. In CI the install lives on the runner's work drive and TEMP
on the system drive, in a folder whose name has a typographic quote,
brackets and diacritics.

Needs the two exes the CI job frozen-update-e2e builds (.github/workflows/
tests.yml): PF_E2E_V1 and PF_E2E_V2; PF_E2E_WORKDIR picks the drive for the
installs. Skipped everywhere else.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

V1 = os.environ.get("PF_E2E_V1", "")
V2 = os.environ.get("PF_E2E_V2", "")
REPO_ROOT = Path(__file__).resolve().parent.parent
HOSTILE_NAME = "Jano’s PF [2024] ľščť"

pytestmark = [
    pytest.mark.frozen_e2e,
    pytest.mark.skipif(
        sys.platform != "win32" or not (V1 and V2),
        reason="needs PF_E2E_V1/PF_E2E_V2 from the frozen-update-e2e CI job",
    ),
]


@pytest.fixture
def work_dir(tmp_path):
    root = Path(os.environ["PF_E2E_WORKDIR"]) / tmp_path.name if os.environ.get("PF_E2E_WORKDIR") else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    yield root
    # Whatever is left of the probes (a relaunched v2 that hung, a swap that
    # never finished) must not outlive the test. CI only - see the skip.
    subprocess.run(["taskkill", "/IM", "PortableFix.exe", "/F", "/T"], capture_output=True, timeout=60)


def _release_zip(work: Path) -> Path:
    """The v2 package in the release shape: one PortableFix folder, zipped by
    Compress-Archive like scripts/build_release_zip.ps1 does, with a
    SHA256SUMS from scripts/generate_sha256sums.py."""
    tree = work / "release" / "PortableFix"
    for rel, data in {
        "Modules/m00_probe/actions.yaml": b"new-module",
        "Vendor/probe.txt": b"new-vendor",
        "Data/.gitkeep": b"",
    }.items():
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (tree / "App").mkdir()
    shutil.copyfile(V2, tree / "App" / "PortableFix.exe")
    shutil.copyfile(REPO_ROOT / "PortableFix.cmd", tree / "PortableFix.cmd")
    shutil.copyfile(REPO_ROOT / "portablefix.ico", tree / "portablefix.ico")
    subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "generate_sha256sums.py"), str(tree)],
                   check=True, timeout=120)
    zip_path = work / "PortableFix-Portable.zip"
    from portablefix.paths import powershell_executable

    env = dict(os.environ, PF_TREE=str(tree), PF_ZIP=str(zip_path))
    subprocess.run(
        [powershell_executable(), "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference = 'Stop'; Compress-Archive -Path $env:PF_TREE -DestinationPath $env:PF_ZIP"],
        env=env, check=True, timeout=300,
    )
    return zip_path


def _install(work: Path) -> Path:
    install_dir = work / "install" / HOSTILE_NAME
    for rel, data in {
        "Modules/m00_probe/actions.yaml": b"old-module",
        "Vendor/probe.txt": b"old-vendor",
        "Data/settings.json": b'{"language": "sk"}',
        "PortableFix.cmd": b"@echo off\r\nold-launcher\r\n",
    }.items():
        path = install_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (install_dir / "App").mkdir()
    shutil.copyfile(V1, install_dir / "App" / "PortableFix.exe")
    return install_dir


def _read_json(path: Path, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
        time.sleep(0.5)


def _status(install_dir: Path) -> str | None:
    try:
        return (install_dir / "Data" / "update_status.txt").read_text(encoding="ascii").strip()
    except OSError:
        return None


def _wait_for_status(install_dir: Path, expected: str, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while True:
        status = _status(install_dir)
        if status == expected or time.monotonic() >= deadline:
            return status
        time.sleep(0.5)


def _logs(launch: dict | None) -> str:
    """Everything the updater wrote, for the failure message."""
    if not launch or not launch.get("log_dir"):
        return "(no log folder known)"
    texts = []
    for path in sorted(Path(launch["log_dir"]).glob(f"*_{launch['pid']}*")):
        if path.suffix in (".txt", ".log"):
            texts.append(f"--- {path.name}\n{path.read_text(encoding='utf-8-sig', errors='replace')}")
    return "\n".join(texts) or "(no logs)"


@pytest.mark.parametrize("start_in", ["install_root", "app_folder"])
def test_frozen_app_updates_itself_and_the_new_version_relaunches(work_dir, start_in):
    zip_path = _release_zip(work_dir)
    install_dir = _install(work_dir)
    # The Start-menu shortcuts of <= 1.11.4 installs start the app in App\.
    cwd = install_dir if start_in == "install_root" else install_dir / "App"

    probe = subprocess.run(
        [str(install_dir / "App" / "PortableFix.exe"), "--probe-apply", str(zip_path)], cwd=str(cwd), timeout=120,
    )

    launch = _read_json(install_dir / "Data" / "probe_launch.json", 5)
    print(f"probe launch: {launch}")
    assert probe.returncode == 0 and launch and launch["ok"], (launch, _logs(launch))
    assert launch["version"] == "1"
    # The bootloader parent was found, so the swap waited for it too.
    assert launch["parent_pid"], launch
    status = _wait_for_status(install_dir, "ok", 120)
    assert status == "ok", (status, _logs(launch))
    assert (install_dir / "App" / "PortableFix.exe").read_bytes() == Path(V2).read_bytes()
    assert (install_dir / "Modules" / "m00_probe" / "actions.yaml").read_bytes() == b"new-module"
    assert (install_dir / "Data" / "settings.json").read_bytes() == b'{"language": "sk"}'

    relaunched = _read_json(install_dir / "Data" / "relaunched.json", 60)
    print(f"relaunched: {relaunched}")
    assert relaunched, ("v2 never started - 'Failed to load Python DLL' if it reused v1's _MEI", _logs(launch))
    assert relaunched["version"] == "2"
    assert relaunched["meipass"] != launch["meipass"]
    assert "--post-update" in relaunched["argv"]
    assert os.path.samefile(relaunched["cwd"], install_dir)
