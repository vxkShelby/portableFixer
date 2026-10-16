"""Settings menu, appearance, job/branding dialogs and presets."""

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from . import job_forms, style
from .. import intake
from ..settings import (
    MAX_CUSTOM_PRESETS,
    MAX_PRESET_NAME_LENGTH,
    MAX_TECHNICIAN_NAME_LENGTH,
    PRESETS,
    save_settings,
)
from ..version import APP_VERSION
from .mw_common import CUSTOM_PRESET_PREFIX, _thread_running


class SettingsMixin:
    """Settings menu, appearance, job/branding dialogs and presets.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _build_settings_button(self) -> QToolButton:
        """B13: the one place for the settings that were scattered over the
        sysinfo panel, the Job dialog and the dashboard. The actions only
        forward to the existing widgets and handlers; their state is
        re-read from them each time the menu opens."""
        button = QToolButton()
        button.setObjectName("selectionBtn")
        button.setText("⚙")
        button.setToolTip(self._t("settings_button_tooltip"))
        button.setAccessibleName(self._t("settings_button_tooltip"))
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        menu = QMenu(button)
        target_language = "English" if self.settings.language == "sk" else "Slovenčina"
        menu.addAction(self._t("language_switch_tooltip").format(language=target_language)).triggered.connect(
            lambda _checked=False: self._on_toggle_language())
        # Parent it explicitly and hold a reference: PySide can free the
        # submenu returned by addMenu(str) once the Python wrapper is collected.
        theme_menu = QMenu(self._t("settings_menu_theme"), menu)
        menu.addMenu(theme_menu)
        self._theme_menu = theme_menu
        theme_actions = {}
        for value in style.THEMES:
            theme_actions[value] = theme_menu.addAction(self._t(f"theme_{value}"))
            theme_actions[value].setCheckable(True)
            theme_actions[value].triggered.connect(lambda _checked=False, v=value: self._set_theme(v))
        scheme_changed = getattr(QGuiApplication.styleHints(), "colorSchemeChanged", None)
        if scheme_changed is not None and not getattr(self, "_scheme_hooked", False):
            self._scheme_hooked = True
            scheme_changed.connect(self._on_color_scheme_changed)
        quiet = menu.addAction(self._t("quiet_mode_toggle"))
        quiet.setCheckable(True)
        quiet.toggled.connect(lambda checked: self.quiet_mode_checkbox.setChecked(checked))
        winget_auto = menu.addAction(self._t("settings_menu_winget_auto"))
        winget_auto.setCheckable(True)
        winget_auto.toggled.connect(lambda checked: self.winget_auto_check_checkbox.setChecked(checked))
        menu.addSeparator()
        redact = menu.addAction(self._t("job_redact_label"))
        redact.setCheckable(True)
        redact.toggled.connect(self._set_redact_for_client)
        menu.addAction(self._t("settings_menu_technician")).triggered.connect(
            lambda _checked=False: self._open_job_dialog())
        menu.addAction(self._t("job_branding_button")).triggered.connect(
            lambda _checked=False: self._open_branding_dialog())
        menu.addSeparator()
        menu.addAction(self._t("settings_menu_reports")).triggered.connect(
            lambda _checked=False: self._open_reports_folder())
        menu.addAction(self._t("settings_menu_about")).triggered.connect(
            lambda _checked=False: self._show_about())

        def sync() -> None:
            # setChecked on an unchanged value emits nothing, so this
            # cannot re-trigger the handlers it mirrors.
            quiet.setChecked(self.quiet_mode_checkbox.isChecked())
            winget_auto.setChecked(self.winget_auto_check_checkbox.isChecked())
            redact.setChecked(self.settings.redact_for_client)
            for value, action in theme_actions.items():
                action.setChecked(value == self.settings.theme)

        menu.aboutToShow.connect(sync)
        button.setMenu(menu)
        self._settings_menu = menu
        return button

    def _open_reports_folder(self) -> None:
        folder = self.state_dir / "Reports"
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _about_text(self) -> str:
        if self.integrity_mismatches is None:
            integrity = self._t("about_integrity_pending")
        elif self.integrity_mismatches:
            integrity = self._t("about_integrity_changed").format(count=len(self.integrity_mismatches))
        else:
            integrity = self._t("about_integrity_ok")
        return self._t("about_text").format(
            version=APP_VERSION, integrity=integrity, state_dir=self.state_dir, logs_dir=self.state_dir / "Logs",
        )

    def _show_about(self) -> None:
        QMessageBox.information(self, self._t("settings_menu_about").rstrip("…"), self._about_text())

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

    def _set_theme(self, value: str) -> None:
        """Appearance menu: remember the choice and re-skin right away."""
        if value in style.THEMES and value != self.settings.theme:
            self.settings.theme = value
            self._persist_settings()
        self._apply_theme()

    def _on_color_scheme_changed(self, _scheme=None) -> None:
        if self.settings.theme == "auto":
            self._apply_theme()

    def _apply_theme(self) -> None:
        style.set_theme(self.settings.theme)
        sheet = style.stylesheet()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(sheet)
        self.setStyleSheet(sheet)
        if self._console_window is not None:
            self._console_window.setStyleSheet(sheet)

    def _set_redact_for_client(self, checked: bool) -> None:
        if checked == self.settings.redact_for_client:
            return
        self.settings.redact_for_client = checked
        self._persist_settings()

    def _open_job_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
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
        # Research G20: a setting, not part of the job - remembered like the
        # technician's name, since it is the same choice on every visit.
        redact_checkbox = QCheckBox(self._t("job_redact_label"))
        redact_checkbox.setObjectName("jobRedact")
        redact_checkbox.setChecked(self.settings.redact_for_client)
        redact_checkbox.setToolTip(self._t("job_redact_tooltip"))
        redact_checkbox.setAccessibleDescription(self._t("job_redact_tooltip"))
        form.addRow("", redact_checkbox)
        # Research G20: the manual work timer acts at once (it is a clock,
        # not a field), the forms and the branding have dialogs of their own.
        self._work_timer_widget = job_forms.WorkTimerWidget(
            self.settings.language, self._run_audit_entries, self._log_work_timer, parent=dialog,
        )
        form.addRow(self._t("work_timer_label"), self._work_timer_widget)
        extras = QHBoxLayout()
        # Opened over the Job details window, not beside it.
        forms_button = self._make_selection_button(self._t("job_forms_button"), lambda: self._open_forms_dialog(dialog))
        forms_button.setToolTip(self._t("job_forms_tooltip"))
        extras.addWidget(forms_button)
        extras.addWidget(self._make_selection_button(self._t("job_branding_button"), lambda: self._open_branding_dialog(dialog)))
        extras.addStretch(1)
        form.addRow(extras)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # Qt ships no Slovak translations for standard buttons - label it ourselves.
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("dialog_cancel"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        (client_edit if self.settings.technician_name else technician_edit).setFocus()
        self._job_dialog = dialog
        dialog.accepted.connect(
            lambda: (
                self._set_job(technician_edit.text(), client_edit.text(), note_edit.toPlainText()),
                self._set_redact_for_client(redact_checkbox.isChecked()),
            )
        )
        dialog.open()

    def _open_forms_dialog(self, parent: QWidget | None = None) -> None:
        entries = self._run_audit_entries()
        saved_intake = intake.latest_intake(entries)
        saved_outtake = intake.latest_outtake(entries)
        dialog = job_forms.FormsDialog(
            self.settings.language,
            saved_intake[0] if saved_intake else None,
            saved_outtake[0] if saved_outtake else None,
            parent=parent or self,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._forms_dialog = dialog
        dialog.accepted.connect(lambda: self._save_forms(dialog.intake_form(), dialog.outtake_form()))
        dialog.open()

    def _save_forms(self, intake_form: intake.IntakeForm, outtake_form: intake.OuttakeForm) -> None:
        # Logged only when changed: every save is kept in the audit log, and
        # OK on an untouched dialog must not add a copy. A cleared form is
        # logged too, so the report stops showing the old one.
        entries = self._run_audit_entries()
        changed = False
        for kind, form, saved in (
            (intake.INTAKE_EVENT, intake_form, intake.latest_intake(entries)),
            (intake.OUTTAKE_EVENT, outtake_form, intake.latest_outtake(entries)),
        ):
            previous = saved[0] if saved else None
            if form.is_empty() and previous is None:
                continue
            if previous is not None and form.to_dict() == previous.to_dict():
                continue
            self._log_system_event(kind, 0, intake.form_to_output(form))
            changed = True
        if changed:
            self._refresh_report()

    def _open_branding_dialog(self, parent: QWidget | None = None) -> None:
        values = {
            "company": self.settings.branding_company,
            "company_id": self.settings.branding_company_id,
            "contact": self.settings.branding_contact,
            "logo": self.settings.branding_logo,
        }
        dialog = job_forms.BrandingDialog(self.settings.language, values, parent=parent or self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self._branding_dialog = dialog
        dialog.accepted.connect(lambda: self._set_branding(dialog.values()))
        dialog.open()

    def _set_branding(self, values: dict) -> None:
        self.settings.branding_company = values.get("company", "")
        self.settings.branding_company_id = values.get("company_id", "")
        self.settings.branding_contact = values.get("contact", "")
        self.settings.branding_logo = values.get("logo", "")
        self._persist_settings()

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
            button.setToolTip(self._tn("preset_custom_tooltip", len(action_ids), "action"))
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
            self._report_write_failure()

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
        self.statusBar().showMessage(self._tn("preset_saved", len(action_ids), "action", name=name), 5000)
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

    def _on_dry_run_toggled(self, checked: bool) -> None:
        self.settings.dry_run = checked
        self._apply_run_button_dry_run_tint()

    def _apply_run_button_dry_run_tint(self) -> None:
        """Amber Run button while DRY-RUN is on (research-design-additions
        item 9) - QSS runButton[dryrun="true"], like riskBadge[risk=...]."""
        dry_run = bool(self.settings.dry_run)
        self.run_button.setProperty("dryrun", "true" if dry_run else "false")
        self.run_button.setText(self._t("run_selected_preview" if dry_run else "run_selected"))
        tip = self._t("run_dry_run_tooltip") if dry_run else self._t("run_selected")
        self.run_button.setToolTip(f"{tip} (F5)")
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)

    def _language_toggle_busy(self) -> bool:
        # A panel's restore point, winget scan/update or uninstall (G01)
        # finishes into that panel's widgets - a rebuild now would delete
        # them under it (and, when the runner was parented to the panel,
        # destroy its live QThread: qFatal).
        return any(_thread_running(r) for r in (
            self._pending_panel_restore_point_runner, self._winget_scan_runner,
            self._winget_update_runner, self._uninstall_runner,
        ))

    def _on_toggle_language(self) -> None:
        if self._language_toggle_busy():
            self.statusBar().showMessage(self._t("language_busy"))
            return
        # ponytail: keyboard-only/screen-reader users lose their place if a
        # full UI rebuild silently resets category and focus - remember and
        # restore both so a language switch doesn't strand them at the top.
        saved_category_row = self.category_list.currentRow()
        saved_focused_action_id = next(
            (aid for aid, cb in self._action_checkboxes.items() if cb.hasFocus()), None
        )
        # The rebuild makes every widget fresh: carry the technician's work over.
        saved_checked = [aid for aid, cb in self._action_checkboxes.items() if cb.isChecked()]
        saved_console = self.console.toPlainText()
        saved_search = self.search_box.text()
        saved_symptom = self.symptom_box.text()
        if self._console_window is not None:
            self._console_window.close()  # hands the console back before the old UI goes
        self.settings.language = "en" if self.settings.language == "sk" else "sk"
        old_central = self.centralWidget()
        self._action_checkboxes = {}
        self._build_ui()
        for action_id in saved_checked:
            checkbox = self._action_checkboxes.get(action_id)
            if checkbox is not None:
                checkbox.setChecked(True)
        if saved_console:
            self.console.setPlainText(saved_console)
            bar = self.console.verticalScrollBar()
            bar.setValue(bar.maximum())
        self.search_box.setText(saved_search)
        self.symptom_box.setText(saved_symptom)
        self._refresh_dashboard()
        if old_central is not None:
            old_central.deleteLater()
        if 0 <= saved_category_row < self.category_list.count():
            self.category_list.setCurrentRow(saved_category_row)
        if saved_focused_action_id is not None:
            checkbox = self._action_checkboxes.get(saved_focused_action_id)
            if checkbox is not None:
                checkbox.setFocus()
