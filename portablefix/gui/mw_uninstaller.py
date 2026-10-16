"""The uninstaller card."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from pathlib import Path

from . import style
from .. import panel_safety, paths, uninstall_plan, uninstaller
from ..models import RiskLevel
from .mw_common import _ignore_deleted


class UninstallerPanelMixin:
    """The uninstaller card.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _make_avatar(self, name: str) -> QLabel:
        color, text_color = style.avatar_colors(name)
        words = name.split()
        initials = "".join(w[0] for w in words[:2]).upper() if words else "?"
        avatar = QLabel(initials)
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # High Contrast applies no stylesheet (style.stylesheet): an inline
        # colour here would be the one thing that ignores the user's palette.
        if not style.is_high_contrast():
            avatar.setStyleSheet(
                f"background:{color}; color:{text_color}; border-radius:14px; font-weight:bold; font-size:9pt;"
            )
        return avatar

    def _uninstaller_row_label(self, program: "uninstaller.InstalledProgram") -> str:
        parts = [program.name]
        if program.version:
            parts.append(f"v{program.version}")
        if program.estimated_size_kb:
            parts.append(f"{program.estimated_size_kb / 1024:.1f} MB")
        if program.install_date:
            parts.append(program.install_date)
        return "  |  ".join(parts)

    def _msi_log_dir(self) -> Path:
        # msiexec's verbose log (/l*v) per product, next to the audit log -
        # the first thing to read when an MSI uninstall fails.
        return self.state_dir / "Logs" / f"{self.run_id}_msi"

    def _uninstall_queue_line(self, program: "uninstaller.InstalledProgram", plan: "uninstall_plan.UninstallPlan") -> str:
        if plan.kind in (uninstall_plan.KIND_NONE, uninstall_plan.KIND_UNSAFE):
            return f"{program.name}  [{self._uninstall_no_run_text(plan)}]"
        if plan.kind == uninstall_plan.KIND_INTERACTIVE:
            return f"{program.name}  [{self._t('uninstaller_confirm_interactive_marker')}]"
        kind = self._t(f"uninstaller_kind_{plan.kind}")
        return f"{program.name}  [{self._t('uninstaller_confirm_silent_marker')}, {kind}]"

    def _uninstall_no_run_text(self, plan: "uninstall_plan.UninstallPlan") -> str:
        # Why a plan runs nothing: no command at all, or refused as unsafe.
        if plan.kind == uninstall_plan.KIND_UNSAFE:
            return self._t("uninstaller_unsafe_command")
        return self._t("uninstaller_no_command")

    def _uninstall_queue_summary(self, interactive: list, silent: list) -> list[str]:
        # Which queue each program is in, shown before anything starts.
        lines = []
        if interactive:
            lines.append(self._t("uninstaller_queue_interactive_heading").format(
                count=len(interactive), programs=", ".join(p.name for p in interactive),
            ))
        if silent:
            lines.append(self._t("uninstaller_queue_silent_heading").format(
                count=len(silent), programs=", ".join(p.name for p in silent),
                minutes=uninstaller.UNINSTALL_TIMEOUT_SEC // 60,
            ))
        return lines

    def _build_uninstaller_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("actionCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(8)
        heading = QLabel(self._t("category_uninstaller"))
        heading.setObjectName("cardHeading")
        card_layout.addWidget(heading)

        search_box = QLineEdit()
        search_box.setObjectName("searchBox")
        search_box.setPlaceholderText(self._t("uninstaller_search_placeholder"))
        card_layout.addWidget(search_box)

        list_status_label = QLabel("")
        list_status_label.setObjectName("selectionScope")
        list_status_label.setVisible(False)
        card_layout.addWidget(list_status_label)

        programs = uninstaller.list_installed_programs()
        app_dir = str(paths.get_base_dir())
        # name -> PROTECTED_* code (research G01): shown, never uninstallable.
        protected: dict[str, str] = {}
        row_checkboxes: dict[str, QCheckBox] = {}
        row_widgets: dict[str, QWidget] = {}
        program_by_name: dict[str, uninstaller.InstalledProgram] = {}

        list_container = QWidget()
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(2)
        for program in programs:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(self._make_avatar(program.name))
            checkbox = QCheckBox(self._uninstaller_row_label(program))
            tooltip = program.install_location or ""
            if program.publisher:
                tooltip = f"{program.publisher}\n{tooltip}" if tooltip else program.publisher
            reason = panel_safety.protected_reason(program, app_dir)
            if reason is not None:
                protected[program.name] = reason
                checkbox.setText(f"{checkbox.text()}  [{self._t('uninstaller_protected_marker')}]")
                checkbox.setEnabled(False)
                reason_text = self._t(f"uninstaller_protected_{reason}")
                tooltip = f"{reason_text}\n{tooltip}" if tooltip else reason_text
            checkbox.setToolTip(tooltip)
            row.addWidget(checkbox, 1)
            row_checkboxes[program.name] = checkbox
            row_widgets[program.name] = row_widget
            program_by_name[program.name] = program
            list_layout.addWidget(row_widget)
        list_layout.addStretch(1)

        list_scroll = QScrollArea()
        list_scroll.setWidgetResizable(True)
        list_scroll.setMaximumHeight(320)
        list_scroll.setWidget(list_container)
        card_layout.addWidget(list_scroll)

        def apply_search(text: str) -> None:
            needle = text.strip().lower()
            visible_count = 0
            for name, row_widget in row_widgets.items():
                is_match = not needle or needle in name.lower()
                row_widget.setHidden(not is_match)
                if is_match:
                    visible_count += 1
            if not row_widgets:
                # No installed programs at all (registry gave nothing back) -
                # an empty scroll area with no explanation looks broken.
                list_status_label.setText(self._t("uninstaller_no_programs"))
                list_status_label.setVisible(True)
            elif needle and visible_count == 0:
                list_status_label.setText(self._t("search_no_matches").format(query=text.strip()))
                list_status_label.setVisible(True)
            else:
                list_status_label.setVisible(False)

        search_box.textChanged.connect(apply_search)
        apply_search("")

        select_row = QHBoxLayout()
        select_all_btn = self._make_selection_button(
            self._t("select_all"),
            lambda: [
                cb.setChecked(True) for name, cb in row_checkboxes.items()
                if not row_widgets[name].isHidden() and name not in protected
            ],
        )
        select_row.addWidget(select_all_btn)
        select_none_btn = self._make_selection_button(
            self._t("select_none"), lambda: [cb.setChecked(False) for cb in row_checkboxes.values()]
        )
        select_row.addWidget(select_none_btn)
        select_row.addStretch(1)
        card_layout.addLayout(select_row)

        console = QPlainTextEdit()
        console.setObjectName("console")
        console.setReadOnly(True)
        console.setMaximumHeight(140)
        console.setVisible(False)
        card_layout.addWidget(console)

        cleanup_container = QWidget()
        cleanup_layout = QVBoxLayout(cleanup_container)
        cleanup_layout.setContentsMargins(0, 0, 0, 0)
        cleanup_layout.setSpacing(2)
        cleanup_container.setVisible(False)
        card_layout.addWidget(cleanup_container)

        uninstall_button = QPushButton(self._t("uninstaller_uninstall_button"))
        uninstall_button.setObjectName("runButton")
        card_layout.addWidget(uninstall_button)

        def show_orphan_cleanup() -> None:
            while cleanup_layout.count():
                item = cleanup_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            orphans = uninstaller.find_orphaned_uninstall_entries()
            if not orphans:
                cleanup_container.setVisible(False)
                return
            heading = QLabel(self._t("uninstaller_leftovers_heading"))
            heading.setObjectName("cardHeading")
            cleanup_layout.addWidget(heading)
            # InstalledProgram is a plain (unhashable) dataclass - using it as
            # the key raised TypeError. Where the entry lives is its identity.
            orphan_checkboxes: dict[tuple[int, str], QCheckBox] = {}
            orphan_by_key: dict[tuple[int, str], uninstaller.InstalledProgram] = {}
            for orphan in orphans:
                key = (orphan.registry_hive, orphan.registry_path)
                cb = QCheckBox(orphan.name)
                # Unchecked: every deleted entry is the technician's explicit
                # pick, not a default they would have to notice and untick.
                cb.setToolTip(f"{orphan.install_location or ''}\n{uninstaller.registry_key_name(*key)}")
                orphan_checkboxes[key] = cb
                orphan_by_key[key] = orphan
                cleanup_layout.addWidget(cb)
            clean_button = QPushButton(self._t("uninstaller_clean_leftovers_button"))
            clean_button.setObjectName("selectionBtn")

            def do_clean() -> None:
                chosen = [orphan_by_key[key] for key, cb in orphan_checkboxes.items() if cb.isChecked()]
                if not chosen:
                    return
                console.setVisible(True)
                risk = RiskLevel.DESTRUCTIVE.value

                def delete_command(orphan: uninstaller.InstalledProgram) -> str:
                    key_name = uninstaller.registry_key_name(orphan.registry_hive, orphan.registry_path)
                    return f'reg delete "{key_name}" /f'

                if self.settings.dry_run:
                    console.appendPlainText(self._t("dry_run_batch_note"))
                    for orphan in chosen:
                        command = delete_command(orphan)
                        console.appendPlainText(f"[DRY-RUN] {command}")
                        self._log_panel_action(
                            "_uninstaller", f"orphan_cleanup:{orphan.name}", command, 0, f"[DRY-RUN] {command}",
                            True, risk,
                        )
                    return
                if self._panel_refused_without_admin(console):
                    return
                warning_text = self._t("uninstaller_orphan_confirm_text").format(
                    count=len(chosen),
                    entries=self._panel_confirm_list([
                        f"{o.name} ({uninstaller.registry_key_name(o.registry_hive, o.registry_path)})" for o in chosen
                    ]),
                )
                answer = QMessageBox.warning(
                    self, self._t("uninstaller_leftovers_heading"), warning_text,
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    for orphan in chosen:
                        self._log_system_event(
                            "risk_declined", None, "Technician declined the leftover registry cleanup - entry not deleted.",
                            risk=risk, warned=True, warning_text=warning_text,
                            subject=f"_uninstaller/orphan_cleanup:{orphan.name}", decision="declined",
                        )
                    return

                def after_restore_point(proceed: bool) -> None:
                    clean_button.setEnabled(True)
                    if not proceed:
                        console.appendPlainText(self._t("panel_cancelled_no_restore_point"))
                        return
                    delete_chosen(chosen, warning_text)

                # The .reg export covers each key; the restore point (G01)
                # covers what a wrongly flagged entry's program still needed.
                clean_button.setEnabled(False)
                if not self._start_panel_restore_point(
                    [f"_uninstaller/orphan_cleanup:{orphan.name}" for orphan in chosen], console, after_restore_point,
                ):
                    clean_button.setEnabled(True)

            def delete_chosen(chosen: list, warning_text: str) -> None:
                risk = RiskLevel.DESTRUCTIVE.value

                def delete_command(orphan: uninstaller.InstalledProgram) -> str:
                    key_name = uninstaller.registry_key_name(orphan.registry_hive, orphan.registry_path)
                    return f'reg delete "{key_name}" /f'

                backup_dir = self.state_dir / "Backups" / self.run_id
                removed = 0
                for orphan in chosen:
                    command = delete_command(orphan)
                    action_id = f"orphan_cleanup:{orphan.name}"
                    backup_path = uninstaller.orphan_backup_path(backup_dir, orphan.name)
                    # No backup, no delete: a wrongly flagged entry (e.g. a
                    # program on a drive that reappears later) must stay
                    # recoverable with "reg import".
                    if not uninstaller.backup_registry_key(orphan.registry_hive, orphan.registry_path, backup_path):
                        console.appendPlainText(self._t("uninstaller_orphan_backup_failed").format(name=orphan.name))
                        self._log_panel_action(
                            "_uninstaller", action_id, command, 1,
                            f"Registry backup to {backup_path} failed - entry not deleted.",
                            False, risk, True, warning_text,
                        )
                        continue
                    deleted = uninstaller.remove_registry_key(orphan.registry_hive, orphan.registry_path)
                    output = f"Backup: {backup_path}"
                    if deleted:
                        removed += 1
                        # The .reg backup makes this reversible - undo.ps1
                        # re-imports it (PowerShell single-quote escaping).
                        quoted = str(backup_path).replace("'", "''")
                        self._undo_steps.append(f"reg import '{quoted}'")
                    else:
                        output += "\nDeleting the registry entry failed."
                        console.appendPlainText(f"[{self._t('status_failed')}] {orphan.name}")
                    self._log_panel_action(
                        "_uninstaller", action_id, command, 0 if deleted else 1, output,
                        False, risk, True, warning_text,
                    )
                console.appendPlainText(self._t("uninstaller_leftovers_removed").format(count=removed))
                if removed:
                    self._write_undo_script()
                # Rescan: deleted entries drop off the list, anything that
                # failed or was left unticked stays for another try.
                show_orphan_cleanup()

            clean_button.clicked.connect(lambda _checked=False: do_clean())
            cleanup_layout.addWidget(clean_button)
            cleanup_container.setVisible(True)

        def start_uninstall() -> None:
            selected = [program_by_name[name] for name, cb in row_checkboxes.items() if cb.isChecked()]
            if not selected:
                return
            risk = RiskLevel.DESTRUCTIVE.value
            # Protected rows can't be ticked, but a checkbox can still be set
            # programmatically - refuse here too, and say so on record.
            refused = [p for p in selected if p.name in protected]
            if refused:
                selected = [p for p in selected if p.name not in protected]
                lines = [f"{p.name} - {self._t('uninstaller_protected_' + protected[p.name])}" for p in refused]
                console.setVisible(True)
                for line in lines:
                    console.appendPlainText(f"[{self._t('uninstaller_protected_marker')}] {line}")
                for program in refused:
                    self._log_system_event(
                        "protected_program", None,
                        f"Uninstall refused - protected program ({protected[program.name]}).",
                        risk=risk, subject=f"_uninstaller/{program.name}",
                    )
                if not self.settings.dry_run:
                    QMessageBox.information(
                        self, self._t("uninstaller_confirm_title"),
                        self._t("uninstaller_protected_refused").format(programs=self._panel_confirm_list(lines)),
                    )
                if not selected:
                    return
            # Built once (research G15): the installer type decides the
            # silent switches and the queue, and the same plan is what the
            # dialog shows, the runner runs and the audit log records.
            plans = {p.name: uninstaller.program_plan(p, self._msi_log_dir()) for p in selected}
            interactive_queue, silent_queue = uninstall_plan.split_queues(selected, plans)
            selected = interactive_queue + silent_queue
            if self.settings.dry_run:
                # DRY-RUN never starts an uninstaller - it shows and logs the
                # exact command each one would run, and the rows stay put.
                console.setVisible(True)
                console.appendPlainText(self._t("uninstaller_dry_run_notice"))
                for line in self._uninstall_queue_summary(interactive_queue, silent_queue):
                    console.appendPlainText(line)
                for program in selected:
                    command = plans[program.name].command
                    console.appendPlainText(
                        f"[DRY-RUN] {program.name}: {command or self._uninstall_no_run_text(plans[program.name])}"
                    )
                    self._log_panel_action(
                        "_uninstaller", program.name, command, 0,
                        f"[DRY-RUN] {command}" if command else "[DRY-RUN] No uninstall command found for this program.",
                        True, risk,
                    )
                # Same next step as a real run: the leftover scan only reads
                # the registry, and cleaning honours DRY-RUN as well.
                show_orphan_cleanup()
                return
            if self._panel_refused_without_admin(console):
                return
            # A silent uninstall runs with no uninstaller window at all -
            # the only chance to stop it is this dialog, so say so; and say
            # which queue each program is in (research G15).
            warning_text = self._t("uninstaller_confirm_text").format(
                count=len(selected),
                programs=self._panel_confirm_list([self._uninstall_queue_line(p, plans[p.name]) for p in selected]),
            ) + "\n\n" + self._t("uninstaller_confirm_queues_note").format(
                minutes=uninstaller.UNINSTALL_TIMEOUT_SEC // 60,
            )
            answer = QMessageBox.warning(
                self, self._t("uninstaller_confirm_title"), warning_text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                # Logged like a declined catalog action (_dispatch_action).
                for program in selected:
                    self._log_system_event(
                        "risk_declined", None, "Technician declined the uninstall confirmation - program not uninstalled.",
                        risk=risk, warned=True, warning_text=warning_text,
                        subject=f"_uninstaller/{program.name}", decision="declined",
                    )
                return
            # A running program's uninstaller fails half-way or leaves files
            # it could not delete (research G01) - ask to close it first.
            if not self._confirm_programs_closed(
                self._t("uninstaller_confirm_title"),
                [(p.name, p, f"_uninstaller/{p.name}") for p in selected],
                [f"_uninstaller/{p.name}" for p in selected], risk,
                "Technician cancelled the uninstall - the program was still running, not uninstalled.",
            ):
                return

            def set_controls_enabled(enabled: bool) -> None:
                uninstall_button.setEnabled(enabled)
                select_all_btn.setEnabled(enabled)
                select_none_btn.setEnabled(enabled)

            def after_restore_point(proceed: bool) -> None:
                if not proceed:
                    set_controls_enabled(True)
                    console.appendPlainText(self._t("panel_cancelled_no_restore_point"))
                    return
                run_uninstall(selected, plans, warning_text)

            # An uninstall has no undo - the restore point is the way back.
            set_controls_enabled(False)
            if not self._start_panel_restore_point(
                [f"_uninstaller/{program.name}" for program in selected], console, after_restore_point,
            ):
                set_controls_enabled(True)

        def run_uninstall(selected: list, plans: dict, warning_text: str) -> None:
            risk = RiskLevel.DESTRUCTIVE.value
            # Still installed after msiexec 1618 - the row stays for a retry.
            keep_rows: set[str] = set()
            uninstall_button.setEnabled(False)
            select_all_btn.setEnabled(False)
            select_none_btn.setEnabled(False)
            console.setVisible(True)
            console.appendPlainText(self._t("uninstaller_running"))
            for line in self._uninstall_queue_summary(*uninstall_plan.split_queues(selected, plans)):
                console.appendPlainText(line)
            # On self, not the card: closeEvent and the app update's hand-off
            # guard must both know an uninstall is still running.
            runner = uninstaller.UninstallRunner(selected, parent=self, plans=plans)
            self._uninstall_runner = runner

            def on_program_finished(name: str, ok: bool, output: str, outcome: str) -> None:
                # Recorded before any widget is touched, so a console error
                # can never leave a real uninstall out of the log.
                plan = plans.get(name)
                command = plan.command if plan is not None else ""
                self._log_panel_action(
                    "_uninstaller", name, command, 0 if ok else 1, output, False, risk, True, warning_text,
                )
                # An uninstall has no rollback - undo.ps1 lists it under
                # "NOT reversible" (a failed one too: it may have removed
                # part of the program before failing).
                # Not for a program with no uninstall command at all: nothing
                # ran, so there is nothing to call irreversible.
                if command:
                    irreversible = f"[{risk}] {self._t('category_uninstaller')}: {name}"
                    if not ok:
                        irreversible += " - exit 1"
                    self._irreversible_actions.append(irreversible)
                    self._write_undo_script()
                if outcome == uninstall_plan.OUTCOME_BUSY_RETRY:
                    keep_rows.add(name)
                status = self._t("status_ok") if ok else self._t("status_failed")
                console.appendPlainText(f"[{status}] {name}")
                # msiexec's 1605/1641/3010/1618 etc. in the technician's
                # language; the English text for the audit stays in the output.
                if outcome and outcome != uninstall_plan.OUTCOME_OK:
                    console.appendPlainText(self._t(f"uninstaller_outcome_{outcome}"))
                if output:
                    console.appendPlainText(output)

            def on_all_finished() -> None:
                uninstall_button.setEnabled(True)
                select_all_btn.setEnabled(True)
                select_none_btn.setEnabled(True)
                for program in selected:
                    if program.name in keep_rows:
                        continue
                    row_checkboxes.pop(program.name, None)
                    row_widget = row_widgets.pop(program.name, None)
                    if row_widget is not None:
                        row_widget.setParent(None)
                        row_widget.deleteLater()
                show_orphan_cleanup()

            runner.program_finished.connect(_ignore_deleted(on_program_finished))
            runner.all_finished.connect(_ignore_deleted(on_all_finished))
            runner.start()

        uninstall_button.clicked.connect(lambda _checked=False: start_uninstall())
        return card
