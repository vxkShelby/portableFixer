import os
import shutil
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
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
from .. import elevation, i18n, report, restore_point, undo
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleCategory, ModuleDef, RiskLevel
from ..module_engine import load_all_modules
from ..settings import Settings


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
        self._queue: list[str] = []
        self._runner: ActionRunner | None = None
        self._restore_point_attempted = False
        self._pending_restore_point_runner: restore_point.RestorePointRunner | None = None
        self._batch_active = False
        self._snapshot_before: dict = {}
        self._undo_steps: list[str] = []
        self._closed = False
        self._build_ui()

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
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 6, 0)
        scroll_layout.setSpacing(8)

        self._category_groups: dict[ModuleCategory, QWidget] = {}
        for category in self._categories_order:
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            heading = QLabel(self._t(category_i18n_keys[category]))
            heading.setObjectName("cardHeading")
            card_layout.addWidget(heading)
            for module in self.modules:
                if module.category != category:
                    continue
                for action in module.actions:
                    row = QHBoxLayout()
                    row.setSpacing(8)
                    checkbox = QCheckBox(action.label(self.settings.language))
                    checkbox.setToolTip(action.description(self.settings.language))
                    self._action_checkboxes[action.id] = checkbox
                    row.addWidget(checkbox)
                    badge = QLabel(action.risk.value)
                    badge.setObjectName("riskBadge")
                    badge.setProperty("risk", action.risk.value)
                    row.addWidget(badge)
                    row.addStretch(1)
                    card_layout.addLayout(row)
            self._category_groups[category] = card
            scroll_layout.addWidget(card)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        center_layout.addWidget(scroll, 1)

        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self.run_selected_actions)
        center_layout.addWidget(self.run_button)
        body_layout.addLayout(center_layout, 1)
        root_layout.addLayout(body_layout, 3)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        root_layout.addWidget(self.console, 1)

        self.category_list.currentRowChanged.connect(self._on_category_changed)
        if self._categories_order:
            self.category_list.setCurrentRow(0)

    def _on_category_changed(self, row: int) -> None:
        for index, category in enumerate(self._categories_order):
            self._category_groups[category].setHidden(index != row)

    def _on_dry_run_toggled(self, checked: bool) -> None:
        self.settings.dry_run = checked

    def _on_toggle_language(self) -> None:
        self.settings.language = "en" if self.settings.language == "sk" else "sk"
        old_central = self.centralWidget()
        self._action_checkboxes = {}
        self._build_ui()
        if old_central is not None:
            old_central.deleteLater()

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

    def run_selected_actions(self) -> None:
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._restore_point_attempted = False
        if self._queue:
            self._batch_active = True
            self._snapshot_before = self._take_snapshot()
            self.run_button.setEnabled(False)
        self._run_next()

    def _run_next(self) -> None:
        if not self._queue:
            if self._batch_active:
                self._batch_active = False
                if not self._closed:
                    self.run_button.setEnabled(True)
                snapshot_after = self._take_snapshot()
                report.generate_report(
                    self.state_dir,
                    self.run_id,
                    self.modules,
                    self.settings.language,
                    self._snapshot_before,
                    snapshot_after,
                )
            return
        action_id = self._queue.pop(0)
        module, action = self._find_action(action_id)

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
        if not self.settings.dry_run and exit_code == 0:
            _, action = self._find_action(action_id)
            if action.undo_command:
                self._undo_steps.append(action.undo_command)
                undo.create_undo_script(self.state_dir, self.run_id, steps=list(reversed(self._undo_steps)))
        self._run_next()
