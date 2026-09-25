"""One review screen before a real batch (research G12).

Replaces the per-action "are you sure?" boxes, which a technician running
an 8-action preset learned to click through unread. Everything that used to
be spread over those boxes - and what only the report said afterwards (no
undo, needs a restart, restore point or not) - is on one screen, together
with the pre-flight blockers/warnings, and one confirmation covers the batch.
"""

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import style
from .. import i18n, preflight
from ..models import ActionDef, ModuleDef, RiskLevel

# Most dangerous first - what the technician must read is at the top.
_RISK_ORDER = {
    RiskLevel.DESTRUCTIVE: 0,
    RiskLevel.REQUIRES_REBOOT: 1,
    RiskLevel.MODERATE: 2,
    RiskLevel.SAFE: 3,
}


@dataclass(frozen=True)
class ReviewItem:
    module_id: str
    action_id: str
    label: str
    risk: RiskLevel
    # The exact text shown for this action and, on confirm/decline, quoted
    # in its audit entry. Empty for SAFE actions (nothing to confirm).
    warning_text: str
    irreversible: bool
    needs_reboot: bool
    restore_point: bool

    @property
    def subject(self) -> str:
        return f"{self.module_id}/{self.action_id}"


@dataclass(frozen=True)
class BatchReview:
    items: tuple[ReviewItem, ...]
    preflight: preflight.PreflightResult
    language: str
    # Size of the live SOFTWARE + SYSTEM hives (hive_backup.estimate_bytes),
    # None when unknown - shown next to the hive backup offer.
    hive_backup_bytes: int | None = None
    # Batch-level facts in the review's language that are not about a single
    # action: a restart that ends the batch, the actions waiting for it, a
    # batch continued after a restart (research G03).
    notes: tuple[str, ...] = ()

    @property
    def restore_point_planned(self) -> bool:
        return any(item.restore_point for item in self.items)

    @property
    def offers_hive_backup(self) -> bool:
        # Only a DESTRUCTIVE batch is worth ~200+ MB and a minute of
        # `reg save` (research G24) - everything else has its restore point.
        return any(item.risk == RiskLevel.DESTRUCTIVE for item in self.items)

    @property
    def needs_confirmation(self) -> bool:
        # A batch of SAFE actions on a PC with nothing to report starts
        # directly (a restore point alone is nothing to decide) - a dialog
        # with no decision in it is what trains people to click without
        # reading.
        # A note is always something to decide on (a batch that stops for a
        # restart, or one continued after it - never started unasked).
        return bool(self.preflight.issues) or any(item.warning_text for item in self.items) or bool(self.notes)


@dataclass
class ReviewDecision:
    confirmed: bool
    # Actions that will not run: every risky one on cancel, an unticked
    # DESTRUCTIVE one on confirm.
    declined: list[ReviewItem] = field(default_factory=list)
    accepted: list[ReviewItem] = field(default_factory=list)
    overrode_blockers: bool = False
    # The technician asked for the full registry hive backup before the
    # first DESTRUCTIVE action (never on by default).
    hive_backup: bool = False


def warning_text_for(action: ActionDef, language: str) -> str:
    if action.risk == RiskLevel.SAFE:
        return ""
    if action.risk == RiskLevel.DESTRUCTIVE:
        # A few DESTRUCTIVE actions do have an undo_command - never claim
        # "cannot be undone" for them.
        key = "review_note_destructive_undo" if action.undo_command else "review_note_destructive"
    else:
        key = "review_note_risky"
    text = f"[{action.risk.value}] {action.label(language)}\n\n{i18n.translate(key, language)}"
    if action.risk == RiskLevel.REQUIRES_REBOOT:
        text += " " + i18n.translate("review_note_reboot", language)
    if not action.undo_command and action.risk != RiskLevel.DESTRUCTIVE:
        text += " " + i18n.translate("review_note_no_undo", language)
    return text


def build_review(
    items: list[tuple[ModuleDef, ActionDef]], result: preflight.PreflightResult, language: str,
    hive_backup_bytes: int | None = None, notes: tuple[str, ...] = (),
) -> BatchReview:
    review_items = []
    for module, action in items:
        review_items.append(ReviewItem(
            module_id=module.module_id,
            action_id=action.id,
            label=action.label(language),
            risk=action.risk,
            warning_text=warning_text_for(action, language),
            irreversible=action.risk != RiskLevel.SAFE and not action.undo_command,
            needs_reboot=action.risk == RiskLevel.REQUIRES_REBOOT,
            restore_point=preflight.needs_restore_point(module, action),
        ))
    # Stable sort: within a tier the batch's own order is kept.
    review_items.sort(key=lambda item: _RISK_ORDER.get(item.risk, 9))
    return BatchReview(tuple(review_items), result, language, hive_backup_bytes, tuple(notes))


class BatchReviewDialog(QDialog):
    """Drive it in tests through the public widgets: tick
    destructive_checkboxes / override_checkbox, then confirm_button.click()
    or reject(); decision() reads the outcome without exec()."""

    def __init__(self, review: BatchReview, parent=None):
        super().__init__(parent)
        self.review = review
        self._t = lambda key: i18n.translate(key, review.language)
        self.setWindowTitle(self._t("review_title"))
        self.setStyleSheet(style.stylesheet())
        self.setMinimumWidth(560)
        self.destructive_checkboxes: dict[str, QCheckBox] = {}
        self.override_checkbox: QCheckBox | None = None
        self.hive_backup_checkbox: QCheckBox | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        header = QLabel(self._t("review_header").format(count=len(review.items)))
        header.setObjectName("summaryHeader")
        layout.addWidget(header)

        result = review.preflight
        for issue in result.blockers:
            label = QLabel(f"{self._t('review_blocker')}: {issue.text(review.language)}")
            label.setObjectName("summaryRow")
            label.setProperty("ok", "false")
            label.setWordWrap(True)
            layout.addWidget(label)
        for issue in result.warnings:
            label = QLabel(f"{self._t('review_warning')}: {issue.text(review.language)}")
            label.setObjectName("summaryDryRunNote")
            label.setWordWrap(True)
            layout.addWidget(label)

        # Text set by _update_confirm_state: unticking the only DESTRUCTIVE
        # action that needed a restore point means none will be made.
        self.restore_point_label = QLabel()
        self.restore_point_label.setWordWrap(True)
        layout.addWidget(self.restore_point_label)

        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(6)
        for item in review.items:
            rows_layout.addWidget(self._build_row(item))
        rows_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(380)
        scroll.setWidget(rows)
        layout.addWidget(scroll)

        irreversible = [item.label for item in review.items if item.irreversible]
        if irreversible:
            label = QLabel(self._t("review_irreversible_list").format(actions=", ".join(irreversible)))
            label.setWordWrap(True)
            layout.addWidget(label)
        reboot = [item.label for item in review.items if item.needs_reboot]
        if reboot:
            label = QLabel(self._t("review_reboot_list").format(actions=", ".join(reboot)))
            label.setWordWrap(True)
            layout.addWidget(label)
        self.note_labels: list[QLabel] = []
        for note in review.notes:
            label = QLabel(note)
            label.setObjectName("summaryDryRunNote")
            label.setWordWrap(True)
            layout.addWidget(label)
            self.note_labels.append(label)

        if review.offers_hive_backup:
            # Unticked by default: it costs disk space and time, and the
            # restore point is the first safety net.
            if review.hive_backup_bytes:
                size_mb = max(1, round(review.hive_backup_bytes / 1024**2))
                text = self._t("review_hive_backup_size").format(size=size_mb)
            else:
                text = self._t("review_hive_backup")
            self.hive_backup_checkbox = QCheckBox(text)
            self.hive_backup_checkbox.setToolTip(self._t("review_hive_backup_tooltip"))
            layout.addWidget(self.hive_backup_checkbox)

        if result.blockers and not result.hard_blocked:
            # An override is possible (research G11) but never by default,
            # and main_window logs it.
            self.override_checkbox = QCheckBox(self._t("review_override_blockers"))
            self.override_checkbox.toggled.connect(self._update_confirm_state)
            layout.addWidget(self.override_checkbox)

        buttons = QDialogButtonBox()
        self.confirm_button = buttons.addButton(self._t("review_confirm"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton(self._t("dialog_cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        # Enter must not confirm a batch by accident - Cancel is the default.
        self.cancel_button.setDefault(True)
        self.confirm_button.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_confirm_state()
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _build_row(self, item: ReviewItem) -> QWidget:
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)
        top = QHBoxLayout()
        badge = QLabel(item.risk.value)
        badge.setObjectName("riskBadge")
        badge.setProperty("risk", item.risk.value)
        top.addWidget(badge)
        title = QLabel(item.label)
        title.setWordWrap(True)
        top.addWidget(title, 1)
        row_layout.addLayout(top)
        if item.warning_text:
            # The exact text the audit log will quote for this action.
            text = QLabel(item.warning_text)
            text.setObjectName("actionDetailDescription")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row_layout.addWidget(text)
        if item.risk == RiskLevel.DESTRUCTIVE:
            # Per action, unticked by default: one batch-wide "OK" must not
            # be enough for something PortableFix cannot undo.
            tick_text = self._t("review_destructive_tick" if item.irreversible else "review_destructive_undo_tick")
            checkbox = QCheckBox(tick_text)
            checkbox.setAccessibleName(f"{tick_text} - {item.label}")
            checkbox.toggled.connect(self._update_confirm_state)
            self.destructive_checkboxes[item.action_id] = checkbox
            row_layout.addWidget(checkbox)
        return row

    def _unticked(self, item: ReviewItem) -> bool:
        checkbox = self.destructive_checkboxes.get(item.action_id)
        return checkbox is not None and not checkbox.isChecked()

    def _update_confirm_state(self, *_args) -> None:
        planned = any(item.restore_point and not self._unticked(item) for item in self.review.items)
        if self.hive_backup_checkbox is not None:
            # Offered while at least one DESTRUCTIVE action is still ticked.
            self.hive_backup_checkbox.setEnabled(any(
                item.risk == RiskLevel.DESTRUCTIVE and not self._unticked(item) for item in self.review.items
            ))
        self.restore_point_label.setText(
            self._t("review_restore_point_yes" if planned else "review_restore_point_no")
        )
        result = self.review.preflight
        allowed = not result.hard_blocked
        if result.blockers and allowed:
            allowed = self.override_checkbox is not None and self.override_checkbox.isChecked()
        # Something must be left to run: a batch whose only real work is
        # unticked DESTRUCTIVE actions would "confirm" into doing nothing.
        if allowed and self.destructive_checkboxes and len(self.destructive_checkboxes) == len(self.review.items):
            allowed = any(cb.isChecked() for cb in self.destructive_checkboxes.values())
        self.confirm_button.setEnabled(allowed)

    def decision(self) -> ReviewDecision:
        risky = [item for item in self.review.items if item.warning_text]
        if self.result() != QDialog.DialogCode.Accepted or not self.confirm_button.isEnabled():
            return ReviewDecision(confirmed=False, declined=risky)
        declined = [item for item in risky if self._unticked(item)]
        accepted = [item for item in risky if item not in declined]
        overrode = bool(self.review.preflight.blockers)
        hive = self.hive_backup_checkbox
        return ReviewDecision(
            confirmed=True, declined=declined, accepted=accepted, overrode_blockers=overrode,
            hive_backup=hive is not None and hive.isEnabled() and hive.isChecked(),
        )

    def ask(self) -> ReviewDecision:
        self.exec()
        return self.decision()
