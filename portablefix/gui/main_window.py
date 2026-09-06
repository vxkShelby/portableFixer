import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from . import style
from .. import elevation, i18n, paths, report, restore_point, sysinfo, undo, updater
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..module_engine import load_all_modules
from ..settings import Settings
from ..version import APP_VERSION

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
        self._batch_active = False
        self._snapshot_before: dict = {}
        self._undo_steps: list[str] = []
        self._batch_results: list[tuple[str, int]] = []
        self._summary_dialog: QDialog | None = None
        self._closed = False
        self._cancel_requested = False
        self._pending_update_info = None
        self._update_check_runner = None
        self._update_download_runner = None
        self._update_in_progress = False
        self._cpu_load_sampler = sysinfo.CpuLoadSampler()
        self._static_info_runner = None
        self._ping_runner = None
        self._vpn_runner = None
        self._speed_test_runner = None
        self._hw_sensor_runner = None
        self._ping_busy = False
        self._vpn_busy = False
        self._speed_test_busy = False
        self._hw_sensor_busy = False
        self._sysinfo_timer = None
        self._hw_sensor_timer = None
        self._ping_timer = None
        self._vpn_timer = None
        self._build_ui()
        self._start_update_check()
        self._start_sysinfo_polling()

    def closeEvent(self, event) -> None:
        # ponytail: plain-Python flag (safe even if a delayed cross-thread
        # callback fires after the C++ widgets are gone) so async batch-completion
        # handlers know not to touch self.run_button once the window is closing.
        self._closed = True
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
        # Destroying self while a runner's native thread is still mid-flight
        # is a use-after-free risk - wait for each to actually finish first.
        # A one-shot runner may already be auto-deleted by Qt once its thread
        # ended; that RuntimeError just means there's nothing left to wait for.
        # Neither the speed test nor the update download can be cancelled
        # mid-flight (both make one blocking, uninterruptible network call),
        # so their wait must cover their real worst-case duration - a short
        # timeout here would let closeEvent proceed while that QThread is
        # still alive, which is the exact crash this loop exists to prevent.
        quick_runners = (
            self._static_info_runner,
            self._hw_sensor_runner,
            self._ping_runner,
            self._vpn_runner,
            self._runner,
            self._update_check_runner,
            self._pending_restore_point_runner,
        )
        slow_runners = (
            (self._speed_test_runner, 25_000),
            (self._update_download_runner, updater.DOWNLOAD_TIMEOUT_SEC * 1000 + 5_000),
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
        super().closeEvent(event)

    def _t(self, key: str) -> str:
        return i18n.translate(key, self.settings.language)

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{self._t('app_title')} v{APP_VERSION}")
        self.setStyleSheet(style.STYLE)
        self.resize(1000, 700)
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        top_bar = QHBoxLayout()
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
        }
        self._categories_order: list[ModuleCategory] = []
        for module in self.modules:
            if module.category not in self._categories_order:
                self._categories_order.append(module.category)

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
        self.category_list.setFixedWidth(190)
        for category in self._categories_order:
            self.category_list.addItem(QListWidgetItem(self._t(category_i18n_keys[category])))
        for risk in self._risk_tabs_order:
            self.category_list.addItem(QListWidgetItem(f"{self._t('risk_tab_prefix')} {risk.value}"))
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
        preset_row.addWidget(self.search_box)
        center_layout.addLayout(preset_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 6, 0)
        scroll_layout.setSpacing(8)

        self._category_groups: dict[ModuleCategory, QWidget] = {}
        self._category_action_ids: dict[ModuleCategory, list[str]] = {}
        self._category_select_buttons: dict[ModuleCategory, tuple[QPushButton, QPushButton, QPushButton]] = {}
        for category in self._categories_order:
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            heading_row = QHBoxLayout()
            heading_row.setSpacing(6)
            heading = QLabel(self._t(category_i18n_keys[category]))
            heading.setObjectName("cardHeading")
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            cat_all = self._make_selection_button(
                self._t("select_all"), lambda c=category: self._apply_selection(self._category_action_ids[c], "all")
            )
            heading_row.addWidget(cat_all)
            cat_safe = self._make_selection_button(
                self._t("select_safe_only"),
                lambda c=category: self._apply_selection(self._category_action_ids[c], RiskLevel.SAFE.value),
            )
            heading_row.addWidget(cat_safe)
            cat_none = self._make_selection_button(
                self._t("select_none"), lambda c=category: self._apply_selection(self._category_action_ids[c], "none")
            )
            heading_row.addWidget(cat_none)
            self._category_select_buttons[category] = (cat_all, cat_safe, cat_none)
            card_layout.addLayout(heading_row)
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
        body_layout.addLayout(center_layout, 1)
        body_layout.addWidget(self._build_sysinfo_panel())
        root_layout.addLayout(body_layout, 3)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        root_layout.addWidget(self.console, 1)

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
                self.update_banner_label.setText(self._t("update_downloading"))
                self.update_button.setEnabled(False)
                self.update_dismiss_button.setEnabled(False)
            else:
                self.update_banner_label.setText(
                    self._t("update_available_banner").format(version=self._pending_update_info.version)
                )
            self.update_banner.setVisible(True)

    def _on_category_changed(self, row: int) -> None:
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

        # A match hiding in a category/risk tab the user isn't currently
        # looking at previously looked identical to "no such action" - tell
        # them explicitly instead of leaving an empty-looking list.
        current_row = self.category_list.currentRow()
        if 0 <= current_row < len(self._categories_order):
            current_view_ids = set(self._category_action_ids[self._categories_order[current_row]])
        else:
            risk_index = current_row - len(self._categories_order)
            if 0 <= risk_index < len(self._risk_tabs_order):
                current_view_ids = set(self._risk_action_ids[self._risk_tabs_order[risk_index]])
            else:
                current_view_ids = set()

        if not matched_ids:
            self.statusBar().showMessage(self._t("search_no_matches").format(query=text.strip()))
        elif not (current_view_ids & matched_ids):
            self.statusBar().showMessage(self._t("search_matches_elsewhere").format(count=len(matched_ids)))
        else:
            self._update_status_bar()

    def _apply_preset(self, preset_key: str) -> None:
        wanted = [aid for aid in PRESETS[preset_key] if aid in self._action_checkboxes]
        self._apply_selection(list(self._action_checkboxes), "none")
        self._apply_selection(wanted, "all")
        # Exclusive QButtonGroup membership already unchecks the other two
        # preset buttons on a real click; set this one explicitly too so the
        # highlight is correct even when _apply_preset is called directly
        # (e.g. from tests) for a key with no matching button.
        button = self._preset_buttons.get(preset_key)
        if button is not None:
            button.setChecked(True)

    def _update_status_bar(self) -> None:
        if self._batch_active:
            return
        selected = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
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
        for action_id in action_ids:
            if mode == "all":
                checked = True
            elif mode == "none":
                checked = False
            else:
                _, action = self._find_action(action_id)
                checked = action.risk.value == mode
            self._action_checkboxes[action_id].setChecked(checked)

    def _show_batch_summary(self, html_path: Path) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(self._t("batch_results_title"))
        dialog.setStyleSheet(style.STYLE)
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

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        open_button = QPushButton(self._t("open_report"))
        open_button.setObjectName("runButton")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
        )
        button_row.addWidget(open_button)
        layout.addLayout(button_row)

        dialog.show()
        self._summary_dialog = dialog

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
        if info is None:
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
        if self._batch_active:
            return
        if self._pending_update_info is None:
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_download").format(version=self._pending_update_info.version),
        )
        if confirmed != QMessageBox.Yes:
            return
        dest_dir = Path(tempfile.mkdtemp(prefix="PortableFixUpdate_"))
        self.update_banner_label.setText(self._t("update_downloading"))
        self.update_button.setEnabled(False)
        self.update_dismiss_button.setEnabled(False)
        self._update_in_progress = True
        self.progress_bar.setMaximum(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._update_download_runner = updater.UpdateDownloadRunner(self._pending_update_info, dest_dir, parent=self)
        self._update_download_runner.download_finished.connect(self._on_update_download_finished)
        self._update_download_runner.progress.connect(self._on_update_download_progress)
        self._update_download_runner.start()

    def _on_update_download_progress(self, downloaded: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(downloaded)
        else:
            # Server didn't send a usable Content-Length - indeterminate
            # (busy/marquee) beats a progress bar frozen at 0%.
            self.progress_bar.setMaximum(0)

    def _on_update_download_finished(self, zip_path, error: str) -> None:
        info = self._pending_update_info
        self._update_in_progress = False
        self.progress_bar.setVisible(False)
        self.update_button.setEnabled(True)
        self.update_dismiss_button.setEnabled(True)
        if not zip_path:
            self.update_banner_label.setText(self._t("update_download_failed"))
            return
        install_dir = paths.get_base_dir()
        if not updater.is_writable(install_dir):
            self.update_banner_label.setText(self._t("update_not_writable"))
            return
        confirmed = QMessageBox.question(
            self, self._t("app_title"),
            self._t("update_confirm_restart").format(version=info.version),
        )
        if confirmed != QMessageBox.Yes:
            self.update_banner_label.setText(
                self._t("update_available_banner").format(version=info.version)
            )
            return
        if not updater.apply_update(zip_path, install_dir):
            self.update_banner_label.setText(self._t("update_apply_failed"))
            return
        self._quit_app()

    def _on_restart_as_admin(self) -> None:
        # In a frozen build sys.executable IS the app - no args needed. In
        # dev mode it's python.exe, which needs the script path re-passed or
        # elevating just opens a bare interpreter instead of restarting the app.
        args = None if getattr(sys, "frozen", False) else sys.argv
        result = elevation.relaunch_as_admin(sys.executable, args)
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
            )

        self._queue = [aid for aid in self._queue if not _is_high_risk(aid)]

    def _take_snapshot(self) -> dict:
        system_drive = os.environ.get("SystemDrive", "C:") + "\\"
        usage = shutil.disk_usage(system_drive)
        return {
            "free_gb": round(usage.free / (1024**3), 2),
            "total_gb": round(usage.total / (1024**3), 2),
        }

    def _on_cancel_clicked(self) -> None:
        self._cancel_requested = True
        self._queue = []
        self.cancel_button.setEnabled(False)
        if self._runner is not None:
            self._runner.cancel()

    def _action_accessible_name(self, action: ActionDef, status_text: str = "") -> str:
        name = f"{action.label(self.settings.language)} — risk: {action.risk.value}"
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

    def run_selected_actions(self) -> None:
        if self._update_in_progress:
            return
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._queue_total = len(self._queue)
        self._restore_point_attempted = False
        self._batch_results = []
        self._summary_dialog = None
        self._cancel_requested = False
        for action_id in self._queue:
            self._set_action_status(action_id, "", "")
        if self._queue:
            self._batch_active = True
            self._snapshot_before = self._take_snapshot()
            self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.language_button.setEnabled(False)
            self.progress_bar.setMaximum(self._queue_total)
            self.progress_bar.setValue(0)
            self.progress_bar.setVisible(True)
        self._run_next()

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
                    self.run_button.setEnabled(True)
                    self.cancel_button.setEnabled(False)
                    self.language_button.setEnabled(True)
                    self.progress_bar.setValue(self._queue_total)
                    self.progress_bar.setVisible(False)
                    self._update_status_bar()
                snapshot_after = self._take_snapshot()
                try:
                    html_path, _ = report.generate_report(
                        self.state_dir,
                        self.run_id,
                        self.modules,
                        self.settings.language,
                        self._snapshot_before,
                        snapshot_after,
                    )
                except OSError:
                    html_path = None
                    if not self._closed:
                        self.console.appendPlainText(self._t("disk_write_failed"))
                if html_path is not None and not self._closed:
                    self._show_batch_summary(html_path)
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
            self.statusBar().showMessage(
                self._t("status_bar_running").format(
                    pos=position, total=self._queue_total, label=action.label(self.settings.language)
                )
            )
            self.progress_bar.setValue(position - 1)

        needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
            ModuleCategory.REPAIR,
            ModuleCategory.SECURITY,
        )
        if needs_restore_point and not self._restore_point_attempted and not self.settings.dry_run:
            self._restore_point_attempted = True
            try:
                undo.create_undo_script(self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)))
            except OSError:
                if not self._closed:
                    self.console.appendPlainText(self._t("disk_write_failed"))
            rp_runner = restore_point.RestorePointRunner(f"PortableFix {self.run_id}", parent=self)
            rp_runner.result_ready.connect(
                lambda success, detail, m=module, a=action: self._on_restore_point_checked(success, detail, m, a)
            )
            self._pending_restore_point_runner = rp_runner
            rp_runner.start()
            return

        self._dispatch_action(module, action)

    def _on_restore_point_checked(self, success: bool, detail: str, module: ModuleDef, action: ActionDef) -> None:
        output = "System Restore Point created." if success else (
            f"System Restore Point creation failed: {detail}" if detail else "System Restore Point creation failed."
        )
        entry = make_entry(
            "_system",
            "restore_point",
            f"Checkpoint-Computer -Description 'PortableFix {self.run_id}'",
            0 if success else 1,
            output,
            self.settings.dry_run,
            self.run_id,
            elevated=self.is_admin,
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))
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
            if proceed != QMessageBox.Yes:
                self._skip_high_risk_actions_in_queue()
                self._run_next()
                return
        self._dispatch_action(module, action)

    def _dispatch_action(self, module: ModuleDef, action: ActionDef) -> None:
        if action.risk == RiskLevel.DESTRUCTIVE:
            confirmed = QMessageBox.warning(
                self,
                self._t("app_title"),
                f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_destructive_action')}",
                QMessageBox.Yes | QMessageBox.No,
            )
            if confirmed != QMessageBox.Yes:
                self._run_next()
                return
        elif action.risk != RiskLevel.SAFE:
            confirmed = QMessageBox.question(
                self,
                self._t("app_title"),
                f"[{action.risk.value}] {action.label(self.settings.language)}\n\n{self._t('confirm_risky_action')}",
            )
            if confirmed != QMessageBox.Yes:
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
            lambda code, m=module.module_id, a=action.id, c=action.command, r=runner: self._on_action_finished(
                m, a, c, code, r
            )
        )
        runner.start()

    def _on_action_finished(
        self, module_id: str, action_id: str, command: str, exit_code: int, runner: ActionRunner
    ) -> None:
        output = "\n".join(runner.captured_output)
        _, action = self._find_action(action_id)
        entry = make_entry(
            module_id, action_id, command, exit_code, output, self.settings.dry_run, self.run_id,
            risk=action.risk.value, warned=action.risk != RiskLevel.SAFE, elevated=self.is_admin,
        )
        try:
            append_entry(self.state_dir, self.run_id, entry)
        except OSError:
            if not self._closed:
                self.console.appendPlainText(self._t("disk_write_failed"))
        self._batch_results.append((action_id, exit_code))
        elapsed = time.monotonic() - self._action_start_times.pop(action_id, time.monotonic())
        status_text = f"{self._t('status_ok') if exit_code == 0 else self._t('status_failed')} ({elapsed:.1f}s)"
        self._set_action_status(action_id, "ok" if exit_code == 0 else "fail", status_text)
        if not self.settings.dry_run and exit_code == 0:
            if action.undo_command:
                self._undo_steps.append(action.undo_command)
                try:
                    undo.create_undo_script(self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)))
                except OSError:
                    if not self._closed:
                        self.console.appendPlainText(self._t("disk_write_failed"))
        self._run_next()

    def _build_sysinfo_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("actionCard")
        panel.setFixedWidth(345)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        self._sysinfo_labels: dict[str, QLabel] = {}

        def add_row(key: str, label_key: str, tooltip: str | None = None) -> None:
            row = QHBoxLayout()
            row.setSpacing(6)
            caption = QLabel(self._t(label_key))
            caption.setObjectName("selectionScope")
            row.addWidget(caption)
            row.addStretch(1)
            value = QLabel(self._t("sysinfo_loading"))
            value.setWordWrap(True)
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
            row.addWidget(value)
            layout.addLayout(row)

        add_row("os", "sysinfo_os")
        add_row("uptime", "sysinfo_uptime")
        add_row("cpu_name", "sysinfo_cpu")
        add_row("cpu_load", "sysinfo_cpu_load")
        add_row("cpu_clock", "sysinfo_cpu_clock", tooltip=self._t("sysinfo_cpu_clock_hint"))
        add_row("ram", "sysinfo_ram")
        add_row("ram_speed", "sysinfo_ram_speed")
        add_row("battery", "sysinfo_battery")
        add_row("disk_health", "sysinfo_disk_health")
        add_row("gpu_name", "sysinfo_gpu")
        add_row("gpu_load", "sysinfo_gpu_load")
        add_row("gpu_temp", "sysinfo_gpu_temp")
        add_row("gpu_clock", "sysinfo_gpu_clock")
        add_row("gpu_vram", "sysinfo_gpu_vram")
        add_row("ip", "sysinfo_ip")
        add_row("ping", "sysinfo_ping")
        add_row("vpn", "sysinfo_vpn")

        self.speed_test_button = self._make_selection_button(
            self._t("sysinfo_speed_test_button"), self._on_speed_test_clicked
        )
        layout.addWidget(self.speed_test_button)
        self.speed_test_result_label = QLabel("")
        self.speed_test_result_label.setWordWrap(True)
        layout.addWidget(self.speed_test_result_label)
        layout.addStretch(1)
        return panel

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
        self.speed_test_result_label.setText(self._t("sysinfo_speed_testing"))
        self._speed_test_runner = sysinfo.SpeedTestRunner(parent=self)
        self._speed_test_runner.speed_test_ready.connect(self._on_speed_test_ready)
        self._speed_test_runner.start()

    def _on_speed_test_ready(self, mbps: float | None) -> None:
        self._speed_test_busy = False
        if self._closed:
            return
        self.speed_test_button.setEnabled(True)
        self.speed_test_result_label.setText(f"{mbps:.1f} Mbps" if mbps is not None else self._t("sysinfo_na"))
