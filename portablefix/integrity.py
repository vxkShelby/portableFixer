import hashlib
from pathlib import Path

from PySide6.QtCore import QThread, Signal

TARGET_DIRS = ("App", "Modules")


def compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256sums(sums_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, rel_path = parts
        result[rel_path.strip()] = digest.strip().lower()
    return result


def _iter_real_files(root: Path):
    """Yield files under root without ever descending into a symlink or an
    NTFS junction (mklink /J - a reparse point pathlib does NOT treat as a
    symlink, so `Path.is_symlink()` alone misses it). rglob() itself already
    recurses into a directory before any per-entry check can run, so a
    planted junction that points back to an ancestor makes it loop forever;
    walking directories ourselves lets us refuse to descend in the first
    place instead of only filtering results after the fact."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or entry.is_junction():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file():
                yield entry


def check_integrity(base_dir: Path) -> list[str]:
    sums_path = base_dir / "Data" / "SHA256SUMS"
    if not sums_path.exists():
        return []
    try:
        expected = parse_sha256sums(sums_path)
    except (OSError, UnicodeDecodeError):
        # An unreadable/corrupted manifest is a failure of this optional,
        # best-effort tamper check itself - it must not take down the app.
        return []
    mismatches = []
    seen: set[str] = set()
    # A single walk covers both directions: a file present on disk that
    # changed or was never in the manifest (extra DLL, planted module), and
    # (via `seen`, checked below) a manifest entry that vanished entirely.
    for target in TARGET_DIRS:
        target_dir = base_dir / target
        if not target_dir.exists():
            continue
        for file_path in _iter_real_files(target_dir):
            rel_path = file_path.relative_to(base_dir).as_posix()
            seen.add(rel_path)
            expected_hash = expected.get(rel_path)
            if expected_hash is None or compute_sha256(file_path) != expected_hash:
                mismatches.append(rel_path)
    for rel_path in expected:
        if rel_path not in seen:
            mismatches.append(rel_path)
    return mismatches


class IntegrityCheckRunner(QThread):
    """Hashing every file under App/ and Modules/ can take a visible moment
    on slow USB media - runs off the GUI thread so the window can appear
    immediately instead of stalling on a blank screen at every launch."""

    check_finished = Signal(list)

    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self._base_dir = base_dir
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.check_finished.emit(check_integrity(self._base_dir))
