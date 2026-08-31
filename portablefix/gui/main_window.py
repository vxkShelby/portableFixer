import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QWidget,
)

from .. import elevation, i18n
from ..audit_log import append_entry, make_entry
from ..executor import ActionRunner, build_execution_plan
from ..models import ActionDef, ModuleDef
from ..module_engine import load_all_modules
from ..settings import Settings


class MainWindow(QMainWindow):
    def __init__(
        self,
        base_dir: Path,
        settings: Settings,
        is_admin: bool,
        run_id: str,
        parent=None,
    ):
        super().__init__(parent)
        self.base_dir = base_dir
        self.settings = settings
        self.is_admin = is_admin
        self.run_id = run_id
        self.modules: list[ModuleDef] = load_all_modules(base_dir / "Modules")
        self._action_checkboxes: dict[str, QCheckBox] = {}
        self._queue: list[str] = []
        self._runner: ActionRunner | None = None
        self._build_ui()

    def _t(self, key: str) -> str:
        return i18n.translate(key, self.settings.language)

    def _build_ui(self) -> None:
        self.setWindowTitle(self._t("app_title"))
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        admin_text = "admin" if self.is_admin else self._t("readonly_banner")
        self.admin_label = QLabel(admin_text)
        top_bar.addWidget(self.admin_label)
        self.restart_admin_button = QPushButton(self._t("restart_as_admin"))
        self.restart_admin_button.setVisible(not self.is_admin)
        self.restart_admin_button.clicked.connect(self._on_restart_as_admin)
        top_bar.addWidget(self.restart_admin_button)
        self.dry_run_checkbox = QCheckBox(self._t("dry_run_toggle"))
        self.dry_run_checkbox.setChecked(self.settings.dry_run)
        self.dry_run_checkbox.toggled.connect(self._on_dry_run_toggled)
        top_bar.addWidget(self.dry_run_checkbox)
        root_layout.addLayout(top_bar)

        body_layout = QHBoxLayout()
        self.category_list = QListWidget()
        for module in self.modules:
            self.category_list.addItem(QListWidgetItem(self._t("category_diagnostics")))
        body_layout.addWidget(self.category_list, 1)

        center_layout = QVBoxLayout()
        for module in self.modules:
            for action in module.actions:
                checkbox = QCheckBox(f"[{action.risk.value}] {action.label(self.settings.language)}")
                self._action_checkboxes[action.id] = checkbox
                center_layout.addWidget(checkbox)
        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.clicked.connect(self.run_selected_actions)
        center_layout.addWidget(self.run_button)
        body_layout.addLayout(center_layout, 2)
        root_layout.addLayout(body_layout)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        root_layout.addWidget(self.console, 1)

    def _on_dry_run_toggled(self, checked: bool) -> None:
        self.settings.dry_run = checked

    def _on_restart_as_admin(self) -> None:
        elevation.relaunch_as_admin(sys.executable)

    def _find_action(self, action_id: str) -> tuple[ModuleDef, ActionDef]:
        for module in self.modules:
            for action in module.actions:
                if action.id == action_id:
                    return module, action
        raise KeyError(action_id)

    def run_selected_actions(self) -> None:
        self._queue = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        self._run_next()

    def _run_next(self) -> None:
        if not self._queue:
            return
        action_id = self._queue.pop(0)
        module, action = self._find_action(action_id)
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
        append_entry(self.base_dir, self.run_id, entry)
        self._run_next()
