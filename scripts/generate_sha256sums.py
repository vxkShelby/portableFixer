import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portablefix.integrity import TARGET_DIRS, compute_sha256


def collect_files(base_dir: Path) -> list[Path]:
    files: list[Path] = []
    for target in TARGET_DIRS:
        target_dir = base_dir / target
        if target_dir.exists():
            files.extend(p for p in target_dir.rglob("*") if p.is_file())
    return files


def build_sums_content(base_dir: Path, files: list[Path]) -> str:
    lines = []
    for file_path in sorted(files):
        rel = file_path.relative_to(base_dir).as_posix()
        lines.append(f"{compute_sha256(file_path)}  {rel}")
    return "\n".join(lines) + "\n"


def main() -> None:
    base_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    files = collect_files(base_dir)
    content = build_sums_content(base_dir, files)
    sums_dir = base_dir / "Data"
    sums_dir.mkdir(parents=True, exist_ok=True)
    (sums_dir / "SHA256SUMS").write_text(content, encoding="utf-8")
    print(f"Wrote {len(files)} entries to {sums_dir / 'SHA256SUMS'}")


if __name__ == "__main__":
    main()
