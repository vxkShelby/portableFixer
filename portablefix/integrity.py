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
    # Symlinks are skipped - not just because a symlinked file could point
    # outside base_dir, but because a symlinked *directory* can cycle back to
    # an ancestor and make rglob recurse forever, letting a planted symlink
    # hang this check instead of getting caught by it.
    for target in TARGET_DIRS:
        target_dir = base_dir / target
        if not target_dir.exists():
            continue
        for file_path in target_dir.rglob("*"):
            if file_path.is_symlink() or not file_path.is_file():
                continue
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
