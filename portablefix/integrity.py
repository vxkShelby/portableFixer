import hashlib
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

TARGET_DIRS = ("App", "Modules")


def compute_sha256(path: Path) -> str:
    digest = _sha256_unless_stopped(path, None)
    assert digest is not None  # only a should_stop callback can cut it short
    return digest


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
            try:
                if entry.is_symlink() or entry.is_junction():
                    continue
                is_dir = entry.is_dir()
                is_file = entry.is_file()
            except OSError:
                # A stat failure (e.g. permission denied) on one entry must
                # not abort this best-effort check for every other entry.
                continue
            if is_dir:
                stack.append(entry)
            elif is_file:
                yield entry


def _sha256_unless_stopped(path: Path, should_stop: Callable[[], bool] | None) -> str | None:
    """compute_sha256, but gives up (returning None) between chunks once
    should_stop() is true - a single large file on slow USB media takes
    seconds, far too long to make an app that is closing wait for it."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            if should_stop is not None and should_stop():
                return None
            digest.update(chunk)
    return digest.hexdigest()


def check_integrity(base_dir: Path, should_stop: Callable[[], bool] | None = None) -> list[str]:
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
            if expected_hash is None:
                mismatches.append(rel_path)
                continue
            try:
                actual_hash = _sha256_unless_stopped(file_path, should_stop)
            except OSError:
                # Locked by AV, or the stick dropped out mid-read: this runs
                # on a background thread where an uncaught error just kills
                # the check silently - and a file that can't be read can't
                # be told apart from a tampered one, so don't claim it was.
                continue
            if actual_hash is None:
                return []
            if actual_hash != expected_hash:
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
        mismatches = check_integrity(self._base_dir, should_stop=self.isInterruptionRequested)
        if not self.isInterruptionRequested():
            self.check_finished.emit(mismatches)

    def stop(self, timeout_ms: int = 10000) -> None:
        """Must run before the app tears down its window (this thread's
        parent): Qt aborts the whole process - "QThread: Destroyed while
        thread is still running" - if a running QThread is destroyed, which
        is exactly what closing the app during a slow first-launch hash did.
        That crash also kept the old process alive long enough to abort a
        pending update swap."""
        try:
            self.requestInterruption()
            self.wait(timeout_ms)
        except RuntimeError:
            # Already finished and deleted via deleteLater - nothing to stop.
            pass


def format_mismatches(mismatches: list[str], more_template: str, limit: int = 20) -> str:
    """A garbled or badly outdated manifest can flag hundreds of files; one
    line each made the warning dialog taller than the screen, pushing its
    OK button out of reach."""
    lines = mismatches[:limit]
    if len(mismatches) > limit:
        lines.append(more_template.format(count=len(mismatches) - limit))
    return "\n".join(lines)
