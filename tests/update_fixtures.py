"""Release-zip and install fixtures shared by the update tests.

The zips copy the shape of the real v1.11.4 release: Compress-Archive on
Windows PowerShell 5.1 wrote backslash-separated names, no entry for most
folders, and only a stray one for Modules\\ and Vendor\\."""
import hashlib
import zipfile
from pathlib import Path

NEW_EXE = b"new-exe"


def release_files(exe: bytes = NEW_EXE) -> dict[str, bytes]:
    return {
        "App/PortableFix.exe": exe,
        "Modules/m01_diagnostics/actions.yaml": b"new-m",
        "Vendor/Fonts/OFL.txt": b"new-v",
        "Data/.gitkeep": b"\r\n",
        "Data/PortableFix-SelfSigned.cer": b"new-cer",
        # Older zips shipped the build machine's settings - must never be
        # installed over the user's.
        "Data/settings.json": b'{"language": "BUILD MACHINE"}',
        "PortableFix.cmd": b"@echo off\r\nnew-launcher\r\n",
    }


def sums_for(files: dict[str, bytes], skip: tuple[str, ...] = ()) -> bytes:
    # Same coverage as scripts/generate_sha256sums.py: App/, Modules/, Vendor/.
    from portablefix.integrity import TARGET_DIRS

    lines = [
        f"{hashlib.sha256(data).hexdigest()}  {rel}\n"
        for rel, data in sorted(files.items())
        if rel.split("/")[0] in TARGET_DIRS and rel not in skip
    ]
    return "".join(lines).encode("ascii")


def write_release_zip(
    zip_path: Path,
    files: dict[str, bytes] | None = None,
    *,
    sums: bytes | None = None,
    top: str = "PortableFix",
    extra_names: dict[str, bytes] | None = None,
) -> Path:
    """files maps forward-slash paths inside the top folder to bytes; sums
    defaults to a correct Data/SHA256SUMS (pass b"" to leave it out).
    extra_names are raw zip entry names, written as-is."""
    if files is None:
        files = release_files()
    files = dict(files)
    if sums is None:
        sums = sums_for(files)
    if sums:
        files["Data/SHA256SUMS"] = sums
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder in ("Modules", "Vendor"):
            if any(rel.startswith(folder + "/") for rel in files):
                _write(zf, f"{top}\\{folder}\\", b"")
        for rel, data in files.items():
            _write(zf, top + "\\" + rel.replace("/", "\\"), data)
        for name, data in (extra_names or {}).items():
            _write(zf, name, data)
    return zip_path


def _write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo("x")
    # Assigned after construction: ZipInfo() turns backslashes into slashes
    # on Windows, and the point is to keep the release's own separators.
    info.filename = name
    info.compress_type = zipfile.ZIP_STORED if name.endswith("\\") else zipfile.ZIP_DEFLATED
    zf.writestr(info, data)


def make_install(install_dir: Path) -> None:
    for rel, data in {
        "App/PortableFix.exe": b"old-exe",
        "Modules/mod.yaml": b"old-m",
        "Vendor/vendor.dll": b"old-v",
        "Data/settings.json": b'{"k": "v"}',
        "Data/SHA256SUMS": b"old-sums",
        "Data/notes.txt": b"user file",
        "PortableFix.cmd": b"@echo off\r\nold-launcher\r\n",
    }.items():
        path = install_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
