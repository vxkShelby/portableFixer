"""Console pane: appending, collapse, full screen and pop-out."""

import html

from PySide6.QtWidgets import QDialog, QVBoxLayout

from . import style
from ..models import RiskLevel
from .mw_common import _console_severity


class ConsoleMixin:
    """Console pane: appending, collapse, full screen and pop-out.

    Methods moved verbatim out of MainWindow, which inherits them."""

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
            self._t("status_bar_selected").format(count=len(selected), risk=self._risk_text(highest))
        )

    def _append_console(self, line: str) -> None:
        """One line into the console, tinted by severity (research-design-
        additions item 7). Escaped - PowerShell tables are full of < and &;
        white-space:pre keeps their column padding."""
        color = self._console_colors.get(_console_severity(line))
        if color is None:
            self.console.appendPlainText(line)
        else:
            self.console.appendHtml(f'<span style="color:{color}; white-space:pre">{html.escape(line)}</span>')

    def _on_console_collapse_toggled(self) -> None:
        """Hide the console down to its toolbar row, or bring it back - the
        splitter hands the space to the action list (item 7)."""
        if self._console_window is not None:
            # Popped out: the console lives in its own window, nothing to fold.
            return
        if self._console_fullscreen:
            self._on_console_fullscreen_toggled()
        self._console_collapsed = not self._console_collapsed
        self.console.setVisible(not self._console_collapsed)
        self.console_fullscreen_button.setEnabled(not self._console_collapsed)
        self.console_popout_button.setEnabled(not self._console_collapsed)
        if self._console_collapsed:
            self._console_expanded_sizes = self._main_splitter.sizes()
            # The toolbar row only - a splitter would otherwise keep the
            # console's old height as empty space.
            self._console_panel.setMaximumHeight(self._console_panel.minimumSizeHint().height())
            self.console_collapse_button.setText(f"▸ {self._t('console_expand')}")
        else:
            self._console_panel.setMaximumHeight(16777215)
            if self._console_expanded_sizes:
                self._main_splitter.setSizes(self._console_expanded_sizes)
            self.console_collapse_button.setText(f"▾ {self._t('console_collapse')}")

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
        self.console_collapse_button.setEnabled(False)
        window.show()

    def _reattach_console(self) -> None:
        if self._console_window is None:
            return
        self._console_container_layout.addWidget(self.console)
        self._console_window = None
        self.console_collapse_button.setEnabled(True)
