import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import signing

# Re-exported: callers and tests have always imported these from here.
from .sha256sums import _sha256_unless_stopped, compute_sha256, parse_sha256sums, parse_sha256sums_text  # noqa: F401

# Vendor/ too (research G32): its DLLs are loaded into the process. Never
# UserModules/ - the shop's own actions are not in the release manifest.
TARGET_DIRS = ("App", "Modules", "Vendor")


def _iter_real_files(root: Path, links: list[Path] | None = None):
    """Yield files under root without ever descending into a symlink or an
    NTFS junction (mklink /J - a reparse point pathlib does NOT treat as a
    symlink, so `Path.is_symlink()` alone misses it). rglob() itself already
    recurses into a directory before any per-entry check can run, so a
    planted junction that points back to an ancestor makes it loop forever;
    walking directories ourselves lets us refuse to descend in the first
    place instead of only filtering results after the fact. Each link
    skipped is appended to `links` when given."""
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
                    if links is not None:
                        links.append(entry)
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


MANIFEST = "Data/SHA256SUMS"
# The folders whose modules are blocked on a mismatch (research G32). App/
# is the running exe itself - too late to block, so it is only reported.
BLOCKING_DIRS = ("Modules", "Vendor")
ALL_MODULES = "*"


def _manifest_required() -> bool:
    """The shipped (frozen) app demands a signed manifest. Run from source
    there is none to demand - Data/SHA256SUMS is a build output - so the
    check stays the old best-effort warning there."""
    return bool(getattr(sys, "frozen", False))


def check_integrity(
    base_dir: Path,
    should_stop: Callable[[], bool] | None = None,
    *,
    dirs: tuple[str, ...] = TARGET_DIRS,
    required: bool | None = None,
) -> list[str]:
    """Files under dirs that differ from Data/SHA256SUMS, are not in it, or
    are listed but gone. When required (default: the frozen app), a missing,
    unreadable or unsigned manifest is reported as MANIFEST itself - fail
    closed, research G32 - instead of skipping the check."""
    if required is None:
        required = _manifest_required()
    sums_path = base_dir / "Data" / "SHA256SUMS"
    try:
        raw = sums_path.read_bytes()
        # Only the signed part: nothing after the signature line counts.
        body = signing.verified_body(raw) if required else raw
        if body is None:
            return [MANIFEST]
        expected = parse_sha256sums_text(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError):
        # From source this is a failure of an optional, best-effort check and
        # must not take down the app; in the shipped app it is a finding.
        return [MANIFEST] if required else []
    mismatches = []
    seen: set[str] = set()
    # A single walk covers both directions: a file present on disk that
    # changed or was never in the manifest (extra DLL, planted module), and
    # (via `seen`, checked below) a manifest entry that vanished entirely.
    # Required (research G32): a release holds no links, and the walk does
    # not follow one - but load_all_modules' glob would, so a planted
    # junction Modules/m99 -> anywhere must be a finding, not invisible.
    links: list[Path] | None = [] if required else None
    for target in dirs:
        target_dir = base_dir / target
        if not target_dir.exists():
            continue
        for file_path in _iter_real_files(target_dir, links):
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
                # be told apart from a tampered one, so don't claim it was -
                # except where the manifest is required: there an unread file
                # is not a verified one (research G32, fail closed).
                if required:
                    mismatches.append(rel_path)
                continue
            if actual_hash is None:
                return []
            if actual_hash != expected_hash:
                mismatches.append(rel_path)
    for rel_path in expected:
        if rel_path not in seen and rel_path.split("/")[0] in dirs:
            mismatches.append(rel_path)
    mismatches += [link.relative_to(base_dir).as_posix() for link in links or ()]
    return mismatches


def blocked_module_dirs(base_dir: Path) -> set[str]:
    """Research G32: the Modules/ folder names whose files do not match the
    signed manifest, or {ALL_MODULES} when the manifest itself cannot be
    trusted or a file outside a module folder (Vendor/, Modules/symptoms.yaml)
    is off - those are shared by every module. UserModules/ is never in the
    manifest and never blocked."""
    blocked: set[str] = set()
    for rel_path in check_integrity(base_dir, dirs=BLOCKING_DIRS, required=True):
        parts = rel_path.split("/")
        if parts[0] != "Modules" or len(parts) < 3:
            return {ALL_MODULES}
        blocked.add(parts[1])
    return blocked


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
