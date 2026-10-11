"""Release-zip and install fixtures shared by the update tests.

The zips copy the shape of the real v1.11.4 release: Compress-Archive on
Windows PowerShell 5.1 wrote backslash-separated names, no entry for most
folders, and only a stray one for Modules\\ and Vendor\\."""
import hashlib
import struct
import zipfile
from pathlib import Path

from signing_keys import sign_for_tests

from portablefix.sha256sums import manifest_version_line

NEW_EXE = b"new-exe"
# Newer than any APP_VERSION, so a stage_update in the tests passes the
# version check without pinning the tests to the current release number.
NEW_VERSION = "99.0.0"


def noop_gui_exe(variant: int = 0) -> bytes:
    """A 1 KB PE32+ GUI-subsystem exe that does nothing and exits 0 (it only calls ExitProcess(0)).

    A byte copy of a System32 console tool (whoami.exe) relaunched from a temp folder can die with
    "Application Error 0xc0000142" - a dialog on a developer's desktop. This one imports one function,
    has no manifest and no console, so the loader has nothing to fail on. `variant` only changes the
    timestamp field, to get a second, byte-different exe (old install vs. update)."""
    file_align, rva = 0x200, 0x1000
    dos = b"MZ" + bytes(58) + struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0x5F000000 + variant, 0, 0, 0xF0, 0x22)
    optional = struct.pack(
        "<HBBIIIIIQIIHHHHHHIIIIHHQQQQII",
        0x20B, 14, 0, file_align, 0, 0, rva, rva,         # magic, linker, SizeOfCode.., entry point, BaseOfCode
        0x140000000, rva, file_align,                     # ImageBase, SectionAlignment, FileAlignment
        6, 0, 0, 0, 6, 0,                                 # OS / image / subsystem versions
        0, 0x2000, file_align, 0, 2, 0x8100,              # Win32Version, SizeOfImage/Headers, CheckSum, GUI, NX|TS-aware
        0x100000, 0x1000, 0x100000, 0x1000, 0, 16,        # stack / heap sizes, LoaderFlags, directory count
    ) + bytes(8) + struct.pack("<II", rva + 0x40, 40) + bytes(14 * 8)  # data directory 1 = imports
    section = b".text\0\0\0" + struct.pack("<IIIIIIHHI", 0x200, rva, file_align, file_align, 0, 0, 0, 0, 0xE0000060)
    headers = dos + b"PE\0\0" + coff + optional + section
    body = bytearray(file_align)
    code = b"\x31\xc9" + b"\x48\x83\xec\x28" + b"\xff\x15" + struct.pack("<i", 0x90 - 12)  # ExitProcess(0)
    body[0:len(code)] = code
    body[0x40:0x54] = struct.pack("<IIIII", rva + 0x80, 0, 0, rva + 0xB0, rva + 0x90)  # then a zero descriptor
    body[0x80:0x90] = struct.pack("<QQ", rva + 0xA0, 0)   # import lookup table
    body[0x90:0xA0] = struct.pack("<QQ", rva + 0xA0, 0)   # import address table
    body[0xA0:0xAE] = struct.pack("<H", 0) + b"ExitProcess\0"
    body[0xB0:0xBD] = b"KERNEL32.dll\0"
    return headers.ljust(file_align, b"\0") + bytes(body)


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


def sums_for(files: dict[str, bytes], skip: tuple[str, ...] = (), version: str | None = NEW_VERSION) -> bytes:
    # Same coverage as scripts/generate_sha256sums.py: App/, Modules/, Vendor/
    # and the root files; version=None leaves the version line out (a
    # manifest of <= 1.16).
    from portablefix.integrity import TARGET_DIRS
    from portablefix.update_swap import ROOT_ALLOWLIST

    lines = [manifest_version_line(version)] if version else []
    lines += [
        f"{hashlib.sha256(data).hexdigest()}  {rel}\n"
        for rel, data in sorted(files.items())
        if (rel.split("/")[0] in TARGET_DIRS or rel in ROOT_ALLOWLIST) and rel not in skip
    ]
    # Signed like a real release (research G32), with the test key.
    return sign_for_tests("".join(lines).encode("ascii"))


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
