import hashlib
import os
import subprocess

import pytest

from portablefix.integrity import IntegrityCheckRunner, check_integrity, compute_sha256, parse_sha256sums


def test_compute_sha256_matches_hashlib(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello world")
    assert compute_sha256(file_path) == hashlib.sha256(b"hello world").hexdigest()


def test_parse_sha256sums(tmp_path):
    sums_path = tmp_path / "SHA256SUMS"
    sums_path.write_text(
        "aaaa  App/PortableFix.exe\nbbbb  Modules/m01_diagnostics/actions.yaml\n",
        encoding="utf-8",
    )
    result = parse_sha256sums(sums_path)
    assert result == {
        "App/PortableFix.exe": "aaaa",
        "Modules/m01_diagnostics/actions.yaml": "bbbb",
    }


def test_check_integrity_no_sums_file_returns_empty(tmp_path):
    assert check_integrity(tmp_path) == []


def test_check_integrity_all_match(tmp_path):
    (tmp_path / "App").mkdir()
    target = tmp_path / "App" / "file.txt"
    target.write_bytes(b"content")
    digest = compute_sha256(target)
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/file.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == []


def test_check_integrity_detects_tampered_file(tmp_path):
    (tmp_path / "App").mkdir()
    target = tmp_path / "App" / "file.txt"
    target.write_bytes(b"content")
    digest = compute_sha256(target)
    target.write_bytes(b"tampered")
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/file.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == ["App/file.txt"]


def test_check_integrity_detects_missing_file(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("aaaa  App/missing.txt\n", encoding="utf-8")
    assert check_integrity(tmp_path) == ["App/missing.txt"]


def test_check_integrity_detects_file_added_outside_the_manifest(tmp_path):
    (tmp_path / "App").mkdir()
    known = tmp_path / "App" / "known.txt"
    known.write_bytes(b"content")
    digest = compute_sha256(known)
    (tmp_path / "Modules").mkdir()
    (tmp_path / "Modules" / "planted.dll").write_bytes(b"not in the manifest")
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text(f"{digest}  App/known.txt\n", encoding="utf-8")

    assert check_integrity(tmp_path) == ["Modules/planted.dll"]


def test_check_integrity_skips_symlinks_instead_of_following_them(tmp_path):
    (tmp_path / "App").mkdir()
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "secret.txt").write_bytes(b"outside base_dir")
    link_path = tmp_path / "App" / "linked"
    try:
        os.symlink(real_dir, link_path, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks needs Developer Mode or admin on this machine")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    # A symlinked directory under App/ must not be walked into (it isn't in
    # the manifest either way, but if rglob followed it and it looped back
    # to an ancestor, this call would hang instead of returning).
    assert check_integrity(tmp_path) == []


def test_check_integrity_skips_junctions_instead_of_following_them(tmp_path):
    # An NTFS junction (mklink /J) is a reparse point but pathlib's
    # is_symlink() returns False for it - only is_junction() (3.12+) catches
    # it. No admin/Developer Mode needed to create one, unlike a real symlink.
    (tmp_path / "App").mkdir()
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "secret.txt").write_bytes(b"outside base_dir")
    link_path = tmp_path / "App" / "linked_junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(real_dir)],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        pytest.skip("mklink /J not available in this environment")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    assert check_integrity(tmp_path) == []


def test_check_integrity_skips_a_junction_that_cycles_back_to_an_ancestor(tmp_path):
    # This is the scenario that actually hangs a plain rglob() walk: a
    # junction inside App/ pointing back at App/ itself recurses forever
    # since rglob has already descended into it before any per-entry check
    # runs. The fix must refuse to descend in the first place.
    (tmp_path / "App").mkdir()
    (tmp_path / "App" / "sub").mkdir()
    link_path = tmp_path / "App" / "sub" / "cycle_back"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(tmp_path / "App")],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        pytest.skip("mklink /J not available in this environment")

    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("", encoding="utf-8")

    assert check_integrity(tmp_path) == []


def test_check_integrity_unreadable_manifest_degrades_to_empty_instead_of_raising(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_bytes(b"\xff\xfe\x00\xff not valid utf-8")
    assert check_integrity(tmp_path) == []


def test_integrity_check_runner_emits_mismatches_off_the_gui_thread(qtbot, tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "SHA256SUMS").write_text("aaaa  App/missing.txt\n", encoding="utf-8")

    runner = IntegrityCheckRunner(tmp_path)
    with qtbot.waitSignal(runner.check_finished, timeout=5000) as blocker:
        runner.start()
    assert blocker.args == [["App/missing.txt"]]


# --- docs/research/research-resilience.md 7.2 / research-app-performance.md 1.1 follow-ups ---


@pytest.fixture
def _is_junction_compat(monkeypatch):
    # Path.is_junction() is Python 3.12+ (the app's target); older test
    # interpreters get a stand-in so check_integrity's walk can run at all.
    from pathlib import Path

    if not hasattr(Path, "is_junction"):
        monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)


def _manifest_for(base, files: dict[str, bytes]) -> None:
    lines = []
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {rel}")
    (base / "Data").mkdir(exist_ok=True)
    (base / "Data" / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_check_integrity_skips_a_file_it_cannot_read_instead_of_raising(tmp_path, monkeypatch, _is_junction_compat):
    # Runs on a QThread: an OSError (AV lock, stick dropping out) used to
    # kill the check silently, so a real tamper elsewhere went unreported.
    import portablefix.integrity as integrity_module

    _manifest_for(tmp_path, {"App/locked.bin": b"a", "App/ok.bin": b"b"})
    (tmp_path / "App" / "ok.bin").write_bytes(b"tampered")
    real = integrity_module._sha256_unless_stopped

    def flaky(path, should_stop):
        if path.name == "locked.bin":
            raise PermissionError("locked by AV")
        return real(path, should_stop)

    monkeypatch.setattr(integrity_module, "_sha256_unless_stopped", flaky)

    assert check_integrity(tmp_path) == ["App/ok.bin"]


def test_check_integrity_stops_early_when_asked(tmp_path, _is_junction_compat):
    _manifest_for(tmp_path, {"App/a.bin": b"a", "App/b.bin": b"b"})
    (tmp_path / "App" / "a.bin").write_bytes(b"tampered")

    assert check_integrity(tmp_path, should_stop=lambda: True) == []


def test_format_mismatches_caps_a_huge_list():
    from portablefix.integrity import format_mismatches

    text = format_mismatches([f"App/f{i}" for i in range(500)], "... and {count} more", limit=20)

    lines = text.splitlines()
    assert len(lines) == 21
    assert lines[-1] == "... and 480 more"
    assert format_mismatches(["App/a"], "... and {count} more") == "App/a"


def test_integrity_runner_stop_is_safe_after_the_thread_was_deleted(qtbot, tmp_path):
    runner = IntegrityCheckRunner(tmp_path)
    with qtbot.waitSignal(runner.check_finished, timeout=5000):
        runner.start()
    qtbot.wait(50)  # let deleteLater run

    runner.stop()  # must not raise RuntimeError on the deleted C++ object


_CLOSE_DURING_CHECK_SCRIPT = r"""
import sys, time
sys.path.insert(0, {repo!r})
from PySide6.QtWidgets import QApplication, QWidget
import portablefix.integrity as integrity

def slow_check(base_dir, should_stop=None):
    while not (should_stop and should_stop()):
        time.sleep(0.01)
    return []

integrity.check_integrity = slow_check
app = QApplication(sys.argv)
window = QWidget()
runner = integrity.IntegrityCheckRunner({base!r}, parent=window)
runner.start()
time.sleep(0.2)
if {stop}:
    runner.stop()
del window
print("clean exit", flush=True)
"""


def test_closing_the_app_during_the_integrity_check_does_not_abort_the_process(tmp_path):
    # main.py's window owns the runner; destroying a still-running QThread
    # makes Qt abort() the whole process ("QThread: Destroyed while thread
    # is still running") - which is what closing the app during a slow
    # first-launch hash did. Run in a child process so an abort can't take
    # the test session down with it.
    import sys
    from pathlib import Path

    repo = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    script = _CLOSE_DURING_CHECK_SCRIPT.format(repo=repo, base=str(tmp_path), stop=True)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60, env=env)

    assert result.returncode == 0, result.stderr
    assert "clean exit" in result.stdout
