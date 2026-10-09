"""Writes Data/SHA256SUMS for a built tree:

    generate_sha256sums.py [<tree>] [<version>]

The version line is what the client's stage_update compares against its
own APP_VERSION (an older signed release can never be installed as an
update); it defaults to this checkout's APP_VERSION."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portablefix.integrity import TARGET_DIRS, compute_sha256
from portablefix.sha256sums import manifest_version_line
from portablefix.update_swap import ROOT_ALLOWLIST
from portablefix.version import APP_VERSION


def collect_files(base_dir: Path) -> list[Path]:
    """The program folders plus the root files the update installs. The
    integrity check at runtime only looks at the folders (a root entry is
    ignored by every client's check_integrity), so the root files are
    covered where it matters: when a package is staged."""
    files: list[Path] = []
    for target in TARGET_DIRS:
        target_dir = base_dir / target
        if target_dir.exists():
            files.extend(p for p in target_dir.rglob("*") if p.is_file())
    files += [base_dir / name for name in ROOT_ALLOWLIST if (base_dir / name).is_file()]
    return files


def build_sums_content(base_dir: Path, files: list[Path], version: str = APP_VERSION) -> str:
    lines = [manifest_version_line(version).rstrip("\n")]
    for file_path in sorted(files):
        rel = file_path.relative_to(base_dir).as_posix()
        lines.append(f"{compute_sha256(file_path)}  {rel}")
    return "\n".join(lines) + "\n"


def main() -> None:
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    version = sys.argv[2] if len(sys.argv) > 2 else APP_VERSION
    files = collect_files(base_dir)
    content = build_sums_content(base_dir, files, version)
    sums_dir = base_dir / "Data"
    sums_dir.mkdir(parents=True, exist_ok=True)
    (sums_dir / "SHA256SUMS").write_text(content, encoding="utf-8")
    print(f"Wrote {len(files)} entries for version {version} to {sums_dir / 'SHA256SUMS'}")


if __name__ == "__main__":
    main()
