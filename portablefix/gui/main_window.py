import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from . import style
from .. import elevation, i18n, paths, report, restore_point, undo, updater
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
        self.modules: list[ModuleDef] = load_all_modules(assets_dir / "Modules")
        self._action_checkboxes: dict[str, QCheckBox] = {}
        self._action_rows: dict[str, QWidget] = {}
        self._action_status_labels: dict[str, QLabel] = {}
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
        self._build_ui()
        self._start_update_check()

    def closeEvent(self, event) -> None:
        # ponytail: plain-Python flag (safe even if a delayed cross-thread
        # callback fires after the C++ widgets are gone) so async batch-completion
        # handlers know not to touch self.run_button once the window is closing.
        self._closed = True
        super().closeEvent(event)

    def _t(self, key: str) -> str:
        return i18n.translate(key, self.settings.language)

    def _build_ui(self) -> None:
        self.setWindowTitle(self._t("app_title"))
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

        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)
        self.category_list = QListWidget()
        self.category_list.setObjectName("categoryList")
        self.category_list.setFixedWidth(190)
        for category in self._categories_order:
            self.category_list.addItem(QListWidgetItem(self._t(category_i18n_keys[category])))
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
            self._t("select_safe_only"), lambda: self._apply_selection(list(self._action_checkboxes), "safe")
        )
        global_select_row.addWidget(self.global_select_safe_button)
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
        preset_row.addWidget(self._make_selection_button(
            self._t("preset_quick_clean"), lambda: self._apply_preset("quick_clean")
        ))
        preset_row.addWidget(self._make_selection_button(
            self._t("preset_full_diagnostic"), lambda: self._apply_preset("full_diagnostic")
        ))
        preset_row.addWidget(self._make_selection_button(
            self._t("preset_privacy_debloat"), lambda: self._apply_preset("privacy_debloat")
        ))
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
                self._t("select_safe_only"), lambda c=category: self._apply_selection(self._category_action_ids[c], "safe")
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
                    card_layout.addWidget(row_widget)
                    self._action_rows[action.id] = row_widget
            self._category_groups[category] = card
            scroll_layout.addWidget(card)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        center_layout.addWidget(scroll, 1)

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
        root_layout.addLayout(body_layout, 3)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        root_layout.addWidget(self.console, 1)

        self.category_list.currentRowChanged.connect(self._on_category_changed)
        if self._categories_order:
            self.category_list.setCurrentRow(0)
        self._update_status_bar()
        if self._pending_update_info is not None:
            self.update_banner_label.setText(
                self._t("update_available_banner").format(version=self._pending_update_info.version)
            )
            self.update_banner.setVisible(True)

    def _on_category_changed(self, row: int) -> None:
        for index, category in enumerate(self._categories_order):
            self._category_groups[category].setHidden(index != row)

    def _on_search_changed(self, text: str) -> None:
        needle = text.strip().lower()
        for action_id, row_widget in self._action_rows.items():
            if not needle:
                row_widget.setHidden(False)
                continue
            _, action = self._find_action(action_id)
            haystack = action.label(self.settings.language).lower()
            row_widget.setHidden(needle not in haystack)

    def _apply_preset(self, preset_key: str) -> None:
        wanted = [aid for aid in PRESETS[preset_key] if aid in self._action_checkboxes]
        self._apply_selection(list(self._action_checkboxes), "none")
        self._apply_selection(wanted, "all")

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

    def _apply_selection(self, action_ids: list[str], mode: str) -> None:
        for action_id in action_ids:
            if mode == "all":
                checked = True
            elif mode == "none":
                checked = False
            else:
                _, action = self._find_action(action_id)
                checked = action.risk == RiskLevel.SAFE
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
        self.settings.language = "en" if self.settings.language == "sk" else "sk"
        old_central = self.centralWidget()
        self._action_checkboxes = {}
        self._build_ui()
        if old_central is not None:
            old_central.deleteLater()

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

    def _on_restart_as_admin(self) -> None:
        result = elevation.relaunch_as_admin(sys.executable)
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

    def _set_action_status(self, action_id: str, state: str, text: str) -> None:
        label = self._action_status_labels.get(action_id)
        if label is None:
            return
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    def run_selected_actions(self) -> None:
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
        self._run_next()

    def _run_next(self) -> None:
        if not self._queue:
            if self._batch_active:
                self._batch_active = False
                if not self._closed:
                    self.run_button.setEnabled(True)
                    self.cancel_button.setEnabled(False)
                    self._update_status_bar()
                snapshot_after = self._take_snapshot()
                html_path, _ = report.generate_report(
                    self.state_dir,
                    self.run_id,
                    self.modules,
                    self.settings.language,
                    self._snapshot_before,
                    snapshot_after,
                )
                if not self._closed:
                    self._show_batch_summary(html_path)
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

        needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
            ModuleCategory.REPAIR,
            ModuleCategory.SECURITY,
        )
        if needs_restore_point and not self._restore_point_attempted and not self.settings.dry_run:
            self._restore_point_attempted = True
            undo.create_undo_script(self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)))
            rp_runner = restore_point.RestorePointRunner(f"PortableFix {self.run_id}", parent=self)
            rp_runner.result_ready.connect(
                lambda success, m=module, a=action: self._on_restore_point_checked(success, m, a)
            )
            self._pending_restore_point_runner = rp_runner
            rp_runner.start()
            return

        self._dispatch_action(module, action)

    def _on_restore_point_checked(self, success: bool, module: ModuleDef, action: ActionDef) -> None:
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

        if self.settings.dry_run and action.preview_command:
            plan = build_execution_plan(action.preview_command, dry_run=False)
        else:
            plan = build_execution_plan(action.command, self.settings.dry_run)

        self._set_action_status(action.id, "running", self._t("status_running"))
        self._action_start_times[action.id] = time.monotonic()
        runner = ActionRunner(plan, parent=self)
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
        entry = make_entry(module_id, action_id, command, exit_code, output, self.settings.dry_run)
        append_entry(self.state_dir, self.run_id, entry)
        self._batch_results.append((action_id, exit_code))
        elapsed = time.monotonic() - self._action_start_times.pop(action_id, time.monotonic())
        status_text = f"{self._t('status_ok') if exit_code == 0 else self._t('status_failed')} ({elapsed:.1f}s)"
        self._set_action_status(action_id, "ok" if exit_code == 0 else "fail", status_text)
        if not self.settings.dry_run and exit_code == 0:
            _, action = self._find_action(action_id)
            if action.undo_command:
                self._undo_steps.append(action.undo_command)
                undo.create_undo_script(self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)))
        self._run_next()
