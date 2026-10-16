"""The winget updates panel."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path

from .. import uninstaller, winget_updates
from ..models import RiskLevel
from .mw_common import _ignore_deleted, _thread_running


class WingetPanelMixin:
    """The winget updates panel.

    Methods moved verbatim out of MainWindow, which inherits them."""

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
        # The unavailable/failed messages carry a hint and run to a few
        # lines; unwrapped they would stretch the whole dashboard card.
        status_label.setWordWrap(True)
        panel_layout.addWidget(status_label)

        def set_status(text: str, state: str, detail: str = "") -> None:
            status_label.setText(text)
            # winget's own (localized) words on a failed scan - shown as-is,
            # never parsed; cleared by every other status.
            status_label.setToolTip(detail)
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
        self.winget_auto_check_checkbox = auto_check_checkbox
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
                self._persist_settings()
            remove_row(package_id)
            refresh_ignored_panel()

        def unignore_package(package_id: str) -> None:
            if package_id in self.settings.winget_ignored_ids:
                self.settings.winget_ignored_ids.remove(package_id)
                self._persist_settings()
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

        def describe_scan_error(error) -> str:
            code = ""
            if error.exit_code is not None:
                code = self._t("winget_exit_code_suffix").format(code=error.exit_code_hex)
            if error.kind == "unavailable":
                key = "winget_unavailable_not_found" if error.reason == "not_found" else "winget_unavailable_cannot_start"
                return self._t(key).format(code=code)
            if error.reason == "timeout":
                return self._t("winget_scan_timeout").format(seconds=winget_updates._SCAN_TIMEOUT_SEC)
            if error.reason == "unparsed":
                return self._t("winget_scan_unparsed_rows" if error.packages else "winget_scan_unparsed")
            text = self._t("winget_scan_failed").format(code=code)
            hint = {"sources": "winget_scan_hint_sources", "outdated": "winget_scan_hint_outdated"}.get(error.reason)
            if hint:
                text += " " + self._t(hint)
            return text

        def on_scan_failed(error) -> None:
            # An empty list here used to read as "no updates" - a false
            # all-clear on a PC where winget is missing or the scan broke.
            # Rows winget still listed before failing are real updates, so
            # they stay; the banner says the list may be incomplete.
            packages = list(error.packages)
            populate(packages)
            text = describe_scan_error(error)
            # Only when a list is actually shown - populate hides it when
            # every partial row is on the ignore list.
            ignored_ids = set(self.settings.winget_ignored_ids)
            if any(p.id not in ignored_ids for p in packages):
                text += " " + self._t("winget_scan_partial")
            set_status(text, "warn", error.detail)

        def start_scan() -> None:
            set_status(self._t("winget_scanning"), "ok")
            list_scroll.setVisible(False)
            select_row_widget.setVisible(False)
            runner = winget_updates.WingetScanRunner(parent=self)
            self._winget_scan_runner = runner
            runner.scan_finished.connect(_ignore_deleted(populate))
            runner.scan_failed.connect(_ignore_deleted(on_scan_failed))
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
            try:
                winget_updates.export_package_list(packages, Path(path_str))
            except OSError as exc:
                set_status(self._t("winget_export_failed"), "warn", str(exc))
                return
            set_status(self._tn("winget_export_success", len(packages), "package"), "ok")

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
            set_status(self._tn("winget_import_success", checked, "package"), "ok")

        def apply_auto_check_setting() -> None:
            minutes = auto_check_interval.currentData() if auto_check_checkbox.isChecked() else 0
            self.settings.winget_auto_check_minutes = minutes
            auto_check_interval.setEnabled(auto_check_checkbox.isChecked())
            # Quiet mode keeps the saved interval but never scans on its
            # own - winget refreshes its sources over the network. A
            # minimized window doesn't scan either, like the sysinfo polling.
            if minutes and not self.settings.quiet_mode and not self._window_minimized:
                # Every window state change re-applies this; restarting an
                # unchanged running timer would push the next scan back.
                if not auto_check_timer.isActive() or auto_check_timer.interval() != minutes * 60_000:
                    auto_check_timer.start(minutes * 60_000)
            else:
                auto_check_timer.stop()

        # True while the update confirmation (or the running-programs prompt,
        # or the restore point before the update) is pending: the nested event
        # loops still fire auto_check_timer, and a scan then would rebuild the
        # rows under the pending answer.
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
            # _thread_running, not isRunning(): the update runner deletes
            # itself when done, and the dead wrapper raised on every tick -
            # the periodic scan silently never ran again.
            if not _thread_running(self._winget_update_runner):
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
        def on_auto_check_changed(*_args) -> None:
            apply_auto_check_setting()
            self._persist_settings()

        auto_check_checkbox.toggled.connect(on_auto_check_changed)
        auto_check_interval.currentIndexChanged.connect(on_auto_check_changed)

        def on_quiet_mode_changed() -> None:
            # The timer itself follows _apply_polling_state, which the
            # toggle already ran. Leaving quiet mode brings back the scan it skipped at start -
            # otherwise the panel keeps saying "click Refresh" in loud mode.
            if not self.settings.quiet_mode and status_label.text() == self._t("winget_quiet_mode"):
                auto_check_tick()

        self._winget_apply_auto_check = apply_auto_check_setting
        self._winget_on_quiet_mode_changed = on_quiet_mode_changed

        refresh_ignored_panel()

        def winget_upgrade_command(package_id: str) -> str:
            # Same argv as winget_updates._run_winget_upgrade; its
            # "--location" retry, when needed, shows up in the output.
            return (
                f"winget upgrade --id {package_id} --silent --include-unknown "
                "--accept-package-agreements --accept-source-agreements --disable-interactivity"
            )

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
            upgrade_command = winget_upgrade_command

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
            if self._panel_refused_without_admin(console):
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
            # An installer replacing files of a running program fails, asks
            # for a reboot or kills it with unsaved work (research G01).
            confirm_state["open"] = True
            try:
                closed = self._confirm_programs_closed(
                    self._t("category_winget"), self._winget_running_targets(selected_packages),
                    [f"_winget/{p.id}" for p in selected_packages], risk,
                    "Technician cancelled the winget update - the program was still running, package not updated.",
                )
            finally:
                confirm_state["open"] = False
            if not closed:
                return

            def set_controls_enabled(enabled: bool) -> None:
                update_btn.setEnabled(enabled)
                select_all_btn.setEnabled(enabled)
                select_none_btn.setEnabled(enabled)
                refresh_btn.setEnabled(enabled)

            def after_restore_point(proceed: bool) -> None:
                confirm_state["open"] = False
                if proceed and (self._update_phase == "launch" or self._closing_for_update):
                    # The app update's hand-off began while the restore point
                    # ran - same refusal as a click at that moment.
                    self.statusBar().showMessage(self._t("winget_update_blocked_by_app_update"))
                    proceed = False
                if not proceed:
                    set_controls_enabled(True)
                    console.appendPlainText(self._t("panel_cancelled_no_restore_point"))
                    return
                run_update(selected_packages, warning_text)

            # Same safety net as a batch (G01): winget has no way back to the
            # previous version, a restore point does.
            set_controls_enabled(False)
            confirm_state["open"] = True
            if not self._start_panel_restore_point(
                [f"_winget/{package.id}" for package in selected_packages], console, after_restore_point,
            ):
                confirm_state["open"] = False
                set_controls_enabled(True)

        def run_update(selected_packages: list, warning_text: str) -> None:
            risk = RiskLevel.MODERATE.value
            upgrade_command = winget_upgrade_command
            update_btn.setEnabled(False)
            select_all_btn.setEnabled(False)
            select_none_btn.setEnabled(False)
            refresh_btn.setEnabled(False)
            console.setVisible(True)
            console.appendPlainText(self._t("winget_updating"))
            runner = winget_updates.WingetUpdateRunner(selected_packages, parent=self)
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

            runner.package_started.connect(_ignore_deleted(on_package_started))
            runner.package_finished.connect(_ignore_deleted(on_package_finished))
            runner.all_finished.connect(_ignore_deleted(on_all_finished))
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

        if self.settings.quiet_mode:
            # No automatic scan in quiet mode; Refresh still scans on click.
            set_status(self._t("winget_quiet_mode"), "ok")
        else:
            start_scan()
        return panel

    def _winget_running_targets(self, packages: list) -> list[tuple[str, object, str]]:
        # winget's package name is the program's Add/Remove Programs
        # DisplayName, which is where the install folder/exe are recorded.
        try:
            by_name = {p.name.strip().lower(): p for p in uninstaller.list_installed_programs()}
        except OSError:
            return []
        targets = []
        for package in packages:
            program = by_name.get(str(package.name).strip().lower())
            if program is not None:
                targets.append((package.name, program, f"_winget/{package.id}"))
        return targets
