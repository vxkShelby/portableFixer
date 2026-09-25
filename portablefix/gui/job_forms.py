"""Intake / hand-over forms, the manual work timer and the report branding
(research G20) - reached from the Job details dialog. Everything here is
optional and never blocks a batch.

The widgets only collect and show values; main_window writes them (the
forms and the timer as `_system` audit events, the branding into
settings). Their rules live in the Qt-free intake.py and branding.py.
"""

from collections.abc import Callable
from datetime import datetime, timezone

from PySide6.QtCore import QDateTime, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import style
from .. import branding, i18n, intake

# The format of OuttakeForm.handed_at - local time, as the technician sees it.
HANDED_AT_FORMAT = "yyyy-MM-dd HH:mm"


def _combo(language: str, key_prefix: str, choices: tuple[str, ...], current: str) -> QComboBox:
    # The first item is "not filled in" - every field stays optional.
    combo = QComboBox()
    combo.addItem(i18n.translate("forms_choice_none", language), "")
    for choice in choices:
        combo.addItem(i18n.translate(f"{key_prefix}{choice}", language), choice)
    index = combo.findData(current)
    combo.setCurrentIndex(max(index, 0))
    return combo


class FormsDialog(QDialog):
    """Two compact tabs: Intake (the state the PC was received in) and
    Hand-over (Pass / Fail / N/A per function, who took it and when)."""

    def __init__(self, language: str, intake_form: intake.IntakeForm | None,
                 outtake_form: intake.OuttakeForm | None, parent=None):
        super().__init__(parent)
        self._language = language
        intake_form = intake_form or intake.IntakeForm()
        outtake_form = outtake_form or intake.OuttakeForm()
        self.setWindowTitle(self._t("forms_dialog_title"))
        self.setStyleSheet(style.stylesheet())
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_intake(intake_form), self._t("forms_tab_intake"))
        self.tabs.addTab(self._build_outtake(outtake_form), self._t("forms_tab_outtake"))
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        # Qt ships no Slovak translations for standard buttons.
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("dialog_cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _t(self, key: str) -> str:
        return i18n.translate(key, self._language)

    def _build_intake(self, form: intake.IntakeForm) -> QWidget:
        page = QWidget()
        grid = QFormLayout(page)
        self.problem_edit = QPlainTextEdit(form.problem)
        self.problem_edit.setObjectName("actionDetailCommand")
        self.problem_edit.setFixedHeight(70)
        grid.addRow(self._t("intake_problem_label"), self.problem_edit)
        flags_row = QWidget()
        # Two by two - four in a row made the dialog too wide.
        flags_layout = QGridLayout(flags_row)
        flags_layout.setContentsMargins(0, 0, 0, 0)
        self.condition_checkboxes: dict[str, QCheckBox] = {}
        for index, flag in enumerate(intake.CONDITION_FLAGS):
            box = QCheckBox(self._t(f"intake_flag_{flag}"))
            box.setChecked(flag in form.condition_flags)
            self.condition_checkboxes[flag] = box
            flags_layout.addWidget(box, index // 2, index % 2)
        grid.addRow(self._t("intake_condition_label"), flags_row)
        self.condition_edit = QPlainTextEdit(form.condition)
        self.condition_edit.setObjectName("actionDetailCommand")
        self.condition_edit.setPlaceholderText(self._t("intake_condition_placeholder"))
        self.condition_edit.setFixedHeight(54)
        grid.addRow("", self.condition_edit)
        self.accessories_edit = QLineEdit(form.accessories)
        self.accessories_edit.setObjectName("searchBox")
        self.accessories_edit.setMaxLength(intake.MAX_ACCESSORIES_LENGTH)
        self.accessories_edit.setPlaceholderText(self._t("intake_accessories_placeholder"))
        grid.addRow(self._t("intake_accessories_label"), self.accessories_edit)
        self.backup_combo = _combo(self._language, "intake_backup_", intake.BACKUP_CHOICES, form.backup)
        grid.addRow(self._t("intake_backup_label"), self.backup_combo)
        # Only how the password was handled - there is no field for the
        # password itself, on purpose.
        self.password_combo = _combo(
            self._language, "intake_password_", intake.PASSWORD_CHOICES, form.password_handling,
        )
        self.password_combo.setToolTip(self._t("intake_password_note"))
        grid.addRow(self._t("intake_password_label"), self.password_combo)
        note = QLabel(self._t("intake_password_note"))
        note.setWordWrap(True)
        grid.addRow("", note)
        return page

    def _build_outtake(self, form: intake.OuttakeForm) -> QWidget:
        page = QWidget()
        grid = QFormLayout(page)
        self.check_combos: dict[str, QComboBox] = {}
        for key in intake.OUTTAKE_CHECKS:
            combo = _combo(self._language, "outtake_result_", intake.CHECK_RESULTS, form.checks.get(key, ""))
            self.check_combos[key] = combo
            grid.addRow(self._t(f"outtake_check_{key}"), combo)
        self.handed_to_edit = QLineEdit(form.handed_to)
        self.handed_to_edit.setObjectName("searchBox")
        self.handed_to_edit.setMaxLength(intake.MAX_NAME_LENGTH)
        grid.addRow(self._t("outtake_handed_to_label"), self.handed_to_edit)
        when_row = QWidget()
        when_layout = QHBoxLayout(when_row)
        when_layout.setContentsMargins(0, 0, 0, 0)
        saved = QDateTime.fromString(form.handed_at, HANDED_AT_FORMAT) if form.handed_at else QDateTime()
        self.handed_at_edit = QDateTimeEdit(saved if saved.isValid() else QDateTime.currentDateTime())
        self.handed_at_edit.setDisplayFormat(HANDED_AT_FORMAT)
        self.handed_at_edit.setCalendarPopup(True)
        when_layout.addWidget(self.handed_at_edit, 1)
        now_button = QPushButton(self._t("outtake_now_button"))
        now_button.setObjectName("selectionBtn")
        now_button.clicked.connect(lambda _checked=False: self.handed_at_edit.setDateTime(QDateTime.currentDateTime()))
        when_layout.addWidget(now_button)
        grid.addRow(self._t("outtake_handed_at_label"), when_row)
        return page

    def intake_form(self) -> intake.IntakeForm:
        return intake.IntakeForm.from_dict({
            "problem": self.problem_edit.toPlainText(),
            "condition": self.condition_edit.toPlainText(),
            "condition_flags": [flag for flag, box in self.condition_checkboxes.items() if box.isChecked()],
            "accessories": self.accessories_edit.text(),
            "backup": self.backup_combo.currentData() or "",
            "password_handling": self.password_combo.currentData() or "",
        })

    def outtake_form(self) -> intake.OuttakeForm:
        return intake.OuttakeForm.from_dict({
            "checks": {key: combo.currentData() for key, combo in self.check_combos.items() if combo.currentData()},
            "handed_to": self.handed_to_edit.text(),
            "handed_at": self.handed_at_edit.dateTime().toString(HANDED_AT_FORMAT),
        })


class WorkTimerWidget(QWidget):
    """The optional manual timer on the Job details (G20): start / stop,
    the total so far. Its state is read back from the run's audit log every
    time - what the widget shows is what the report will count."""

    def __init__(self, language: str, load_entries: Callable[[], list[dict]],
                 log_toggle: Callable[[str], None], parent=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        super().__init__(parent)
        self._language = language
        self._load_entries = load_entries
        self._log_toggle = log_toggle
        self._clock = clock
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.time_label = QLabel()
        layout.addWidget(self.time_label, 1)
        self.toggle_button = QPushButton()
        self.toggle_button.setObjectName("selectionBtn")
        self.toggle_button.clicked.connect(lambda _checked=False: self.toggle())
        layout.addWidget(self.toggle_button)
        self.setToolTip(i18n.translate("work_timer_tooltip", language))
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_label)
        self.reload()

    def reload(self) -> None:
        self.state = intake.timer_state(self._load_entries())
        running = self.state.running_since is not None
        self.toggle_button.setText(i18n.translate("work_timer_stop" if running else "work_timer_start", self._language))
        if running:
            self._tick.start(1000)
        else:
            self._tick.stop()
        self._refresh_label()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.reload()

    def hideEvent(self, event) -> None:
        # The Job details dialog is closed, not deleted: no ticking behind it.
        self._tick.stop()
        super().hideEvent(event)

    def _refresh_label(self) -> None:
        self.time_label.setText(intake.format_clock(self.state.total_seconds(self._clock())))

    def toggle(self) -> None:
        running = self.state.running_since is not None
        self._log_toggle(intake.TIMER_STOP if running else intake.TIMER_START)
        # Re-read: a write that failed (the USB stick was pulled) leaves the
        # timer as it was instead of showing time that is not on record.
        self.reload()


class BrandingDialog(QDialog):
    """Company, company ID (IČO), contact and logo for the report header.
    The logo is checked (magic bytes, size) as soon as it is picked; a bad
    one is refused here with the reason, never saved."""

    def __init__(self, language: str, values: dict, parent=None):
        super().__init__(parent)
        self._language = language
        self.logo = values.get("logo", "") if branding.decode_logo(values.get("logo")) else ""
        self.setWindowTitle(self._t("branding_dialog_title"))
        self.setStyleSheet(style.stylesheet())
        self.setMinimumWidth(440)
        form = QFormLayout(self)
        hint = QLabel(self._t("branding_hint"))
        hint.setWordWrap(True)
        form.addRow(hint)
        self.company_edit = QLineEdit(values.get("company", ""))
        self.company_edit.setObjectName("searchBox")
        self.company_edit.setMaxLength(branding.MAX_COMPANY_LENGTH)
        form.addRow(self._t("branding_company_label"), self.company_edit)
        self.company_id_edit = QLineEdit(values.get("company_id", ""))
        self.company_id_edit.setObjectName("searchBox")
        self.company_id_edit.setMaxLength(branding.MAX_COMPANY_ID_LENGTH)
        form.addRow(self._t("branding_company_id_label"), self.company_id_edit)
        self.contact_edit = QPlainTextEdit(values.get("contact", ""))
        self.contact_edit.setObjectName("actionDetailCommand")
        self.contact_edit.setFixedHeight(54)
        form.addRow(self._t("branding_contact_label"), self.contact_edit)
        logo_row = QWidget()
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        self.logo_label = QLabel()
        logo_layout.addWidget(self.logo_label, 1)
        choose = QPushButton(self._t("branding_logo_choose"))
        choose.setObjectName("selectionBtn")
        choose.clicked.connect(lambda _checked=False: self._choose_logo())
        logo_layout.addWidget(choose)
        self.remove_logo_button = QPushButton(self._t("branding_logo_remove"))
        self.remove_logo_button.setObjectName("selectionBtn")
        self.remove_logo_button.clicked.connect(lambda _checked=False: self.remove_logo())
        logo_layout.addWidget(self.remove_logo_button)
        form.addRow(self._t("branding_logo_label"), logo_row)
        self.logo_error = QLabel("")
        self.logo_error.setWordWrap(True)
        self.logo_error.setVisible(False)
        form.addRow("", self.logo_error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self._t("dialog_cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._refresh_logo()

    def _t(self, key: str) -> str:
        return i18n.translate(key, self._language)

    def _refresh_logo(self) -> None:
        if self.logo:
            size_kb = max(1, round(len(self.logo) * 3 / 4 / 1024))
            self.logo_label.setText(self._t("branding_logo_set").format(size=size_kb))
        else:
            self.logo_label.setText(self._t("branding_logo_none"))
        self.remove_logo_button.setEnabled(bool(self.logo))

    def _choose_logo(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._t("branding_logo_label"), "", self._t("branding_logo_filter"))
        if path:
            self.load_logo(path)

    def load_logo(self, path) -> bool:
        """Takes the logo from `path` when it passes the checks; otherwise
        keeps the previous one and says why. True when it was taken."""
        try:
            self.logo = branding.load_logo_file(path)
        except branding.LogoError as error:
            self.logo_error.setText(self._t(f"branding_logo_{error.code}"))
            self.logo_error.setVisible(True)
            return False
        self.logo_error.setVisible(False)
        self._refresh_logo()
        return True

    def remove_logo(self) -> None:
        self.logo = ""
        self.logo_error.setVisible(False)
        self._refresh_logo()

    def values(self) -> dict:
        return {
            "company": branding.clean_text(self.company_edit.text(), branding.MAX_COMPANY_LENGTH),
            "company_id": branding.clean_text(self.company_id_edit.text(), branding.MAX_COMPANY_ID_LENGTH),
            "contact": branding.clean_text(self.contact_edit.toPlainText(), branding.MAX_CONTACT_LENGTH),
            "logo": self.logo,
        }
