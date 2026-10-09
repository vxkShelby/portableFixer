"""SHA256SUMS hashing and parsing without any Qt import, so the update core
(update_swap) and the build scripts can use it in a process that must never
load PySide6."""
import hashlib
from collections.abc import Callable
from pathlib import Path


# The release version travels inside the signed manifest body (the GitHub
# tag is not signed) as one line with no whitespace: parse_sha256sums_text
# of every client since 1.11 keeps only lines that split in two, so one
# word is skipped, while "# version 1.17.0" would have been read as the
# entry "version 1.17.0" and refused as a missing file.
MANIFEST_VERSION_PREFIX = "#portablefix-version:"


def manifest_version_line(version: str) -> str:
    return f"{MANIFEST_VERSION_PREFIX}{version}\n"


def parse_manifest_version(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(MANIFEST_VERSION_PREFIX):
            return line[len(MANIFEST_VERSION_PREFIX):] or None
    return None


def compute_sha256(path: Path) -> str:
    digest = _sha256_unless_stopped(path, None)
    assert digest is not None  # only a should_stop callback can cut it short
    return digest


def parse_sha256sums(sums_path: Path) -> dict[str, str]:
    return parse_sha256sums_text(sums_path.read_text(encoding="utf-8"))


def parse_sha256sums_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, rel_path = parts
        result[rel_path.strip()] = digest.strip().lower()
    return result


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
