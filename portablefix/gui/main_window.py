import os
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence, QShortcut
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
    QPushButton,
    QScrollArea,
    QSplitter,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from . import style
from .. import diagnostics, elevation, handoff, history, i18n, paths, report, restore_point, snapshot, sysinfo, undo, uninstaller, update_swap, updater, winget_updates
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..module_engine import load_all_modules
from ..settings import (
    MAX_CUSTOM_PRESETS,
    MAX_PRESET_NAME_LENGTH,
    MAX_TECHNICIAN_NAME_LENGTH,
    Settings,
    save_settings,
)
from ..version import APP_VERSION

# Keys of user-saved presets in _preset_buttons, kept apart from the
# built-in PRESETS keys so a user can name a preset "quick_clean" safely.
CUSTOM_PRESET_PREFIX = "custom:"
CONSOLE_MAX_LINES = 20000
HISTORY_MAX_ROWS = 5

PRESETS: dict[str, list[str]] = {
    "quick_clean": [
        "user_temp", "system_temp", "recycle_bin", "prefetch", "wer_reports",
        "thumbnail_cache", "directx_shader_cache", "browser_cache_sweep",
    ],
    "full_diagnostic": [
        "os_info", "computer_info", "bios_info", "cpu_info", "memory_info",
        "volumes", "physical_disks", "recent_hotfixes", "pending_reboot",
        "eventlog_critical_7d", "bsod_summary", "disk_reliability_counters",
        "defender_status", "top_cpu_processes", "sec_defender_status",
        "sec_firewall_status", "sec_uac_status",
    ],
    "privacy_debloat": [
        "debloat_disable_telemetry", "debloat_disable_suggestions",
        "debloat_disable_web_search", "debloat_disable_copilot",
        "debloat_disable_widgets", "debloat_disable_advertising_id",
        "debloat_disable_diagtrack", "debloat_disable_ceip_tasks",
    ],
}


try:
    # Qt 6.8+. requirements.txt still pins 6.7.2, where announcements are a
    # silent no-op - the status bar text stays readable either way.
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


class _DashboardTile(QFrame):
    """Dashboard category tile. Was a QFrame with a patched mousePressEvent,
    unreachable by Tab and silent for screen readers
    (research-accessibility.md "keyboard-only operability")."""

    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Picked up by the "tile" focus rule in style.py.
        self.setProperty("tile", "true")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit()
            return
        super().keyPressEvent(event)


# Banner text for each step of an in-app update (see _begin_update_step).
_UPDATE_PHASE_KEYS = {
    "download": "update_downloading",
    "stage": "update_preparing",
    "launch": "update_starting",
}


def _thread_running(runner) -> bool:
    # A finished QThread may already be deleteLater'd - its wrapper then
    # raises RuntimeError, which just means "not running".
    try:
        return bool(runner.isRunning())
    except (RuntimeError, AttributeError):
        return False


def _score_state(score: int) -> str:
    """Color bucket for the dashboard score (see dashboardScoreValue in style.py)."""
    if score >= 80:
        return "good"
    if score >= 60:
        return "warn"
    return "bad"


class MainWindow(QMainWindow):
    def __init__(
        self,
        assets_dir: Path,
        state_dir: Path,
        settings: Settings,
        is_admin: bool,
        run_id: str,
        parent=None,
    ):
        super().__init__(parent)
        self.assets_dir = assets_dir
        self.state_dir = state_dir
        self.settings = settings
        self.is_admin = is_admin
        self.run_id = run_id
        # main.py hands over the raw USB dir as assets_dir and the writable
        # dir as state_dir - they only differ when resolve_writable_base_dir
        # fell back to %TEMP% on the client machine, which the report must
        # then say (research-reporting.md F4).
        self._storage_fallback = Path(state_dir) != Path(assets_dir)
        self.modules, module_load_errors = load_all_modules(assets_dir / "Modules")
        if module_load_errors:
            QMessageBox.warning(
                self,
                self._t("app_title"),
                self._t("module_load_warning") + "\n" + "\n".join(module_load_errors),
            )
        elif not self.modules:
            QMessageBox.warning(self, self._t("app_title"), self._t("no_modules_warning"))
        self._action_checkboxes: dict[str, QCheckBox] = {}
        self._action_rows: dict[str, QWidget] = {}
        self._action_status_labels: dict[str, QLabel] = {}
        self._action_detail_toggles: dict[str, QToolButton] = {}
        self._action_detail_panels: dict[str, QWidget] = {}
        self._action_start_times: dict[str, float] = {}
        self._queue_total = 0
        self._queue: list[str] = []
        self._runner: ActionRunner | None = None
        self._restore_point_attempted = False
        self._pending_restore_point_runner: restore_point.RestorePointRunner | None = None
        self._report_runner: report.ReportRunner | None = None
        self._batch_active = False
        self._snapshot_before: dict = {}
        self._snapshot_after: dict = {}
        self._undo_steps: list[str] = []
        # Non-SAFE changes that ran for real but have no undo_command -
        # listed in undo.ps1 so it never implies everything was reversible.
        self._irreversible_actions: list[str] = []
        self._batch_results: list[tuple[str, int]] = []
        self._recommended_action_ids: set[str] = set()
        self._summary_dialog: QDialog | None = None
        self._closed = False
        # Per-run job details for the report header. Kept on self (not in a
        # widget) so a language toggle's full UI rebuild doesn't lose them;
        # the technician name lives in settings since it rarely changes.
        self._job_client = ""
        self._job_note = ""
        self._tray_icon: QSystemTrayIcon | None = None
        self._cancel_requested = False
        self._close_after_restore_point = False
        self._pending_update_info = None
        self._update_check_runner = None
        self._update_download_runner = None
        self._update_download_dir: Path | None = None
        self._update_stage_runner = None
        self._update_launch_runner = None
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
        self._undo_script_path: Path | None = None
        # (len(_undo_steps), len(_irreversible_actions)) last written to
        # undo.ps1 - both lists only ever grow, so the lengths identify it.
        self._undo_written_state: tuple[int, int] | None = None
        self._build_ui()
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
                self.statusBar().showMessage(self._t("closing_waiting_restore_point"))
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
        # Ask anything still actively running to stop before we wait on it -
        # otherwise the wait below just burns its whole timeout doing nothing.
        self._queue = []
        if self._runner is not None:
            self._runner.cancel()
        if self._winget_update_runner is not None:
            self._winget_update_runner.request_stop()
        if self._uninstall_runner is not None:
            try:
                self._uninstall_runner.requestInterruption()
            except RuntimeError:
                pass
        update_runners = [
            runner for runner in (self._update_download_runner, self._update_stage_runner, self._update_launch_runner)
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
            self._static_info_runner,
            self._hw_sensor_runner,
            self._ping_runner,
            self._vpn_runner,
            self._runner,
            self._update_check_runner,
        )
        slow_runners = (
            (self._speed_test_runner, 25_000),
            # Checkpoint-Computer can legitimately run for minutes (VSS on a
            # slow disk); a 5s wait let closeEvent destroy the still-running
            # QThread, aborting the process before create_restore_point could
            # put the 24h throttle registry value back.
            (self._pending_restore_point_runner, restore_point.RESTORE_POINT_TIMEOUT_SEC * 1000 + 5_000),
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
            # Stops between programs once interrupted, but the uninstaller
            # already running cannot be cut short.
            (self._uninstall_runner, uninstaller.UNINSTALL_TIMEOUT_SEC * 1000 + 10_000),
        )
        for runner in quick_runners:
            if runner is None:
                continue
            try:
                runner.wait(5000)
            except RuntimeError:
                pass
        for runner, timeout_ms in slow_runners:
            if runner is None:
                continue
            try:
                runner.wait(timeout_ms)
            except RuntimeError:
                pass
        # No cap: all three stop within a chunk or a poll once interrupted,
        # and a capped wait that ran out would destroy a live QThread (the
        # per-read socket timeout alone can exceed any sensible cap).
        for runner in update_runners:
            try:
                runner.wait()
            except RuntimeError:
                pass
        self._discard_update_download()
        super().closeEvent(event)

    def _t(self, key: str) -> str:
        return i18n.translate(key, self.settings.language)

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{self._t('app_title')} v{APP_VERSION}")
        self.setStyleSheet(style.stylesheet())
        self.resize(1200, 760)
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        icon_path = self.assets_dir / "portablefix.ico"
        if icon_path.exists():
            logo_label = QLabel()
            logo_label.setPixmap(QIcon(str(icon_path)).pixmap(28, 28))
            top_bar.addWidget(logo_label)
        title_label = QLabel(self._t("app_title"))
        title_label.setObjectName("appTitle")
        top_bar.addWidget(title_label)
        admin_text = "admin" if self.is_admin else self._t("readonly_banner")
        self.admin_label = QLabel(admin_text)
        self.admin_label.setObjectName("adminPill")
        self.admin_label.setProperty("admin", "true" if self.is_admin else "false")
        top_bar.addWidget(self.admin_label)
        self.restart_admin_button = QPushButton(self._t("restart_as_admin"))
        self.restart_admin_button.setVisible(not self.is_admin)
        self.restart_admin_button.clicked.connect(self._on_restart_as_admin)
        top_bar.addWidget(self.restart_admin_button)
        top_bar.addStretch(1)
        self.job_button = self._make_selection_button(self._t("job_button"), self._open_job_dialog)
        self.job_button.setObjectName("jobBtn")
        self.job_button.setToolTip(self._t("job_tooltip"))
        top_bar.addWidget(self.job_button)
        self._refresh_job_button()
        self.dry_run_checkbox = QCheckBox(self._t("dry_run_toggle"))
        self.dry_run_checkbox.setChecked(self.settings.dry_run)
        self.dry_run_checkbox.toggled.connect(self._on_dry_run_toggled)
        top_bar.addWidget(self.dry_run_checkbox)
        self.language_button = QPushButton(self.settings.language.upper())
        self.language_button.clicked.connect(self._on_toggle_language)
        top_bar.addWidget(self.language_button)
        root_layout.addLayout(top_bar)

        self.update_banner = QWidget()
        self.update_banner.setObjectName("updateBanner")
        update_banner_layout = QHBoxLayout(self.update_banner)
        update_banner_layout.setContentsMargins(10, 6, 10, 6)
        self.update_banner_label = QLabel("")
        update_banner_layout.addWidget(self.update_banner_label, 1)
        self.update_button = QPushButton(self._t("update_button"))
        self.update_button.setObjectName("runButton")
        self.update_button.clicked.connect(lambda _checked=False: self._on_update_button_clicked())
        update_banner_layout.addWidget(self.update_button)
        self.update_dismiss_button = QPushButton(self._t("update_dismiss"))
        self.update_dismiss_button.setObjectName("selectionBtn")
        self.update_dismiss_button.clicked.connect(lambda _checked=False: self._on_update_dismiss_clicked())
        update_banner_layout.addWidget(self.update_dismiss_button)
        self.update_banner.setVisible(False)
        root_layout.addWidget(self.update_banner)

        category_i18n_keys = {
            ModuleCategory.DIAGNOSTICS: "category_diagnostics",
            ModuleCategory.CLEANUP: "category_cleanup",
            ModuleCategory.REPAIR: "category_repair",
            ModuleCategory.SECURITY: "category_security",
            ModuleCategory.ANTIVIRUS: "category_antivirus",
            ModuleCategory.DRIVER_UPDATES: "category_driver_updates",
            ModuleCategory.WINGET: "category_winget",
            ModuleCategory.UNINSTALLER: "category_uninstaller",
            ModuleCategory.DASHBOARD: "category_dashboard",
        }
        self._categories_order: list[ModuleCategory] = []
        for module in self.modules:
            if module.category not in self._categories_order:
                self._categories_order.append(module.category)
        # Uninstaller has no YAML-declared actions of its own (installed
        # programs are dynamic, discovered at dialog-open time, not a fixed
        # catalog) - always show its sidebar entry regardless of modules.
        if ModuleCategory.UNINSTALLER not in self._categories_order:
            self._categories_order.append(ModuleCategory.UNINSTALLER)
        # Dashboard is a pure navigation/overview screen, not module-backed -
        # always shown first so it's the default landing view.
        self._categories_order.insert(0, ModuleCategory.DASHBOARD)

        risk_tab_order = [RiskLevel.SAFE, RiskLevel.MODERATE, RiskLevel.DESTRUCTIVE, RiskLevel.REQUIRES_REBOOT]
        self._risk_action_ids: dict[RiskLevel, list[str]] = {r: [] for r in risk_tab_order}
        for module in self.modules:
            for action in module.actions:
                self._risk_action_ids[action.risk].append(action.id)
        self._risk_tabs_order = [r for r in risk_tab_order if self._risk_action_ids[r]]

        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)
        self.category_list = QListWidget()
        self.category_list.setObjectName("categoryList")
        category_labels = [self._t(category_i18n_keys[category]) for category in self._categories_order]
        category_labels += [f"{self._t('risk_tab_prefix')} {risk.value}" for risk in self._risk_tabs_order]
        for label in category_labels:
            self.category_list.addItem(QListWidgetItem(label))
        # Fixed 190px clipped longer entries (e.g. "Risk: REQUIRES_REBOOT")
        # behind a horizontal scrollbar - size to the longest actual label
        # instead so everything is readable without scrolling sideways.
        # +64 covers the stylesheet's item padding (12px each side) and
        # margin (4px each side), list padding (6px each side) and border.
        # Measured in bold: the selected item is rendered bold (see style.py),
        # and measuring the regular weight elided the longest labels
        # ("Odinstalovanie programov", "Riziko: REQUIRES_REBOOT") with "..."
        # as soon as they were selected.
        self.category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bold_font = QFont(self.category_list.font())
        bold_font.setBold(True)
        metrics = QFontMetrics(bold_font)
        widest_label = max((metrics.horizontalAdvance(label) for label in category_labels), default=0)
        self.category_list.setFixedWidth(min(max(widest_label + 64, 190), 300))
        body_layout.addWidget(self.category_list)

        center_layout = QVBoxLayout()
        center_layout.setSpacing(8)

        global_select_row = QHBoxLayout()
        global_select_row.setSpacing(6)
        scope_label = QLabel(self._t("all_categories"))
        scope_label.setObjectName("selectionScope")
        global_select_row.addWidget(scope_label)
        self.global_select_all_button = self._make_selection_button(
            self._t("select_all"), lambda: self._apply_selection(list(self._action_checkboxes), "all")
        )
        global_select_row.addWidget(self.global_select_all_button)
        self.global_select_safe_button = self._make_selection_button(
            self._t("select_safe_only"),
            lambda: self._apply_selection(list(self._action_checkboxes), RiskLevel.SAFE.value),
        )
        global_select_row.addWidget(self.global_select_safe_button)
        self.global_select_moderate_button = self._make_selection_button(
            self._t("select_moderate_only"),
            lambda: self._apply_selection(list(self._action_checkboxes), RiskLevel.MODERATE.value),
        )
        global_select_row.addWidget(self.global_select_moderate_button)
        self.global_select_destructive_button = self._make_selection_button(
            self._t("select_destructive_only"),
            lambda: self._apply_selection(list(self._action_checkboxes), RiskLevel.DESTRUCTIVE.value),
        )
        global_select_row.addWidget(self.global_select_destructive_button)
        self.global_select_reboot_button = self._make_selection_button(
            self._t("select_reboot_only"),
            lambda: self._apply_selection(list(self._action_checkboxes), RiskLevel.REQUIRES_REBOOT.value),
        )
        global_select_row.addWidget(self.global_select_reboot_button)
        self.global_select_none_button = self._make_selection_button(
            self._t("select_none"), lambda: self._apply_selection(list(self._action_checkboxes), "none")
        )
        self.global_select_none_button.setProperty("danger", True)
        self.global_select_none_button.setEnabled(False)
        global_select_row.addWidget(self.global_select_none_button)
        global_select_row.addStretch(1)
        center_layout.addLayout(global_select_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_label = QLabel(self._t("presets_label"))
        preset_label.setObjectName("selectionScope")
        preset_row.addWidget(preset_label)
        self._preset_buttons: dict[str, QPushButton] = {}
        self._preset_button_group = QButtonGroup(self)
        self._preset_button_group.setExclusive(True)
        preset_row.addWidget(self._make_preset_button(self._t("preset_quick_clean"), "quick_clean"))
        preset_row.addWidget(self._make_preset_button(self._t("preset_full_diagnostic"), "full_diagnostic"))
        preset_row.addWidget(self._make_preset_button(self._t("preset_privacy_debloat"), "privacy_debloat"))
        preset_row.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText(self._t("search_placeholder"))
        self.search_box.setMaximumWidth(220)
        self.search_box.textChanged.connect(self._on_search_changed)
        # Esc clears the search - handled on the box itself (eventFilter)
        # rather than as a window-wide shortcut, which would also swallow
        # Esc from dialogs and the console.
        self.search_box.installEventFilter(self)
        preset_row.addWidget(self.search_box)
        center_layout.addLayout(preset_row)

        # User-saved presets get their own row: sharing the built-in preset
        # row squeezed the search box down to nothing once a couple existed.
        # The inner sub-layout lets saving/deleting rebuild only these
        # buttons, not the whole window.
        custom_preset_row = QHBoxLayout()
        custom_preset_row.setSpacing(6)
        custom_preset_label = QLabel(self._t("custom_presets_label"))
        custom_preset_label.setObjectName("selectionScope")
        custom_preset_row.addWidget(custom_preset_label)
        self._custom_preset_layout = QHBoxLayout()
        self._custom_preset_layout.setSpacing(6)
        self._custom_preset_layout.setContentsMargins(0, 0, 0, 0)
        custom_preset_row.addLayout(self._custom_preset_layout)
        self.save_preset_button = self._make_selection_button(self._t("preset_save_button"), self._on_save_preset_clicked)
        self.save_preset_button.setEnabled(False)
        custom_preset_row.addWidget(self.save_preset_button)
        custom_preset_row.addStretch(1)
        self._rebuild_custom_preset_buttons()
        center_layout.addLayout(custom_preset_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 6, 0)
        scroll_layout.setSpacing(8)

        self._category_groups: dict[ModuleCategory, QWidget] = {}
        self._category_action_ids: dict[ModuleCategory, list[str]] = {}
        self._category_select_buttons: dict[ModuleCategory, tuple[QPushButton, QPushButton, QPushButton]] = {}
        self._category_module_action_counts: dict[ModuleCategory, int] = {}
        for module in self.modules:
            self._category_module_action_counts[module.category] = (
                self._category_module_action_counts.get(module.category, 0) + len(module.actions)
            )
        self._dashboard_tile_count_labels: dict[ModuleCategory, QLabel] = {}
        self._dashboard_tiles: dict[ModuleCategory, _DashboardTile] = {}
        self._category_i18n_keys = category_i18n_keys
        self._dashboard_score_label: QLabel | None = None
        for category in self._categories_order:
            if category == ModuleCategory.DASHBOARD:
                self._category_action_ids[category] = []
                card = self._build_dashboard_card(category_i18n_keys)
                self._category_groups[category] = card
                scroll_layout.addWidget(card)
                continue
            if category == ModuleCategory.UNINSTALLER:
                self._category_action_ids[category] = []
                card = self._build_uninstaller_card()
                self._category_groups[category] = card
                scroll_layout.addWidget(card)
                continue
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            def _make_select_buttons_row(c=category) -> QHBoxLayout:
                row = QHBoxLayout()
                row.setSpacing(6)
                row.addStretch(1)
                all_btn = self._make_selection_button(
                    self._t("select_all"), lambda: self._apply_selection(self._category_action_ids[c], "all")
                )
                row.addWidget(all_btn)
                safe_btn = self._make_selection_button(
                    self._t("select_safe_only"),
                    lambda: self._apply_selection(self._category_action_ids[c], RiskLevel.SAFE.value),
                )
                row.addWidget(safe_btn)
                none_btn = self._make_selection_button(
                    self._t("select_none"), lambda: self._apply_selection(self._category_action_ids[c], "none")
                )
                row.addWidget(none_btn)
                self._category_select_buttons[c] = (all_btn, safe_btn, none_btn)
                return row

            heading_row = QHBoxLayout()
            heading_row.setSpacing(6)
            heading = QLabel(self._t(category_i18n_keys[category]))
            heading.setObjectName("cardHeading")
            heading_row.addWidget(heading)
            if category != ModuleCategory.WINGET:
                heading_row.addLayout(_make_select_buttons_row())
            else:
                heading_row.addStretch(1)
            card_layout.addLayout(heading_row)
            if category == ModuleCategory.WINGET:
                # The live update panel now lives on the Prehlad dashboard
                # card - this page keeps only the static winget actions
                # (list installed, export, source management).
                card_layout.addLayout(_make_select_buttons_row())
            self._category_action_ids[category] = []
            for module in self.modules:
                if module.category != category:
                    continue
                for action in module.actions:
                    self._category_action_ids[category].append(action.id)
                    row_widget = QWidget()
                    row = QHBoxLayout(row_widget)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.setSpacing(8)
                    checkbox = QCheckBox(action.label(self.settings.language))
                    checkbox.setToolTip(action.description(self.settings.language))
                    checkbox.setAccessibleDescription(action.description(self.settings.language))
                    checkbox.setAccessibleName(self._action_accessible_name(action))
                    checkbox.stateChanged.connect(lambda _state=0: self._update_status_bar())
                    self._action_checkboxes[action.id] = checkbox
                    row.addWidget(checkbox)
                    badge = QLabel(action.risk.value)
                    badge.setObjectName("riskBadge")
                    badge.setProperty("risk", action.risk.value)
                    row.addWidget(badge)
                    status_label = QLabel("")
                    status_label.setObjectName("actionStatus")
                    self._action_status_labels[action.id] = status_label
                    row.addWidget(status_label)
                    row.addStretch(1)
                    detail_toggle, detail_panel = self._make_action_detail_toggle(action)
                    self._action_detail_toggles[action.id] = detail_toggle
                    self._action_detail_panels[action.id] = detail_panel
                    row.addWidget(detail_toggle)
                    row_container = self._wrap_row_with_detail_panel(row_widget, detail_panel)
                    card_layout.addWidget(row_container)
                    self._action_rows[action.id] = row_container
            self._category_groups[category] = card
            scroll_layout.addWidget(card)
        self._nav_row_order: list[QWidget] = [self._category_groups[c] for c in self._categories_order]

        self._risk_view_checkboxes: dict[str, QCheckBox] = {}
        self._risk_view_rows: dict[str, QWidget] = {}
        self._risk_view_detail_toggles: dict[str, QToolButton] = {}
        self._risk_view_detail_panels: dict[str, QWidget] = {}
        for risk in self._risk_tabs_order:
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            heading_row = QHBoxLayout()
            heading_row.setSpacing(6)
            heading = QLabel(f"{self._t('risk_tab_prefix')} {risk.value}")
            heading.setObjectName("cardHeading")
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            heading_row.addWidget(self._make_selection_button(
                self._t("select_all"), lambda r=risk: self._apply_selection(self._risk_action_ids[r], "all")
            ))
            heading_row.addWidget(self._make_selection_button(
                self._t("select_none"), lambda r=risk: self._apply_selection(self._risk_action_ids[r], "none")
            ))
            card_layout.addLayout(heading_row)
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
                category_label = QLabel(self._t(category_i18n_keys[module.category]))
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
            scroll_layout.addWidget(card)
            card.setHidden(True)
            self._nav_row_order.append(card)

        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        center_layout.addWidget(scroll, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("batchProgress")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        center_layout.addWidget(self.progress_bar)

        run_row = QHBoxLayout()
        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self.run_selected_actions)
        run_row.addWidget(self.run_button, 1)

        self.cancel_button = QPushButton(self._t("cancel_batch"))
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.clicked.connect(lambda _checked=False: self._on_cancel_clicked())
        self.cancel_button.setEnabled(False)
        run_row.addWidget(self.cancel_button)
        center_layout.addLayout(run_row)
        body_layout.addLayout(center_layout, 2)
        body_layout.addWidget(self._build_sysinfo_panel(), 1)
        body_widget = QWidget()
        body_widget.setLayout(body_layout)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        # Unbounded, the console kept every line of every batch for the whole
        # session (DISM/SFC alone emit thousands) - memory only ever grew.
        # Full per-action output is still in the audit log and the report.
        self.console.setMaximumBlockCount(CONSOLE_MAX_LINES)
        self._console_window: QDialog | None = None
        self._console_fullscreen = False
        self._console_splitter_sizes: list[int] | None = None

        console_toolbar = QHBoxLayout()
        console_toolbar.addStretch(1)
        self.console_fullscreen_button = self._make_selection_button(
            "⤢", lambda: self._on_console_fullscreen_toggled()
        )
        self.console_fullscreen_button.setToolTip(self._t("console_fullscreen_toggle"))
        console_toolbar.addWidget(self.console_fullscreen_button)
        self.console_popout_button = self._make_selection_button(
            "⧉", lambda: self._on_console_popout_clicked()
        )
        self.console_popout_button.setToolTip(self._t("console_popout"))
        console_toolbar.addWidget(self.console_popout_button)

        self._console_container_layout = QVBoxLayout()
        self._console_container_layout.setContentsMargins(0, 0, 0, 0)
        self._console_container_layout.setSpacing(4)
        self._console_container_layout.addLayout(console_toolbar)
        self._console_container_layout.addWidget(self.console)
        console_panel = QWidget()
        console_panel.setLayout(self._console_container_layout)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.addWidget(body_widget)
        self._main_splitter.addWidget(console_panel)
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(self._main_splitter, 1)

        self.category_list.currentRowChanged.connect(self._on_category_changed)
        if self._categories_order:
            self.category_list.setCurrentRow(0)
        self._update_status_bar()
        if self._batch_active:
            # A language toggle mid-batch rebuilds run_button/cancel_button/
            # progress_bar/console fresh - restore the in-flight state onto
            # the new widgets, otherwise a freshly-enabled run_button lets a
            # second click stomp on the still-running batch's queue/runner,
            # and the still-running action's output silently stops reaching
            # the (now orphaned) old console.
            self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.language_button.setEnabled(False)
            self.progress_bar.setMaximum(self._queue_total)
            self.progress_bar.setValue(self._queue_total - len(self._queue))
            self.progress_bar.setVisible(True)
            if self._runner is not None:
                self._runner.output_line.connect(self.console.appendPlainText)
        if self._pending_update_info is not None:
            if self._update_in_progress:
                self.update_banner_label.setText(self._t(_UPDATE_PHASE_KEYS.get(self._update_phase, "update_downloading")))
                self.update_button.setEnabled(False)
                self.update_dismiss_button.setEnabled(False)
                # The rebuilt bar starts hidden - the download/stage/launch
                # still running would otherwise look finished.
                done, total = self._update_progress
                self.progress_bar.setMaximum(total)
                self.progress_bar.setValue(done)
                self.progress_bar.setVisible(True)
            else:
                self.update_banner_label.setText(
                    self._t("update_available_banner").format(version=self._pending_update_info.version)
                )
            self.update_banner.setVisible(True)

    def _on_category_changed(self, row: int) -> None:
        if self.search_box.text().strip():
            # A search is active - every card stays visible (with only the
            # matching rows shown, per _on_search_changed) so results from
            # every category are reachable, not just whichever one the
            # sidebar happens to be on.
            for widget in self._nav_row_order:
                widget.setHidden(False)
            return
        for index, widget in enumerate(self._nav_row_order):
            widget.setHidden(index != row)

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
            action.command,
        )).lower()

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        if needle:
            # Search every category/risk card at once instead of just the
            # one the sidebar currently has open - a match hidden inside an
            # unopened card looked identical to "no such action".
            for widget in self._nav_row_order:
                widget.setHidden(False)
        else:
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

        # Search now shows every matching card at once (see above), so
        # there's no more "hidden in a tab you're not looking at" case -
        # just report whether anything matched at all.
        if not matched_ids:
            self.statusBar().showMessage(self._t("search_no_matches").format(query=text.strip()))
        else:
            self.statusBar().showMessage(self._t("search_matches_count").format(count=len(matched_ids)))

    def _preset_action_ids(self, preset_key: str) -> list[str]:
        if preset_key.startswith(CUSTOM_PRESET_PREFIX):
            return self.settings.custom_presets.get(preset_key[len(CUSTOM_PRESET_PREFIX):], [])
        return PRESETS.get(preset_key, [])

    def _apply_preset(self, preset_key: str) -> None:
        wanted = [aid for aid in self._preset_action_ids(preset_key) if aid in self._action_checkboxes]
        self._apply_selection(list(self._action_checkboxes), "none")
        if preset_key.startswith(CUSTOM_PRESET_PREFIX):
            # A custom preset is a snapshot of boxes the technician checked
            # by hand, opt-out ones (drv_restore_backup, ...) included - the
            # bulk "all" path would silently drop exactly those, so restore
            # every saved id as-is.
            for action_id in wanted:
                self._action_checkboxes[action_id].setChecked(True)
        else:
            self._apply_selection(wanted, "all")
        # Exclusive QButtonGroup membership already unchecks the other two
        # preset buttons on a real click; set this one explicitly too so the
        # highlight is correct even when _apply_preset is called directly
        # (e.g. from tests) for a key with no matching button.
        button = self._preset_buttons.get(preset_key)
        if button is not None:
            button.setChecked(True)
        # Jump the sidebar to the category holding the first action the
        # preset just selected - without this, applying a preset checked
        # the right boxes correctly but left whichever category the sidebar
        # already happened to be on visible, which usually wasn't the one
        # the preset actually touched.
        wanted_set = set(wanted)
        for index, category in enumerate(self._categories_order):
            if wanted_set.intersection(self._category_action_ids.get(category, [])):
                self.category_list.setCurrentRow(index)
                break

    def _update_status_bar(self) -> None:
        selected = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self.global_select_none_button.setEnabled(bool(selected))
        self.save_preset_button.setEnabled(bool(selected))
        if self._batch_active:
            return
        if not selected:
            self.statusBar().showMessage(self._t("status_bar_none_selected"))
            return
        risk_order = [RiskLevel.DESTRUCTIVE, RiskLevel.REQUIRES_REBOOT, RiskLevel.MODERATE, RiskLevel.SAFE]
        highest = RiskLevel.SAFE
        for aid in selected:
            _, action = self._find_action(aid)
            if risk_order.index(action.risk) < risk_order.index(highest):
                highest = action.risk
        self.statusBar().showMessage(
            self._t("status_bar_selected").format(count=len(selected), risk=highest.value)
        )

    def _on_console_fullscreen_toggled(self) -> None:
        if self._console_fullscreen:
            sizes = self._console_splitter_sizes or [3, 1]
            self._main_splitter.setSizes(sizes)
            self.console_fullscreen_button.setText("⤢")
        else:
            self._console_splitter_sizes = self._main_splitter.sizes()
            total = sum(self._console_splitter_sizes) or 1
            self._main_splitter.setSizes([0, total])
            self.console_fullscreen_button.setText("⤡")
        self._console_fullscreen = not self._console_fullscreen

    def _on_console_popout_clicked(self) -> None:
        if self._console_window is not None:
            self._console_window.raise_()
            self._console_window.activateWindow()
            return
        window = QDialog(self)
        window.setWindowTitle(self._t("console_popout_title"))
        window.setStyleSheet(style.stylesheet())
        window.resize(700, 400)
        window.setModal(False)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.console)
        window.finished.connect(lambda _result=0: self._reattach_console())
        self._console_window = window
        window.show()

    def _reattach_console(self) -> None:
        if self._console_window is None:
            return
        self._console_container_layout.addWidget(self.console)
        self._console_window = None

    def _make_selection_button(self, text: str, on_click) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("selectionBtn")
        # clicked(bool) would overwrite a lambda's bound default argument,
        # so swallow the checked flag before invoking the handler.
        button.clicked.connect(lambda _checked=False: on_click())
        return button

    def _make_preset_button(self, text: str, preset_key: str) -> QPushButton:
        # A distinct objectName/checkable state from selectionBtn on purpose -
        # selectionBtn is shared by many one-shot action buttons elsewhere in
        # this file, and those must stay plain (non-toggling) buttons.
        button = self._make_selection_button(text, lambda: self._apply_preset(preset_key))
        button.setObjectName("presetBtn")
        button.setCheckable(True)
        self._preset_button_group.addButton(button)
        self._preset_buttons[preset_key] = button
        return button

    def _job_info(self) -> dict:
        return {
            "technician": self.settings.technician_name,
            "client": self._job_client,
            "note": self._job_note,
        }

    def _refresh_job_button(self) -> None:
        client = self._job_client
        if client:
            shown = client if len(client) <= 24 else client[:23] + "…"
            self.job_button.setText(self._t("job_button_set").format(client=shown))
        else:
            self.job_button.setText(self._t("job_button"))
        self.job_button.setProperty("set", bool(client or self.settings.technician_name))
        self.job_button.style().unpolish(self.job_button)
        self.job_button.style().polish(self.job_button)

    def _set_job(self, technician: str, client: str, note: str) -> None:
        technician = technician.strip()[:MAX_TECHNICIAN_NAME_LENGTH]
        if technician != self.settings.technician_name:
            self.settings.technician_name = technician
            self._persist_settings()
        self._job_client = client.strip()[:120]
        self._job_note = note.strip()[:2000]
        self._refresh_job_button()

    def _open_job_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self._t("job_dialog_title"))
        dialog.setStyleSheet(style.stylesheet())
        dialog.setMinimumWidth(420)
        form = QFormLayout(dialog)
        technician_edit = QLineEdit(self.settings.technician_name)
        technician_edit.setObjectName("searchBox")
        technician_edit.setMaxLength(MAX_TECHNICIAN_NAME_LENGTH)
        client_edit = QLineEdit(self._job_client)
        client_edit.setObjectName("searchBox")
        client_edit.setMaxLength(120)
        note_edit = QPlainTextEdit(self._job_note)
        note_edit.setObjectName("actionDetailCommand")
        note_edit.setFixedHeight(90)
        form.addRow(self._t("job_technician_label"), technician_edit)
        form.addRow(self._t("job_client_label"), client_edit)
        form.addRow(self._t("job_note_label"), note_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # Qt ships no Slovak translations for standard buttons - label it ourselves.
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("dialog_cancel"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        (client_edit if self.settings.technician_name else technician_edit).setFocus()
        self._job_dialog = dialog
        dialog.accepted.connect(
            lambda: self._set_job(technician_edit.text(), client_edit.text(), note_edit.toPlainText())
        )
        dialog.open()

    def _notify_batch_finished(self) -> None:
        # Long batches (DISM, SFC, chkdsk) run for many minutes - a technician
        # usually switches to something else meanwhile. Flash the taskbar
        # entry and, where a system tray exists, show a notification with the
        # outcome. Nothing happens when the window is already in front.
        if self.isActiveWindow():
            return
        ok_count = sum(1 for _, code in self._batch_results if code == 0)
        failed = len(self._batch_results) - ok_count
        QApplication.alert(self, 0)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._tray_icon is None:
            self._tray_icon = QSystemTrayIcon(self.windowIcon(), self)
            self._tray_icon.activated.connect(lambda _reason: (self.showNormal(), self.activateWindow()))
        self._tray_icon.show()
        self._tray_icon.showMessage(
            self._t("batch_done_title"),
            self._t("batch_done_message").format(ok=ok_count, failed=failed),
            QSystemTrayIcon.MessageIcon.Warning if failed else QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _rebuild_custom_preset_buttons(self) -> None:
        while self._custom_preset_layout.count():
            item = self._custom_preset_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                key = next((k for k, b in self._preset_buttons.items() if b is widget), None)
                if key is not None:
                    del self._preset_buttons[key]
                self._preset_button_group.removeButton(widget)
                widget.deleteLater()
        for name, action_ids in self.settings.custom_presets.items():
            button = self._make_preset_button(name, CUSTOM_PRESET_PREFIX + name)
            button.setProperty("custom", True)
            button.setToolTip(self._t("preset_custom_tooltip").format(count=len(action_ids)))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, b=button, n=name: self._show_custom_preset_menu(b, n, pos)
            )
            self._custom_preset_layout.addWidget(button)

    def _persist_settings(self) -> None:
        # Saved right away (not only at exit) so a crash or a yanked USB
        # stick doesn't lose a preset the technician just created.
        try:
            save_settings(self.state_dir, self.settings)
        except OSError:
            self.console.appendPlainText(self._t("disk_write_failed"))

    def _on_save_preset_clicked(self) -> None:
        selected = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        if not selected:
            return
        name, ok = QInputDialog.getText(self, self._t("preset_save_title"), self._t("preset_save_prompt"))
        self._save_custom_preset(name if ok else "", selected)

    def _save_custom_preset(self, name: str, action_ids: list[str]) -> bool:
        name = name.strip()[:MAX_PRESET_NAME_LENGTH]
        if not name or not action_ids:
            return False
        presets = self.settings.custom_presets
        if name in presets:
            answer = QMessageBox.question(
                self, self._t("preset_save_title"), self._t("preset_overwrite_confirm").format(name=name)
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        elif len(presets) >= MAX_CUSTOM_PRESETS:
            QMessageBox.warning(
                self, self._t("preset_save_title"), self._t("preset_limit_reached").format(max=MAX_CUSTOM_PRESETS)
            )
            return False
        presets[name] = list(action_ids)
        self._persist_settings()
        self._rebuild_custom_preset_buttons()
        button = self._preset_buttons.get(CUSTOM_PRESET_PREFIX + name)
        if button is not None:
            button.setChecked(True)
        self.statusBar().showMessage(self._t("preset_saved").format(name=name, count=len(action_ids)), 5000)
        return True

    def _show_custom_preset_menu(self, button: QPushButton, name: str, pos) -> None:
        menu = QMenu(self)
        delete_action = menu.addAction(self._t("preset_delete").format(name=name))
        if menu.exec(button.mapToGlobal(pos)) is delete_action:
            self._delete_custom_preset(name)

    def _delete_custom_preset(self, name: str) -> None:
        if self.settings.custom_presets.pop(name, None) is None:
            return
        self._persist_settings()
        self._rebuild_custom_preset_buttons()

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
        toggle.setText("▼")

        panel = QWidget()
        panel.setObjectName("actionDetailPanel")
        panel.setHidden(True)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 8, 10, 8)
        panel_layout.setSpacing(4)

        description_label = QLabel(action.description(self.settings.language))
        description_label.setObjectName("actionDetailDescription")
        description_label.setWordWrap(True)
        panel_layout.addWidget(description_label)

        command_box = QPlainTextEdit(action.command)
        command_box.setObjectName("actionDetailCommand")
        command_box.setReadOnly(True)
        command_box.setFixedHeight(60)
        panel_layout.addWidget(command_box)

        if action.undo_command:
            undo_label = QLabel(self._t("action_detail_undo_label"))
            undo_label.setObjectName("actionDetailLabel")
            panel_layout.addWidget(undo_label)
            undo_box = QPlainTextEdit(action.undo_command)
            undo_box.setObjectName("actionDetailCommand")
            undo_box.setReadOnly(True)
            undo_box.setFixedHeight(48)
            panel_layout.addWidget(undo_box)

        def _on_toggled(checked: bool) -> None:
            panel.setHidden(not checked)
            toggle.setText("▲" if checked else "▼")
            toggle.setToolTip(self._t("hide_action_details") if checked else self._t("show_action_details"))

        toggle.toggled.connect(_on_toggled)
        return toggle, panel

    @staticmethod
    def _wrap_row_with_detail_panel(row_widget: QWidget, detail_panel: QWidget) -> QWidget:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(4)
        container_layout.addWidget(row_widget)
        container_layout.addWidget(detail_panel)
        return container

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

    def _build_snapshot_metrics_widget(self) -> QWidget | None:
        rows = snapshot.compare_snapshots(self._snapshot_before, self._snapshot_after)
        if not rows:
            return None
        box = QWidget()
        box.setObjectName("summaryMetrics")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 4, 0, 4)
        box_layout.setSpacing(4)
        heading = QLabel(self._t("snapshot_heading"))
        heading.setObjectName("summaryHeader")
        box_layout.addWidget(heading)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        for index, row in enumerate(rows):
            name = QLabel(self._t(row["label_key"]))
            name.setObjectName("summaryMetricName")
            values = QLabel(f"{row['before']} \u2192 {row['after']}")
            values.setObjectName("selectionScope")
            delta = QLabel(f"({row['delta']})" if row["delta"] else "")
            delta.setObjectName("summaryMetricDelta")
            delta.setProperty("trend", row["trend"] or "same")
            grid.addWidget(name, index, 0)
            grid.addWidget(values, index, 1)
            grid.addWidget(delta, index, 2)
        grid.setColumnStretch(3, 1)
        box_layout.addLayout(grid)
        if any(row["lower_bound"] for row in rows):
            note = QLabel(self._t("snapshot_lower_bound_note"))
            note.setObjectName("selectionScope")
            box_layout.addWidget(note)
        return box

    def _show_batch_summary(self, html_path: Path) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self._t("batch_results_title"))
        dialog.setStyleSheet(style.stylesheet())
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        ok_count = sum(1 for _, code in self._batch_results if code == 0)
        fail_count = len(self._batch_results) - ok_count
        header = QLabel(
            f"{self._t('status_ok')}: {ok_count}    {self._t('status_failed')}: {fail_count}"
        )
        header.setObjectName("summaryHeader")
        layout.addWidget(header)

        # Before -> after metrics, same rows (snapshot.compare_snapshots) as
        # the report's "Before / after" table; only metrics known both times.
        if not self.settings.dry_run:
            metrics = self._build_snapshot_metrics_widget()
            if metrics is not None:
                layout.addWidget(metrics)

        if self.settings.dry_run:
            note = QLabel(self._t("dry_run_batch_note"))
            note.setObjectName("summaryDryRunNote")
            layout.addWidget(note)

        rows_container = QWidget()
        rows_layout = QVBoxLayout(rows_container)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(4)
        for action_id, exit_code in self._batch_results:
            _, action = self._find_action(action_id)
            status = self._t("status_ok") if exit_code == 0 else self._t("status_failed")
            row_label = QLabel(f"[{status}] {action.label(self.settings.language)}")
            row_label.setObjectName("summaryRow")
            row_label.setProperty("ok", "true" if exit_code == 0 else "false")
            rows_layout.addWidget(row_label)
        rows_layout.addStretch(1)

        rows_scroll = QScrollArea()
        rows_scroll.setWidgetResizable(True)
        rows_scroll.setMaximumHeight(320)
        rows_scroll.setWidget(rows_container)
        layout.addWidget(rows_scroll)

        recommended_ids = [aid for aid in self._recommended_action_ids if aid in self._action_checkboxes]
        if recommended_ids:
            rec_header = QLabel(self._t("recommended_fixes_title"))
            rec_header.setObjectName("summaryHeader")
            layout.addWidget(rec_header)

            rec_checkboxes: dict[str, QCheckBox] = {}
            rec_container = QWidget()
            rec_layout = QVBoxLayout(rec_container)
            rec_layout.setContentsMargins(0, 0, 0, 0)
            rec_layout.setSpacing(4)
            for action_id in recommended_ids:
                _, rec_action = self._find_action(action_id)
                cb = QCheckBox(rec_action.label(self.settings.language))
                cb.setChecked(True)
                rec_checkboxes[action_id] = cb
                rec_layout.addWidget(cb)
            rec_layout.addStretch(1)

            rec_scroll = QScrollArea()
            rec_scroll.setWidgetResizable(True)
            rec_scroll.setMaximumHeight(160)
            rec_scroll.setWidget(rec_container)
            layout.addWidget(rec_scroll)

            rec_button_row = QHBoxLayout()
            rec_button_row.addStretch(1)
            apply_rec_button = QPushButton(self._t("recommended_fixes_apply_button"))
            apply_rec_button.setObjectName("runButton")
            apply_rec_button.clicked.connect(
                lambda: self._apply_recommended_selection(
                    [aid for aid, cb in rec_checkboxes.items() if cb.isChecked()], dialog
                )
            )
            rec_button_row.addWidget(apply_rec_button)
            layout.addLayout(rec_button_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        open_button = QPushButton(self._t("open_report"))
        open_button.setObjectName("runButton")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
        )
        button_row.addWidget(open_button)
        if self._undo_steps and self._undo_script_path is not None:
            # Mirrors the "Open report" button above exactly - the undo
            # script already exists on disk whenever there are reversible
            # steps, but until now there was no UI entry point to find it.
            undo_script_path = self._undo_script_path
            open_undo_button = QPushButton(self._t("open_undo_script"))
            open_undo_button.setObjectName("selectionBtn")
            open_undo_button.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(undo_script_path)))
            )
            button_row.addWidget(open_undo_button)
        handoff_button = self._make_selection_button(
            self._t("handoff_button"), lambda: self._save_handoff_package(self.run_id, dialog)
        )
        button_row.addWidget(handoff_button)
        layout.addLayout(button_row)

        open_button.setDefault(True)
        dialog.show()
        # A batch often finishes while the technician is elsewhere - bring the
        # (non-modal) results forward and put keyboard focus on its primary
        # button so Enter opens the report and a screen reader lands on it
        # (research-accessibility.md Finding 7).
        dialog.raise_()
        dialog.activateWindow()
        open_button.setFocus()
        self._summary_dialog = dialog

    def _apply_recommended_selection(self, action_ids: list[str], dialog: QDialog) -> None:
        if not action_ids:
            dialog.close()
            return
        self._apply_selection(list(self._action_checkboxes), "none")
        self._apply_selection(action_ids, "all")
        wanted_set = set(action_ids)
        for index, category in enumerate(self._categories_order):
            if wanted_set.intersection(self._category_action_ids.get(category, [])):
                self.category_list.setCurrentRow(index)
                break
        dialog.close()

    def _build_winget_updates_panel(self) -> QWidget:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 8)
        panel_layout.setSpacing(6)

        search_box = QLineEdit()
        search_box.setObjectName("searchBox")
        search_box.setPlaceholderText(self._t("winget_search_placeholder"))
        panel_layout.addWidget(search_box)

        search_status_label = QLabel("")
        search_status_label.setObjectName("selectionScope")
        search_status_label.setVisible(False)
        panel_layout.addWidget(search_status_label)

        status_label = QLabel(self._t("winget_scanning"))
        status_label.setObjectName("wingetBanner")
        status_label.setProperty("state", "ok")
        panel_layout.addWidget(status_label)

        def set_status(text: str, state: str) -> None:
            status_label.setText(text)
            status_label.setProperty("state", state)
            status_label.style().unpolish(status_label)
            status_label.style().polish(status_label)

        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(2)
        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setMinimumHeight(380)
        list_scroll.setMaximumHeight(420)
        list_scroll.setWidget(list_container)
        list_scroll.setVisible(False)
        panel_layout.addWidget(list_scroll)

        # Refresh must stay reachable even when the scan finds nothing to
        # update (or hasn't run yet) - it used to live inside select_row_widget,
        # which hid it exactly when there was nothing selectable, leaving no
        # way to ever re-check for updates again without restarting the app.
        refresh_row = QHBoxLayout()
        refresh_btn = QPushButton(self._t("winget_refresh_button"))
        refresh_btn.setObjectName("selectionBtn")
        refresh_row.addWidget(refresh_btn)
        refresh_row.addStretch(1)
        export_btn = QPushButton(self._t("winget_export_button"))
        export_btn.setObjectName("selectionBtn")
        refresh_row.addWidget(export_btn)
        import_btn = QPushButton(self._t("winget_import_button"))
        import_btn.setObjectName("selectionBtn")
        refresh_row.addWidget(import_btn)
        manage_ignored_btn = QPushButton(self._t("winget_manage_ignored_button"))
        manage_ignored_btn.setObjectName("selectionBtn")
        refresh_row.addWidget(manage_ignored_btn)
        refresh_row_widget = QWidget()
        refresh_row_widget.setLayout(refresh_row)
        panel_layout.addWidget(refresh_row_widget)

        auto_check_row = QHBoxLayout()
        auto_check_checkbox = QCheckBox(self._t("winget_auto_check_label"))
        auto_check_row.addWidget(auto_check_checkbox)
        auto_check_interval = QComboBox()
        _AUTO_CHECK_MINUTES = [15, 30, 60, 120]
        for minutes in _AUTO_CHECK_MINUTES:
            auto_check_interval.addItem(f"{minutes} min", minutes)
        auto_check_row.addWidget(auto_check_interval)
        auto_check_row.addStretch(1)
        auto_check_row_widget = QWidget()
        auto_check_row_widget.setLayout(auto_check_row)
        panel_layout.addWidget(auto_check_row_widget)

        ignored_panel = QWidget()
        ignored_panel_layout = QVBoxLayout(ignored_panel)
        ignored_panel_layout.setContentsMargins(0, 0, 0, 0)
        ignored_panel_layout.setSpacing(2)
        ignored_panel.setVisible(False)
        panel_layout.addWidget(ignored_panel)

        select_row = QHBoxLayout()
        select_all_btn = QPushButton(self._t("select_all"))
        select_all_btn.setObjectName("selectionBtn")
        select_row.addWidget(select_all_btn)
        select_none_btn = QPushButton(self._t("select_none"))
        select_none_btn.setObjectName("selectionBtn")
        select_row.addWidget(select_none_btn)
        select_row.addStretch(1)
        update_btn = QPushButton(self._t("winget_update_selected_button"))
        update_btn.setObjectName("runButton")
        update_btn.setEnabled(False)
        select_row.addWidget(update_btn)
        select_row_widget = QWidget()
        select_row_widget.setLayout(select_row)
        select_row_widget.setVisible(False)
        panel_layout.addWidget(select_row_widget)

        console = QPlainTextEdit()
        console.setObjectName("console")
        console.setReadOnly(True)
        console.setMinimumHeight(200)
        console.setMaximumHeight(260)
        console.setVisible(False)
        panel_layout.addWidget(console)

        row_checkboxes: dict[str, QCheckBox] = {}
        row_progress: dict[str, QProgressBar] = {}
        row_ignore_buttons: dict[str, QPushButton] = {}
        row_widgets: dict[str, QWidget] = {}
        package_by_id: dict[str, object] = {}
        known_packages: dict[str, object] = {}

        def update_button_state() -> None:
            update_btn.setEnabled(any(cb.isChecked() for cb in row_checkboxes.values()))

        def apply_search(text: str) -> None:
            needle = text.strip().lower()
            visible_count = 0
            for pkg_id, checkbox in row_checkboxes.items():
                is_match = not needle or needle in checkbox.text().lower() or needle in pkg_id.lower()
                checkbox.setHidden(not is_match)
                if is_match:
                    visible_count += 1
            # Only the zero-match case needs a message - a non-empty result
            # is already visible feedback, an empty one looks like the scan
            # silently found nothing (or is broken) without this.
            if needle and visible_count == 0 and row_checkboxes:
                search_status_label.setText(self._t("search_no_matches").format(query=text.strip()))
                search_status_label.setVisible(True)
            else:
                search_status_label.setVisible(False)

        search_box.textChanged.connect(apply_search)

        def remove_row(package_id: str) -> None:
            row_widget = row_widgets.pop(package_id, None)
            row_checkboxes.pop(package_id, None)
            row_progress.pop(package_id, None)
            row_ignore_buttons.pop(package_id, None)
            if row_widget is not None:
                row_widget.setParent(None)
                row_widget.deleteLater()
            update_button_state()

        def ignore_package(package_id: str) -> None:
            if package_id not in self.settings.winget_ignored_ids:
                self.settings.winget_ignored_ids.append(package_id)
            remove_row(package_id)
            refresh_ignored_panel()

        def unignore_package(package_id: str) -> None:
            if package_id in self.settings.winget_ignored_ids:
                self.settings.winget_ignored_ids.remove(package_id)
            refresh_ignored_panel()
            start_scan()

        def refresh_ignored_panel() -> None:
            while ignored_panel_layout.count():
                item = ignored_panel_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            ignored_ids = self.settings.winget_ignored_ids
            heading = QLabel(self._t("winget_ignored_heading"))
            heading.setObjectName("selectionScope")
            ignored_panel_layout.addWidget(heading)
            if not ignored_ids:
                ignored_panel_layout.addWidget(QLabel(self._t("winget_ignored_empty")))
                return
            for pid in ignored_ids:
                package = known_packages.get(pid)
                name = package.name if package is not None else pid
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(QLabel(name), 1)
                unignore_btn = QPushButton(self._t("winget_unignore_button"))
                unignore_btn.setObjectName("selectionBtn")
                unignore_btn.clicked.connect(lambda _checked=False, p=pid: unignore_package(p))
                row.addWidget(unignore_btn)
                ignored_panel_layout.addWidget(row_widget)

        def populate(packages: list) -> None:
            while list_layout.count():
                item = list_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            row_checkboxes.clear()
            row_progress.clear()
            row_ignore_buttons.clear()
            row_widgets.clear()
            package_by_id.clear()
            for package in packages:
                known_packages[package.id] = package
            ignored_ids = set(self.settings.winget_ignored_ids)
            visible_packages = [p for p in packages if p.id not in ignored_ids]
            if not visible_packages:
                set_status(self._t("winget_no_updates"), "ok")
                list_scroll.setVisible(False)
                select_row_widget.setVisible(False)
                return
            set_status(self._t("winget_updates_found").format(count=len(visible_packages)), "warn")
            for package in visible_packages:
                label = f"{package.name}  {package.installed_version} → {package.available_version}"
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(self._make_avatar(package.name))
                checkbox = QCheckBox(label)
                checkbox.setToolTip(package.id)
                checkbox.stateChanged.connect(lambda _state=0: update_button_state())
                row.addWidget(checkbox, 1)
                progress = QProgressBar()
                progress.setObjectName("batchProgress")
                progress.setFixedWidth(70)
                progress.setMaximumHeight(10)
                progress.setTextVisible(False)
                progress.setRange(0, 0)
                progress.setVisible(False)
                row.addWidget(progress)
                ignore_btn = QPushButton(self._t("winget_ignore_button"))
                ignore_btn.setObjectName("selectionBtn")
                ignore_btn.clicked.connect(lambda _checked=False, pid=package.id: ignore_package(pid))
                row.addWidget(ignore_btn)
                row_checkboxes[package.id] = checkbox
                row_progress[package.id] = progress
                row_ignore_buttons[package.id] = ignore_btn
                row_widgets[package.id] = row_widget
                package_by_id[package.id] = package
                list_layout.addWidget(row_widget)
            list_layout.addStretch(1)
            list_scroll.setVisible(True)
            select_row_widget.setVisible(True)
            update_button_state()

        def start_scan() -> None:
            set_status(self._t("winget_scanning"), "ok")
            list_scroll.setVisible(False)
            select_row_widget.setVisible(False)
            runner = winget_updates.WingetScanRunner(parent=panel)
            self._winget_scan_runner = runner
            runner.scan_finished.connect(populate)
            runner.start()

        def export_list() -> None:
            packages = list(package_by_id.values())
            if not packages:
                return
            path_str, _ = QFileDialog.getSaveFileName(
                panel, self._t("winget_export_button"), "winget_packages.json", "JSON (*.json)"
            )
            if not path_str:
                return
            winget_updates.export_package_list(packages, Path(path_str))
            set_status(self._t("winget_export_success").format(count=len(packages)), "ok")

        def import_list() -> None:
            path_str, _ = QFileDialog.getOpenFileName(
                panel, self._t("winget_import_button"), "", "JSON (*.json)"
            )
            if not path_str:
                return
            try:
                imported_ids = winget_updates.import_package_ids(Path(path_str))
            except (OSError, ValueError):
                set_status(self._t("winget_import_failed"), "warn")
                return
            checked = 0
            for pid, checkbox in row_checkboxes.items():
                if pid in imported_ids:
                    checkbox.setChecked(True)
                    checked += 1
            set_status(self._t("winget_import_success").format(count=checked), "ok")

        def apply_auto_check_setting() -> None:
            minutes = auto_check_interval.currentData() if auto_check_checkbox.isChecked() else 0
            self.settings.winget_auto_check_minutes = minutes
            auto_check_interval.setEnabled(auto_check_checkbox.isChecked())
            if minutes:
                auto_check_timer.start(minutes * 60_000)
            else:
                auto_check_timer.stop()

        # True while the update confirmation is open: its nested event loop
        # still fires auto_check_timer, and a scan then would rebuild the rows
        # under the pending answer.
        confirm_state = {"open": False}

        def auto_check_tick() -> None:
            # A scan mid-batch would clear row_checkboxes/row_progress/
            # row_widgets out from under the update in progress (populate()
            # rebuilds them from scratch), visibly resetting the panel.
            if confirm_state["open"]:
                return
            # A scan started now would be a long task the update hand-off
            # has to refuse, or one the closing app has to wait for.
            if self._update_in_progress or self._closing_for_update:
                return
            runner = self._winget_update_runner
            if runner is None or not runner.isRunning():
                start_scan()

        auto_check_timer = QTimer(panel)
        auto_check_timer.timeout.connect(auto_check_tick)
        saved_minutes = self.settings.winget_auto_check_minutes
        if saved_minutes in _AUTO_CHECK_MINUTES:
            auto_check_checkbox.setChecked(True)
            auto_check_interval.setCurrentIndex(_AUTO_CHECK_MINUTES.index(saved_minutes))
        else:
            auto_check_interval.setEnabled(False)
        apply_auto_check_setting()
        auto_check_checkbox.toggled.connect(lambda _checked=False: apply_auto_check_setting())
        auto_check_interval.currentIndexChanged.connect(lambda _index=0: apply_auto_check_setting())

        refresh_ignored_panel()

        def start_update() -> None:
            selected_packages = [package_by_id[pid] for pid, cb in row_checkboxes.items() if cb.isChecked()]
            if not selected_packages:
                return
            # The app update's hand-off checked for long tasks right before
            # its handshake; one begun during it would hold up the exit the
            # updater is waiting for.
            if self._update_phase == "launch" or self._closing_for_update:
                self.statusBar().showMessage(self._t("winget_update_blocked_by_app_update"))
                return
            # Installs a newer version with no way back - a MODERATE change
            # by the catalog's own yardstick, confirmed like one.
            risk = RiskLevel.MODERATE.value

            def upgrade_command(package_id: str) -> str:
                # Same argv as winget_updates._run_winget_upgrade; its
                # "--location" retry, when needed, shows up in the output.
                return (
                    f"winget upgrade --id {package_id} --silent --include-unknown "
                    "--accept-package-agreements --accept-source-agreements --disable-interactivity"
                )

            if self.settings.dry_run:
                # Nothing is started: DRY-RUN must never install anything,
                # so this only shows (and logs) what would have run.
                console.setVisible(True)
                console.appendPlainText(self._t("winget_update_dry_run_notice"))
                for package in selected_packages:
                    command = upgrade_command(package.id)
                    console.appendPlainText(f"[DRY-RUN] {command}")
                    self._log_panel_action("_winget", package.id, command, 0, f"[DRY-RUN] {command}", True, risk)
                return
            warning_text = self._t("winget_update_confirm_text").format(
                count=len(selected_packages),
                packages=self._panel_confirm_list(
                    [f"{p.name}  {p.installed_version} → {p.available_version}" for p in selected_packages]
                ),
            )
            confirm_state["open"] = True
            try:
                # Default No, like the uninstaller's: the installers run silently.
                answer = QMessageBox.question(
                    self, self._t("category_winget"), warning_text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
            finally:
                confirm_state["open"] = False
            if answer != QMessageBox.Yes:
                # Logged like a declined catalog action (_dispatch_action).
                for package in selected_packages:
                    self._log_system_event(
                        "risk_declined", None, "Technician declined the winget update confirmation - package not updated.",
                        risk=risk, warned=True, warning_text=warning_text,
                        subject=f"_winget/{package.id}", decision="declined",
                    )
                return
            update_btn.setEnabled(False)
            select_all_btn.setEnabled(False)
            select_none_btn.setEnabled(False)
            refresh_btn.setEnabled(False)
            console.setVisible(True)
            console.appendPlainText(self._t("winget_updating"))
            runner = winget_updates.WingetUpdateRunner(selected_packages, parent=panel)
            self._winget_update_runner = runner

            # A single winget call can legitimately take minutes with only a
            # busy/indeterminate bar to show for it - a ticking elapsed-time
            # counter is concrete evidence it's still alive, not frozen.
            elapsed_timer = QTimer(panel)
            elapsed_state = {"package_id": None, "seconds": 0}

            def tick_elapsed() -> None:
                pid = elapsed_state["package_id"]
                progress = row_progress.get(pid) if pid is not None else None
                if progress is None:
                    return
                elapsed_state["seconds"] += 1
                progress.setTextVisible(True)
                progress.setFormat(f"{elapsed_state['seconds']}s")

            elapsed_timer.timeout.connect(tick_elapsed)

            def on_package_started(package_id: str) -> None:
                package = package_by_id.get(package_id)
                name = package.name if package is not None else package_id
                # Without this, a slow (or hung) winget call left the panel
                # showing nothing at all until it finished or timed out -
                # easy to mistake for the app having frozen.
                console.appendPlainText(self._t("winget_updating_one").format(name=name))
                progress = row_progress.get(package_id)
                if progress is not None:
                    progress.setVisible(True)
                    progress.setTextVisible(True)
                    progress.setFormat("0s")
                elapsed_state["package_id"] = package_id
                elapsed_state["seconds"] = 0
                elapsed_timer.start(1000)
                ignore_btn = row_ignore_buttons.get(package_id)
                if ignore_btn is not None:
                    ignore_btn.setVisible(False)

            def on_package_finished(package_id: str, ok: bool, output: str) -> None:
                package = package_by_id.get(package_id)
                name = package.name if package is not None else package_id
                # Recorded before any widget is touched, so a console error
                # can never leave a real update out of the log.
                self._log_panel_action(
                    "_winget", package_id, upgrade_command(package_id), 0 if ok else 1, output,
                    False, risk, True, warning_text,
                )
                # winget has no rollback to the previous version - undo.ps1
                # says so rather than implying the update can be undone. A
                # failed update is listed too: the installer may have run.
                irreversible = f"[{risk}] {self._t('category_winget')}: {name} ({package_id})"
                if not ok:
                    irreversible += " - exit 1"
                self._irreversible_actions.append(irreversible)
                self._write_undo_script()
                elapsed_timer.stop()
                elapsed_state["package_id"] = None
                status = self._t("status_ok") if ok else self._t("status_failed")
                console.appendPlainText(f"[{status}] {name}")
                if output:
                    console.appendPlainText(output)
                if ok:
                    # Successfully updated - it's no longer outdated, so drop
                    # it from the list right away instead of waiting for the
                    # post-batch rescan to quietly remove it later.
                    remove_row(package_id)
                else:
                    row_widget = row_widgets.get(package_id)
                    progress = row_progress.get(package_id)
                    if progress is not None:
                        progress.setVisible(False)
                    if row_widget is not None and package is not None:
                        def open_app(_checked=False, name=package.name) -> None:
                            found = uninstaller.find_program_by_name(name)
                            if found is not None:
                                uninstaller.launch_program(found)

                        manual_btn = QPushButton(self._t("winget_manual_update_button"))
                        manual_btn.setObjectName("selectionBtn")
                        manual_btn.clicked.connect(open_app)
                        row_widget.layout().addWidget(manual_btn)

            def on_all_finished() -> None:
                update_btn.setEnabled(True)
                select_all_btn.setEnabled(True)
                select_none_btn.setEnabled(True)
                refresh_btn.setEnabled(True)
                start_scan()

            runner.package_started.connect(on_package_started)
            runner.package_finished.connect(on_package_finished)
            runner.all_finished.connect(on_all_finished)
            runner.start()

        select_all_btn.clicked.connect(
            lambda _checked=False: [cb.setChecked(True) for cb in row_checkboxes.values() if not cb.isHidden()]
        )
        select_none_btn.clicked.connect(lambda _checked=False: [cb.setChecked(False) for cb in row_checkboxes.values()])
        refresh_btn.clicked.connect(lambda _checked=False: start_scan())
        update_btn.clicked.connect(lambda _checked=False: start_update())
        export_btn.clicked.connect(lambda _checked=False: export_list())
        import_btn.clicked.connect(lambda _checked=False: import_list())
        manage_ignored_btn.clicked.connect(lambda _checked=False: ignored_panel.setVisible(not ignored_panel.isVisible()))

        start_scan()
        return panel

    def _build_dashboard_card(self, category_i18n_keys: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("actionCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(10)

        heading = QLabel(self._t("category_dashboard"))
        heading.setObjectName("cardHeading")
        card_layout.addWidget(heading)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        score_box = QVBoxLayout()
        score_box.setSpacing(0)
        score_value = QLabel(self._t("dashboard_no_run_yet"))
        score_value.setObjectName("dashboardScoreValue")
        # "none" until the first analysis - a big green "not run yet" read
        # like a healthy result before anything had been checked.
        score_value.setProperty("state", "none")
        score_caption = QLabel(self._t("dashboard_score_label"))
        score_caption.setObjectName("selectionScope")
        score_box.addWidget(score_value)
        score_box.addWidget(score_caption)
        self._dashboard_score_label = score_value
        top_row.addLayout(score_box)
        # Kept on self so batch start/end can lock it together with
        # run_button - it starts a batch too. Its initial state matters when
        # a language toggle rebuilds the dashboard while a batch, its report
        # or an update download is still in flight.
        self.dashboard_analyze_button = QPushButton(self._t("dashboard_analyze_button"))
        self.dashboard_analyze_button.setObjectName("runButton")
        self.dashboard_analyze_button.setEnabled(not self._batch_start_blocked())
        self.dashboard_analyze_button.clicked.connect(lambda _checked=False: self._run_dashboard_analysis())
        top_row.addWidget(self.dashboard_analyze_button)
        top_row.addStretch(1)
        card_layout.addLayout(top_row)

        grid = QGridLayout()
        grid.setSpacing(10)
        tile_categories = [c for c in self._categories_order if c != ModuleCategory.DASHBOARD]
        columns = 4
        for index, category in enumerate(tile_categories):
            tile = _DashboardTile()
            tile.setObjectName("actionCard")
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            tile_layout.setSpacing(2)
            tile_top = QHBoxLayout()
            name_label = QLabel(self._t(category_i18n_keys.get(category, "")))
            name_label.setObjectName("cardHeading")
            name_label.setWordWrap(True)
            tile_top.addWidget(name_label, 1)
            count_pill = QLabel("0")
            count_pill.setObjectName("countPill")
            count_pill.setProperty("state", "idle")
            tile_top.addWidget(count_pill)
            tile_layout.addLayout(tile_top)
            count = self._category_module_action_counts.get(category, 0)
            sub_label = QLabel(self._t("dashboard_actions_count").format(count=count) if count else "")
            sub_label.setObjectName("selectionScope")
            tile_layout.addWidget(sub_label)
            self._dashboard_tile_count_labels[category] = count_pill
            self._dashboard_tiles[category] = tile
            tile.setAccessibleDescription(self._t("a11y_dashboard_tile_hint"))
            self._update_dashboard_tile_accessible_name(category)
            tile.activated.connect(lambda c=category: self._dashboard_tile_clicked(c))
            grid.addWidget(tile, index // columns, index % columns)
        card_layout.addLayout(grid)

        history_heading = QLabel(self._t("history_heading"))
        history_heading.setObjectName("cardHeading")
        card_layout.addWidget(history_heading)
        history_widget = QWidget()
        self._history_layout = QVBoxLayout(history_widget)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(4)
        card_layout.addWidget(history_widget)
        self._refresh_history()

        winget_heading = QLabel(self._t("category_winget"))
        winget_heading.setObjectName("cardHeading")
        card_layout.addWidget(winget_heading)
        card_layout.addWidget(self._build_winget_updates_panel())

        card_layout.addStretch(1)
        return card

    def _update_dashboard_tile_accessible_name(self, category: ModuleCategory) -> None:
        # The tile's text lives in child QLabels focus never reaches - fold
        # name, recommended-fix count and action count into the tile itself.
        tile = self._dashboard_tiles.get(category)
        if tile is None:
            return
        name = self._t(self._category_i18n_keys.get(category, ""))
        pill = self._dashboard_tile_count_labels.get(category)
        findings = pill.text() if pill is not None else "0"
        parts = [name, self._t("a11y_dashboard_tile_findings").format(count=findings)]
        action_count = self._category_module_action_counts.get(category, 0)
        if action_count:
            parts.append(self._t("dashboard_actions_count").format(count=action_count))
        tile.setAccessibleName(", ".join(parts))

    def _dashboard_tile_clicked(self, category: ModuleCategory) -> None:
        if category in self._categories_order:
            self.category_list.setCurrentRow(self._categories_order.index(category))

    def _run_dashboard_analysis(self) -> None:
        # Checked before _apply_preset, not left to run_selected_actions:
        # mid-batch the preset would still wipe the technician's checkbox
        # selection even though no second batch starts.
        if self._batch_start_blocked():
            return
        if "full_diagnostic" in PRESETS:
            self._apply_preset("full_diagnostic")
            self.run_selected_actions()

    def _refresh_history(self) -> None:
        layout = getattr(self, "_history_layout", None)
        if layout is None:
            return
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        runs = history.recent_runs(self.state_dir / "Reports", socket.gethostname(), limit=HISTORY_MAX_ROWS)
        if not runs:
            empty = QLabel(self._t("history_empty"))
            empty.setObjectName("selectionScope")
            layout.addWidget(empty)
            return
        for run in runs:
            row = QFrame()
            row.setObjectName("historyRow")
            row.setProperty("failed", run.failed_count > 0)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 4, 6, 4)
            row_layout.setSpacing(8)
            text = QLabel(
                self._t("history_row").format(
                    date=run.display_date(), count=run.action_count, failed=run.failed_count
                )
            )
            text.setObjectName("historyText")
            row_layout.addWidget(text, 1)
            if run.dry_run:
                tag = QLabel(self._t("history_dry_run_tag"))
                tag.setObjectName("summaryDryRunNote")
                row_layout.addWidget(tag)
            if run.html_path is not None:
                open_button = self._make_selection_button(
                    self._t("history_open"),
                    lambda path=run.html_path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))),
                )
                row_layout.addWidget(open_button)
            handoff_button = self._make_selection_button(
                self._t("handoff_button"), lambda rid=run.run_id: self._save_handoff_package(rid)
            )
            handoff_button.setProperty("handoffRunId", run.run_id)
            row_layout.addWidget(handoff_button)
            layout.addWidget(row)

    def _save_handoff_package(self, run_id: str, parent: QWidget | None = None) -> Path | None:
        """Ask where to save the client handoff zip of one run and write it."""
        hostname = socket.gethostname()
        default_path = self.state_dir / "Reports" / handoff.default_package_name(hostname, run_id)
        dest, _ = QFileDialog.getSaveFileName(
            parent or self, self._t("handoff_button"), str(default_path), "Zip (*.zip)"
        )
        if not dest:
            return None
        try:
            saved = handoff.build_handoff_zip(self.state_dir, hostname, run_id, Path(dest))
        except ValueError:
            QMessageBox.warning(parent or self, self._t("app_title"), self._t("handoff_no_files"))
            return None
        except OSError as exc:
            self.console.appendPlainText(self._t("handoff_failed"))
            QMessageBox.warning(parent or self, self._t("app_title"), f"{self._t('handoff_failed')}\n{exc}")
            return None
        self.statusBar().showMessage(self._t("handoff_saved").format(path=saved), 15000)
        button = getattr(self, "_handoff_folder_button", None)
        if button is None:
            button = QPushButton(self._t("handoff_open_folder"))
            button.setObjectName("selectionBtn")
            button.clicked.connect(self._open_handoff_folder)
            self.statusBar().addPermanentWidget(button)
            self._handoff_folder_button = button
        button.setText(self._t("handoff_open_folder"))
        button.setProperty("folder", str(saved.parent))
        button.setVisible(True)
        return saved

    def _open_handoff_folder(self) -> None:
        button = self._handoff_folder_button
        QDesktopServices.openUrl(QUrl.fromLocalFile(button.property("folder")))
        button.setVisible(False)

    def _refresh_dashboard(self) -> None:
        self._refresh_history()
        if self._dashboard_score_label is not None:
            unique_recommended = len(self._recommended_action_ids)
            score = max(40, 100 - unique_recommended * 10)
            self._dashboard_score_label.setText(str(score))
            self._dashboard_score_label.setProperty("state", _score_state(score))
            self._dashboard_score_label.style().unpolish(self._dashboard_score_label)
            self._dashboard_score_label.style().polish(self._dashboard_score_label)
        counts: dict[ModuleCategory, int] = {}
        for action_id in self._recommended_action_ids:
            try:
                module, _ = self._find_action(action_id)
            except KeyError:
                continue
            counts[module.category] = counts.get(module.category, 0) + 1
        for category, pill in self._dashboard_tile_count_labels.items():
            needs_fix_count = counts.get(category, 0)
            pill.setText(str(needs_fix_count))
            pill.setProperty("state", "warn" if needs_fix_count else "ok")
            pill.style().unpolish(pill)
            pill.style().polish(pill)
            self._update_dashboard_tile_accessible_name(category)

    _AVATAR_COLORS = ["#5ee6ff", "#39c2ff", "#6bd4c2", "#8f7cff", "#4dd0e1", "#64b5f6"]

    def _make_avatar(self, name: str) -> QLabel:
        color = self._AVATAR_COLORS[sum(ord(c) for c in name) % len(self._AVATAR_COLORS)]
        words = name.split()
        initials = "".join(w[0] for w in words[:2]).upper() if words else "?"
        avatar = QLabel(initials)
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background:{color}; color:#06141a; border-radius:14px; font-weight:bold; font-size:9pt;"
        )
        return avatar

    def _uninstaller_row_label(self, program: "uninstaller.InstalledProgram") -> str:
        parts = [program.name]
        if program.version:
            parts.append(f"v{program.version}")
        if program.estimated_size_kb:
            parts.append(f"{program.estimated_size_kb / 1024:.1f} MB")
        if program.install_date:
            parts.append(program.install_date)
        return "  |  ".join(parts)

    def _build_uninstaller_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("actionCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(8)
        heading = QLabel(self._t("category_uninstaller"))
        heading.setObjectName("cardHeading")
        card_layout.addWidget(heading)

        search_box = QLineEdit()
        search_box.setObjectName("searchBox")
        search_box.setPlaceholderText(self._t("uninstaller_search_placeholder"))
        card_layout.addWidget(search_box)

        list_status_label = QLabel("")
        list_status_label.setObjectName("selectionScope")
        list_status_label.setVisible(False)
        card_layout.addWidget(list_status_label)

        programs = uninstaller.list_installed_programs()
        row_checkboxes: dict[str, QCheckBox] = {}
        row_widgets: dict[str, QWidget] = {}
        program_by_name: dict[str, uninstaller.InstalledProgram] = {}

        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(2)
        for program in programs:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(self._make_avatar(program.name))
            checkbox = QCheckBox(self._uninstaller_row_label(program))
            tooltip = program.install_location or ""
            if program.publisher:
                tooltip = f"{program.publisher}\n{tooltip}" if tooltip else program.publisher
            checkbox.setToolTip(tooltip)
            row.addWidget(checkbox, 1)
            row_checkboxes[program.name] = checkbox
            row_widgets[program.name] = row_widget
            program_by_name[program.name] = program
            list_layout.addWidget(row_widget)
        list_layout.addStretch(1)

        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setMaximumHeight(320)
        list_scroll.setWidget(list_container)
        card_layout.addWidget(list_scroll)

        def apply_search(text: str) -> None:
            needle = text.strip().lower()
            visible_count = 0
            for name, row_widget in row_widgets.items():
                is_match = not needle or needle in name.lower()
                row_widget.setHidden(not is_match)
                if is_match:
                    visible_count += 1
            if not row_widgets:
                # No installed programs at all (registry gave nothing back) -
                # an empty scroll area with no explanation looks broken.
                list_status_label.setText(self._t("uninstaller_no_programs"))
                list_status_label.setVisible(True)
            elif needle and visible_count == 0:
                list_status_label.setText(self._t("search_no_matches").format(query=text.strip()))
                list_status_label.setVisible(True)
            else:
                list_status_label.setVisible(False)

        search_box.textChanged.connect(apply_search)
        apply_search("")

        select_row = QHBoxLayout()
        select_all_btn = self._make_selection_button(
            self._t("select_all"),
            lambda: [cb.setChecked(True) for name, cb in row_checkboxes.items() if not row_widgets[name].isHidden()],
        )
        select_row.addWidget(select_all_btn)
        select_none_btn = self._make_selection_button(
            self._t("select_none"), lambda: [cb.setChecked(False) for cb in row_checkboxes.values()]
        )
        select_row.addWidget(select_none_btn)
        select_row.addStretch(1)
        card_layout.addLayout(select_row)

        console = QPlainTextEdit()
        console.setObjectName("console")
        console.setReadOnly(True)
        console.setMaximumHeight(140)
        console.setVisible(False)
        card_layout.addWidget(console)

        cleanup_container = QWidget()
        cleanup_layout = QVBoxLayout(cleanup_container)
        cleanup_layout.setContentsMargins(0, 0, 0, 0)
        cleanup_layout.setSpacing(2)
        cleanup_container.setVisible(False)
        card_layout.addWidget(cleanup_container)

        uninstall_button = QPushButton(self._t("uninstaller_uninstall_button"))
        uninstall_button.setObjectName("runButton")
        card_layout.addWidget(uninstall_button)

        def show_orphan_cleanup() -> None:
            while cleanup_layout.count():
                item = cleanup_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            orphans = uninstaller.find_orphaned_uninstall_entries()
            if not orphans:
                cleanup_container.setVisible(False)
                return
            heading = QLabel(self._t("uninstaller_leftovers_heading"))
            heading.setObjectName("cardHeading")
            cleanup_layout.addWidget(heading)
            # InstalledProgram is a plain (unhashable) dataclass - using it as
            # the key raised TypeError. Where the entry lives is its identity.
            orphan_checkboxes: dict[tuple[int, str], QCheckBox] = {}
            orphan_by_key: dict[tuple[int, str], uninstaller.InstalledProgram] = {}
            for orphan in orphans:
                key = (orphan.registry_hive, orphan.registry_path)
                cb = QCheckBox(orphan.name)
                # Unchecked: every deleted entry is the technician's explicit
                # pick, not a default they would have to notice and untick.
                cb.setToolTip(f"{orphan.install_location or ''}\n{uninstaller.registry_key_name(*key)}")
                orphan_checkboxes[key] = cb
                orphan_by_key[key] = orphan
                cleanup_layout.addWidget(cb)
            clean_button = QPushButton(self._t("uninstaller_clean_leftovers_button"))
            clean_button.setObjectName("selectionBtn")

            def do_clean() -> None:
                chosen = [orphan_by_key[key] for key, cb in orphan_checkboxes.items() if cb.isChecked()]
                if not chosen:
                    return
                console.setVisible(True)
                risk = RiskLevel.DESTRUCTIVE.value

                def delete_command(orphan: uninstaller.InstalledProgram) -> str:
                    key_name = uninstaller.registry_key_name(orphan.registry_hive, orphan.registry_path)
                    return f'reg delete "{key_name}" /f'

                if self.settings.dry_run:
                    console.appendPlainText(self._t("dry_run_batch_note"))
                    for orphan in chosen:
                        command = delete_command(orphan)
                        console.appendPlainText(f"[DRY-RUN] {command}")
                        self._log_panel_action(
                            "_uninstaller", f"orphan_cleanup:{orphan.name}", command, 0, f"[DRY-RUN] {command}",
                            True, risk,
                        )
                    return
                warning_text = self._t("uninstaller_orphan_confirm_text").format(
                    count=len(chosen),
                    entries=self._panel_confirm_list([
                        f"{o.name} ({uninstaller.registry_key_name(o.registry_hive, o.registry_path)})" for o in chosen
                    ]),
                )
                answer = QMessageBox.warning(
                    self, self._t("uninstaller_leftovers_heading"), warning_text,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    for orphan in chosen:
                        self._log_system_event(
                            "risk_declined", None, "Technician declined the leftover registry cleanup - entry not deleted.",
                            risk=risk, warned=True, warning_text=warning_text,
                            subject=f"_uninstaller/orphan_cleanup:{orphan.name}", decision="declined",
                        )
                    return
                backup_dir = self.state_dir / "Backups" / self.run_id
                removed = 0
                for orphan in chosen:
                    command = delete_command(orphan)
                    action_id = f"orphan_cleanup:{orphan.name}"
                    backup_path = uninstaller.orphan_backup_path(backup_dir, orphan.name)
                    # No backup, no delete: a wrongly flagged entry (e.g. a
                    # program on a drive that reappears later) must stay
                    # recoverable with "reg import".
                    if not uninstaller.backup_registry_key(orphan.registry_hive, orphan.registry_path, backup_path):
                        console.appendPlainText(self._t("uninstaller_orphan_backup_failed").format(name=orphan.name))
                        self._log_panel_action(
                            "_uninstaller", action_id, command, 1,
                            f"Registry backup to {backup_path} failed - entry not deleted.",
                            False, risk, True, warning_text,
                        )
                        continue
                    deleted = uninstaller.remove_registry_key(orphan.registry_hive, orphan.registry_path)
                    output = f"Backup: {backup_path}"
                    if deleted:
                        removed += 1
                        # The .reg backup makes this reversible - undo.ps1
                        # re-imports it (PowerShell single-quote escaping).
                        quoted = str(backup_path).replace("'", "''")
                        self._undo_steps.append(f"reg import '{quoted}'")
                    else:
                        output += "\nDeleting the registry entry failed."
                        console.appendPlainText(f"[{self._t('status_failed')}] {orphan.name}")
                    self._log_panel_action(
                        "_uninstaller", action_id, command, 0 if deleted else 1, output,
                        False, risk, True, warning_text,
                    )
                console.appendPlainText(self._t("uninstaller_leftovers_removed").format(count=removed))
                if removed:
                    self._write_undo_script()
                # Rescan: deleted entries drop off the list, anything that
                # failed or was left unticked stays for another try.
                show_orphan_cleanup()

            clean_button.clicked.connect(lambda _checked=False: do_clean())
            cleanup_layout.addWidget(clean_button)
            cleanup_container.setVisible(True)

        def start_uninstall() -> None:
            selected = [program_by_name[name] for name, cb in row_checkboxes.items() if cb.isChecked()]
            if not selected:
                return
            risk = RiskLevel.DESTRUCTIVE.value
            if self.settings.dry_run:
                # DRY-RUN never starts an uninstaller - it shows and logs the
                # exact command each one would run, and the rows stay put.
                console.setVisible(True)
                console.appendPlainText(self._t("uninstaller_dry_run_notice"))
                for program in selected:
                    command = uninstaller.program_command(program) or ""
                    console.appendPlainText(f"[DRY-RUN] {program.name}: {command or self._t('uninstaller_no_command')}")
                    self._log_panel_action(
                        "_uninstaller", program.name, command, 0,
                        f"[DRY-RUN] {command}" if command else "[DRY-RUN] No uninstall command found for this program.",
                        True, risk,
                    )
                # Same next step as a real run: the leftover scan only reads
                # the registry, and cleaning honours DRY-RUN as well.
                show_orphan_cleanup()
                return
            # A quiet uninstall string runs with no uninstaller window at
            # all - the only chance to stop it is this dialog, so say so.
            silent_marker = self._t("uninstaller_confirm_silent_marker")
            warning_text = self._t("uninstaller_confirm_text").format(
                count=len(selected),
                programs=self._panel_confirm_list([
                    f"{p.name}  [{silent_marker}]" if p.quiet_uninstall_string else p.name for p in selected
                ]),
            )
            answer = QMessageBox.warning(
                self, self._t("uninstaller_confirm_title"), warning_text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                # Logged like a declined catalog action (_dispatch_action).
                for program in selected:
                    self._log_system_event(
                        "risk_declined", None, "Technician declined the uninstall confirmation - program not uninstalled.",
                        risk=risk, warned=True, warning_text=warning_text,
                        subject=f"_uninstaller/{program.name}", decision="declined",
                    )
                return
            selected_by_name = {program.name: program for program in selected}
            uninstall_button.setEnabled(False)
            select_all_btn.setEnabled(False)
            select_none_btn.setEnabled(False)
            console.setVisible(True)
            console.appendPlainText(self._t("uninstaller_running"))
            # On self, not the card: closeEvent and the app update's hand-off
            # guard must both know an uninstall is still running.
            runner = uninstaller.UninstallRunner(selected, parent=card)
            self._uninstall_runner = runner

            def on_program_finished(name: str, ok: bool, output: str) -> None:
                # Recorded before any widget is touched, so a console error
                # can never leave a real uninstall out of the log.
                program = selected_by_name.get(name)
                command = (uninstaller.program_command(program) or "") if program is not None else ""
                self._log_panel_action(
                    "_uninstaller", name, command, 0 if ok else 1, output, False, risk, True, warning_text,
                )
                # An uninstall has no rollback - undo.ps1 lists it under
                # "NOT reversible" (a failed one too: it may have removed
                # part of the program before failing).
                # Not for a program with no uninstall command at all: nothing
                # ran, so there is nothing to call irreversible.
                if command:
                    irreversible = f"[{risk}] {self._t('category_uninstaller')}: {name}"
                    if not ok:
                        irreversible += " - exit 1"
                    self._irreversible_actions.append(irreversible)
                    self._write_undo_script()
                status = self._t("status_ok") if ok else self._t("status_failed")
                console.appendPlainText(f"[{status}] {name}")
                if output:
                    console.appendPlainText(output)

            def on_all_finished() -> None:
                uninstall_button.setEnabled(True)
                select_all_btn.setEnabled(True)
                select_none_btn.setEnabled(True)
                for program in selected:
                    row_checkboxes.pop(program.name, None)
                    row_widget = row_widgets.pop(program.name, None)
                    if row_widget is not None:
                        row_widget.setParent(None)
                        row_widget.deleteLater()
                show_orphan_cleanup()

            runner.program_finished.connect(on_program_finished)
            runner.all_finished.connect(on_all_finished)
            runner.start()

        uninstall_button.clicked.connect(lambda _checked=False: start_uninstall())
        return card

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
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))

    def _panel_confirm_list(self, lines: list[str], limit: int = 20) -> str:
        # Capped: a confirmation listing 150 programs grows taller than the
        # screen and pushes its own Yes/No buttons out of reach.
        shown = [f"• {line}" for line in lines[:limit]]
        if len(lines) > limit:
            shown.append(self._t("uninstaller_and_more").format(count=len(lines) - limit))
        return "\n".join(shown)

    def _on_dry_run_toggled(self, checked: bool) -> None:
        self.settings.dry_run = checked

    def _on_toggle_language(self) -> None:
        # ponytail: keyboard-only/screen-reader users lose their place if a
        # full UI rebuild silently resets category and focus - remember and
        # restore both so a language switch doesn't strand them at the top.
        saved_category_row = self.category_list.currentRow()
        saved_focused_action_id = next(
            (aid for aid, cb in self._action_checkboxes.items() if cb.hasFocus()), None
        )
        self.settings.language = "en" if self.settings.language == "sk" else "sk"
        old_central = self.centralWidget()
        self._action_checkboxes = {}
        self._build_ui()
        if old_central is not None:
            old_central.deleteLater()
        if 0 <= saved_category_row < self.category_list.count():
            self.category_list.setCurrentRow(saved_category_row)
        if saved_focused_action_id is not None:
            checkbox = self._action_checkboxes.get(saved_focused_action_id)
            if checkbox is not None:
                checkbox.setFocus()

    def _start_update_check(self) -> None:
        if not getattr(sys, "frozen", False):
            return
        self._update_check_runner = updater.UpdateCheckRunner(APP_VERSION, parent=self)
        self._update_check_runner.check_finished.connect(self._on_update_check_finished)
        self._update_check_runner.start()

    def _on_update_check_finished(self, info) -> None:
        # A local update started with the developer switch may already be
        # running when the release check comes back.
        if info is None or self._update_in_progress:
            return
        self._pending_update_info = info
        self.update_banner_label.setText(self._t("update_available_banner").format(version=info.version))
        self.update_banner.setVisible(True)

    def _on_update_dismiss_clicked(self) -> None:
        self._pending_update_info = None
        self.update_banner.setVisible(False)

    def _quit_app(self) -> None:
        # Route through close() (not QApplication.quit() directly) so
        # closeEvent's cancel/wait cleanup for every in-flight runner always
        # runs first - quit() bypasses closeEvent entirely.
        self.close()

    def _on_update_button_clicked(self) -> None:
        if self._batch_active or self._update_in_progress:
            return
        info = self._pending_update_info
        if info is None:
            return
        if self._reusable_stage() is not None:
            # Downloaded and verified already (the last hand-off failed or
            # was refused) - straight to the restart question.
            self._confirm_and_launch_update()
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_download").format(version=info.version),
        )
        if confirmed != QMessageBox.Yes:
            return
        self._start_update_download(info)

    def start_local_update(self, zip_path: Path, sha256: str) -> None:
        """Developer switch (--update-from-zip with PORTABLEFIX_DEV_UPDATE=1,
        see main.py): feeds a local release zip through the same verify,
        stage, confirm, hand-off and close steps as a downloaded one, so the
        real updater can be tested before a release is published."""
        if self._batch_active or self._update_in_progress:
            return
        zip_path = Path(zip_path)
        info = updater.UpdateInfo(version=f"{zip_path.name} (dev)", package_url=str(zip_path), sha256_url=None, notes="")
        self._pending_update_info = info
        self._staged_update = None
        self.update_banner.setVisible(True)
        self._start_update_download(info, local_zip=zip_path, local_sha256=sha256)

    def _begin_update_step(self, phase: str) -> None:
        self._update_in_progress = True
        self._update_phase = phase
        self._update_progress = (0, 0)
        self.update_banner_label.setText(self._t(_UPDATE_PHASE_KEYS[phase]))
        self.update_banner_label.setToolTip("")
        self.update_button.setEnabled(False)
        self.update_dismiss_button.setEnabled(False)
        self.progress_bar.setMaximum(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

    def _end_update_step(self) -> None:
        self._update_in_progress = False
        self._update_phase = None
        self.progress_bar.setVisible(False)
        self.update_button.setEnabled(True)
        self.update_dismiss_button.setEnabled(True)
        # A language toggle mid-update rebuilt the dashboard with Analyze
        # locked (_build_dashboard_card) - nothing else would unlock it.
        self.dashboard_analyze_button.setEnabled(not self._batch_start_blocked())

    def _discard_update_download(self) -> None:
        # The zip is gone once staged; the folder (or a zip that failed to
        # download, verify or stage) would otherwise pile up in %TEMP%.
        if self._update_download_dir is not None:
            shutil.rmtree(self._update_download_dir, ignore_errors=True)
            self._update_download_dir = None

    def _reusable_stage(self):
        staged = self._staged_update
        info = self._pending_update_info
        if staged is None or info is None or staged.version != info.version:
            return None
        if not (staged.stage_root / "App" / "PortableFix.exe").is_file():
            # Removed meanwhile (by the updater, or by hand) - stage again.
            self._staged_update = None
            return None
        return staged

    def _start_update_download(self, info, local_zip: Path | None = None, local_sha256: str = "") -> None:
        try:
            dest_dir = Path(tempfile.mkdtemp(prefix="PortableFixUpdate_"))
        except OSError as exc:
            self.update_banner_label.setText(self._t("update_download_failed"))
            self.update_banner_label.setToolTip(str(exc))
            return
        self._discard_update_download()
        self._update_download_dir = dest_dir
        self._begin_update_step("download")
        runner = updater.UpdateDownloadRunner(info, dest_dir, parent=self, local_zip=local_zip, local_sha256=local_sha256)
        self._update_download_runner = runner
        runner.download_finished.connect(self._on_update_download_finished)
        runner.progress.connect(self._on_update_download_progress)
        runner.start()

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        # Staging reports through here too. A late signal from a step that
        # already ended must not bring the hidden bar back.
        if not self._update_in_progress:
            return
        self._update_progress = (downloaded, total) if total > 0 else (0, 0)
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)
        else:
            # Server didn't send a usable Content-Length - indeterminate
            # (busy/marquee) beats a progress bar frozen at 0%.
            self.progress_bar.setMaximum(0)

    def _on_update_download_finished(self, zip_path, error: str) -> None:
        if self._closed:
            return
        info = self._pending_update_info
        if not zip_path or info is None:
            self._end_update_step()
            self._discard_update_download()
            self.update_banner_label.setText(self._t("update_download_failed"))
            # The technician's "why" (HTTP error, hash mismatch) without
            # pushing a raw exception text into the banner itself.
            self.update_banner_label.setToolTip(error)
            return
        install_dir = paths.get_base_dir()
        if not updater.is_writable(install_dir):
            self._end_update_step()
            self._discard_update_download()
            key = "update_needs_admin" if updater.needs_elevation_for_update(install_dir) else "update_not_writable"
            self.update_banner_label.setText(self._t(key))
            return
        self._staged_update = None
        self._begin_update_step("stage")
        runner = updater.UpdateStageRunner(Path(zip_path), install_dir, info.version, parent=self)
        self._update_stage_runner = runner
        runner.stage_finished.connect(self._on_update_stage_finished)
        runner.progress.connect(self._on_update_download_progress)
        runner.start()

    def _on_update_stage_finished(self, staged, error: str) -> None:
        if self._closed:
            return
        self._discard_update_download()
        if staged is None:
            self._end_update_step()
            self.update_banner_label.setText(self._t("update_stage_failed"))
            self.update_banner_label.setToolTip(error)
            QMessageBox.warning(
                self, self._t("app_title"),
                self._t("update_stage_failed_detail").format(detail=error, url=updater.RELEASES_PAGE_URL),
            )
            return
        self._staged_update = staged
        self._confirm_and_launch_update()

    def _long_running_tasks(self) -> list[str]:
        """What would keep this process alive long after close() - the
        updater waits for it to exit, and a close that takes minutes (winget
        alone allows 5) would outlast that wait. Named for the refusal."""
        tasks = []
        if self._batch_active:
            tasks.append(self._t("update_busy_batch"))
        if self._report_runner is not None:
            tasks.append(self._t("update_busy_report"))
        if self._speed_test_busy or _thread_running(self._speed_test_runner):
            tasks.append(self._t("update_busy_speed_test"))
        # Not the winget scan: it only reads, starts on its own with the
        # window (so it ran on every early "Update" click), and closeEvent
        # waits at most 65 s for it - well inside the updater's 600 s.
        if _thread_running(self._winget_update_runner):
            tasks.append(self._t("update_busy_winget_update"))
        if _thread_running(self._pending_restore_point_runner):
            tasks.append(self._t("update_busy_restore_point"))
        if _thread_running(self._uninstall_runner):
            tasks.append(self._t("update_busy_uninstall"))
        return tasks

    def _show_update_available(self) -> None:
        info = self._pending_update_info
        if info is not None:
            self.update_banner_label.setText(self._t("update_available_banner").format(version=info.version))

    def _confirm_and_launch_update(self) -> None:
        self._end_update_step()
        info = self._pending_update_info
        staged = self._staged_update
        if info is None or staged is None:
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_restart").format(version=info.version),
        )
        if confirmed != QMessageBox.Yes or self._closed:
            self._show_update_available()
            return
        # Checked after the question: its nested event loop keeps timers
        # (the winget auto-check) running.
        busy = self._long_running_tasks()
        if busy:
            self._show_update_available()
            QMessageBox.warning(
                self, self._t("app_title"),
                self._t("update_busy_tasks").format(tasks="\n".join(f"• {task}" for task in busy)),
            )
            return
        self._begin_update_step("launch")
        runner = updater.UpdateLaunchRunner(staged, paths.get_base_dir(), parent=self)
        self._update_launch_runner = runner
        runner.launch_finished.connect(self._on_update_launch_finished)
        runner.start()

    def _on_update_launch_finished(self, result) -> None:
        if self._closed:
            return
        if result.ok:
            # The updater holds this process's handle and starts replacing
            # App\ the moment it exits - no questions, no lingering.
            self._closing_for_update = True
            self._quit_app()
            return
        self._end_update_step()
        self.update_banner_label.setText(self._t("update_apply_failed"))
        if result.reason == updater.REASON_CANCELLED:
            return
        detail = (result.detail or "").strip()
        if len(detail) > 800:
            # The full launch-log tail is in the diagnostics file.
            detail = "..." + detail[-800:]
        log_dir = result.log_dir or updater.update_log_dir() or "%TEMP%\\PortableFixUpdate"
        QMessageBox.warning(
            self, self._t("app_title"),
            self._t("update_launch_failed_detail").format(
                reason=self._t(f"update_reason_{result.reason}"),
                detail=detail,
                log_dir=log_dir,
                url=updater.RELEASES_PAGE_URL,
            ),
        )

    def _on_restart_as_admin(self) -> None:
        # In a frozen build sys.executable IS the app - no args needed. In
        # dev mode it's python.exe, which needs the script path re-passed or
        # elevating just opens a bare interpreter instead of restarting the app.
        args = [] if getattr(sys, "frozen", False) else list(sys.argv)
        # The elevated copy waits for this process (and the onefile
        # bootloader, which still maps the exe) to exit before it takes the
        # single-instance mutex - otherwise it lost that race and quit.
        wait_pids = [os.getpid()]
        parent_pid = update_swap.onefile_parent_pid()
        if parent_pid:
            wait_pids.append(parent_pid)
        result = elevation.relaunch_as_admin(sys.executable, args, wait_pids=wait_pids)
        if result <= 32:
            QMessageBox.warning(
                self,
                self._t("app_title"),
                self._t("elevation_failed"),
            )
        else:
            self.close()

    def _find_action(self, action_id: str) -> tuple[ModuleDef, ActionDef]:
        for module in self.modules:
            for action in module.actions:
                if action.id == action_id:
                    return module, action
        raise KeyError(action_id)

    def _skip_high_risk_actions_in_queue(self) -> None:
        def _is_high_risk(action_id: str) -> bool:
            module, action = self._find_action(action_id)
            return action.risk == RiskLevel.DESTRUCTIVE or module.category in (
                ModuleCategory.REPAIR,
                ModuleCategory.SECURITY,
                ModuleCategory.DRIVER_UPDATES,
                ModuleCategory.WINGET,
            )

        self._queue = [aid for aid in self._queue if not _is_high_risk(aid)]

    def _take_snapshot(self) -> dict:
        # GUI thread, at batch start and end - snapshot.py keeps it bounded
        # (time-budgeted folder walks, no process spawns) and never raises.
        return snapshot.take_snapshot(disk_usage=shutil.disk_usage)

    def _on_cancel_clicked(self) -> None:
        self._cancel_requested = True
        self._queue = []
        self.cancel_button.setEnabled(False)
        if self._runner is not None:
            self._runner.cancel()

    def _action_accessible_name(self, action: ActionDef, status_text: str = "") -> str:
        # "riziko"/"risk" translated - Narrator reads the whole name in the
        # UI language, and a lone English word mid-sentence is jarring.
        name = f"{action.label(self.settings.language)} — {self._t('a11y_risk')}: {action.risk.value}"
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

    def _batch_start_blocked(self) -> bool:
        # A batch is running, the previous batch's report is still being
        # written (see _run_next) or an update is downloading.
        return self._batch_active or self._update_in_progress or self._report_runner is not None

    def run_selected_actions(self) -> None:
        # Mid-batch re-entry (the dashboard's Analyze button, a direct call)
        # would overwrite _queue, reset _cancel_requested - un-cancelling a
        # batch whose Cancel was clicked during its restore point - and start
        # a second ActionRunner alongside the running one.
        if self._batch_start_blocked():
            return
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._queue_total = len(self._queue)
        self._restore_point_attempted = False
        self._batch_results = []
        self._recommended_action_ids = set()
        self._summary_dialog = None
        self._cancel_requested = False
        for action_id in self._queue:
            self._set_action_status(action_id, "", "")
        if self._queue:
            self._batch_active = True
            self._snapshot_before = self._take_snapshot()
            self.run_button.setEnabled(False)
            self.dashboard_analyze_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.language_button.setEnabled(False)
            self.progress_bar.setMaximum(self._queue_total)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
        self._run_next()

    def _on_report_ready(self, html_path: Path | None, write_failed: bool) -> None:
        self._report_runner = None
        if not self._closed:
            self.run_button.setEnabled(True)
            self.dashboard_analyze_button.setEnabled(True)
            self.language_button.setEnabled(True)
            if write_failed:
                self.console.appendPlainText(self._t("disk_write_failed"))
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
                report_kwargs = {"job": self._job_info(), "storage_fallback": self._storage_fallback}
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
                self.settings.dry_run,
                self.run_id,
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

        needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
            ModuleCategory.REPAIR,
            ModuleCategory.SECURITY,
            ModuleCategory.DRIVER_UPDATES,
            ModuleCategory.WINGET,
        )
        if needs_restore_point and not self._restore_point_attempted and not self.settings.dry_run:
            self._restore_point_attempted = True
            self._write_undo_script()
            rp_runner = restore_point.RestorePointRunner(f"PortableFix {self.run_id}", parent=self)
            rp_runner.result_ready.connect(
                lambda success, detail, info, m=module, a=action: self._on_restore_point_checked(success, detail, m, a, info)
            )
            self._pending_restore_point_runner = rp_runner
            rp_runner.start()
            return

        self._dispatch_action(module, action)

    def _on_restore_point_checked(
        self, success: bool, detail: str, module: ModuleDef, action: ActionDef, info: dict | None = None,
    ) -> None:
        # info: the created point's identity (restore_point.parse_restore_point_output),
        # {} / None when it could not be looked up.
        sequence = (info or {}).get("sequence_number") if success else None
        output = "System Restore Point created." if success else (
            f"System Restore Point creation failed: {detail}" if detail else "System Restore Point creation failed."
        )
        if sequence is not None:
            output = f"System Restore Point created (#{sequence})."
        subject = f"{module.module_id}/{action.id}"
        self._log_system_event(
            "restore_point", 0 if success else 1, output,
            command=f"Checkpoint-Computer -Description 'PortableFix {self.run_id}'",
            subject=subject, restore_point_sequence=sequence,
            restore_point_created=(info or {}).get("creation_time", "") if success else "",
        )
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
        self._dispatch_action(module, action)

    def _log_system_event(self, action_id: str, exit_code: int | None, output: str, **fields) -> None:
        # Safety facts about the run (restore point, the technician's answers
        # to safety prompts) go in the same audit log as the actions, under
        # the "_system" module report.py lists in its safety section.
        entry = make_entry(
            "_system", action_id, fields.pop("command", ""), exit_code, output,
            self.settings.dry_run, self.run_id, elevated=self.is_admin, **fields,
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))

    def _dispatch_action(self, module: ModuleDef, action: ActionDef) -> None:
        if self._closed:
            # Never start an action (or pop its confirmation) after the
            # window is gone - it would run unlogged and outlive the app.
            return
        warning_text = ""
        confirmed = QMessageBox.Yes
        if action.risk == RiskLevel.DESTRUCTIVE:
            warning_text = f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_destructive_action')}"
            confirmed = QMessageBox.warning(
                self,
                self._t("app_title"),
                warning_text,
                QMessageBox.Yes | QMessageBox.No,
            )
        elif action.risk != RiskLevel.SAFE:
            warning_text = f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_risky_action')}"
            confirmed = QMessageBox.question(
                self,
                self._t("app_title"),
                warning_text,
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

        app_dir = paths.get_base_dir()
        temp_protect = paths.compute_temp_protected_child(app_dir)
        windir_temp_protect = paths.compute_windir_temp_protected_child(app_dir)
        if action.id == "user_temp" and temp_protect is not None and temp_protect == paths.resolve_temp_root():
            # The app's own root IS %TEMP% itself, or %TEMP% is redirected
            # via a junction/symlink so a resolved-path comparison could
            # never match PowerShell's unresolved view of it - either way
            # there's no single safe child to exclude, so refuse to run
            # this action at all rather than risk wiping the app out from
            # under itself.
            QMessageBox.warning(self, self._t("app_title"), self._t("user_temp_blocked_app_is_temp_root"))
            self._run_next()
            return
        if (
            action.id == "system_temp"
            and windir_temp_protect is not None
            and windir_temp_protect == paths.resolve_windir_temp_root()
        ):
            # Same refusal, mirrored for %WINDIR%\Temp.
            QMessageBox.warning(self, self._t("app_title"), self._t("system_temp_blocked_app_is_temp_root"))
            self._run_next()
            return

        if action.id == "system_temp":
            action_temp_protect = windir_temp_protect
        else:
            action_temp_protect = temp_protect

        if self.settings.dry_run and action.preview_command:
            plan = build_execution_plan(action.preview_command, dry_run=False, temp_protect=action_temp_protect)
        else:
            plan = build_execution_plan(action.command, self.settings.dry_run, temp_protect=action_temp_protect)

        self._set_action_status(action.id, "running", self._t("status_running"))
        self._action_start_times[action.id] = time.monotonic()
        runner = ActionRunner(
            plan, parent=self,
            inactivity_timeout_sec=action.inactivity_timeout_sec,
            hard_cap_sec=action.hard_cap_sec,
        )
        self._runner = runner
        runner.output_line.connect(self.console.appendPlainText)
        runner.finished_with_code.connect(
            lambda code, m=module.module_id, a=action.id, c=action.command, r=runner, w=warning_text: self._on_action_finished(
                m, a, c, code, r, w
            )
        )
        runner.start()

    def _on_action_finished(
        self, module_id: str, action_id: str, command: str, exit_code: int, runner: ActionRunner,
        warning_text: str = "",
    ) -> None:
        output = "\n".join(runner.captured_output)
        _, action = self._find_action(action_id)
        # warned/warning_text come from the dialog _dispatch_action actually
        # showed and the technician accepted, not re-derived from the risk.
        entry = make_entry(
            module_id, action_id, command, exit_code, output, self.settings.dry_run, self.run_id,
            risk=action.risk.value, warned=bool(warning_text), elevated=self.is_admin,
            warning_text=warning_text,
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))
        self._batch_results.append((action_id, exit_code))
        if action.problem_keywords and action.recommended_action_ids:
            output_lower = output.lower()
            if any(keyword.lower() in output_lower for keyword in action.problem_keywords):
                self._recommended_action_ids.update(
                    rid for rid in action.recommended_action_ids if rid in self._action_checkboxes
                )
        elapsed = time.monotonic() - self._action_start_times.pop(action_id, time.monotonic())
        status_text = f"{self._t('status_ok') if exit_code == 0 else self._t('status_failed')} ({elapsed:.1f}s)"
        self._set_action_status(action_id, "ok" if exit_code == 0 else "fail", status_text)
        if not self.settings.dry_run:
            if exit_code == 0 and action.undo_command:
                self._undo_steps.append(action.undo_command)
                self._write_undo_script()
            elif not action.undo_command and action.risk != RiskLevel.SAFE:
                # A failed run may still have changed part of the system, so
                # it is listed too - with its exit code, not hidden.
                entry_text = f"[{action.risk.value}] {action.label(self.settings.language)} ({action.id})"
                if exit_code != 0:
                    entry_text += f" - exit {exit_code}"
                self._irreversible_actions.append(entry_text)
                self._write_undo_script()
        self._run_next()

    def _write_undo_script(self) -> None:
        # LIFO order puts the newest step at the top, so each change is a
        # rewrite - but only an actual change: every later batch's
        # pre-restore-point write used to rewrite an identical file. The
        # exists() check keeps a deleted Backups/ from staying missing.
        state = (len(self._undo_steps), len(self._irreversible_actions))
        if (
            state == self._undo_written_state
            and self._undo_script_path is not None
            and self._undo_script_path.exists()
        ):
            return
        try:
            self._undo_script_path = undo.create_undo_script(
                self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)),
                irreversible=self._irreversible_actions,
            )
            self._undo_written_state = state
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))

    def _build_sysinfo_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("actionCard")
        panel.setMinimumWidth(460)
        panel.setMaximumWidth(640)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        self._sysinfo_labels: dict[str, QLabel] = {}

        def add_row(key: str, label_key: str, tooltip: str | None = None, long: bool = False) -> None:
            caption = QLabel(self._t(label_key))
            caption.setObjectName("selectionScope")
            value = QLabel(self._t("sysinfo_loading"))
            # These labels show text sourced from hardware/OS reports (GPU
            # name, VPN adapter name, disk health string, ...) - an
            # unprivileged local process can name a device or VPN profile
            # almost anything, so force plain text rather than letting Qt's
            # auto-detection ever render it as rich text.
            value.setTextFormat(Qt.TextFormat.PlainText)
            if tooltip:
                caption.setToolTip(tooltip)
                value.setToolTip(tooltip)
            self._sysinfo_labels[key] = value
            # "long" fields (full CPU/GPU model names, disk health list,
            # VPN status) must stay on one line rather than wrap - the
            # panel is wide enough for that now (400-600px), unlike the
            # fixed 345px it used to be capped at.
            value.setWordWrap(not long)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(caption)
            row.addStretch(1)
            row.addWidget(value)
            layout.addLayout(row)

        add_row("os", "sysinfo_os", long=True)
        add_row("uptime", "sysinfo_uptime")
        add_row("cpu_name", "sysinfo_cpu", long=True)
        add_row("cpu_load", "sysinfo_cpu_load")
        add_row("cpu_clock", "sysinfo_cpu_clock", tooltip=self._t("sysinfo_cpu_clock_hint"))
        add_row("ram", "sysinfo_ram")
        add_row("ram_speed", "sysinfo_ram_speed")
        add_row("battery", "sysinfo_battery")
        add_row("disk_health", "sysinfo_disk_health", long=True)
        add_row("gpu_name", "sysinfo_gpu", long=True)
        add_row("gpu_load", "sysinfo_gpu_load")
        add_row("gpu_temp", "sysinfo_gpu_temp")
        add_row("gpu_clock", "sysinfo_gpu_clock")
        add_row("gpu_vram", "sysinfo_gpu_vram")
        add_row("ip", "sysinfo_ip")
        add_row("ping", "sysinfo_ping")
        add_row("vpn", "sysinfo_vpn", long=True)

        self.speed_test_button = self._make_selection_button(
            self._t("sysinfo_speed_test_button"), self._on_speed_test_clicked
        )
        self.speed_test_button.setObjectName("panelBtn")
        layout.addWidget(self.speed_test_button)
        self.speed_test_result_label = QLabel("")
        self.speed_test_result_label.setWordWrap(True)
        layout.addWidget(self.speed_test_result_label)

        export_diag_button = self._make_selection_button(
            self._t("export_diagnostics_button"), self._on_export_diagnostics_clicked
        )
        export_diag_button.setObjectName("panelBtn")
        layout.addWidget(export_diag_button)

        report_bug_button = self._make_selection_button(
            self._t("report_bug_button"), self._on_report_bug_clicked
        )
        report_bug_button.setObjectName("panelBtn")
        layout.addWidget(report_bug_button)
        layout.addStretch(1)
        return panel

    def _on_export_diagnostics_clicked(self) -> None:
        default_name = f"PortableFix-diagnostics-{self.run_id}.zip"
        dest, _ = QFileDialog.getSaveFileName(
            self, self._t("export_diagnostics_button"), default_name, "Zip (*.zip)"
        )
        if not dest:
            return
        try:
            diagnostics.export_diagnostics_zip(self.state_dir, Path(dest))
        except OSError as exc:
            QMessageBox.critical(self, self._t("app_title"), f"{self._t('export_diagnostics_failed')}\n{exc}")
            return
        QMessageBox.information(self, self._t("app_title"), self._t("export_diagnostics_done"))

    def _on_report_bug_clicked(self) -> None:
        QDesktopServices.openUrl(QUrl(diagnostics.build_bug_report_url(APP_VERSION)))

    def _start_sysinfo_polling(self) -> None:
        self._static_info_runner = sysinfo.StaticInfoRunner(parent=self)
        self._static_info_runner.static_info_ready.connect(self._on_static_info_ready)
        self._static_info_runner.start()

        self._sysinfo_timer = QTimer(self)
        self._sysinfo_timer.timeout.connect(self._on_sysinfo_tick)
        self._sysinfo_timer.start(2000)
        self._on_sysinfo_tick()

        self._hw_sensor_timer = QTimer(self)
        self._hw_sensor_timer.timeout.connect(self._on_hw_sensor_tick)
        self._hw_sensor_timer.start(2500)
        self._on_hw_sensor_tick()

        self._ping_timer = QTimer(self)
        self._ping_timer.timeout.connect(self._on_ping_tick)
        self._ping_timer.start(4000)
        self._on_ping_tick()

        # VPN state doesn't change on a 4s cadence like ping does, and unlike
        # ping.exe, checking it spawns a full powershell.exe - a much longer
        # interval avoids needless CPU/battery cost from a process spawn
        # every few seconds for the whole session.
        self._vpn_timer = QTimer(self)
        self._vpn_timer.timeout.connect(self._on_vpn_tick)
        self._vpn_timer.start(60_000)
        self._on_vpn_tick()

    def _on_static_info_ready(self, info: sysinfo.StaticInfo) -> None:
        if self._closed:
            return
        self._sysinfo_labels["os"].setText(info.os_name)
        self._sysinfo_labels["cpu_name"].setText(f"{info.cpu_name} ({info.cpu_cores} cores)")
        self._sysinfo_labels["ram_speed"].setText(
            f"{info.ram_speed_mhz} MHz" if info.ram_speed_mhz else self._t("sysinfo_na")
        )
        self._sysinfo_labels["disk_health"].setText(info.disk_health_summary or self._t("sysinfo_na"))
        self._sysinfo_labels["ip"].setText(info.local_ip)

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        total_minutes = int(seconds // 60)
        days, rem_minutes = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem_minutes, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def _on_sysinfo_tick(self) -> None:
        if self._closed:
            return
        cpu_load = self._cpu_load_sampler.sample()
        self._sysinfo_labels["cpu_load"].setText(f"{cpu_load:.0f}%" if cpu_load is not None else self._t("sysinfo_na"))
        used_gb, total_gb = sysinfo.get_ram_usage_gb()
        self._sysinfo_labels["ram"].setText(f"{used_gb} / {total_gb} GB")
        self._sysinfo_labels["uptime"].setText(self._format_uptime(sysinfo.get_uptime_seconds()))
        battery = sysinfo.get_battery_percent()
        self._sysinfo_labels["battery"].setText(f"{battery}%" if battery is not None else self._t("sysinfo_na"))

    def _on_hw_sensor_tick(self) -> None:
        if self._closed or self._hw_sensor_busy:
            return
        self._hw_sensor_busy = True
        self._hw_sensor_runner = sysinfo.HardwareSensorRunner(self.assets_dir, parent=self)
        self._hw_sensor_runner.sensors_ready.connect(self._on_hw_sensors_ready)
        self._hw_sensor_runner.start()

    def _on_hw_sensors_ready(self, hw: dict) -> None:
        self._hw_sensor_busy = False
        if self._closed:
            return
        self._sysinfo_labels["cpu_clock"].setText(
            f"{hw['cpu_clock_mhz']:.0f} MHz" if hw["cpu_clock_mhz"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_name"].setText(hw["gpu_name"] or self._t("sysinfo_na"))
        self._sysinfo_labels["gpu_load"].setText(
            f"{hw['gpu_load_percent']:.0f}%" if hw["gpu_load_percent"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_temp"].setText(
            f"{hw['gpu_temp_c']:.0f}°C" if hw["gpu_temp_c"] is not None else self._t("sysinfo_na")
        )
        self._sysinfo_labels["gpu_clock"].setText(
            f"{hw['gpu_clock_mhz']:.0f} MHz" if hw["gpu_clock_mhz"] is not None else self._t("sysinfo_na")
        )
        if hw["gpu_vram_used_gb"] is not None and hw["gpu_vram_total_gb"] is not None:
            self._sysinfo_labels["gpu_vram"].setText(f"{hw['gpu_vram_used_gb']} / {hw['gpu_vram_total_gb']} GB")
        else:
            self._sysinfo_labels["gpu_vram"].setText(self._t("sysinfo_na"))

    def _on_ping_tick(self) -> None:
        if self._closed or self._ping_busy:
            return
        self._ping_busy = True
        self._ping_runner = sysinfo.PingRunner(parent=self)
        self._ping_runner.ping_ready.connect(self._on_ping_ready)
        self._ping_runner.start()

    def _on_ping_ready(self, latency_ms: float | None) -> None:
        self._ping_busy = False
        if self._closed:
            return
        self._sysinfo_labels["ping"].setText(f"{latency_ms:.0f} ms" if latency_ms is not None else self._t("sysinfo_na"))

    def _on_vpn_tick(self) -> None:
        if self._closed or self._vpn_busy:
            return
        self._vpn_busy = True
        self._vpn_runner = sysinfo.VpnStatusRunner(parent=self)
        self._vpn_runner.vpn_status_ready.connect(self._on_vpn_status_ready)
        self._vpn_runner.start()

    def _on_vpn_status_ready(self, name: str | None) -> None:
        self._vpn_busy = False
        if self._closed:
            return
        if name is None:
            text = self._t("sysinfo_na")
        elif name == "":
            text = self._t("sysinfo_vpn_off")
        else:
            text = self._t("sysinfo_vpn_connected").format(name=name)
        self._sysinfo_labels["vpn"].setText(text)

    def _on_speed_test_clicked(self) -> None:
        if self._speed_test_busy:
            return
        self._speed_test_busy = True
        self.speed_test_button.setEnabled(False)
        self._speed_test_stage_values = {"download": None, "upload": None, "ping": None}
        self._speed_test_stage_done: set[str] = set()
        self._render_speed_test_result()
        self._speed_test_runner = sysinfo.SpeedTestRunner(parent=self)
        self._speed_test_runner.stage_ready.connect(self._on_speed_test_stage_ready)
        self._speed_test_runner.all_finished.connect(self._on_speed_test_all_finished)
        self._speed_test_runner.start()

    def _on_speed_test_stage_ready(self, stage: str, value: object) -> None:
        if self._closed:
            return
        self._speed_test_stage_values[stage] = value
        self._speed_test_stage_done.add(stage)
        self._render_speed_test_result()

    def _render_speed_test_result(self) -> None:
        stage_labels = {
            "download": self._t("sysinfo_speed_download"),
            "upload": self._t("sysinfo_speed_upload"),
            "ping": self._t("sysinfo_ping"),
        }
        lines = []
        for stage in ("download", "upload", "ping"):
            value = self._speed_test_stage_values.get(stage)
            if stage not in self._speed_test_stage_done:
                text = self._t("sysinfo_speed_testing")
            elif value is None:
                text = self._t("sysinfo_na")
            elif stage == "ping":
                text = f"{value:.0f} ms"
            else:
                text = f"{value:.1f} Mbit/s"
            lines.append(f"{stage_labels[stage]}: {text}")
        self.speed_test_result_label.setText("\n".join(lines))

    def _on_speed_test_all_finished(self) -> None:
        self._speed_test_busy = False
        if self._closed:
            return
        self.speed_test_button.setEnabled(True)
