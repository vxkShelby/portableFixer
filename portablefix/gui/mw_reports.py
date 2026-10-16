"""Report refresh, batch summary and the technician hand-off package."""

import os
import socket

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path

from . import style
from .. import handoff, intake, report, snapshot
from .mw_common import _elapsed_text, _thread_running


class ReportsMixin:
    """Report refresh, batch summary and the technician hand-off package.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _run_audit_entries(self) -> list[dict]:
        return report.read_audit_entries(self.state_dir, self.run_id)

    def _log_work_timer(self, decision: str) -> None:
        self._log_system_event(
            intake.WORK_TIMER_EVENT, 0,
            "Manual work timer started." if decision == intake.TIMER_START else "Manual work timer stopped.",
            decision=decision,
        )
        self._refresh_report()

    def _refresh_report(self) -> None:
        """Rewrites the run's report after the forms or the timer changed
        (G20). The hand-over is usually filled in after the last batch, when
        the PC is tested and handed over - without this the report (and the
        handoff zip that packs it) would stay as of the last batch end."""
        if self._closed or self._batch_active or self._restarting:
            # A running batch writes its own report at the end, from the
            # same audit log; a restarting PC keeps the report written
            # before it (see _run_next).
            return
        if self._report_runner is not None:
            self._report_refresh_pending = True
            return
        if not self._report_written:
            # No report yet - the first batch end writes one with the forms.
            return
        # The snapshots of the last batch, not a new one: the report
        # describes the repair, and only the forms and the timer changed.
        runner = report.ReportRunner(
            self.state_dir, self.run_id, self.modules, self.settings.language,
            self._snapshot_before, self._snapshot_after,
            job=self._job_info(), storage_fallback=self._storage_fallback,
            redact=self.settings.redact_for_client, branding=self.settings.branding_info(), parent=self,
        )
        runner.result_ready.connect(self._on_report_refreshed)
        self._report_runner = runner
        runner.start()

    def _on_report_refreshed(self, html_path: Path | None, write_failed: bool) -> None:
        self._report_runner = None
        if self._closed:
            return
        if write_failed:
            self._report_write_failure()
        elif html_path is not None:
            self._append_console(self._t("report_refreshed"))
        self._run_pending_report_refresh()

    def _run_pending_report_refresh(self) -> None:
        if self._report_refresh_pending:
            self._report_refresh_pending = False
            self._refresh_report()

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
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
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
        if not self._batch_dry_run:
            metrics = self._build_snapshot_metrics_widget()
            if metrics is not None:
                layout.addWidget(metrics)

        if self._batch_dry_run:
            note = QLabel(self._t("dry_run_batch_note"))
            note.setObjectName("summaryDryRunNote")
            layout.addWidget(note)

        rows_container = QWidget()
        rows_layout = QVBoxLayout(rows_container)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(4)
        # Plain text of what the dialog shows, for the Copy button (item 15) -
        # pasted into a ticket or a chat with the client.
        copy_lines = [header.text()]
        if self._batch_dry_run:
            copy_lines.append(self._t("dry_run_batch_note"))
        # Failed first: that is what the technician has to act on (stable
        # sort keeps the run order inside each group).
        for action_id, exit_code in sorted(self._batch_results, key=lambda result: result[1] == 0):
            _, action = self._find_action(action_id)
            status = self._t("status_ok") if exit_code == 0 else self._t("status_failed")
            row_text = f"[{status}] {action.label(self.settings.language)}"
            if action_id in self._action_durations:
                row_text += f" ({_elapsed_text(self._action_durations[action_id])})"
            copy_lines.append(row_text)
            row_label = QLabel(row_text)
            row_label.setObjectName("summaryRow")
            row_label.setProperty("ok", "true" if exit_code == 0 else "false")
            rows_layout.addWidget(row_label)
            if exit_code != 0:
                hint = self._t(self._failure_hints.get(action_id, "failure_hint_generic"))
                copy_lines.append("    " + hint)
                hint_label = QLabel(hint)
                hint_label.setObjectName("summaryHint")
                hint_label.setWordWrap(True)
                rows_layout.addWidget(hint_label)
        rows_layout.addStretch(1)

        rows_scroll = QScrollArea()
        rows_scroll.setWidgetResizable(True)
        rows_scroll.setMaximumHeight(320)
        rows_scroll.setWidget(rows_container)
        layout.addWidget(rows_scroll)

        # Fixes of the problems still open - not the ones this batch just ran.
        succeeded = {aid for aid, code in self._batch_results if code == 0}
        recommended_ids = [aid for aid in self._recommended_ids() if aid not in succeeded]
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

        if self.settings.redact_for_client:
            tech_note = QLabel(self._t("report_technician_copy_note").format(run_id=self.run_id))
            tech_note.setWordWrap(True)
            layout.addWidget(tech_note)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        open_button = QPushButton(self._t("open_report"))
        open_button.setObjectName("runButton")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
        )
        button_row.addWidget(open_button)
        folder_button = self._make_selection_button(
            self._t("open_report_folder"),
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path.parent))),
        )
        button_row.addWidget(folder_button)
        failed_ids = [aid for aid, code in self._batch_results if code != 0]
        if failed_ids:
            button_row.addWidget(self._make_selection_button(
                self._t("rerun_failed"), lambda: self._select_failed_again(failed_ids, dialog)
            ))
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
        diag_checkbox = self._make_handoff_diagnostics_checkbox()
        button_row.addWidget(diag_checkbox)
        handoff_button = self._make_selection_button(
            self._t("handoff_button"),
            lambda: self._save_handoff_package(self.run_id, dialog, diag_checkbox.isChecked()),
        )
        button_row.addWidget(handoff_button)

        def copy_summary() -> None:
            QApplication.clipboard().setText("\n".join(copy_lines))
            copy_button.setText(self._t("summary_copied"))

        copy_button = self._make_selection_button(self._t("summary_copy"), copy_summary)
        button_row.addWidget(copy_button)
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

    def _select_failed_again(self, action_ids: list[str], dialog: QDialog) -> None:
        # Ticked directly, not through "select all": that sweep skips
        # actions that opt out of it, and a failed one must stay re-runnable.
        self._apply_selection(list(self._action_checkboxes), "none")
        for action_id in action_ids:
            checkbox = self._action_checkboxes.get(action_id)
            if checkbox is not None:
                checkbox.setChecked(True)
        dialog.close()

    def _apply_recommended_selection(self, action_ids: list[str], dialog: QDialog | None) -> None:
        if not action_ids:
            if dialog is not None:
                dialog.close()
            return
        self._apply_selection(list(self._action_checkboxes), "none")
        self._apply_selection(action_ids, "all")
        wanted_set = set(action_ids)
        for index, category in enumerate(self._categories_order):
            if wanted_set.intersection(self._category_action_ids.get(category, [])):
                self.category_list.setCurrentRow(index)
                break
        if dialog is not None:
            dialog.close()

    def _make_handoff_diagnostics_checkbox(self) -> QCheckBox:
        # Off by default and never remembered: the reports hold personal
        # data (names, network config), so each package is a fresh choice.
        checkbox = QCheckBox(self._t("handoff_include_diagnostics"))
        checkbox.setObjectName("handoffDiagnostics")
        checkbox.setChecked(False)
        checkbox.setToolTip(self._t("handoff_include_diagnostics_tip"))
        checkbox.setAccessibleDescription(self._t("handoff_include_diagnostics_tip"))
        return checkbox

    def _save_handoff_package(
        self, run_id: str, parent: QWidget | None = None, include_diagnostics: bool = False
    ) -> Path | None:
        """Ask where to save the client handoff zip of one run and write it.

        With diagnostics the zip is built by a worker thread and this returns
        None at once - the result arrives in _on_handoff_result."""
        if include_diagnostics and _thread_running(self._handoff_runner):
            QMessageBox.information(parent or self, self._t("app_title"), self._t("handoff_diag_busy"))
            return None
        hostname = socket.gethostname()
        default_path = self.state_dir / "Reports" / handoff.default_package_name(hostname, run_id, redact=self.settings.redact_for_client)
        dest, _ = QFileDialog.getSaveFileName(
            parent or self, self._t("handoff_button"), str(default_path), "Zip (*.zip)"
        )
        if not dest:
            return None
        if include_diagnostics:
            self._start_handoff_with_diagnostics(hostname, run_id, Path(dest), parent)
            return None
        try:
            saved = handoff.build_handoff_zip(
                self.state_dir, hostname, run_id, Path(dest), redact=self.settings.redact_for_client,
            )
        except ValueError:
            QMessageBox.warning(parent or self, self._t("app_title"), self._t("handoff_no_files"))
            return None
        except OSError as exc:
            self._append_console(self._t("handoff_failed"))
            self._show_error(parent, self._t("handoff_failed_dialog"), str(exc))
            return None
        self._show_handoff_saved(saved)
        return saved

    def _start_handoff_with_diagnostics(self, hostname: str, run_id: str, dest: Path, parent: QWidget | None) -> None:
        # The reports only read the system, so DRY-RUN does not stop them -
        # but the technician is told, and diagnostics/README.txt says so too.
        if self.settings.dry_run:
            self._append_console(self._t("handoff_diag_dry_run_note"))
        self._append_console(self._t("handoff_diag_started"))
        runner = handoff.HandoffRunner(
            self.state_dir, hostname, run_id, dest, dry_run=self.settings.dry_run,
            redact=self.settings.redact_for_client, parent=self,
        )
        runner.progress.connect(self._on_handoff_progress)
        runner.result_ready.connect(
            lambda saved, error, detail: self._on_handoff_result(saved, error, detail, parent)
        )
        self._handoff_runner = runner
        runner.start()

    def _on_handoff_progress(self, index: int, total: int, name: str) -> None:
        if self._closed:
            return
        text = self._t("handoff_diag_progress").format(index=index, total=total, name=name)
        self.statusBar().showMessage(text)
        self._append_console(text)

    def _on_handoff_result(self, saved, error: str, detail: str, parent: QWidget | None) -> None:
        self._handoff_runner = None
        # Stopped because the window is closing: nothing left to tell.
        if self._closed or error == "cancelled":
            return
        # The summary dialog may have been closed during the minutes the
        # reports took - never parent a message box to a deleted widget.
        if parent is not None:
            try:
                parent.isVisible()
            except RuntimeError:
                parent = None
        self.statusBar().clearMessage()
        if error == "no_files":
            QMessageBox.warning(parent or self, self._t("app_title"), self._t("handoff_no_files"))
            return
        if error:
            self._append_console(self._t("handoff_failed"))
            self._show_error(parent, self._t("handoff_failed_dialog"), detail)
            return
        self._append_console(self._t("handoff_saved").format(path=saved))
        self._show_handoff_saved(saved)

    def _show_handoff_saved(self, saved: Path) -> None:
        note = f" {self._t('handoff_saved_redacted_note')}" if self.settings.redact_for_client else ""
        self.statusBar().showMessage(self._t("handoff_saved").format(path=saved) + note, 15000)
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

    def _open_handoff_folder(self) -> None:
        button = self._handoff_folder_button
        QDesktopServices.openUrl(QUrl.fromLocalFile(button.property("folder")))
        button.setVisible(False)

    def _report_write_failure(self) -> None:
        """An audit/report write failed (USB full or pulled): the client
        record is incomplete. The console line scrolls away, so a red banner
        with the count stays until a retry shows the stick writable again."""
        self._append_console(self._t("disk_write_failed"))
        self._write_failures += 1
        self._refresh_write_banner()

    def _refresh_write_banner(self) -> None:
        self.write_banner.setVisible(self._write_failures > 0)
        self.write_banner_label.setText(self._t("write_banner").format(count=self._write_failures))

    def _on_write_retry(self) -> None:
        probe = self.state_dir / f".write_check_{os.getpid()}"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            self.statusBar().showMessage(self._t("write_retry_failed"))
            return
        self._write_failures = 0
        self._refresh_write_banner()
        self.statusBar().showMessage(self._t("write_retry_ok"))
