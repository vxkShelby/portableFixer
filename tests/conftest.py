import pytest

from portablefix import disk_health, preflight, signing
from signing_keys import TEST_PUBLIC_KEY

try:
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    from portablefix.gui.batch_review import BatchReviewDialog
    from portablefix.gui.items_dialog import ItemsDialog
except ImportError:  # pragma: no cover - non-GUI environments
    QThread = QMessageBox = QInputDialog = QFileDialog = BatchReviewDialog = ItemsDialog = None

# Threads that outlived even the teardown wait: kept referenced for the rest
# of the session, because dropping them would abort the whole process.
_LEAKED_THREADS = []


class UnexpectedDialogError(AssertionError):
    pass


def _refuse(kind: str):
    def _raise(*args, **kwargs):
        text = " | ".join(str(a) for a in args[1:4] if isinstance(a, str))
        raise UnexpectedDialogError(f"unexpected modal {kind} in a test: {text!r}")

    return _raise


@pytest.fixture(autouse=True)
def _trust_the_test_signing_key(monkeypatch):
    """The release key's private half is a CI secret; tests sign with
    tests/signing_keys.TEST_SEED and the app trusts that key instead."""
    monkeypatch.setattr(signing, "PUBLIC_KEY", TEST_PUBLIC_KEY)


@pytest.fixture(autouse=True)
def _no_blocking_modal_dialogs(monkeypatch):
    """Fail fast instead of hanging forever on an unexpected modal dialog.

    Headless (CI, offscreen) nobody can click a static QMessageBox, so a
    test that ended with a batch still running blocked in closeEvent's
    "a batch is running, close anyway?" question during qtbot teardown -
    the process never exited and the per-test CI loop hung until GitHub's
    6h job limit. Tests that expect a dialog still monkeypatch it
    themselves; that later patch overrides this default.
    """
    if QMessageBox is None:
        return
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, _refuse(f"QMessageBox.{name}"))
    for name in ("getText", "getItem", "getInt", "getDouble"):
        monkeypatch.setattr(QInputDialog, name, _refuse(f"QInputDialog.{name}"))
    for name in ("getSaveFileName", "getOpenFileName", "getExistingDirectory"):
        monkeypatch.setattr(QFileDialog, name, _refuse(f"QFileDialog.{name}"))
    # The pre-run review screen (G12) is modal too; tests that expect it
    # answer it through BatchReviewDialog.exec themselves.
    monkeypatch.setattr(BatchReviewDialog, "exec", _refuse("BatchReviewDialog.exec"))
    # So is the per-item checklist (G05).
    monkeypatch.setattr(ItemsDialog, "exec", _refuse("ItemsDialog.exec"))


@pytest.fixture(autouse=True)
def _healthy_preflight_probes(request, monkeypatch):
    """A healthy machine for the pre-flight check unless a test asks otherwise.

    On Windows the real probes read the host's reboot flags, free space on
    %SystemDrive% and battery - a pending Windows Update restart on a CI
    runner or a developer's PC would put a blocker on the review screen and
    stall every real-batch GUI test. Tests that want a specific pre-flight
    state inject their own Probes; tests of the real probes opt out with
    @pytest.mark.real_preflight_probes.
    """
    if request.node.get_closest_marker("real_preflight_probes"):
        return
    monkeypatch.setattr(preflight, "_windows_power", lambda: preflight.PowerStatus(on_battery=False, percent=None))
    monkeypatch.setattr(preflight, "_windows_pending_reboot", lambda: [])
    monkeypatch.setattr(preflight, "_windows_system_free_bytes", lambda: 100 * 1024**3)
    # The disk health probe launches PowerShell (G13) - a healthy system
    # disk here, so no GUI test pays for it or trips over the host's disks.
    monkeypatch.setattr(
        preflight, "_windows_disk_health",
        lambda: [disk_health.DiskVerdict(disk="0", status=disk_health.OK, system=True)],
    )


@pytest.fixture(autouse=True)
def _no_real_sysinfo_probes(request, monkeypatch):
    """Instant, fixed sysinfo values for every Qt test.

    Each MainWindow starts sysinfo runners (PowerShell, ping.exe, the
    hardware monitor) parented to the window, and closeEvent waits only 5 s
    for them. On a loaded CI runner a cold PowerShell outlived that, qtbot
    then deleted the window with the QThread still running, and Qt's qFatal
    aborted the whole pytest process with no traceback. tests/test_sysinfo.py
    has no qtbot and still exercises the real functions.
    """
    if "qtbot" not in request.fixturenames:
        return
    from portablefix import sysinfo

    monkeypatch.setattr(sysinfo, "get_static_info", lambda: sysinfo.StaticInfo(
        os_name="Windows 11 Pro", cpu_name="Test CPU", cpu_cores=4,
        local_ip="192.0.2.10", ram_speed_mhz=3200, disk_health_summary="OK",
    ))
    monkeypatch.setattr(sysinfo, "ping_once", lambda *a, **k: 12.0)
    monkeypatch.setattr(sysinfo, "check_vpn_status", lambda: "")
    monkeypatch.setattr(sysinfo, "read_hardware_sensors", lambda assets_dir: {
        key: None for key in (
            "cpu_clock_mhz", "gpu_name", "gpu_load_percent", "gpu_temp_c",
            "gpu_clock_mhz", "gpu_vram_used_gb", "gpu_vram_total_gb",
        )
    })


@pytest.fixture(autouse=True)
def _join_parentless_threads(monkeypatch):
    """Let every parentless QThread a test started finish before it is freed.

    A test that waits for a runner's signal returns while run() is still
    unwinding. If the test held the only reference, the last one left is
    run()'s own `self`, so the QThread got destroyed on its own still-running
    thread - Qt's qFatal "QThread: Destroyed while thread is still running"
    killed the whole pytest process mid-suite with no summary (seen on CI,
    reproduced ~3 in 90 local runs). Runners created with a parent (as the
    app does) are owned by it, not by Python, so only parentless ones are
    tracked.
    """
    if QThread is None:
        yield
        return
    started = []
    original_start = QThread.start

    def start(self, *args, **kwargs):
        if self.parent() is None:
            started.append(self)
        return original_start(self, *args, **kwargs)

    monkeypatch.setattr(QThread, "start", start)
    yield
    hung = []
    for thread in started:
        try:
            if not thread.wait(10_000):
                hung.append(thread)
        except RuntimeError:
            # Already deleted by its finished->deleteLater, which Qt only
            # completes once the thread is done.
            pass
    started.clear()
    if hung:
        _LEAKED_THREADS.extend(hung)
        pytest.fail(f"{len(hung)} QThread(s) still running 10s after the test ended")
