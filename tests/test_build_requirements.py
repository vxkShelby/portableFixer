"""The hash-locked release install (requirements-build.lock) and the
environment check stay in step with the plain requirements files."""
import re
from pathlib import Path

from scripts import check_env

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements-build.lock"


def _lock_entries() -> dict[str, tuple[str, int]]:
    """name -> (version, number of --hash entries) of the lock."""
    entries = {}
    for block in re.split(r"\n(?=\S)", LOCK.read_text(encoding="utf-8")):
        match = re.match(r"([A-Za-z0-9._-]+)==(\S+)", block)
        if match:
            entries[check_env._norm(match.group(1))] = (match.group(2), block.count("--hash=sha256:"))
    return entries


def test_every_lock_entry_carries_hashes():
    entries = _lock_entries()
    assert entries
    assert [name for name, (_, hashes) in entries.items() if hashes == 0] == []


def test_the_lock_covers_every_pin_of_the_build_requirements():
    # pip install --require-hashes -r requirements-build.lock is what the
    # release builds with; a bump in requirements-build.txt / requirements.txt
    # that is not re-locked would silently build the old version (or fail).
    entries = _lock_entries()
    pins = {
        name: version
        for name, version in check_env.read_pins().items()
        if name not in ("pytest", "pytest-qt", "pytest-timeout")  # dev only
    }
    assert pins
    stale = {name: (version, entries.get(name, ("missing",))[0]) for name, version in pins.items() if entries.get(name, ("",))[0] != version}
    assert stale == {}, "regenerate requirements-build.lock (command in its header)"


def test_release_workflow_installs_the_locked_requirements():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "pip install --require-hashes -r requirements-build.lock" in text


def test_find_drift_names_a_missing_and_a_different_version(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest==0.0.1\nno-such-package-xyz==1.0  # c\n-r other.txt\n", encoding="utf-8")

    drift = check_env.find_drift(tmp_path)

    assert any(line.startswith("pytest: installed ") and line.endswith("pinned 0.0.1") for line in drift)
    assert "no-such-package-xyz: not installed (pinned 1.0)" in drift
