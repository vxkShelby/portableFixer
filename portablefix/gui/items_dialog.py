"""The checklist a per-item action (research G05) asks with before a batch:
which of the listed items the action should work on. Nothing is checked
beforehand - an item is changed only when the technician picked it."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..items import Item

_ID_ROLE = Qt.ItemDataRole.UserRole


class ItemsDialog(QDialog):
    def __init__(self, title: str, intro: str, items: list[Item], texts: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 480)
        layout = QVBoxLayout(self)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("itemsList")
        self.list_widget.setAccessibleName(title)
        for item in items:
            text = item.label
            if item.risk_hint:
                text += f"  [{item.risk_hint}]"
            if item.detail:
                text += f"\n    {item.detail}"
            row = QListWidgetItem(text)
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            row.setData(_ID_ROLE, item.id)
            row.setToolTip(f"{item.id}\n{item.detail}" if item.detail else item.id)
            self.list_widget.addItem(row)
        layout.addWidget(self.list_widget, 1)
        select_row = QHBoxLayout()
        for key, state in (("select_all", Qt.CheckState.Checked), ("select_none", Qt.CheckState.Unchecked)):
            button = QPushButton(texts[key])
            button.setObjectName("selectionBtn")
            button.clicked.connect(lambda _checked=False, s=state: self._set_all(s))
            select_row.addWidget(button)
        select_row.addStretch(1)
        layout.addLayout(select_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, state) -> None:
        for index in range(self.list_widget.count()):
            self.list_widget.item(index).setCheckState(state)

    def chosen_ids(self) -> list[str]:
        return [
            self.list_widget.item(i).data(_ID_ROLE)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).checkState() == Qt.CheckState.Checked
        ]

    def ask(self) -> list[str] | None:
        """The checked ids ([] = none), or None when the batch was cancelled."""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.chosen_ids()
