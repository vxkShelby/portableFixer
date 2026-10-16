import dataclasses
import html
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QEventLoop, QThread, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from . import job_forms, style
from .batch_review import BatchReview, BatchReviewDialog, ReviewDecision, build_review
from .items_dialog import ItemsDialog
from .mw_common import (
    CONSOLE_MAX_LINES,
    CUSTOM_PRESET_PREFIX,
    DASHBOARD_SYMPTOM_CHIPS,
    HISTORY_MAX_ROWS,
    _CONSOLE_ERROR_RE,
    _CONSOLE_NO_ERROR_RE,
    _CONSOLE_SUCCESS_RE,
    _CONSOLE_WARNING_RE,
    _DashboardTile,
    _UPDATE_PHASE_KEYS,
    _console_severity,
    _elapsed_text,
    _ignore_deleted,
    _thread_running,
)
from .mw_console import ConsoleMixin
from .mw_layout import LayoutMixin
from .mw_settings import SettingsMixin
from .mw_reports import ReportsMixin
from .mw_winget import WingetPanelMixin
from .mw_uninstaller import UninstallerPanelMixin
from .mw_dashboard import DashboardMixin
from .mw_updates import UpdatesMixin
from .mw_sysinfo import SysinfoMixin
from .. import action_service, batch_resume, diagnostics, disk_health, elevation, handoff, health, hive_backup, history, i18n, intake, ops, panel_safety, paths, pfjson, preflight, report, restore_point, snapshot, symptoms, sysinfo, target_user, undo, uninstall_plan, uninstaller, update_swap, updater, winget_updates
from .. import items as items_mod
from ..audit_log import append_entry, make_entry
from ..executor import CANCELLED_EXIT_CODE, POWERSHELL_NOT_FOUND_EXIT_CODE, TIMEOUT_EXIT_CODE, ActionRunner
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..integrity import format_mismatches
from ..module_engine import load_catalog
from ..settings import (
    MAX_CUSTOM_PRESETS,
    MAX_PRESET_NAME_LENGTH,
    MAX_TECHNICIAN_NAME_LENGTH,
    PRESETS,
    Settings,
    save_settings,
)
from ..version import APP_VERSION

# Lines of a card's preview output shown in its result box (the console
# keeps all of them).
PREVIEW_MAX_LINES = 30

def _failure_hint(exit_code: int, output_lines: list[str]) -> str:
    """i18n key of the next step for a failed action (batch summary)."""
    if exit_code == TIMEOUT_EXIT_CODE:
        return "failure_hint_timeout"
    if exit_code == CANCELLED_EXIT_CODE:
        return "failure_hint_cancelled"
    if exit_code == POWERSHELL_NOT_FOUND_EXIT_CODE:
        return "failure_hint_powershell"
    if any("access is denied" in line.lower() for line in output_lines):
        return "failure_hint_denied"
    return "failure_hint_generic"


try:
    # Qt 6.8+ (requirements.txt pins 6.8.3). Older Qt has neither name:
    # announcements are then a silent no-op - the status bar text stays readable.
    from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
except ImportError:  # pragma: no cover - depends on the installed PySide6
    QAccessible = QAccessibleAnnouncementEvent = None


def _announce_to_screen_reader(widget: QWidget, text: str) -> None:
    # QStatusBar.showMessage() is silent for Narrator/NVDA - batch progress
    # and the final outcome would only reach sighted users otherwise
    # (research-accessibility.md Finding 4).
    if QAccessibleAnnouncementEvent is None or not text:
        return
    QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(widget, text))


# Waits closeEvent gives the quick one-shot runners: PowerShell-backed
# sysinfo checks run subprocess.run(timeout=10) plus process start, the
# update check's urlopen(timeout=5) does not cover DNS, and the speed test
# is two 20 s socket timeouts plus a ping.
QUICK_RUNNER_WAIT_MS = 5_000
SUBPROCESS_RUNNER_WAIT_MS = 15_000
SPEED_TEST_WAIT_MS = 45_000

# Runners a closing window could not wait out: re-parented away from it so
# its destruction cannot take a live QThread down with it (qFatal), and
# referenced here until they finish.
_ORPHANED_RUNNERS: list = []


def _orphan_if_running(runner) -> None:
    if not _thread_running(runner):
        return
    runner.setParent(None)
    _ORPHANED_RUNNERS.append(runner)
    runner.finished.connect(lambda r=runner: r in _ORPHANED_RUNNERS and _ORPHANED_RUNNERS.remove(r))


class _DiskHealthProbeRunner(QThread):
    """The G13 disk health probe off the GUI thread: it launches PowerShell
    and may take up to disk_health.PROBE_TIMEOUT_SEC on a dying disk.

    The verdicts go into a plain list owned by the caller rather than a
    signal argument or an attribute: the caller reads them after the thread
    has finished, when this object may already be deleteLater'd."""

    def __init__(self, probe, results: list, parent=None):
        super().__init__(parent)
        self._probe = probe
        self._results = results
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            verdicts = self._probe()
        except Exception:  # noqa: BLE001 - any probe failure means "unknown"
            verdicts = None
        self._results.append(verdicts)


class _CheckRunner(QThread):
    """Research G09: the state checks of one category, one after another,
    off the GUI thread. stop() kills the PowerShell running now."""

    state_ready = Signal(str, str)

    def __init__(self, jobs: list, target, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._target = target
        self._holder: list = []
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        for action_id, action in self._jobs:
            if self.isInterruptionRequested():
                break
            self._holder = []
            try:
                state = action_service.check_state(action, self._target, self._holder)
            except Exception:  # noqa: BLE001 - a failed check just means "unknown"
                state = "UNKNOWN"
            if self.isInterruptionRequested():
                break
            self.state_ready.emit(action_id, state)

    def stop(self) -> None:
        self.requestInterruption()
        for run in list(self._holder):
            run.cancel()


class MainWindow(
    ConsoleMixin,
    LayoutMixin,
    SettingsMixin,
    ReportsMixin,
    WingetPanelMixin,
    UninstallerPanelMixin,
    DashboardMixin,
    UpdatesMixin,
    SysinfoMixin,
    QMainWindow,
):
    # Emitted from diagnostics' excepthook (main.py wires it): a signal is
    # queued to the GUI thread whichever thread the exception came from.
    internal_error = Signal()

    def __init__(
        self,
        assets_dir: Path,
        state_dir: Path,
        settings: Settings,
        is_admin: bool,
        run_id: str,
        parent=None,
        target: "target_user.TargetUser | None" = None,
    ):
        super().__init__(parent)
        self.assets_dir = assets_dir
        self.state_dir = state_dir
        # main.py sets it when the background integrity check is done.
        self.integrity_mismatches: list | None = None
        self.internal_error.connect(
            lambda: self.statusBar().showMessage(self._t("internal_error_see_log"), 15000)
        )
        self.settings = settings
        self.is_admin = is_admin
        self.run_id = run_id
        # Whose hive per-user settings go to (research G25) - detected once:
        # who is signed in does not change under a running batch, and a
        # sign-out is caught by the commands themselves (hive not loaded).
        self.target_user = target if target is not None else target_user.detect()
        # main.py hands over the raw USB dir as assets_dir and the writable
        # dir as state_dir - they only differ when resolve_writable_base_dir
        # fell back to %TEMP% on the client machine, which the report must
        # then say (research-reporting.md F4).
        self._storage_fallback = Path(state_dir) != Path(assets_dir)
        self.modules, module_load_errors = load_catalog(assets_dir, settings.allow_modified_modules)
        self._action_index: dict[str, tuple[ModuleDef, ActionDef]] = {}
        self._bulk_selecting = False
        # One group for the window's life: a language toggle rebuilds its
        # buttons (Qt drops the deleted ones), not the group.
        self._preset_button_group = QButtonGroup(self)
        self._preset_button_group.setExclusive(True)
        # Research G27: client complaint -> the symptoms to suggest. A broken
        # entry is skipped and reported with the module errors.
        self._symptoms, symptom_errors = symptoms.load(
            Path(assets_dir) / "Modules" / symptoms.SYMPTOMS_FILE,
            {a.id: a for m in self.modules for a in m.actions},
        )
        module_load_errors = module_load_errors + symptom_errors
        if module_load_errors:
            QMessageBox.warning(
                self,
                self._t("app_title"),
                # Capped: a fully blocked catalog (research G32) also breaks
                # every symptom entry - one line each outgrew the screen.
                self._t("module_load_warning") + "\n" + format_mismatches(module_load_errors, self._t("load_errors_more")),
            )
        elif not self.modules:
            QMessageBox.warning(self, self._t("app_title"), self._t("no_modules_warning"))
        self._action_checkboxes: dict[str, QCheckBox] = {}
        self._action_rows: dict[str, QWidget] = {}
        self._action_status_labels: dict[str, QLabel] = {}
        self._action_detail_toggles: dict[str, QToolButton] = {}
        self._action_detail_panels: dict[str, QWidget] = {}
        self._action_start_times: dict[str, float] = {}
        # action_id -> seconds it took in the current batch (summary rows).
        self._action_durations: dict[str, float] = {}
        # Ticks the "RUNNING (12s)" text of the action running now - created
        # here, not in _build_ui, so a language toggle doesn't stack timers.
        self._action_tick_timer = QTimer(self)
        self._action_tick_timer.setInterval(1000)
        self._action_tick_timer.timeout.connect(self._on_action_tick)
        # A card's one-shot Preview run (research-design-additions item 6).
        self._preview_runner: ActionRunner | None = None
        self._preview_buttons: dict[str, QPushButton] = {}
        self._preview_box: QMessageBox | None = None
        self._queue_total = 0
        self._queue: list[str] = []
        self._runner: ActionRunner | None = None
        self._restore_point_attempted = False
        self._pending_restore_point_runner: restore_point.RestorePointRunner | None = None
        # A panel's own restore point (uninstaller, leftover cleanup, winget -
        # G01) is kept apart from the batch's: overwriting one attribute
        # with the other lost track of a running Checkpoint-Computer, so
        # close waited for the wrong thread.
        self._pending_panel_restore_point_runner: restore_point.RestorePointRunner | None = None
        # Full registry hive backup (research G24): asked for on the review
        # screen, made once per batch right before its first DESTRUCTIVE
        # action; every folder made this session is named in undo.ps1.
        self._hive_backup_requested = False
        self._hive_backup_attempted = False
        self._pending_hive_backup_runner: hive_backup.HiveBackupRunner | None = None
        # Disk health probes (G13) still running - a cancelled one is left to
        # finish on its own (PowerShell times out), and closeEvent waits for it.
        self._disk_health_runners: list[_DiskHealthProbeRunner] = []
        self._hive_backups: list[Path] = []
        self._report_runner: report.ReportRunner | None = None
        # A batch-end report of this run was written by this window - the
        # forms and the timer, changed afterwards, rewrite it (G20).
        self._report_written = False
        # Such a change came while a report was being written: that report
        # may have read the audit log before it, so one more rewrite follows.
        self._report_refresh_pending = False
        self._batch_active = False
        # DRY-RUN as it was when the running batch started: the checkbox is
        # locked for the batch, and every batch-engine decision reads this.
        self._batch_dry_run = settings.dry_run
        self._snapshot_before: dict = {}
        self._snapshot_after: dict = {}
        self._undo_steps: list[str] = []
        # action_id -> the state file its running `ops:` command (research
        # G10) captures the previous state into; undo is generated from it.
        self._ops_state_paths: dict[str, Path] = {}
        # action_id -> the item ids the technician picked for a per-item
        # action (research G05) in the running batch.
        self._selected_items: dict[str, list[str]] = {}
        # Research G09: action_id -> APPLIED / NOT_APPLIED / UNKNOWN from the
        # last state check this session, the chips showing them, and the
        # background checks still to run.
        self._check_results: dict[str, str] = {}
        self._action_check_chips: dict[str, QLabel] = {}
        self._check_pending: set[str] = set()
        self._check_queue: list[str] = []
        self._check_runner: "_CheckRunner | None" = None
        # Non-SAFE changes that ran for real but have no undo_command -
        # listed in undo.ps1 so it never implies everything was reversible.
        self._irreversible_actions: list[str] = []
        self._batch_results: list[tuple[str, int]] = []
        self._failure_hints: dict[str, str] = {}
        # action_id -> the warning text the technician accepted for it on
        # the batch review screen (research G12); _dispatch_action asks
        # nothing more for these and quotes the text in the audit entry.
        self._reviewed_warnings: dict[str, str] = {}
        # Research G02: finding id -> the latest finding this session (a
        # later "ok" replaces an earlier problem); the dashboard's health
        # areas and the recommended fixes come from these.
        self._findings: dict[str, dict] = {}
        self._summary_dialog: QDialog | None = None
        self._closed = False
        # Per-run job details for the report header. Kept on self (not in a
        # widget) so a language toggle's full UI rebuild doesn't lose them;
        # the technician name lives in settings since it rarely changes.
        self._job_client = ""
        self._job_note = ""
        # When the running batch (or its part since a restart report)
        # started - its duration is logged at the end for the report's work
        # time (research G20).
        self._batch_started_at: float | None = None
        # action_id -> typical seconds from past reports (progress ETA).
        self._eta_history: dict[str, float] = {}
        # How many audit/report writes failed (USB full or pulled) - the
        # persistent banner counts them.
        self._write_failures = 0
        self._tray_icon: QSystemTrayIcon | None = None
        self._cancel_requested = False
        self._close_after_restore_point = False
        self._pending_update_info = None
        self._update_check_runner = None
        self._update_download_runner = None
        self._update_download_dir: Path | None = None
        self._update_stage_runner = None
        self._update_launch_runner = None
        # Handoff package with Windows diagnostics (G19) - minutes of
        # msinfo32/dxdiag, so it runs off the GUI thread; one at a time.
        self._handoff_runner: handoff.HandoffRunner | None = None
        # True from the download until the updater's handshake has ended;
        # _update_phase says which step runs (the banner text after a
        # language toggle) and _update_progress what the bar last showed.
        self._update_in_progress = False
        self._update_phase: str | None = None
        self._update_progress = (0, 0)
        # Verified and still on disk - a retry after a refusal or a failed
        # hand-off installs it without downloading again.
        self._staged_update = None
        # The updater has proven it is running and is waiting for this
        # process to exit: the close must neither ask nor linger.
        self._closing_for_update = False
        self._cpu_load_sampler = sysinfo.CpuLoadSampler()
        self._static_info_runner = None
        self._ping_runner = None
        self._vpn_runner = None
        self._speed_test_runner = None
        self._hw_sensor_runner = None
        self._winget_scan_runner = None
        self._winget_update_runner = None
        self._uninstall_runner = None
        self._ping_busy = False
        self._vpn_busy = False
        self._speed_test_busy = False
        self._hw_sensor_busy = False
        self._sysinfo_timer = None
        self._hw_sensor_timer = None
        self._ping_timer = None
        self._vpn_timer = None
        # Quiet mode (G33) and the minimized window both stop polling; see
        # _apply_polling_state. The status-bar indicator lives on the
        # QMainWindow's status bar, which survives _build_ui rebuilds, so it
        # is created once and only retranslated afterwards.
        self._window_minimized = False
        self._manual_update_check = False
        self._quiet_status_label: QLabel | None = None
        # The winget panel's hooks, rebound on every _build_ui: the first
        # re-applies its auto-check timer (quiet mode and a minimized window
        # both stop it), the second runs after a quiet-mode toggle.
        self._winget_apply_auto_check = None
        self._winget_on_quiet_mode_changed = None
        # Set when quiet mode skipped the update check at start, so leaving
        # quiet mode runs it - loud mode has no button for it.
        self._startup_update_check_skipped = False
        self._undo_script_path: Path | None = None
        # (len(_undo_steps), len(_irreversible_actions), len(_hive_backups))
        # last written to undo.ps1 - the lists only ever grow, so the lengths
        # identify it.
        self._undo_written_state: tuple[int, int, int] | None = None
        # Batches across a restart (research G03). The PC stays awake while a
        # batch runs; tests swap in a recording KeepAwake.
        self._keep_awake = batch_resume.KeepAwake()
        # restarts_pc actions of this batch whose report + undo.ps1 were
        # already written before they run.
        self._pre_restart_prepared: set[str] = set()
        # The action the resume file was saved for in this batch ("" = none).
        self._resume_saved_for = ""
        # The batch stopped for a restart_before_next action: after the
        # report, tell the technician to restart and start PortableFix again.
        self._restart_needed_after = ""
        # A restarts_pc action succeeded - Windows is going down, so the
        # report written just before it is the final one (a rewrite now could
        # be cut off half-way and leave no report at all).
        self._restart_report_path: Path | None = None
        self._restarting = False
        # The saved batch being continued (resume_batch), until it starts.
        self._resuming: batch_resume.PendingBatch | None = None
        # i18n key of the review note when resume_batch switched DRY-RUN.
        self._resume_mode_note = ""
        self._build_ui()
        # Sized from the screen once, here - not in _build_ui, which a
        # language toggle reruns (it re-sized a maximised window). On the
        # 1366 x 768 / 125 %-scaled laptops this tool is run on, main.py
        # shows the window maximised instead.
        screen = self.screen()
        available = screen.availableGeometry() if screen is not None else None
        if available is not None and available.width() > 0:
            self.resize(min(1200, int(available.width() * 0.95)), min(760, int(available.height() * 0.92)))
            self.prefer_maximized = available.width() < 1400 or available.height() < 850
        else:
            self.resize(1200, 760)
            self.prefer_maximized = False
        # Quiet mode: no GitHub request at start; the sysinfo panel has a
        # button for an explicit check instead.
        if self.settings.quiet_mode:
            self._startup_update_check_skipped = True
        else:
            self._start_update_check()
        self._start_sysinfo_polling()
        # Bound to self (the window), not any widget rebuilt by _build_ui -
        # created once here rather than inside _build_ui, which reruns on
        # every language toggle and would otherwise stack up a fresh
        # duplicate QShortcut (each one firing) on every toggle.
        self._select_all_shortcut = QShortcut(QKeySequence("Ctrl+A"), self)
        self._select_all_shortcut.activated.connect(self._on_select_all_shortcut)
        self._run_shortcut = QShortcut(QKeySequence("F5"), self)
        self._run_shortcut.activated.connect(self._on_run_shortcut)
        self._extra_shortcuts = []
        for keys, handler in (
            ("Ctrl+F", self._on_search_shortcut),
            ("Ctrl+S", self._on_save_preset_clicked),
            ("Ctrl+J", self._open_job_dialog),
            ("Ctrl+L", self._on_toggle_language),
            ("F1", self._show_shortcuts_help),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(handler)
            self._extra_shortcuts.append(shortcut)

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is getattr(self, "search_box", None)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Escape
            and self.search_box.text()
        ):
            self.search_box.clear()
            return True
        return super().eventFilter(watched, event)

    def _on_search_shortcut(self) -> None:
        self.search_box.setFocus()
        self.search_box.selectAll()

    def _show_shortcuts_help(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(self._t("shortcuts_title"))
        box.setText(self._t("shortcuts_body"))
        box.setIcon(QMessageBox.Icon.Information)
        box.open()

    def _on_select_all_shortcut(self) -> None:
        # Qt.WindowShortcut fires regardless of which child widget has focus,
        # so without this guard, Ctrl+A while typing in the search box (or
        # any text field) would select every action instead of the text.
        focused = QApplication.focusWidget()
        if isinstance(focused, (QLineEdit, QPlainTextEdit)):
            return
        self._apply_selection(list(self._action_checkboxes), "all")

    def _on_run_shortcut(self) -> None:
        # run_button.setEnabled(False) during a batch stops a stray mouse
        # click from re-entering run_selected_actions, but a keyboard
        # shortcut bypasses disabled-widget protection entirely - guard here.
        if self._batch_active or self._update_in_progress:
            return
        self.run_selected_actions()

    def closeEvent(self, event) -> None:
        rp_runner = self._pending_restore_point_runner
        waiting_key = "closing_waiting_restore_point"
        if not _thread_running(rp_runner) and _thread_running(self._pending_panel_restore_point_runner):
            rp_runner = self._pending_panel_restore_point_runner
        if not _thread_running(rp_runner) and _thread_running(self._pending_hive_backup_runner):
            # `reg save` of two hives can't be interrupted either and can take
            # minutes on a slow stick - same non-blocking close.
            rp_runner = self._pending_hive_backup_runner
            waiting_key = "closing_waiting_hive_backup"
        if not _thread_running(rp_runner) and _thread_running(self._winget_update_runner):
            # A winget call can run for minutes (its timeout is 5 min); the
            # uninstaller waits for the technician's clicks with no timeout
            # at all - a blocking wait here froze the window for that long.
            rp_runner = self._winget_update_runner
            waiting_key = "closing_waiting_winget"
            rp_runner.request_stop()
        if not _thread_running(rp_runner) and _thread_running(self._uninstall_runner):
            rp_runner = self._uninstall_runner
            waiting_key = "closing_waiting_uninstall"
            rp_runner.requestInterruption()
        if rp_runner is not None and _thread_running(rp_runner):
            # Checkpoint-Computer can take minutes and can't be interrupted.
            # Blocking in closeEvent froze the window ("Not Responding" -
            # an invitation to kill it from Task Manager before the throttle
            # registry value is restored), and the restore point's result
            # then dispatched the action it was guarding with no window
            # left. Instead: cancel the batch, show why we're still here,
            # and close for real once the restore point has finished.
            if not self._close_after_restore_point:
                if self._batch_active and not self._closing_for_update:
                    proceed = QMessageBox.question(
                        self,
                        self._t("app_title"),
                        self._t("confirm_close_during_batch"),
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if proceed != QMessageBox.Yes:
                        event.ignore()
                        return
                self._close_after_restore_point = True
                self._cancel_requested = True
                self._queue = []
                self.cancel_button.setEnabled(False)
                self.run_button.setEnabled(False)
                rp_runner.finished.connect(self.close)
                self.statusBar().showMessage(self._t(waiting_key))
            event.ignore()
            return
        if self._batch_active and not self._close_after_restore_point and not self._closing_for_update:
            proceed = QMessageBox.question(
                self,
                self._t("app_title"),
                self._t("confirm_close_during_batch"),
                QMessageBox.Yes | QMessageBox.No,
            )
            if proceed != QMessageBox.Yes:
                event.ignore()
                return
        # ponytail: plain-Python flag (safe even if a delayed cross-thread
        # callback fires after the C++ widgets are gone) so async batch-completion
        # handlers know not to touch self.run_button once the window is closing.
        self._closed = True
        # Anything still finishing asynchronously (a restore point result, a
        # runner's final signal) must not start new work once we're closing.
        self._cancel_requested = True
        if self._console_window is not None:
            self._console_window.close()
        if self._sysinfo_timer is not None:
            self._sysinfo_timer.stop()
        if self._hw_sensor_timer is not None:
            self._hw_sensor_timer.stop()
        if self._ping_timer is not None:
            self._ping_timer.stop()
        if self._vpn_timer is not None:
            self._vpn_timer.stop()
        self._action_tick_timer.stop()
        # Ask anything still actively running to stop before we wait on it -
        # otherwise the wait below just burns its whole timeout doing nothing.
        self._queue = []
        if self._runner is not None:
            self._runner.cancel()
        if self._preview_runner is not None:
            try:
                self._preview_runner.cancel()
            except RuntimeError:
                pass
        if self._preview_box is not None:
            self._preview_box.close()
        self._check_queue = []
        if self._check_runner is not None:
            try:
                self._check_runner.stop()
            except RuntimeError:
                pass
        if self._winget_update_runner is not None:
            self._winget_update_runner.request_stop()
        if self._uninstall_runner is not None:
            try:
                self._uninstall_runner.requestInterruption()
            except RuntimeError:
                pass
        update_runners = [
            runner for runner in (
                self._update_download_runner, self._update_stage_runner, self._update_launch_runner,
                self._handoff_runner,
                # Stops between programs once interrupted, but the uninstaller
                # already running cannot be cut short - and an interactive one
                # has no timeout (research G15): it waits for the technician's
                # clicks, so a capped wait could destroy the live QThread.
                self._uninstall_runner,
            )
            if runner is not None
        ]
        for runner in update_runners:
            try:
                runner.requestInterruption()
            except RuntimeError:
                pass
        # Destroying self while a runner's native thread is still mid-flight
        # is a use-after-free risk - wait for each to actually finish first.
        # A one-shot runner may already be auto-deleted by Qt once its thread
        # ended; that RuntimeError just means there's nothing left to wait for.
        # The speed test can't be cancelled mid-flight (one blocking,
        # uninterruptible network call), so its wait must cover its real
        # worst-case duration - a short timeout here would let closeEvent
        # proceed while that QThread is still alive, which is the exact crash
        # this loop exists to prevent.
        quick_runners = (
            (self._static_info_runner, SUBPROCESS_RUNNER_WAIT_MS),
            (self._hw_sensor_runner, QUICK_RUNNER_WAIT_MS),
            (self._ping_runner, QUICK_RUNNER_WAIT_MS),
            (self._vpn_runner, SUBPROCESS_RUNNER_WAIT_MS),
            (self._runner, QUICK_RUNNER_WAIT_MS),
            (self._preview_runner, QUICK_RUNNER_WAIT_MS),
            (self._update_check_runner, SUBPROCESS_RUNNER_WAIT_MS),
        )
        slow_runners = (
            (self._speed_test_runner, SPEED_TEST_WAIT_MS),
            # A state check (G09) was just killed; its pipe drains at once.
            (self._check_runner, 15_000),
            # Checkpoint-Computer can legitimately run for minutes (VSS on a
            # slow disk); a 5s wait let closeEvent destroy the still-running
            # QThread, aborting the process before create_restore_point could
            # put the 24h throttle registry value back.
            (self._pending_restore_point_runner, restore_point.RESTORE_POINT_TIMEOUT_SEC * 1000 + 5_000),
            (self._pending_panel_restore_point_runner, restore_point.RESTORE_POINT_TIMEOUT_SEC * 1000 + 5_000),
            (self._pending_hive_backup_runner, hive_backup.HIVE_SAVE_TIMEOUT_SEC * len(hive_backup.HIVES) * 1000 + 5_000),
            # PowerShell is killed at its timeout, its pipes drained for a
            # few seconds more - a live QThread must never be destroyed.
            *(
                (runner, (disk_health.PROBE_TIMEOUT_SEC + disk_health.KILL_DRAIN_TIMEOUT_SEC) * 1000 + 5_000)
                for runner in self._disk_health_runners
            ),
            # Can't be interrupted mid-write, and it re-reads the whole
            # session's audit log - allow for a slow USB stick.
            (self._report_runner, 30_000),
            # These two were previously stored on the winget panel QWidget,
            # not self - closeEvent had no way to know about them, so a scan
            # or update still in flight left this process alive indefinitely.
            # That in turn made the in-app "restart to install update" flow
            # silently fail: the swap script waits ~15s for this PID to
            # exit, gives up, and relaunches the still-old exe.
            (self._winget_scan_runner, 65_000),
            (self._winget_update_runner, winget_updates._UPDATE_TIMEOUT_SEC * 1000 + 10_000),
        )
        for runner, timeout_ms in quick_runners + slow_runners:
            if runner is None:
                continue
            try:
                runner.wait(timeout_ms)
            except RuntimeError:
                pass
            # Still running past its wait (a DNS lookup that hangs, a
            # stalled VSS): it must not be destroyed with this window.
            _orphan_if_running(runner)
        # No cap: all of them stop within a chunk or a poll once interrupted
        # (the handoff kills the report it is waiting on),
        # and a capped wait that ran out would destroy a live QThread (the
        # per-read socket timeout alone can exceed any sensible cap).
        for runner in update_runners:
            try:
                runner.wait()
            except RuntimeError:
                pass
        self._discard_update_download()
        # Normally released with the batch's report; a close mid-batch must
        # not leave this process holding the PC awake while it winds down.
        self._keep_awake.release()
        super().closeEvent(event)

    def _t(self, key: str) -> str:
        return i18n.translate(key, self.settings.language)

    def _risk_text(self, risk: RiskLevel, glyph: bool = True) -> str:
        return i18n.risk_text(risk.value, self.settings.language, glyph)

    def _tn(self, key: str, count: int, noun: str, **fields) -> str:
        """A string with a counted noun ("1 akcia", "3 akcie", "5 akcií")."""
        noun_text = i18n.count_noun(noun, int(count), self.settings.language)
        return self._t(key).format(count=count, noun=noun_text, **fields)

    def _on_category_changed(self, row: int) -> None:
        if self.search_box.text().strip():
            # A search is active - it decides which cards show, whatever
            # the sidebar is on (see _on_search_changed).
            self._on_search_changed(self.search_box.text())
            return
        for index, widget in enumerate(self._nav_row_order):
            widget.setHidden(index != row)
        if 0 <= row < len(self._categories_order):
            self._start_checks(self._category_action_ids.get(self._categories_order[row], []))
        elif 0 <= row - len(self._categories_order) < len(self._risk_tabs_order):
            self._ensure_risk_card(self._risk_tabs_order[row - len(self._categories_order)])

    def _ensure_risk_card(self, risk: RiskLevel) -> None:
        """Fills a risk card's rows on its first visit (see _build_ui)."""
        card_layout = self._risk_card_layouts.pop(risk, None)
        if card_layout is None:
            return
        for action_id in self._risk_action_ids[risk]:
            module, action = self._find_action(action_id)
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            mirror_checkbox = QCheckBox(action.label(self.settings.language))
            mirror_checkbox.setToolTip(action.description(self.settings.language))
            mirror_checkbox.setAccessibleDescription(action.description(self.settings.language))
            canonical_checkbox = self._action_checkboxes[action_id]
            mirror_checkbox.setChecked(canonical_checkbox.isChecked())
            # Two views, one source of truth: setChecked() only emits
            # stateChanged on an actual value change, so this pair never
            # loops - whichever view the user clicks, the other follows.
            mirror_checkbox.stateChanged.connect(
                lambda state, c=canonical_checkbox: c.setChecked(state != 0)
            )
            canonical_checkbox.stateChanged.connect(
                lambda state, m=mirror_checkbox: m.setChecked(state != 0)
            )
            self._risk_view_checkboxes[action_id] = mirror_checkbox
            row.addWidget(mirror_checkbox)
            category_label = QLabel(self._t(self._category_i18n_keys[module.category]))
            category_label.setObjectName("actionStatus")
            row.addWidget(category_label)
            row.addStretch(1)
            # Independent from the category view's toggle on purpose:
            # expand/collapse is display-only, not selection state, so
            # unlike the checkboxes above it has no two-way sync to break.
            detail_toggle, detail_panel = self._make_action_detail_toggle(action)
            self._risk_view_detail_toggles[action_id] = detail_toggle
            self._risk_view_detail_panels[action_id] = detail_panel
            row.addWidget(detail_toggle)
            row_container = self._wrap_row_with_detail_panel(row_widget, detail_panel)
            self._risk_view_rows[action_id] = row_container
            card_layout.addWidget(row_container)
        needle = self.search_box.text().strip().lower()
        if needle:
            for action_id in self._risk_action_ids[risk]:
                _, action = self._find_action(action_id)
                self._risk_view_rows[action_id].setHidden(needle not in self._action_search_haystack(action))

    # --- "already applied?" status chips (research G09) ----------------------

    def _start_checks(self, action_ids: list[str]) -> None:
        """Runs the state checks of the shown category's actions in the
        background, once per session (a batch that changes an action clears
        its answer); each answer lands on the action's chip."""
        jobs = []
        for action_id in action_ids:
            try:
                _, action = self._find_action(action_id)
            except KeyError:
                continue
            if action.check_command and action_id not in self._check_results and action_id not in self._check_pending:
                jobs.append(action_id)
        self._check_pending.update(jobs)
        self._check_queue.extend(jobs)
        self._pump_checks()

    def _pump_checks(self) -> None:
        if self._closed or _thread_running(self._check_runner) or not self._check_queue:
            return
        jobs, self._check_queue = self._check_queue, []
        runner = _CheckRunner([(aid, self._find_action(aid)[1]) for aid in jobs], self.target_user, parent=self)
        runner.state_ready.connect(self._on_check_state)
        runner.finished.connect(self._pump_checks)
        self._check_runner = runner
        runner.start()

    def _on_check_state(self, action_id: str, state: str) -> None:
        self._check_pending.discard(action_id)
        if self._closed:
            return
        self._check_results[action_id] = state
        self._set_check_chip(action_id, state)

    def _set_check_chip(self, action_id: str, state: str | None) -> None:
        chip = self._action_check_chips.get(action_id)
        if chip is None:
            return
        key = {"APPLIED": "check_applied", "NOT_APPLIED": "check_not_applied", "UNKNOWN": "check_unknown"}.get(state or "")
        chip.setText(self._t(key) if key else "")
        chip.setToolTip(self._t("check_tooltip") if key else "")
        chip.setProperty("state", {"APPLIED": "ok", "NOT_APPLIED": "running"}.get(state or "", ""))
        chip.style().unpolish(chip)
        chip.style().polish(chip)

    def _action_search_haystack(self, action) -> str:
        # Label alone misses power-user searches like "sfc" or "dism" - those
        # tool names live in the id/command (e.g. id "sfc_scannow", command
        # "sfc /scannow"), not in the human-friendly label ("System File
        # Checker (repair)"). Description is included too since it sometimes
        # names the underlying tool where the label doesn't.
        return " ".join((
            action.id,
            action.label(self.settings.language),
            action.description(self.settings.language),
            self._action_command_text(action),
        )).lower()

    @staticmethod
    def _action_command_text(action) -> str:
        # An `ops:` action's command is kilobytes of generated engine code
        # naming every op kind (ScheduledTask, sc.exe ...) - searching or
        # reading it would say nothing about what this action changes.
        return ops.describe(action.ops) if action.ops else action.command

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        if not needle:
            self._search_empty_label.setHidden(True)
            self._on_category_changed(self.category_list.currentRow())
        matched_ids: set[str] = set()
        for action_id, row_widget in self._action_rows.items():
            if not needle:
                row_widget.setHidden(False)
                continue
            _, action = self._find_action(action_id)
            haystack = self._action_search_haystack(action)
            is_match = needle in haystack
            row_widget.setHidden(not is_match)
            if is_match:
                matched_ids.add(action_id)
        for action_id, row_widget in self._risk_view_rows.items():
            if not needle:
                row_widget.setHidden(False)
                continue
            _, action = self._find_action(action_id)
            haystack = self._action_search_haystack(action)
            row_widget.setHidden(needle not in haystack)

        if not needle:
            self._update_status_bar()
            return

        # Search every category at once instead of just the one the sidebar
        # has open - a match hidden inside an unopened card looked identical
        # to "no such action". Only cards with a hit are shown: the
        # Dashboard, the Uninstaller and the risk views (which repeat every
        # row) would only bury the results.
        for category in self._categories_order:
            ids = self._category_action_ids.get(category, ())
            self._category_groups[category].setHidden(not any(aid in matched_ids for aid in ids))
        for card in self._nav_row_order[len(self._categories_order):]:
            card.setHidden(True)
        self._search_empty_label.setText(self._t("search_empty_state").format(query=text.strip()))
        self._search_empty_label.setHidden(bool(matched_ids))
        if not matched_ids:
            self.statusBar().showMessage(self._t("search_no_matches").format(query=text.strip()))
        else:
            self.statusBar().showMessage(self._t("search_matches_count").format(count=len(matched_ids)))

    # --- symptom suggestions (research G27) ------------------------------------

    def _on_symptom_changed(self, text: str) -> None:
        layout = self._symptom_results_layout
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        if not text.strip():
            self._symptom_results.setVisible(False)
            return
        language = self.settings.language
        matches = symptoms.suggest(self._symptoms, text)
        if not matches:
            label = QLabel(self._t("symptom_no_matches").format(query=text.strip()))
            label.setObjectName("selectionScope")
            label.setWordWrap(True)
            layout.addWidget(label)
        for match in matches:
            symptom = match.symptom
            diagnostics = [a for a in symptom.diagnostics if a in self._action_checkboxes]
            fixes = [a for a in symptom.fixes if a in self._action_checkboxes]
            tile = QFrame()
            tile.setObjectName("actionCard")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            tile_layout.setSpacing(2)
            title_label = QLabel(symptom.title(language))
            title_label.setObjectName("cardHeading")
            title_label.setWordWrap(True)
            why_label = QLabel(symptom.why(language))
            why_label.setObjectName("selectionScope")
            why_label.setWordWrap(True)
            matched_label = QLabel(self._t("symptom_matched").format(words=", ".join(match.matched_words)))
            matched_label.setObjectName("selectionScope")
            for widget in (title_label, why_label, matched_label):
                tile_layout.addWidget(widget)
            button_row = QHBoxLayout()
            button_row.setSpacing(6)
            diag_button = self._make_selection_button(
                self._t("symptom_select_diagnostics"),
                lambda s=symptom, ids=diagnostics: self._select_symptom(s, ids),
            )
            diag_button.setEnabled(bool(diagnostics))
            button_row.addWidget(diag_button)
            if fixes:
                button_row.addWidget(self._make_selection_button(
                    self._t("symptom_select_with_fixes"),
                    lambda s=symptom, ids=diagnostics + fixes: self._select_symptom(s, ids),
                ))
            button_row.addStretch(1)
            tile_layout.addLayout(button_row)
            tile_layout.addStretch(1)
            tile.setAccessibleName(f"{symptom.title(language)}. {symptom.why(language)}")
            layout.addWidget(tile, 1)
        self._symptom_results.setVisible(True)

    def _select_symptom(self, symptom, action_ids: list[str]) -> None:
        """Selects - never runs - a suggestion's actions; the batch review
        approves anything that is not SAFE, as for every other selection.
        Ticks exclude_from_select_all actions too (AdwCleaner, the external
        backup, the RAM test): that flag keeps them out of bulk sweeps, and
        a suggestion that names them is a deliberate pick, like their box."""
        self._apply_recommended_selection(action_ids, None)
        for action_id in action_ids:
            self._action_checkboxes[action_id].setChecked(True)
        self.statusBar().showMessage(self._t("symptom_selected").format(
            title=symptom.title(self.settings.language), count=len(action_ids)))

    def _log_batch_duration(self) -> None:
        # The report sums these into the run's work time (G20). Reset, so a
        # batch split by a restart report is counted once in two parts.
        if self._batch_started_at is None:
            return
        seconds = time.monotonic() - self._batch_started_at
        self._batch_started_at = time.monotonic()
        entry = make_entry(
            "_system", intake.BATCH_DURATION_EVENT, "", 0, intake.batch_duration_output(seconds),
            self._batch_dry_run, self.run_id, elevated=self.is_admin,
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            # Bookkeeping only: the batch's own entries already said the
            # disk write failed - one more "failed" line would be noise.
            pass

    def _make_action_detail_toggle(self, action: ActionDef) -> tuple[QToolButton, QWidget]:
        # The tooltip only shows the description on hover and disappears on
        # any focus change - this toggle makes the same info (plus the raw
        # command, which the tooltip never showed) stay on screen so a
        # cautious/technical user can read it before ever checking the box.
        # Collapsed by default so the row density doesn't change for anyone
        # who doesn't click it.
        toggle = QToolButton()
        toggle.setObjectName("actionDetailToggle")
        toggle.setCheckable(True)
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setToolTip(self._t("show_action_details"))
        # Narrator would read "black down-pointing triangle" for every row.
        toggle.setAccessibleName(f"{self._t('show_action_details')}: {action.label(self.settings.language)}")
        toggle.setText("▼")

        # An empty placeholder until the first expand: ~540 of these panels,
        # each with 1-2 QPlainTextEdits polished against the stylesheet,
        # were ~60 % of the 10 s start-up build.
        panel = QWidget()
        panel.setObjectName("actionDetailPanel")
        panel.setHidden(True)

        def _on_toggled(checked: bool) -> None:
            if checked and panel.layout() is None:
                self._fill_action_detail_panel(panel, action)
            panel.setHidden(not checked)
            toggle.setText("▲" if checked else "▼")
            toggle.setToolTip(self._t("hide_action_details") if checked else self._t("show_action_details"))

        toggle.toggled.connect(_on_toggled)
        return toggle, panel

    def _fill_action_detail_panel(self, panel: QWidget, action: ActionDef) -> None:
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(4)

        description_label = QLabel(action.description(self.settings.language))
        description_label.setObjectName("actionDetailDescription")
        description_label.setWordWrap(True)
        panel_layout.addWidget(description_label)

        command_box = QPlainTextEdit(self._action_command_text(action))
        command_box.setObjectName("actionDetailCommand")
        command_box.setReadOnly(True)
        command_box.setFixedHeight(60)
        panel_layout.addWidget(command_box)

        if action.has_undo:
            undo_label = QLabel(self._t("action_detail_undo_label"))
            undo_label.setObjectName("actionDetailLabel")
            panel_layout.addWidget(undo_label)
            # An ops action's undo only exists after it ran (it is made from
            # the captured state) - say how it will restore instead.
            undo_box = QPlainTextEdit(action.undo_command or self._t("action_detail_ops_undo"))
            undo_box.setObjectName("actionDetailCommand")
            undo_box.setReadOnly(True)
            undo_box.setFixedHeight(48)
            panel_layout.addWidget(undo_box)

    @staticmethod
    def _wrap_row_with_detail_panel(row_widget: QWidget, detail_panel: QWidget) -> QWidget:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)
        container_layout.addWidget(row_widget)
        container_layout.addWidget(detail_panel)
        return container

    def _on_checkbox_state_changed(self, _state: int = 0) -> None:
        if not self._bulk_selecting:
            self._update_status_bar()

    def _apply_selection(self, action_ids: list[str], mode: str) -> None:
        # mode is "all", "none", or a RiskLevel value (e.g. "SAFE") meaning
        # "check only actions at exactly this risk level".
        # Any manual selection change invalidates a previously lit preset
        # button - without this, "Zrusit vyber" (or any select-by-risk/
        # category button) left the preset button visually checked even
        # though the actual checkbox selection no longer matches that preset.
        # _apply_preset() re-lights the right button itself right after
        # calling this, so clearing here doesn't affect preset switching.
        checked_preset = self._preset_button_group.checkedButton()
        if checked_preset is not None:
            # An exclusive QButtonGroup refuses to drop to zero checked
            # buttons via a direct setChecked(False) call on the sole
            # checked one - toggle exclusivity off for the moment it takes
            # to actually clear it.
            self._preset_button_group.setExclusive(False)
            checked_preset.setChecked(False)
            self._preset_button_group.setExclusive(True)
        # One status-bar recount for the whole sweep, not one per checkbox.
        self._bulk_selecting = True
        try:
            self._set_checks(action_ids, mode)
        finally:
            self._bulk_selecting = False
        self._update_status_bar()

    def _set_checks(self, action_ids: list[str], mode: str) -> None:
        for action_id in action_ids:
            if mode == "none":
                checked = False
            else:
                # Recovery/restore-style actions (e.g. drv_restore_backup,
                # hard_disable_rdp) opt out of every bulk sweep - "select
                # all" and the select-by-risk buttons alike. They must be
                # checked deliberately via their own checkbox; "MODERATE
                # only" used to sweep four of them in as a side effect.
                _, action = self._find_action(action_id)
                checked = not action.exclude_from_select_all and (mode == "all" or action.risk.value == mode)
            self._action_checkboxes[action_id].setChecked(checked)

    def _log_panel_action(
        self, module_id: str, action_id: str, command: str, exit_code: int | None, output: str,
        dry_run: bool, risk: str, warned: bool = False, warning_text: str = "",
    ) -> None:
        # The uninstaller and winget panels change the system outside the
        # batch queue (_dispatch_action / _on_action_finished), so they write
        # their own entries - same log, same fields, same report.
        entry = make_entry(
            module_id, action_id, command, exit_code, output, dry_run, self.run_id,
            risk=risk, warned=warned, elevated=self.is_admin, warning_text=warning_text,
            **self.target_user.audit_fields(),
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self._report_write_failure()

    def _panel_refused_without_admin(self, console: QPlainTextEdit) -> bool:
        """True (and shown) when a panel's real run needs admin rights it
        does not have. The winget/uninstaller/leftover-cleanup panels run
        outside the batch queue, so they never went through preflight's
        "no_admin" blocker - uninstalling a program, installing a winget
        upgrade or deleting an HKLM leftover key all need an elevated
        process, and so does the restore point this same run makes right
        after (Enable-ComputerRestore/Checkpoint-Computer fail without
        admin, previously surfacing only as a generic "continue anyway?"
        prompt with no explanation of why it failed)."""
        if self.is_admin:
            return False
        console.setVisible(True)
        console.appendPlainText(self._t("panel_needs_admin"))
        return True

    def _start_panel_restore_point(self, subjects: list[str], console: QPlainTextEdit, on_done) -> bool:
        """The panels' real runs get the batch's safety net (research G01):
        one restore point before the change, through the same runner, logged
        the same way, with the same "continue without it?" question when it
        fails. on_done(proceed) is called once it is settled - never after
        the window started closing. False when it could not be started (a
        restore point is already being made); the caller then does nothing.
        subjects: every program/package this one point guards - all of them
        are logged, so the report does not read as if only the first had
        a way back."""
        if self.settings.dry_run:
            # Callers never get here in DRY-RUN; a DRY-RUN must never create
            # a restore point even if one did.
            return False
        if self._batch_active:
            # The batch may reach its own restore point (or change the same
            # programs) while this one runs - two Checkpoint-Computer calls
            # at once each save and restore the 24h throttle value, and the
            # second one can put back the first one's temporary 0 for good.
            self.statusBar().showMessage(self._t("panel_blocked_by_batch"))
            return False
        if (
            _thread_running(self._pending_restore_point_runner)
            or _thread_running(self._pending_panel_restore_point_runner)
            or _thread_running(self._pending_hive_backup_runner)
        ):
            # Windows makes one checkpoint at a time - a second one started
            # now would fail and look like "System Restore is broken".
            self.statusBar().showMessage(self._t("panel_restore_point_busy"))
            return False
        console.setVisible(True)
        console.appendPlainText(self._t("panel_restore_point_running"))
        runner = restore_point.RestorePointRunner(f"PortableFix {self.run_id}", parent=self)
        runner.result_ready.connect(
            lambda success, detail, info, s=list(subjects), cb=on_done: self._on_panel_restore_point_checked(success, detail, info, s, cb)
        )
        self._pending_panel_restore_point_runner = runner
        runner.start()
        return True

    def _on_panel_restore_point_checked(self, success: bool, detail: str, info: dict | None, subjects: list[str], on_done) -> None:
        # The first subject keys the record (the "continue anyway?" answer
        # pairs with it); the rest ride along for the report.
        subject = subjects[0] if subjects else ""
        self._log_restore_point_result(success, detail, info, subject, subjects if len(subjects) > 1 else None)
        if self._closed or self._close_after_restore_point:
            # The window is closing and only waited for this checkpoint -
            # the uninstall/update it guarded must never start now.
            return
        proceed = True
        if not success:
            answer = QMessageBox.warning(
                self, self._t("app_title"), self._t("restore_point_failed_confirm"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            proceed = answer == QMessageBox.Yes
            # Same record as the batch's answer (research-reporting.md F3).
            self._log_system_event(
                "restore_point_decision", 0,
                "Technician chose to continue without a restore point." if proceed
                else "Technician declined to continue without a restore point - nothing was changed.",
                warned=True, warning_text=self._t("restore_point_failed_confirm"),
                subject=subject, decision="proceed" if proceed else "skip",
            )
        if proceed and self.settings.dry_run:
            self.statusBar().showMessage(self._t("panel_cancelled_dry_run"))
            # DRY-RUN was switched on during the minutes Checkpoint-Computer
            # took - the batch re-checks it per action, so must the panel:
            # the real uninstall/update it was about to start is dropped.
            self._log_system_event(
                "risk_declined", None,
                "DRY-RUN was switched on while the restore point was being made - nothing was changed.",
                subject=subject, decision="declined",
            )
            proceed = False
        try:
            on_done(proceed)
        except RuntimeError:
            # The panel's widgets were deleted meanwhile (C++ object gone) -
            # nothing ran, and there is no panel left to show it in.
            pass

    def _confirm_programs_closed(
        self, title: str, targets: list[tuple[str, object, str]], all_subjects: list[str], risk: str,
        declined_output: str,
    ) -> bool:
        """Warn when a program about to be uninstalled/updated is running
        (matched by process image path - research G01) and ask to close it
        first. Retry checks again, Ignore continues (logged), Cancel stops
        the whole run (every selected item logged as declined). True = go on."""
        if not targets:
            return True
        while True:
            processes = panel_safety.list_processes()
            running: list[tuple[str, str, list]] = []
            for label, program, subject in targets:
                matches = panel_safety.running_matches(program, processes)
                if matches:
                    running.append((label, subject, matches))
            if not running:
                return True
            lines = []
            for label, _subject, matches in running:
                names = sorted({f"{m.exe_name} (PID {m.pid})" for m in matches})
                shown = ", ".join(names[:3]) + (" …" if len(names) > 3 else "")
                lines.append(f"{label}: {shown}")
            text = self._t("panel_running_programs_text").format(programs=self._panel_confirm_list(lines))
            answer = QMessageBox.warning(
                self, title, text, QMessageBox.Retry | QMessageBox.Ignore | QMessageBox.Cancel, QMessageBox.Cancel,
            )
            if answer == QMessageBox.Retry:
                continue
            if answer == QMessageBox.Ignore:
                for _label, subject, _matches in running:
                    self._log_system_event(
                        "running_programs_decision", 0,
                        "Technician chose to continue while the program was still running.",
                        risk=risk, warned=True, warning_text=text, subject=subject, decision="proceed",
                    )
                return True
            for subject in all_subjects:
                self._log_system_event(
                    "risk_declined", None, declined_output,
                    risk=risk, warned=True, warning_text=text, subject=subject, decision="declined",
                )
            return False

    def _panel_confirm_list(self, lines: list[str], limit: int = 20) -> str:
        # Capped: a confirmation listing 150 programs grows taller than the
        # screen and pushes its own Yes/No buttons out of reach.
        shown = [f"• {line}" for line in lines[:limit]]
        if len(lines) > limit:
            shown.append(self._t("uninstaller_and_more").format(count=len(lines) - limit))
        return "\n".join(shown)

    def _show_error(self, parent: QWidget | None, text: str, detail: str = "") -> None:
        """What happened and what to do next; the raw exception is only under
        'Show Details' for the technician."""
        box = QMessageBox(QMessageBox.Icon.Warning, self._t("app_title"), text, QMessageBox.StandardButton.Ok, parent or self)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _load_eta_history(self) -> dict[str, float]:
        # A preview takes no real time, so it gets no estimate.
        if self._batch_dry_run:
            return {}
        try:
            return history.typical_durations(self.state_dir / "Reports", socket.gethostname())
        except Exception:  # noqa: BLE001 - an ETA is never worth failing a batch for
            return {}

    def _update_progress_eta(self, remaining_ids: list[str]) -> None:
        """'3 / 12' plus 'about 6 min left' from past runs of the same
        actions. Actions with no history add nothing, so the estimate can only
        be low; under half a minute is not worth showing."""
        seconds = sum(self._eta_history.get(action_id, 0.0) for action_id in remaining_ids)
        text = "%v / %m"
        if seconds >= 30:
            text += " · " + self._t("progress_eta").format(minutes=max(1, round(seconds / 60)))
        self.progress_bar.setFormat(text)

    def _find_action(self, action_id: str) -> tuple[ModuleDef, ActionDef]:
        hit = self._action_index.get(action_id)
        if hit is None:
            # Built on first use and again on a miss: self.modules is public
            # and can be extended after construction.
            self._action_index = {}
            for module in self.modules:
                for action in module.actions:
                    self._action_index.setdefault(action.id, (module, action))
            hit = self._action_index.get(action_id)
            if hit is None:
                raise KeyError(action_id)
        return hit

    def _skip_high_risk_actions_in_queue(self) -> None:
        def _is_high_risk(action_id: str) -> bool:
            return preflight.needs_restore_point(*self._find_action(action_id))

        self._queue = [aid for aid in self._queue if not _is_high_risk(aid)]

    def _take_snapshot(self) -> dict:
        # GUI thread, at batch start and end - snapshot.py keeps it bounded
        # (time-budgeted folder walks, no process spawns) and never raises.
        # The state checks known so far ride along (G09): the report compares
        # them with the last visit to show what Windows turned back.
        return snapshot.take_snapshot(disk_usage=shutil.disk_usage, checks=self._check_results)

    def _on_cancel_clicked(self) -> None:
        self._cancel_requested = True
        self._queue = []
        self.cancel_button.setEnabled(False)
        if self._runner is not None:
            self._runner.cancel()

    def _action_accessible_name(self, action: ActionDef, status_text: str = "") -> str:
        # "riziko"/"risk" translated - Narrator reads the whole name in the
        # UI language, and a lone English word mid-sentence is jarring.
        name = f"{action.label(self.settings.language)} — {self._t('a11y_risk')}: {self._risk_text(action.risk, glyph=False)}"
        if status_text:
            name += f", {status_text}"
        return name

    def _set_action_status(self, action_id: str, state: str, text: str) -> None:
        label = self._action_status_labels.get(action_id)
        if label is not None:
            label.setText(text)
            label.setProperty("state", state)
            label.style().unpolish(label)
            label.style().polish(label)
        checkbox = self._action_checkboxes.get(action_id)
        if checkbox is not None:
            _, action = self._find_action(action_id)
            checkbox.setAccessibleName(self._action_accessible_name(action, text))

    def _on_action_tick(self) -> None:
        # "RUNNING (12.0s)" on the card of the action running now (item 2) -
        # tells "still working" from "stuck" on a minutes-long DISM/SFC.
        if self._closed:
            self._action_tick_timer.stop()
            return
        # The label only: re-setting the checkbox's accessible name every
        # second would make a screen reader re-announce the row.
        now = time.monotonic()
        for action_id, started in self._action_start_times.items():
            label = self._action_status_labels.get(action_id)
            text = f"{self._t('status_running')} ({_elapsed_text(now - started)})"
            if label is None:
                continue
            if label.property("state") != "running":
                # A language toggle rebuilt the card mid-action.
                self._set_action_status(action_id, "running", text)
            else:
                label.setText(text)

    def _batch_start_blocked(self) -> bool:
        # A batch is running, the previous batch's report is still being
        # written (see _run_next) or an update is downloading.
        # A card's Preview (item 6) also holds a batch off - both would write
        # into the one console at once.
        return (
            self._batch_active or self._update_in_progress or self._report_runner is not None
            or self._preview_runner is not None
        )

    def run_selected_actions(self) -> None:
        # Mid-batch re-entry (the dashboard's Analyze button, a direct call)
        # would overwrite _queue, reset _cancel_requested - un-cancelling a
        # batch whose Cancel was clicked during its restore point - and start
        # a second ActionRunner alongside the running one.
        if self._batch_start_blocked():
            return
        resuming, self._resuming = self._resuming, None
        self._batch_dry_run = bool(self.settings.dry_run)
        queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        # Research G03: an action that restarts Windows at once runs last, so
        # it cuts nothing off - and only after the report and undo.ps1 exist.
        queue = batch_resume.order_restarting_last(queue, lambda aid: self._find_action(aid)[1].restarts_pc)
        # Research G05: per-item actions ask which items first - the review
        # screen then shows what was picked. A continued batch (G03) keeps
        # the picks made before the restart.
        queue = self._choose_items(queue, picked=resuming.items if resuming is not None else None)
        if queue is None:
            return
        self._reviewed_warnings = {}
        # Per batch, like the restore point: set only by this batch's review.
        self._hive_backup_requested = False
        self._hive_backup_attempted = False
        if queue and (not self.settings.dry_run or resuming is not None):
            # A DRY-RUN changes nothing, so it gets neither the pre-flight
            # nor a confirmation - only a real batch is reviewed, once. A
            # continued batch is always confirmed again: nothing runs by
            # itself after a restart.
            queue = self._review_batch(queue, resuming=resuming)
            if queue is None:
                return
        self._pre_restart_prepared = set()
        self._resume_saved_for = ""
        self._restart_needed_after = ""
        self._restart_report_path = None
        self._restarting = False
        if resuming is not None and queue:
            # Consumed once the continued batch starts; declining it on the
            # review screen leaves it for another try until it goes stale.
            batch_resume.discard_pending(self.state_dir)
            self._log_system_event(
                "resumed_after_reboot", 0,
                f"Continuing the batch after a restart ({len(queue)} action(s): {', '.join(queue)}).",
                subject=self._restart_subject(resuming.restart_after),
            )
        self._queue = queue
        self._queue_total = len(self._queue)
        self._restore_point_attempted = False
        self._batch_results = []
        self._failure_hints = {}
        self._action_durations = {}
        self._eta_history = self._load_eta_history()
        self._summary_dialog = None
        self._cancel_requested = False
        for action_id in self._queue:
            self._set_action_status(action_id, "", "")
        if self._queue:
            self._batch_active = True
            self._batch_started_at = time.monotonic()
            self._keep_awake.acquire()
            # A continued batch compares against the PC as it was before its
            # first half - one report for the whole job.
            if resuming is not None and resuming.snapshot_before:
                self._snapshot_before = resuming.snapshot_before
            else:
                self._snapshot_before = self._take_snapshot()
            self.run_button.setEnabled(False)
            self.dashboard_analyze_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.language_button.setEnabled(False)
            self.dry_run_checkbox.setEnabled(False)
            self.restart_admin_button.setEnabled(False)
            self.progress_bar.setMaximum(self._queue_total)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
        self._run_next()

    def resume_batch(self, pending: batch_resume.PendingBatch) -> None:
        """Continues a batch saved before a restart (research G03). main.py
        asked the technician first; this selects what was left and opens the
        review screen - the batch starts only when it is confirmed there."""
        if self._batch_start_blocked():
            return
        known = [aid for aid in pending.action_ids if aid in self._action_checkboxes]
        missing = [aid for aid in pending.action_ids if aid not in self._action_checkboxes]
        if missing:
            # An update between the halves dropped or renamed an action.
            self._log_system_event(
                "resume_skipped", None,
                f"Not in this version's catalog, not continued: {', '.join(missing)}.",
            )
        if not known:
            batch_resume.discard_pending(self.state_dir)
            return
        job = pending.job
        self._set_job(job.get("technician") or self.settings.technician_name, job.get("client", ""), job.get("note", ""))
        # Same run_id: undo.ps1 is rewritten for it, so it must keep the
        # first half's steps (and the hive backups it points to).
        self._undo_steps = list(pending.undo_steps) + self._undo_steps
        self._irreversible_actions = list(pending.irreversible) + self._irreversible_actions
        # Saved relative to the state dir, so a new USB drive letter after
        # the restart is followed; one that is still missing is said aloud -
        # undo.ps1 would point at a backup that is not there.
        hive_backups = batch_resume.hive_paths_on_load(self.state_dir, pending.hive_backups)
        missing_hives = [str(path) for path in hive_backups if not path.exists()]
        if missing_hives:
            self._log_system_event(
                "resume_hive_backup_missing", None,
                f"Registry hive backup of the first half not found: {', '.join(missing_hives)}.",
            )
            self._append_console(self._t("resume_hive_backup_missing").format(paths=", ".join(missing_hives)))
        self._hive_backups = hive_backups + self._hive_backups
        self._resume_mode_note = ""
        if self.settings.dry_run != pending.dry_run:
            # The continued batch runs in the mode of its first half - but
            # never silently: the review screen says the mode was switched.
            self.dry_run_checkbox.setChecked(pending.dry_run)
            self._resume_mode_note = "review_note_resumed_dry_run" if pending.dry_run else "review_note_resumed_real_run"
            self._append_console(self._t(self._resume_mode_note))
        self._apply_selection(list(self._action_checkboxes), "none")
        for action_id in known:
            self._action_checkboxes[action_id].setChecked(True)
        self._resuming = pending
        self.run_selected_actions()
        # A review that was cancelled (or never shown) leaves the selection
        # in place for the technician, but never a stale "resuming" flag.
        self._resuming = None
        self._resume_mode_note = ""

    def _preflight_busy_tasks(self) -> list[str]:
        # System-changing jobs of this window that would run side by side
        # with the batch (two package/servicing operations at once fail in
        # confusing ways), and an update replacing the install right now.
        tasks = []
        if _thread_running(self._winget_update_runner):
            tasks.append(self._t("update_busy_winget_update"))
        if _thread_running(self._uninstall_runner):
            tasks.append(self._t("update_busy_uninstall"))
        if _thread_running(self._pending_panel_restore_point_runner):
            # A panel's restore point (G01): the batch's own would collide
            # with it - Windows makes one checkpoint at a time.
            tasks.append(self._t("update_busy_restore_point"))
        if update_swap.update_mutex_present():
            tasks.append(self._t("preflight_busy_update"))
        return tasks

    def _preflight_probes(self) -> preflight.Probes:
        return preflight.system_probes(is_admin=lambda: self.is_admin, busy_tasks=self._preflight_busy_tasks)

    def _ask_batch_review(self, review: BatchReview) -> ReviewDecision:
        # Its own method so a test can replace the whole screen; the dialog
        # itself is driven through BatchReviewDialog.exec in the GUI tests.
        return BatchReviewDialog(review, parent=self).ask()

    def _probe_disk_health(self, probe) -> tuple[list | None, bool]:
        """Runs the disk health probe on a worker thread behind a busy
        dialog with Cancel; returns (verdicts, cancelled). The GUI stays
        responsive meanwhile - the probe can take up to
        disk_health.PROBE_TIMEOUT_SEC when the storage stack hangs, which
        a dying disk's often does. Tests inject `probe` via Probes.disk_health."""
        return self._run_in_background(
            probe, self._t("preflight_disk_probe_running").format(seconds=disk_health.PROBE_TIMEOUT_SEC),
            self._t("preflight_disk_probe_cancel"),
        )

    def _run_in_background(self, func, text: str, cancel_text: str, on_cancel=None) -> tuple[object, bool]:
        """func() on a worker thread behind a busy dialog with Cancel;
        returns (result, cancelled). The GUI stays responsive meanwhile.
        on_cancel runs when the technician cancels (e.g. kills the process
        func is waiting on); the thread itself is left to finish."""
        self._disk_health_runners = [r for r in self._disk_health_runners if _thread_running(r)]
        results: list = []
        loop = QEventLoop()
        runner = _DiskHealthProbeRunner(func, results, parent=self)
        # Queued on purpose: a probe that finishes before loop.exec() starts
        # must still quit the loop (a direct quit() before exec() is lost).
        runner.finished.connect(loop.quit, Qt.ConnectionType.QueuedConnection)
        dialog = QProgressDialog(text, cancel_text, 0, 0, self)
        dialog.setWindowTitle(self._t("app_title"))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        # The Cancel button emits canceled; Esc / the title bar's close
        # reject the dialog instead - both mean the same "not now".
        cancel_asked: list[bool] = []
        for signal in (dialog.canceled, dialog.rejected):
            signal.connect(lambda: cancel_asked.append(True))
            signal.connect(loop.quit)
        self._disk_health_runners.append(runner)
        runner.start()
        dialog.show()
        loop.exec()
        # A verdict that arrived together with the click still counts.
        cancelled = bool(cancel_asked) and not results
        dialog.close()
        dialog.deleteLater()
        if cancelled:
            if on_cancel is not None:
                on_cancel()
            return None, True
        return (results[0] if results else None), False

    def _list_items(self, action: ActionDef) -> tuple[list | None, bool]:
        """(items or None when the listing failed, cancelled) - research G05."""
        holder: list = []

        def cancel() -> None:
            for run in holder:
                run.cancel()

        return self._run_in_background(
            lambda: action_service.list_items(action, self.target_user, holder),
            self._t("items_listing").format(action=action.label(self.settings.language)),
            self._t("preflight_disk_probe_cancel"), on_cancel=cancel,
        )

    def _ask_items(self, action: ActionDef, listed: list) -> list[str] | None:
        # Its own method so a test can answer it; the dialog itself is
        # driven through ItemsDialog in the GUI tests.
        label = action.label(self.settings.language)
        dialog = ItemsDialog(
            self._t("items_dialog_title").format(action=label), self._t("items_dialog_intro"), listed,
            {
                "select_all": self._t("items_select_all"), "select_none": self._t("items_select_none"),
                "ok": self._t("dialog_ok"), "cancel": self._t("dialog_cancel"),
            }, parent=self,
        )
        try:
            return dialog.ask()
        finally:
            dialog.deleteLater()

    def _choose_items(self, queue: list[str], picked: dict[str, list[str]] | None = None) -> list[str] | None:
        """Research G05: lists the items of every per-item action in the
        queue and asks which of them to use. Returns the queue without the
        actions that got nothing to work on, or None when the technician
        cancelled the batch. `picked`: ids chosen before a restart (G03) -
        such an action is not asked again, it keeps the ones still listed."""
        picked = picked or {}
        self._selected_items = {}
        result: list[str] = []
        for action_id in queue:
            module, action = self._find_action(action_id)
            if not action.items_command:
                result.append(action_id)
                continue
            label = action.label(self.settings.language)
            listed, cancelled = self._list_items(action)
            if cancelled or self._closed:
                return None
            if listed is None:
                self._append_console(self._t("items_list_failed").format(action=label))
                continue
            if not listed:
                self._append_console(self._t("items_none_found").format(action=label))
                continue
            present = {item.id for item in listed}
            if picked.get(action_id):
                # The restart may have removed some (a disabled entry that
                # is gone now) - the rest keep the earlier answer.
                chosen = [i for i in picked[action_id] if i in present]
            else:
                chosen = self._ask_items(action, listed)
                if chosen is None or self._closed:
                    return None
            try:
                chosen = items_mod.check_ids(chosen, allowed=present)
            except items_mod.ItemsError:
                chosen = []
            if not chosen:
                self._append_console(self._t("items_none_chosen").format(action=label))
                continue
            self._selected_items[action_id] = chosen
            result.append(action_id)
        return result

    def _items_notes(self, queue: list[str]) -> tuple[str, ...]:
        """What the review screen says about the items picked (G05)."""
        notes = []
        for action_id in queue:
            chosen = self._selected_items.get(action_id)
            if chosen:
                notes.append(self._t("review_note_items").format(
                    action=self._action_label(action_id), count=len(chosen), items=", ".join(chosen),
                ))
        return tuple(notes)

    def _restart_subject(self, action_id: str) -> str:
        try:
            module, _ = self._find_action(action_id)
        except KeyError:
            return action_id
        return f"{module.module_id}/{action_id}"

    def _action_label(self, action_id: str) -> str:
        return self._find_action(action_id)[1].label(self.settings.language)

    def _restart_notes(self, queue: list[str], resuming: batch_resume.PendingBatch | None) -> tuple[str, ...]:
        """What the review screen says about restarts in this batch (G03)."""
        notes = []
        if resuming is not None:
            notes.append(self._t("review_note_resumed").format(count=len(queue)))
            if self._resume_mode_note:
                notes.append(self._t(self._resume_mode_note))
        restarting = [aid for aid in queue if self._find_action(aid)[1].restarts_pc]
        if restarting:
            notes.append(self._t("review_note_restarts_last").format(
                actions=", ".join(self._action_label(aid) for aid in restarting),
            ))
        split = batch_resume.plan_restart_split(
            queue,
            lambda aid: self._find_action(aid)[1].restarts_pc,
            lambda aid: self._find_action(aid)[1].restart_before_next,
        )
        if split.waiting_ids:
            notes.append(self._t("review_note_waits_for_restart").format(
                action=self._action_label(split.restart_action_id),
                actions=", ".join(self._action_label(aid) for aid in split.waiting_ids),
                hours=int(batch_resume.MAX_RESUME_AGE.total_seconds() // 3600),
            ))
        return tuple(notes)

    def _review_batch(self, queue: list[str], resuming: batch_resume.PendingBatch | None = None) -> list[str] | None:
        """Pre-flight + the one review screen (research G11/G12). Returns
        the queue to run - without the declined actions - or None when the
        technician cancelled the batch."""
        items = [self._find_action(aid) for aid in queue]
        profile = preflight.profile_for(items)
        probes = self._preflight_probes()
        if profile.stresses_disk:
            verdicts, cancelled = self._probe_disk_health(probes.disk_health)
            if cancelled or self._closed:
                # Cancel on the busy dialog = "not now": the batch does not
                # start (skipping the probe would silently skip the G13 gate).
                outcome = (
                    "Technician cancelled the batch before the review screen." if cancelled
                    else "The window was closed before the review screen."
                )
                self._log_system_event(
                    "batch_review", None, f"Pre-flight: disk health check cancelled. {outcome}", decision="cancelled",
                )
                return None
            probes = dataclasses.replace(probes, disk_health=lambda: verdicts)
        result = preflight.run_preflight(profile, probes)
        review = build_review(
            items, result, self.settings.language,
            hive_backup_bytes=hive_backup.estimate_bytes() if any(a.risk == RiskLevel.DESTRUCTIVE for _, a in items) else None,
            notes=self._restart_notes(queue, resuming) + self._items_notes(queue),
            estimate_seconds=sum(self._load_eta_history().get(aid, 0.0) for aid in queue),
        )
        if not review.needs_confirmation:
            return queue
        decision = self._ask_batch_review(review)
        if self._closed and decision.confirmed:
            # The window went away behind the modal screen - a batch must
            # never start now (it would run unlogged and outlive the app).
            decision = ReviewDecision(confirmed=False, declined=[i for i in review.items if i.warning_text])
        issues_text = "\n".join(issue.text(self.settings.language) for issue in result.issues)
        if not decision.confirmed:
            outcome, decision_value = "Technician cancelled the batch on the review screen.", "cancelled"
        elif decision.overrode_blockers:
            outcome, decision_value = "Technician confirmed the batch and overrode the pre-flight blockers.", "override"
        else:
            outcome, decision_value = "Technician confirmed the batch on the review screen.", "confirmed"
        if decision.confirmed and decision.hive_backup:
            outcome += " Full registry hive backup requested before the first DESTRUCTIVE action."
        # The pre-flight result and the answer go on record either way - an
        # override is exactly what a later dispute is about.
        self._log_system_event(
            "batch_review", 0 if decision.confirmed else None, f"{result.summary()} {outcome}",
            warned=bool(result.issues), warning_text=issues_text, decision=decision_value,
        )
        for item in decision.declined:
            # Same record as a "No" in the old per-action box (research-reporting.md F2).
            self._log_system_event(
                "risk_declined", None, "Technician declined the risk confirmation - action not run.",
                risk=item.risk.value, warned=True, warning_text=item.warning_text,
                subject=item.subject, decision="declined",
            )
        if not decision.confirmed:
            return None
        declined_ids = {item.action_id for item in decision.declined}
        self._hive_backup_requested = decision.hive_backup
        # SAFE actions were reviewed too (with no text to quote).
        self._reviewed_warnings = {
            item.action_id: item.warning_text for item in review.items if item.action_id not in declined_ids
        }
        return [aid for aid in queue if aid not in declined_ids]

    def _on_report_ready(self, html_path: Path | None, write_failed: bool) -> None:
        self._report_runner = None
        if html_path is not None:
            self._report_written = True
        # The batch is over (report written or not): sleep is allowed again.
        self._keep_awake.release()
        if not self._closed:
            self.run_button.setEnabled(True)
            self.dashboard_analyze_button.setEnabled(True)
            self.language_button.setEnabled(True)
            self.dry_run_checkbox.setEnabled(True)
            self.restart_admin_button.setEnabled(True)
            if write_failed:
                self._report_write_failure()
        self._refresh_dashboard()
        if not self._closed:
            self._notify_batch_finished()
        if html_path is not None and not self._closed:
            # batch_done_message says "the report is ready" - only
            # true on this branch.
            ok_count = sum(1 for _, code in self._batch_results if code == 0)
            _announce_to_screen_reader(self, self._t("batch_done_message").format(
                ok=ok_count, failed=len(self._batch_results) - ok_count,
            ))
            self._show_batch_summary(html_path)
        if self._restart_needed_after and not self._closed:
            self._notify_restart_needed()
        self._run_pending_report_refresh()

    def _notify_restart_needed(self) -> None:
        # Own method so a test can see it without a real modal box.
        pending = batch_resume.load_pending(self.state_dir)
        count = len(pending.action_ids) if pending is not None else 0
        text = self._t("restart_needed_to_continue").format(
            action=self._action_label(self._restart_needed_after), count=count,
        )
        self._append_console(text)
        QMessageBox.information(self, self._t("app_title"), text)

    def _app_dir_intact(self) -> bool:
        # Cheap existence check, not a deep scan - Modules/ is the canary
        # because it's the one directory every action's own catalog lives
        # in, so its disappearance is as clear a sign as any that the app's
        # install folder was wiped out from under the running process.
        app_dir = paths.get_base_dir()
        return app_dir.exists() and (app_dir / "Modules").exists()

    def _run_next(self) -> None:
        if not self._queue:
            if self._batch_active:
                self._batch_active = False
                self._log_batch_duration()
                self._batch_started_at = None
                # A confirmation covers this batch only, never a later run.
                self._reviewed_warnings = {}
                if not self._closed:
                    # run/language stay disabled until the report is written
                    # (_on_report_ready): a second batch now would append to
                    # the audit log the report thread is reading and race it
                    # for the same report files.
                    self.cancel_button.setEnabled(False)
                    self.progress_bar.setValue(self._queue_total)
                    self.progress_bar.setVisible(False)
                    self._apply_selection(list(self._action_checkboxes), "none")
                    self._update_status_bar()
                if self._restarting and self._restart_report_path is not None:
                    # Windows is restarting (a restarts_pc action succeeded):
                    # the report written right before it stays the final one
                    # - rewriting it now could be cut off half-way. The
                    # action's own result is in the audit log, and a
                    # continued batch writes the full report under the same
                    # run_id.
                    self._on_report_ready(self._restart_report_path, False)
                    return
                snapshot_after = self._take_snapshot()
                self._snapshot_after = snapshot_after
                report_args = (
                    self.state_dir,
                    self.run_id,
                    self.modules,
                    self.settings.language,
                    self._snapshot_before,
                    snapshot_after,
                )
                report_kwargs = {
                    "job": self._job_info(), "storage_fallback": self._storage_fallback,
                    "redact": self.settings.redact_for_client,
                    "branding": self.settings.branding_info(),
                }
                if self._closed:
                    # closeEvent has already waited on every runner, so a
                    # thread started now could outlive the window (Qt aborts
                    # on a destroyed running QThread) - with no UI left to
                    # stall, just write the report here.
                    try:
                        html_path, _ = report.generate_report(*report_args, **report_kwargs)
                    except OSError:
                        self._on_report_ready(None, True)
                    else:
                        self._on_report_ready(html_path, False)
                    return
                runner = report.ReportRunner(*report_args, **report_kwargs, parent=self)
                runner.result_ready.connect(self._on_report_ready)
                self._report_runner = runner
                runner.start()
            return
        if not self._app_dir_intact():
            # The app's own install folder (or its Modules/ subfolder) has
            # vanished since this batch started - something external wiped
            # it out from under the running process. Continuing would only
            # dispatch further actions against a filesystem state nobody can
            # reason about, and would keep masking exactly this kind of
            # incident: append_entry() happily recreates a missing Logs/ dir
            # before writing, so a silently-continuing batch leaves almost no
            # trace of when/what actually went missing. Stop now instead.
            self._queue = []
            entry = make_entry(
                "_system",
                "integrity_guard",
                "",
                1,
                "App base directory or Modules/ folder was missing before dispatching the next "
                "queued action - batch stopped for safety.",
                self._batch_dry_run,
                self.run_id,
                **self.target_user.audit_fields(),
            )
            try:
                append_entry(self.state_dir, self.run_id, entry)
            except OSError:
                pass
            if not self._closed:
                QMessageBox.warning(self, self._t("app_title"), self._t("batch_stopped_app_dir_missing"))
            self._run_next()
            return
        action_id = self._queue.pop(0)
        module, action = self._find_action(action_id)
        position = self._queue_total - len(self._queue)
        if not self._closed:
            running_text = self._t("status_bar_running").format(
                pos=position, total=self._queue_total, label=action.label(self.settings.language)
            )
            self.statusBar().showMessage(running_text)
            _announce_to_screen_reader(self, running_text)
            self.progress_bar.setValue(position - 1)
            self._update_progress_eta([action_id, *self._queue])

        needs_restore_point = preflight.needs_restore_point(module, action)
        if needs_restore_point and not self._restore_point_attempted and not self._batch_dry_run:
            panel_runner = self._pending_panel_restore_point_runner
            if _thread_running(panel_runner):
                # Pre-flight refuses a batch while a panel's restore point
                # runs, and panels refuse one during a batch - this is the
                # last line: never two checkpoints at once. Wait for it -
                # polled, since its finished signal may already have fired
                # between the check and a connect.
                self._queue.insert(0, action_id)
                QTimer.singleShot(500, self._run_next)
                return
            self._restore_point_attempted = True
            self._write_undo_script()
            rp_runner = restore_point.RestorePointRunner(f"PortableFix {self.run_id}", parent=self)
            rp_runner.result_ready.connect(
                lambda success, detail, info, m=module, a=action: self._on_restore_point_checked(success, detail, m, a, info)
            )
            self._pending_restore_point_runner = rp_runner
            if not self._closed:
                # Checkpoint-Computer can take minutes and prints nothing:
                # say so, and show a busy bar instead of a frozen one.
                self._append_console(self._t("panel_restore_point_running"))
                self.statusBar().showMessage(self._t("panel_restore_point_running"))
                self.progress_bar.setRange(0, 0)
            rp_runner.start()
            return

        self._proceed_to_action(module, action)

    def _proceed_to_action(self, module: ModuleDef, action: ActionDef) -> None:
        # After the restore point: the requested hive backup (G24) is written
        # right before the batch's first DESTRUCTIVE action - not earlier, so
        # it holds the state that action is about to change.
        if (
            action.risk == RiskLevel.DESTRUCTIVE
            and self._hive_backup_requested
            and not self._hive_backup_attempted
            and not self._batch_dry_run
            and not self._closed
        ):
            self._hive_backup_attempted = True
            dest = hive_backup.backup_dir(self.state_dir, self.run_id)
            self._append_console(self._t("hive_backup_running").format(path=dest))
            runner = hive_backup.HiveBackupRunner(dest, parent=self)
            runner.result_ready.connect(
                lambda success, detail, result, m=module, a=action: self._on_hive_backup_finished(success, detail, result, m, a)
            )
            self._pending_hive_backup_runner = runner
            runner.start()
            return
        self._dispatch_action(module, action)

    def _on_hive_backup_finished(self, success: bool, detail: str, result, module: ModuleDef, action: ActionDef) -> None:
        dest = getattr(result, "dest_dir", None)
        subject = f"{module.module_id}/{action.id}"
        if success:
            output = f"Registry hive backup saved: {dest} ({', '.join(h + hive_backup.HIVE_FILE_SUFFIX for h in hive_backup.HIVES)})."
        else:
            output = f"Registry hive backup failed: {detail}" if detail else "Registry hive backup failed."
        self._log_system_event(
            "hive_backup", 0 if success else 1, output,
            command=hive_backup.command_text(dest) if dest is not None else "", subject=subject,
        )
        if success and dest is not None:
            self._hive_backups.append(Path(dest))
            self._write_undo_script()
        if self._cancel_requested:
            # Cancel (or a close) came while reg save ran - like the restore
            # point, the action it was guarding must never run.
            self._run_next()
            return
        if not self._closed:
            self._append_console(
                self._t("hive_backup_done").format(path=dest) if success else self._t("hive_backup_failed_console")
            )
        if not success:
            proceed = QMessageBox.warning(
                self, self._t("app_title"), self._t("hive_backup_failed_confirm"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            self._log_system_event(
                "hive_backup_decision", 0,
                "Technician chose to continue without the registry hive backup." if proceed == QMessageBox.Yes
                else "Technician declined to continue without the registry hive backup - DESTRUCTIVE actions skipped.",
                warned=True, warning_text=self._t("hive_backup_failed_confirm"),
                subject=subject, decision="proceed" if proceed == QMessageBox.Yes else "skip",
            )
            if proceed != QMessageBox.Yes:
                # The backup guarded the DESTRUCTIVE actions only - the rest
                # of the batch keeps its restore point and runs.
                self._queue = [
                    aid for aid in self._queue if self._find_action(aid)[1].risk != RiskLevel.DESTRUCTIVE
                ]
                self._run_next()
                return
        self._dispatch_action(module, action)

    def _on_restore_point_checked(
        self, success: bool, detail: str, module: ModuleDef, action: ActionDef, info: dict | None = None,
    ) -> None:
        subject = f"{module.module_id}/{action.id}"
        self._log_restore_point_result(success, detail, info, subject)
        if not self._closed:
            self.progress_bar.setRange(0, self._queue_total)
            self.progress_bar.setValue(self._queue_total - len(self._queue) - 1)
        if self._cancel_requested:
            # Cancel was clicked while the restore point was still being
            # created - the action it was guarding must never run, and
            # _on_cancel_clicked already emptied the queue.
            self._run_next()
            return
        if not success:
            proceed = QMessageBox.warning(
                self,
                self._t("app_title"),
                self._t("restore_point_failed_confirm"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            # "Continue without a safety net" is exactly what a later dispute
            # is about - record the answer explicitly (research-reporting.md F3).
            self._log_system_event(
                "restore_point_decision", 0,
                "Technician chose to continue without a restore point." if proceed == QMessageBox.Yes
                else "Technician declined to continue without a restore point - high-risk actions skipped.",
                warned=True, warning_text=self._t("restore_point_failed_confirm"),
                subject=subject, decision="proceed" if proceed == QMessageBox.Yes else "skip",
            )
            if proceed != QMessageBox.Yes:
                self._skip_high_risk_actions_in_queue()
                self._run_next()
                return
        self._proceed_to_action(module, action)

    def _log_restore_point_result(
        self, success: bool, detail: str, info: dict | None, subject: str, subjects: list[str] | None = None,
    ) -> None:
        # One record for every restore point, the batch's and a panel's (G01).
        # info: the created point's identity (restore_point.parse_restore_point_output),
        # {} / None when it could not be looked up.
        sequence = (info or {}).get("sequence_number") if success else None
        output = "System Restore Point created." if success else (
            f"System Restore Point creation failed: {detail}" if detail else "System Restore Point creation failed."
        )
        if sequence is not None:
            output = f"System Restore Point created (#{sequence})."
        self._log_system_event(
            "restore_point", 0 if success else 1, output,
            command=f"Checkpoint-Computer -Description 'PortableFix {self.run_id}'",
            subject=subject, restore_point_sequence=sequence,
            restore_point_created=(info or {}).get("creation_time", "") if success else "",
            subjects=subjects,
        )

    def _log_system_event(self, action_id: str, exit_code: int | None, output: str, **fields) -> None:
        # Safety facts about the run (restore point, the technician's answers
        # to safety prompts) go in the same audit log as the actions, under
        # the "_system" module report.py lists in its safety section.
        entry = make_entry(
            "_system", action_id, fields.pop("command", ""), exit_code, output,
            self.settings.dry_run, self.run_id, elevated=self.is_admin,
            **{**self.target_user.audit_fields(), **fields},
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self._report_write_failure()

    def _save_resume(self, restart_action_id: str) -> bool:
        """Saves what is still queued to the resume file (G03). False when
        it could not be written - the technician is told the rest must be
        started by hand."""
        pending = batch_resume.PendingBatch(
            run_id=self.run_id,
            action_ids=list(self._queue),
            dry_run=self._batch_dry_run,
            restart_after=restart_action_id,
            job=self._job_info(),
            undo_steps=list(self._undo_steps),
            irreversible=list(self._irreversible_actions),
            hive_backups=batch_resume.saved_hive_paths(self.state_dir, self._hive_backups),
            items={aid: list(self._selected_items[aid]) for aid in self._queue if self._selected_items.get(aid)},
            snapshot_before=self._snapshot_before,
        )
        try:
            batch_resume.save_pending(self.state_dir, pending)
        except OSError:
            if not self._closed:
                self._append_console(self._t("resume_save_failed"))
            return False
        self._resume_saved_for = restart_action_id
        return True

    def _log_restart_pending(self, module: ModuleDef, action: ActionDef, saved: bool, immediate: bool) -> None:
        waiting = ", ".join(self._queue)
        if immediate:
            output = f"{action.id} restarts Windows immediately - report and undo.ps1 written before it runs."
        else:
            output = f"{action.id} succeeded and needs a restart before the rest of the batch - batch stopped."
        if self._queue:
            output += (
                f" Saved to continue after the restart: {waiting}." if saved
                else f" Could not save the rest of the batch ({waiting}) - start it again by hand after the restart."
            )
        self._log_system_event(
            "restart_pending", 0, output, risk=action.risk.value, subject=f"{module.module_id}/{action.id}",
        )

    def _prepare_for_restart(self, module: ModuleDef, action: ActionDef) -> None:
        """Before an action that restarts Windows at once (G03): what is still
        queued goes to the resume file, then undo.ps1, the audit record and
        the report are written - the action runs only once they exist."""
        self._pre_restart_prepared.add(action.id)
        saved = self._save_resume(action.id) if self._queue else False
        self._log_restart_pending(module, action, saved, immediate=True)
        # The PC may not come back to this process: the time so far goes in
        # the report written now.
        self._log_batch_duration()
        self._write_undo_script()
        self._append_console(self._t("restart_writing_report").format(action=action.label(self.settings.language)))
        # Everything up to this point, snapshot included - the PC may not
        # come back to this process.
        self._snapshot_after = self._take_snapshot()
        runner = report.ReportRunner(
            self.state_dir, self.run_id, self.modules, self.settings.language,
            self._snapshot_before, self._snapshot_after,
            job=self._job_info(), storage_fallback=self._storage_fallback,
            redact=self.settings.redact_for_client, branding=self.settings.branding_info(), parent=self,
        )
        runner.result_ready.connect(
            lambda html_path, write_failed, m=module, a=action: self._on_pre_restart_report_ready(html_path, write_failed, m, a)
        )
        self._report_runner = runner
        runner.start()

    def _on_pre_restart_report_ready(self, html_path, write_failed: bool, module: ModuleDef, action: ActionDef) -> None:
        self._report_runner = None
        if write_failed and not self._closed:
            self._report_write_failure()
        if self._cancel_requested or self._closed:
            # Cancelled while the report was written: the restart never
            # comes, so there is nothing to continue after it.
            if self._resume_saved_for == action.id:
                batch_resume.discard_pending(self.state_dir)
                self._resume_saved_for = ""
            self._run_next()
            return
        self._restart_report_path = html_path
        self._dispatch_action(module, action)

    def _dispatch_action(self, module: ModuleDef, action: ActionDef) -> None:
        if self._closed:
            # Never start an action (or pop its confirmation) after the
            # window is gone - it would run unlogged and outlive the app.
            return
        if action.restarts_pc and not self._batch_dry_run and action.id not in self._pre_restart_prepared:
            # A DRY-RUN only previews - nothing restarts, nothing to prepare.
            self._prepare_for_restart(module, action)
            return
        warning_text = ""
        confirmed = QMessageBox.Yes
        if action.id in self._reviewed_warnings:
            # Confirmed on the batch review screen - quote what was shown there.
            warning_text = self._reviewed_warnings[action.id]
        elif self._batch_dry_run:
            # A DRY-RUN previews and changes nothing - nothing to confirm.
            pass
        elif action.risk == RiskLevel.DESTRUCTIVE:
            warning_text = f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_destructive_action')}"
            confirmed = QMessageBox.warning(
                self,
                self._t("app_title"),
                warning_text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        elif action.risk != RiskLevel.SAFE:
            warning_text = f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_risky_action')}"
            confirmed = QMessageBox.question(
                self,
                self._t("app_title"),
                warning_text,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
        if warning_text and confirmed != QMessageBox.Yes:
            # A "No" is as much a part of the record as a "Yes" - without it
            # the log can't show the technician was warned and backed off
            # (research-reporting.md F2).
            self._log_system_event(
                "risk_declined", None, "Technician declined the risk confirmation - action not run.",
                risk=action.risk.value, warned=True, warning_text=warning_text,
                subject=f"{module.module_id}/{action.id}", decision="declined",
            )
            self._run_next()
            return

        # The app's own root IS %TEMP% itself, or %TEMP% is redirected via a
        # junction/symlink so a resolved-path comparison could never match
        # PowerShell's unresolved view of it - either way there's no single
        # safe child to exclude, so refuse to run the temp cleanup at all
        # rather than risk wiping the app out from under itself.
        action_temp_protect, refusal = action_service.temp_protection(action, paths.get_base_dir())
        if refusal:
            QMessageBox.warning(self, self._t("app_title"), self._t(refusal))
            self._run_next()
            return
        item_ids = self._selected_items.get(action.id, []) if action.items_command else None
        if action.items_command and not item_ids:
            # Research G05: nothing picked (or the list could not be read) -
            # the action has nothing to work on.
            self._run_next()
            return
        try:
            prepared = action_service.prepare_plan(
                action, dry_run=self._batch_dry_run, state_dir=self.state_dir, run_id=self.run_id,
                target_user=self.target_user, temp_protect=action_temp_protect, item_ids=item_ids,
            )
        except (OSError, items_mod.ItemsError):
            self._report_write_failure()
            self._run_next()
            return
        plan = prepared.plan
        if prepared.ops_state is not None:
            self._ops_state_paths[action.id] = prepared.ops_state

        self._set_action_status(action.id, "running", self._t("status_running"))
        self._action_start_times[action.id] = time.monotonic()
        self._action_tick_timer.start()
        runner = ActionRunner(
            plan, parent=self,
            inactivity_timeout_sec=action.inactivity_timeout_sec,
            hard_cap_sec=action.hard_cap_sec,
            # Research G09: a real run first asks whether the change is
            # already in place and skips it (on record) when it is.
            check_plan=action_service.check_plan(action, dry_run=self._batch_dry_run, target_user=self.target_user),
        )
        self._runner = runner
        runner.output_line.connect(self._append_console)
        runner.finished_with_code.connect(
            lambda code, m=module.module_id, a=action.id, c=action.command, r=runner, w=warning_text: self._on_action_finished(
                m, a, c, code, r, w
            )
        )
        runner.start()

    def _on_preview_clicked(self, action_id: str) -> None:
        """One card's Preview (research-design-additions item 6): runs the
        exact plan a DRY-RUN batch runs for this action - its preview_command
        via action_service.prepare_plan - and shows what it printed."""
        if self._batch_start_blocked():
            self.statusBar().showMessage(self._t("preview_busy"))
            return
        _, action = self._find_action(action_id)
        temp_protect, refusal = action_service.temp_protection(action, paths.get_base_dir())
        if refusal:
            QMessageBox.warning(self, self._t("app_title"), self._t(refusal))
            return
        try:
            prepared = action_service.prepare_plan(
                action, dry_run=True, state_dir=self.state_dir, run_id=self.run_id,
                target_user=self.target_user, temp_protect=temp_protect,
            )
        except (OSError, items_mod.ItemsError):
            self._report_write_failure()
            return
        self._append_console(self._t("preview_console_start").format(action=action.label(self.settings.language)))
        runner = ActionRunner(
            prepared.plan, parent=self,
            inactivity_timeout_sec=action.inactivity_timeout_sec, hard_cap_sec=action.hard_cap_sec,
        )
        self._preview_runner = runner
        self._set_preview_busy(True)
        runner.output_line.connect(self._append_console)
        runner.finished_with_code.connect(
            lambda code, a=action_id, r=runner: self._on_preview_finished(a, code, r)
        )
        runner.start()

    def _set_preview_busy(self, busy: bool) -> None:
        for button in self._preview_buttons.values():
            try:
                button.setEnabled(not busy)
            except RuntimeError:
                pass  # a card from before a language toggle
        self.run_button.setEnabled(not self._batch_start_blocked())
        self.dashboard_analyze_button.setEnabled(not self._batch_start_blocked())

    def _on_preview_finished(self, action_id: str, exit_code: int, runner: ActionRunner) -> None:
        if self._preview_runner is runner:
            self._preview_runner = None
        if self._closed:
            return
        self._set_preview_busy(False)
        _, action = self._find_action(action_id)
        lines = [line for line in runner.captured_output if line.strip()]
        text = "\n".join(lines[:PREVIEW_MAX_LINES]) or self._t("preview_no_output")
        if len(lines) > PREVIEW_MAX_LINES:
            text += "\n" + self._t("uninstaller_and_more").format(count=len(lines) - PREVIEW_MAX_LINES)
        if self._preview_box is not None:
            self._preview_box.close()
        box = QMessageBox(
            QMessageBox.Icon.Information if exit_code == 0 else QMessageBox.Icon.Warning,
            self._t("preview_title").format(action=action.label(self.settings.language)),
            text, QMessageBox.StandardButton.Ok, self,
        )
        # Non-modal: the technician can read it next to the cards.
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.show()
        self._preview_box = box

    def _on_action_finished(
        self, module_id: str, action_id: str, command: str, exit_code: int, runner: ActionRunner,
        warning_text: str = "",
    ) -> None:
        _, action = self._find_action(action_id)
        payloads = list(getattr(runner, "pfjson", None) or [])
        item_ids = self._selected_items.get(action_id, []) if action.items_command else []
        skipped = bool(getattr(runner, "skipped_applied", False))
        # warned/warning_text come from the dialog _dispatch_action actually
        # showed and the technician accepted, not re-derived from the risk.
        entry = action_service.audit_entry(
            module_id, action, exit_code, list(runner.captured_output), dry_run=self._batch_dry_run,
            run_id=self.run_id, elevated=self.is_admin, warning_text=warning_text, target_user=self.target_user,
            payloads=payloads, item_ids=item_ids, decision=action_service.ALREADY_APPLIED if skipped else "",
        )
        if action.check_command and not self._batch_dry_run:
            # Research G09: after a success the runner checked again, so this
            # is the state now (kept for the snapshot's drift); after a
            # failure the earlier answer may be stale - the next check looks.
            state = getattr(runner, "check_state", None)
            if state and exit_code == 0:
                self._check_results[action_id] = state
            else:
                self._check_results.pop(action_id, None)
            if not self._closed:
                self._set_check_chip(action_id, self._check_results.get(action_id))
        entry.command = command
        elapsed = time.monotonic() - self._action_start_times.pop(action_id, time.monotonic())
        entry.duration_sec = round(elapsed, 2)
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self._report_write_failure()
        self._batch_results.append((action_id, exit_code))
        if exit_code != 0:
            self._failure_hints[action_id] = _failure_hint(exit_code, list(runner.captured_output))
        for finding in entry.findings:
            # Newest last, so health.latest_findings-style order holds.
            self._findings.pop(finding["id"], None)
            self._findings[finding["id"]] = finding
        if not self._action_start_times:
            self._action_tick_timer.stop()
        self._action_durations[action_id] = elapsed
        status_text = f"{self._t('status_ok') if exit_code == 0 else self._t('status_failed')} ({_elapsed_text(elapsed)})"
        if skipped:
            status_text = self._t("status_already_applied")
        self._set_action_status(action_id, "ok" if exit_code == 0 else "fail", status_text)
        if not self._batch_dry_run:
            if not skipped:
                # Shared with the headless CLI (research G21): undo_command,
                # ops state (G10) and per-item undo (G05) decided in one place.
                outcome = action_service.undo_outcome(
                    action, exit_code, language=self.settings.language, target_user=self.target_user,
                    ops_state=self._ops_state_paths.pop(action.id, None), item_ids=item_ids, payloads=payloads,
                )
                if outcome.steps or outcome.irreversible:
                    self._undo_steps.extend(outcome.steps)
                    if outcome.irreversible:
                        self._irreversible_actions.append(outcome.irreversible)
                    self._write_undo_script()
            # A skipped action changed nothing, so no restart comes after it -
            # for the restart bookkeeping it is a run that did not succeed.
            self._after_restart_action(action, 1 if skipped else exit_code)
        self._run_next()

    def _after_restart_action(self, action: ActionDef, exit_code: int) -> None:
        """Research G03, after a real run of an action: stop the batch where
        a restart has to come first; the rest waits in the resume file."""
        if action.restarts_pc:
            if exit_code == 0:
                # Windows is restarting right now - never start the next
                # action; what was left is already in the resume file.
                self._restarting = True
                self._queue = []
            elif self._resume_saved_for == action.id:
                # It failed, so no restart comes - carry on with the rest here.
                batch_resume.discard_pending(self.state_dir)
                self._resume_saved_for = ""
            return
        if action.restart_before_next and exit_code == 0 and self._queue and not self._cancel_requested:
            module, _ = self._find_action(action.id)
            saved = self._save_resume(action.id)
            self._log_restart_pending(module, action, saved, immediate=False)
            if saved:
                self._restart_needed_after = action.id
            self._queue = []

    def _write_undo_script(self) -> None:
        # LIFO order puts the newest step at the top, so each change is a
        # rewrite - but only an actual change: every later batch's
        # pre-restore-point write used to rewrite an identical file. The
        # exists() check keeps a deleted Backups/ from staying missing.
        state = (len(self._undo_steps), len(self._irreversible_actions), len(self._hive_backups))
        if (
            state == self._undo_written_state
            and self._undo_script_path is not None
            and self._undo_script_path.exists()
        ):
            return
        try:
            self._undo_script_path = undo.create_undo_script(
                self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)),
                irreversible=self._irreversible_actions, hive_backups=self._hive_backups,
            )
            self._undo_written_state = state
        except OSError:
            if not self._closed:
                self._report_write_failure()
        # A batch saved for after a restart under this run_id rewrites
        # undo.ps1 from its own copy of these lists - keep that copy in step
        # with every change made since, or the continued batch drops them.
        try:
            batch_resume.sync_undo(
                self.state_dir, self.run_id, undo_steps=self._undo_steps, irreversible=self._irreversible_actions,
                hive_backups=batch_resume.saved_hive_paths(self.state_dir, self._hive_backups),
            )
        except OSError:
            if not self._closed:
                self._report_write_failure()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        # hasattr: a state change can arrive before __init__ set up polling.
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "_vpn_timer"):
            self._window_minimized = self.isMinimized()
            self._apply_polling_state()
