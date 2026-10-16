"""Dashboard tiles, health, history."""

import socket

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import health, history, pfjson, symptoms
from ..models import ModuleCategory
from ..settings import PRESETS
from .mw_common import DASHBOARD_SYMPTOM_CHIPS, HISTORY_MAX_ROWS, _DashboardTile


class DashboardMixin:
    """Dashboard tiles, health, history.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _build_dashboard_card(self, category_i18n_keys: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("actionCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 10, 14, 10)
        card_layout.setSpacing(10)

        heading = QLabel(self._t("category_dashboard"))
        heading.setObjectName("cardHeading")
        card_layout.addWidget(heading)

        # B14: one-time "start here" hint, dismissed for good.
        self.first_run_banner = self._build_first_run_banner()
        card_layout.addWidget(self.first_run_banner)

        # B6 / research G27: the home user's entry point. The client's
        # complaint -> the top 3 symptoms, each a tile like the health tiles
        # (title, why, buttons that only select - nothing runs until Run
        # selected and its review).
        hero = QWidget()
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(0, 0, 0, 0)
        hero_layout.setSpacing(6)
        hero_title = QLabel(self._t("dashboard_symptom_title"))
        hero_title.setObjectName("cardHeading")
        hero_layout.addWidget(hero_title)
        self.symptom_box = QLineEdit()
        self.symptom_box.setObjectName("searchBox")
        self.symptom_box.setPlaceholderText(self._t("symptom_placeholder"))
        self.symptom_box.setMaxLength(symptoms.MAX_QUERY_CHARS)
        self.symptom_box.setAccessibleName(self._t("dashboard_symptom_title"))
        self.symptom_box.textChanged.connect(self._on_symptom_changed)
        hero_layout.addWidget(self.symptom_box)
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        known = {symptom.id: symptom for symptom in self._symptoms.symptoms}
        for symptom_id in DASHBOARD_SYMPTOM_CHIPS:
            if symptom_id in known:
                title = known[symptom_id].title(self.settings.language)
                chip_row.addWidget(self._make_selection_button(title, lambda t=title: self.symptom_box.setText(t)))
        chip_row.addStretch(1)
        hero_layout.addLayout(chip_row)
        self._symptom_results = QWidget()
        self._symptom_results_layout = QHBoxLayout(self._symptom_results)
        self._symptom_results_layout.setContentsMargins(0, 0, 0, 0)
        self._symptom_results_layout.setSpacing(10)
        self._symptom_results.setVisible(False)
        hero_layout.addWidget(self._symptom_results)
        # No Modules/symptoms.yaml (or nothing usable in it): no box.
        hero.setVisible(bool(self._symptoms.symptoms))
        card_layout.addWidget(hero)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)
        # Kept on self so batch start/end can lock it together with
        # run_button - it starts a batch too. Its initial state matters when
        # a language toggle rebuilds the dashboard while a batch, its report
        # or an update download is still in flight.
        self.dashboard_analyze_button = QPushButton(self._t("dashboard_analyze_button"))
        self.dashboard_analyze_button.setObjectName("runButton")
        self.dashboard_analyze_button.setEnabled(not self._batch_start_blocked())
        self.dashboard_analyze_button.clicked.connect(lambda _checked=False: self._run_dashboard_analysis())
        top_row.addWidget(self.dashboard_analyze_button)
        promise_label = QLabel(self._t("dashboard_analyze_promise"))
        promise_label.setObjectName("selectionScope")
        top_row.addWidget(promise_label)
        top_row.addStretch(1)
        card_layout.addLayout(top_row)

        # Research G02: one verdict per health area from the findings the
        # diagnostics reported - no score formula. Unknown until checked.
        health_grid = QGridLayout()
        health_grid.setSpacing(10)
        self._health_tiles = {}
        for index, area in enumerate(pfjson.AREAS):
            tile = QFrame()
            tile.setObjectName("actionCard")
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            tile_layout.setSpacing(2)
            name_label = QLabel(self._t(f"health_area_{area}"))
            name_label.setObjectName("cardHeading")
            state_label = QLabel()
            state_label.setObjectName("healthState")
            reason_label = QLabel()
            reason_label.setObjectName("selectionScope")
            reason_label.setWordWrap(True)
            fix_button = QPushButton(self._t("health_fix_button"))
            fix_button.setObjectName("selectionBtn")
            fix_button.setVisible(False)
            fix_button.clicked.connect(lambda _checked=False, a=area: self._apply_recommended_selection(
                self._health_fixes(a), None))
            for widget in (name_label, state_label, reason_label, fix_button):
                tile_layout.addWidget(widget)
            tile_layout.addStretch(1)
            self._health_tiles[area] = (tile, state_label, reason_label, fix_button)
            health_grid.addWidget(tile, index // 4, index % 4)
        card_layout.addLayout(health_grid)
        # B14: while nothing has been checked, one line instead of seven
        # identical reasons.
        self._health_empty_label = QLabel(self._t("health_unknown_reason"))
        self._health_empty_label.setObjectName("selectionScope")
        card_layout.addWidget(self._health_empty_label)
        self._refresh_health_tiles()


        grid = QGridLayout()
        grid.setSpacing(10)
        tile_categories = [c for c in self._categories_order if c != ModuleCategory.DASHBOARD]
        columns = 4
        for index, category in enumerate(tile_categories):
            tile = _DashboardTile()
            tile.setObjectName("actionCard")
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(10, 8, 10, 8)
            tile_layout.setSpacing(2)
            tile_top = QHBoxLayout()
            name_label = QLabel(self._t(category_i18n_keys.get(category, "")))
            name_label.setObjectName("cardHeading")
            name_label.setWordWrap(True)
            tile_top.addWidget(name_label, 1)
            count_pill = QLabel("0")
            count_pill.setObjectName("countPill")
            count_pill.setProperty("state", "idle")
            tile_top.addWidget(count_pill)
            tile_layout.addLayout(tile_top)
            count = self._category_module_action_counts.get(category, 0)
            sub_label = QLabel(self._tn("dashboard_actions_count", count, "action") if count else "")
            sub_label.setObjectName("selectionScope")
            tile_layout.addWidget(sub_label)
            self._dashboard_tile_count_labels[category] = count_pill
            self._dashboard_tiles[category] = tile
            tile.setAccessibleDescription(self._t("a11y_dashboard_tile_hint"))
            self._update_dashboard_tile_accessible_name(category)
            tile.activated.connect(lambda c=category: self._dashboard_tile_clicked(c))
            grid.addWidget(tile, index // columns, index % columns)
        card_layout.addLayout(grid)

        history_heading = QLabel(self._t("history_heading"))
        history_heading.setObjectName("cardHeading")
        card_layout.addWidget(history_heading)
        # One choice for every history row's "Save client package" button.
        self._history_handoff_diag_checkbox = self._make_handoff_diagnostics_checkbox()
        card_layout.addWidget(self._history_handoff_diag_checkbox)
        history_widget = QWidget()
        self._history_layout = QVBoxLayout(history_widget)
        self._history_layout.setContentsMargins(0, 0, 0, 0)
        self._history_layout.setSpacing(4)
        card_layout.addWidget(history_widget)
        self._refresh_history()

        winget_heading = QLabel(self._t("category_winget"))
        winget_heading.setObjectName("cardHeading")
        card_layout.addWidget(winget_heading)
        card_layout.addWidget(self._build_winget_updates_panel())

        card_layout.addStretch(1)
        return card

    def _update_dashboard_tile_accessible_name(self, category: ModuleCategory) -> None:
        # The tile's text lives in child QLabels focus never reaches - fold
        # name, recommended-fix count and action count into the tile itself.
        tile = self._dashboard_tiles.get(category)
        if tile is None:
            return
        name = self._t(self._category_i18n_keys.get(category, ""))
        pill = self._dashboard_tile_count_labels.get(category)
        findings = pill.text() if pill is not None else "0"
        parts = [name, self._tn("a11y_dashboard_tile_findings", findings, "fix")]
        action_count = self._category_module_action_counts.get(category, 0)
        if action_count:
            parts.append(self._tn("dashboard_actions_count", action_count, "action"))
        tile.setAccessibleName(", ".join(parts))

    def _dashboard_tile_clicked(self, category: ModuleCategory) -> None:
        if category in self._categories_order:
            self.category_list.setCurrentRow(self._categories_order.index(category))

    def _run_dashboard_analysis(self) -> None:
        # Checked before _apply_preset, not left to run_selected_actions:
        # mid-batch the preset would still wipe the technician's checkbox
        # selection even though no second batch starts.
        if self._batch_start_blocked():
            return
        if "full_diagnostic" in PRESETS:
            self._apply_preset("full_diagnostic")
            self.run_selected_actions()

    def _refresh_history(self) -> None:
        layout = getattr(self, "_history_layout", None)
        if layout is None:
            return
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        runs = history.recent_runs(self.state_dir / "Reports", socket.gethostname(), limit=HISTORY_MAX_ROWS)
        if not runs:
            empty = QLabel(self._t("history_empty"))
            empty.setObjectName("selectionScope")
            layout.addWidget(empty)
            return
        for run in runs:
            row = QFrame()
            row.setObjectName("historyRow")
            row.setProperty("failed", run.failed_count > 0)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 4, 6, 4)
            row_layout.setSpacing(8)
            text = QLabel(
                self._tn("history_row", run.action_count, "action", date=run.display_date(), failed=run.failed_count)
            )
            text.setObjectName("historyText")
            row_layout.addWidget(text, 1)
            if run.dry_run:
                tag = QLabel(self._t("history_dry_run_tag"))
                tag.setObjectName("summaryDryRunNote")
                row_layout.addWidget(tag)
            if run.html_path is not None:
                open_button = self._make_selection_button(
                    self._t("history_open"),
                    lambda path=run.html_path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))),
                )
                row_layout.addWidget(open_button)
            handoff_button = self._make_selection_button(
                self._t("handoff_button"),
                lambda rid=run.run_id: self._save_handoff_package(
                    rid, include_diagnostics=self._history_handoff_diag_checkbox.isChecked()
                ),
            )
            handoff_button.setProperty("handoffRunId", run.run_id)
            row_layout.addWidget(handoff_button)
            layout.addWidget(row)

    def _recommended_ids(self) -> list[str]:
        """Fixes of the open problems the findings reported (research G02)."""
        return health.recommended_fixes(self._findings, known_ids=self._action_checkboxes)

    def _health_fixes(self, area: str) -> list[str]:
        problems = {fid: f for fid, f in self._findings.items() if f.get("area") == area}
        return health.recommended_fixes(problems, known_ids=self._action_checkboxes)

    def _refresh_health_tiles(self) -> None:
        verdicts = health.area_verdicts(self._findings)
        message_key = "msg_en" if self.settings.language == "en" else "msg_sk"
        unchecked = all(v["state"] == "unknown" for v in verdicts.values())
        self._health_empty_label.setVisible(unchecked)
        for area, (tile, state_label, reason_label, fix_button) in self._health_tiles.items():
            verdict = verdicts[area]
            state = verdict["state"]
            state_label.setText(self._t("health_state_unchecked" if unchecked else f"health_state_{state}"))
            state_label.setProperty("state", state)
            state_label.style().unpolish(state_label)
            state_label.style().polish(state_label)
            # The reasons, worst first - the evidence behind the verdict.
            reasons = [f.get(message_key) or f.get("msg_en", "") for f in verdict["findings"]]
            reason_label.setText("\n".join(reasons) if reasons else self._t("health_unknown_reason"))
            reason_label.setVisible(not unchecked)
            fix_button.setVisible(state in ("attention", "critical") and bool(self._health_fixes(area)))
            tile.setAccessibleName(
                f"{self._t(f'health_area_{area}')}: {self._t(f'health_state_{state}')}. {reason_label.text()}"
            )

    def _refresh_dashboard(self) -> None:
        self._refresh_history()
        if getattr(self, "_health_tiles", None):
            self._refresh_health_tiles()
        counts: dict[ModuleCategory, int] = {}
        for action_id in self._recommended_ids():
            try:
                module, _ = self._find_action(action_id)
            except KeyError:
                continue
            counts[module.category] = counts.get(module.category, 0) + 1
        for category, pill in self._dashboard_tile_count_labels.items():
            needs_fix_count = counts.get(category, 0)
            pill.setText(str(needs_fix_count))
            pill.setProperty("state", "warn" if needs_fix_count else "ok")
            pill.style().unpolish(pill)
            pill.style().polish(pill)
            self._update_dashboard_tile_accessible_name(category)
