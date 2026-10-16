"""Building the main window's widgets."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import style
from .. import paths, preflight, target_user
from ..models import ModuleCategory, RiskLevel
from ..version import APP_VERSION
from .mw_common import CONSOLE_MAX_LINES, _DashboardTile, _UPDATE_PHASE_KEYS


class LayoutMixin:
    """Building the main window's widgets.

    Methods moved verbatim out of MainWindow, which inherits them."""

    def _build_target_user_banner(self) -> QLabel:
        """Over-the-shoulder elevation (research G25): say up front that
        user settings go to the signed-in client, not the technician. Hidden
        when both are the same account or detection could not run."""
        target = self.target_user
        banner = QLabel("")
        banner.setObjectName("targetUserBanner")
        banner.setWordWrap(True)
        process_user = target.process_user or target.process_sid or "?"
        if target.differs:
            user = target.target_user or target.target_sid or "?"
            banner.setText(self._t("target_user_banner").format(user=user, process_user=process_user))
            banner.setToolTip(self._t("target_user_banner_tooltip").format(
                user=user, process_user=process_user, sid=target.target_sid or "?",
            ))
        elif target.status in (target_user.AMBIGUOUS, target_user.NO_USER):
            banner.setText(self._t("target_user_unsure_banner").format(process_user=process_user))
        banner.setAccessibleName(banner.text())
        banner.setVisible(bool(banner.text()))
        return banner

    def _build_first_run_banner(self) -> QFrame:
        """B14: a one-time "start here" hint on the Dashboard; the close
        button hides it for good (Settings.first_run_seen)."""
        banner = QFrame()
        banner.setObjectName("firstRunBanner")
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 8, 8)
        label = QLabel(self._t("first_run_hint"))
        label.setWordWrap(True)
        row.addWidget(label, 1)
        close = QToolButton()
        close.setObjectName("selectionBtn")
        close.setText("✕")
        close.setToolTip(self._t("first_run_dismiss"))
        close.setAccessibleName(self._t("first_run_dismiss"))
        close.clicked.connect(self._dismiss_first_run_hint)
        row.addWidget(close)
        banner.setAccessibleName(label.text())
        banner.setVisible(not self.settings.first_run_seen)
        return banner

    def _dismiss_first_run_hint(self) -> None:
        self.first_run_banner.setVisible(False)
        if not self.settings.first_run_seen:
            self.settings.first_run_seen = True
            self._persist_settings()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{self._t('app_title')} v{APP_VERSION}")
        style.set_theme(self.settings.theme)
        self.setStyleSheet(style.stylesheet())
        central = QWidget(self)
        central.setObjectName("central")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(14, 14, 14, 14)
        root_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        icon_path = paths.app_icon_path(self.assets_dir)
        if icon_path.exists():
            logo_label = QLabel()
            logo_label.setPixmap(QIcon(str(icon_path)).pixmap(28, 28))
            top_bar.addWidget(logo_label)
        title_label = QLabel(self._t("app_title"))
        title_label.setObjectName("appTitle")
        top_bar.addWidget(title_label)
        admin_text = "admin" if self.is_admin else self._t("readonly_banner")
        self.admin_label = QLabel(admin_text)
        self.admin_label.setObjectName("adminPill")
        self.admin_label.setProperty("admin", "true" if self.is_admin else "false")
        top_bar.addWidget(self.admin_label)
        self.restart_admin_button = QPushButton(self._t("restart_as_admin"))
        self.restart_admin_button.setVisible(not self.is_admin)
        self.restart_admin_button.clicked.connect(self._on_restart_as_admin)
        top_bar.addWidget(self.restart_admin_button)
        top_bar.addStretch(1)
        self.job_button = self._make_selection_button(self._t("job_button"), self._open_job_dialog)
        self.job_button.setObjectName("jobBtn")
        self.job_button.setToolTip(f"{self._t('job_tooltip')} (Ctrl+J)")
        top_bar.addWidget(self.job_button)
        self._refresh_job_button()
        self.dry_run_checkbox = QCheckBox(self._t("dry_run_toggle"))
        self.dry_run_checkbox.setChecked(self.settings.dry_run)
        self.dry_run_checkbox.toggled.connect(self._on_dry_run_toggled)
        top_bar.addWidget(self.dry_run_checkbox)
        self.language_button = QPushButton(self.settings.language.upper())
        self.language_button.clicked.connect(self._on_toggle_language)
        # The button shows the current language; say what a click does.
        target_language = "English" if self.settings.language == "sk" else "Slovenčina"
        self.language_button.setToolTip(self._t("language_switch_tooltip").format(language=target_language))
        self.language_button.setAccessibleName(self._t("language_switch_tooltip").format(language=target_language))
        top_bar.addWidget(self.language_button)
        top_bar.addWidget(self._build_settings_button())
        root_layout.addLayout(top_bar)

        self.target_user_banner = self._build_target_user_banner()
        root_layout.addWidget(self.target_user_banner)

        self.update_banner = QWidget()
        self.update_banner.setObjectName("updateBanner")
        update_banner_layout = QHBoxLayout(self.update_banner)
        update_banner_layout.setContentsMargins(10, 6, 10, 6)
        self.update_banner_label = QLabel("")
        update_banner_layout.addWidget(self.update_banner_label, 1)
        self.update_button = QPushButton(self._t("update_button"))
        self.update_button.setObjectName("runButton")
        self.update_button.clicked.connect(lambda _checked=False: self._on_update_button_clicked())
        update_banner_layout.addWidget(self.update_button)
        self.update_dismiss_button = QPushButton(self._t("update_dismiss"))
        self.update_dismiss_button.setObjectName("selectionBtn")
        self.update_dismiss_button.clicked.connect(lambda _checked=False: self._on_update_dismiss_clicked())
        update_banner_layout.addWidget(self.update_dismiss_button)
        self.update_banner.setVisible(False)
        root_layout.addWidget(self.update_banner)

        self.write_banner = QWidget()
        self.write_banner.setObjectName("writeBanner")
        write_banner_layout = QHBoxLayout(self.write_banner)
        write_banner_layout.setContentsMargins(10, 6, 10, 6)
        self.write_banner_label = QLabel("")
        self.write_banner_label.setWordWrap(True)
        write_banner_layout.addWidget(self.write_banner_label, 1)
        self.write_retry_button = QPushButton(self._t("write_retry"))
        self.write_retry_button.clicked.connect(self._on_write_retry)
        write_banner_layout.addWidget(self.write_retry_button)
        root_layout.addWidget(self.write_banner)
        self._refresh_write_banner()

        category_i18n_keys = {
            ModuleCategory.DIAGNOSTICS: "category_diagnostics",
            ModuleCategory.CLEANUP: "category_cleanup",
            ModuleCategory.REPAIR: "category_repair",
            ModuleCategory.SECURITY: "category_security",
            ModuleCategory.ANTIVIRUS: "category_antivirus",
            ModuleCategory.DRIVER_UPDATES: "category_driver_updates",
            ModuleCategory.WINGET: "category_winget",
            ModuleCategory.UNINSTALLER: "category_uninstaller",
            ModuleCategory.DASHBOARD: "category_dashboard",
        }
        self._categories_order: list[ModuleCategory] = []
        for module in self.modules:
            if module.category not in self._categories_order:
                self._categories_order.append(module.category)
        # Uninstaller has no YAML-declared actions of its own (installed
        # programs are dynamic, discovered at dialog-open time, not a fixed
        # catalog) - always show its sidebar entry regardless of modules.
        if ModuleCategory.UNINSTALLER not in self._categories_order:
            self._categories_order.append(ModuleCategory.UNINSTALLER)
        # Dashboard is a pure navigation/overview screen, not module-backed -
        # always shown first so it's the default landing view.
        self._categories_order.insert(0, ModuleCategory.DASHBOARD)

        risk_tab_order = [RiskLevel.SAFE, RiskLevel.MODERATE, RiskLevel.DESTRUCTIVE, RiskLevel.REQUIRES_REBOOT]
        self._risk_action_ids: dict[RiskLevel, list[str]] = {r: [] for r in risk_tab_order}
        for module in self.modules:
            for action in module.actions:
                self._risk_action_ids[action.risk].append(action.id)
        self._risk_tabs_order = [r for r in risk_tab_order if self._risk_action_ids[r]]

        body_layout = QHBoxLayout()
        body_layout.setSpacing(10)
        self.category_list = QListWidget()
        self.category_list.setObjectName("categoryList")
        category_labels = [self._t(category_i18n_keys[category]) for category in self._categories_order]
        category_labels += [f"{self._t('risk_tab_prefix')} {self._risk_text(risk)}" for risk in self._risk_tabs_order]
        for label in category_labels:
            self.category_list.addItem(QListWidgetItem(label))
        # Fixed 190px clipped longer entries (e.g. "Risk: REQUIRES_REBOOT")
        # behind a horizontal scrollbar - size to the longest actual label
        # instead so everything is readable without scrolling sideways.
        # +64 covers the stylesheet's item padding (12px each side) and
        # margin (4px each side), list padding (6px each side) and border.
        # Measured in bold: the selected item is rendered bold (see style.py),
        # and measuring the regular weight elided the longest labels
        # ("Odinstalovanie programov", "Riziko: REQUIRES_REBOOT") with "..."
        # as soon as they were selected.
        self.category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bold_font = QFont(self.category_list.font())
        bold_font.setBold(True)
        metrics = QFontMetrics(bold_font)
        widest_label = max((metrics.horizontalAdvance(label) for label in category_labels), default=0)
        self.category_list.setFixedWidth(min(max(widest_label + 64, 190), 300))
        body_layout.addWidget(self.category_list)

        center_layout = QVBoxLayout()
        center_layout.setSpacing(8)

        global_select_row = QHBoxLayout()
        global_select_row.setSpacing(6)
        scope_label = QLabel(self._t("all_categories"))
        scope_label.setObjectName("selectionScope")
        global_select_row.addWidget(scope_label)
        # One menu instead of six buttons in a row: the row alone needed
        # ~800 px and kept the window from fitting a 1366 x 768 laptop.
        self.global_select_button = QToolButton()
        self.global_select_button.setObjectName("selectionBtn")
        self.global_select_button.setText(self._t("select_menu"))
        self.global_select_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.global_select_button.setCursor(Qt.CursorShape.PointingHandCursor)
        global_select_menu = QMenu(self.global_select_button)

        def _select_action(text: str, mode: str):
            action = global_select_menu.addAction(text)
            action.triggered.connect(lambda _checked=False: self._apply_selection(list(self._action_checkboxes), mode))
            return action

        def _select_only(risk: RiskLevel):
            text = self._t("select_risk_only").format(risk=self._risk_text(risk))
            return _select_action(text, risk.value)

        self.global_select_all_button = _select_action(self._t("select_all"), "all")
        self.global_select_safe_button = _select_only(RiskLevel.SAFE)
        self.global_select_moderate_button = _select_only(RiskLevel.MODERATE)
        self.global_select_destructive_button = _select_only(RiskLevel.DESTRUCTIVE)
        self.global_select_reboot_button = _select_only(RiskLevel.REQUIRES_REBOOT)
        global_select_menu.addSeparator()
        self.global_select_none_button = _select_action(self._t("select_none"), "none")
        self.global_select_none_button.setEnabled(False)
        self.global_select_button.setMenu(global_select_menu)
        global_select_row.addWidget(self.global_select_button)
        global_select_row.addStretch(1)
        center_layout.addLayout(global_select_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_label = QLabel(self._t("presets_label"))
        preset_label.setObjectName("selectionScope")
        preset_row.addWidget(preset_label)
        self._preset_buttons: dict[str, QPushButton] = {}
        preset_row.addWidget(self._make_preset_button(self._t("preset_quick_clean"), "quick_clean"))
        preset_row.addWidget(self._make_preset_button(self._t("preset_full_diagnostic"), "full_diagnostic"))
        preset_row.addWidget(self._make_preset_button(self._t("preset_privacy_debloat"), "privacy_debloat"))
        preset_row.addStretch(1)
        self.search_box = QLineEdit()
        self.search_box.setObjectName("searchBox")
        self.search_box.setPlaceholderText(self._t("search_placeholder"))
        self.search_box.setAccessibleName(self._t("search_placeholder"))
        self.search_box.setToolTip(f"{self._t('search_placeholder')} (Ctrl+F)")
        self.search_box.setMaximumWidth(220)
        self.search_box.textChanged.connect(self._on_search_changed)
        # Esc clears the search - handled on the box itself (eventFilter)
        # rather than as a window-wide shortcut, which would also swallow
        # Esc from dialogs and the console.
        self.search_box.installEventFilter(self)
        preset_row.addWidget(self.search_box)
        center_layout.addLayout(preset_row)

        # User-saved presets get their own row: sharing the built-in preset
        # row squeezed the search box down to nothing once a couple existed.
        # The inner sub-layout lets saving/deleting rebuild only these
        # buttons, not the whole window.
        custom_preset_row = QHBoxLayout()
        custom_preset_row.setSpacing(6)
        custom_preset_label = QLabel(self._t("custom_presets_label"))
        custom_preset_label.setObjectName("selectionScope")
        custom_preset_row.addWidget(custom_preset_label)
        self._custom_preset_layout = QHBoxLayout()
        self._custom_preset_layout.setSpacing(6)
        self._custom_preset_layout.setContentsMargins(0, 0, 0, 0)
        custom_preset_row.addLayout(self._custom_preset_layout)
        self.save_preset_button = self._make_selection_button(self._t("preset_save_button"), self._on_save_preset_clicked)
        self.save_preset_button.setEnabled(False)
        custom_preset_row.addWidget(self.save_preset_button)
        custom_preset_row.addStretch(1)
        self._rebuild_custom_preset_buttons()
        center_layout.addLayout(custom_preset_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 6, 0)
        scroll_layout.setSpacing(8)
        self._search_empty_label = QLabel()
        self._search_empty_label.setObjectName("emptyState")
        self._search_empty_label.setWordWrap(True)
        self._search_empty_label.setHidden(True)
        scroll_layout.addWidget(self._search_empty_label)

        self._category_groups: dict[ModuleCategory, QWidget] = {}
        self._category_action_ids: dict[ModuleCategory, list[str]] = {}
        self._category_select_buttons: dict[ModuleCategory, tuple[QPushButton, QPushButton, QPushButton]] = {}
        self._category_module_action_counts: dict[ModuleCategory, int] = {}
        for module in self.modules:
            self._category_module_action_counts[module.category] = (
                self._category_module_action_counts.get(module.category, 0) + len(module.actions)
            )
        self._dashboard_tile_count_labels: dict[ModuleCategory, QLabel] = {}
        self._dashboard_tiles: dict[ModuleCategory, _DashboardTile] = {}
        self._category_i18n_keys = category_i18n_keys
        self._health_tiles: dict = {}
        for category in self._categories_order:
            if category == ModuleCategory.DASHBOARD:
                self._category_action_ids[category] = []
                card = self._build_dashboard_card(category_i18n_keys)
                self._category_groups[category] = card
                scroll_layout.addWidget(card)
                continue
            if category == ModuleCategory.UNINSTALLER:
                self._category_action_ids[category] = []
                card = self._build_uninstaller_card()
                self._category_groups[category] = card
                scroll_layout.addWidget(card)
                continue
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            def _make_select_buttons_row(c=category) -> QHBoxLayout:
                row = QHBoxLayout()
                row.setSpacing(6)
                row.addStretch(1)
                all_btn = self._make_selection_button(
                    self._t("select_all"), lambda: self._apply_selection(self._category_action_ids[c], "all")
                )
                row.addWidget(all_btn)
                safe_btn = self._make_selection_button(
                    self._t("select_safe_only"),
                    lambda: self._apply_selection(self._category_action_ids[c], RiskLevel.SAFE.value),
                )
                row.addWidget(safe_btn)
                none_btn = self._make_selection_button(
                    self._t("select_none"), lambda: self._apply_selection(self._category_action_ids[c], "none")
                )
                row.addWidget(none_btn)
                self._category_select_buttons[c] = (all_btn, safe_btn, none_btn)
                return row

            heading_row = QHBoxLayout()
            heading_row.setSpacing(6)
            heading = QLabel(self._t(category_i18n_keys[category]))
            heading.setObjectName("cardHeading")
            heading_row.addWidget(heading)
            if category != ModuleCategory.WINGET:
                heading_row.addLayout(_make_select_buttons_row())
            else:
                heading_row.addStretch(1)
            card_layout.addLayout(heading_row)
            if category == ModuleCategory.WINGET:
                # The live update panel now lives on the Prehlad dashboard
                # card - this page keeps only the static winget actions
                # (list installed, export, source management).
                card_layout.addLayout(_make_select_buttons_row())
            self._category_action_ids[category] = []
            for module in self.modules:
                if module.category != category:
                    continue
                for action in module.actions:
                    self._category_action_ids[category].append(action.id)
                    row_widget = QWidget()
                    row = QHBoxLayout(row_widget)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.setSpacing(8)
                    checkbox = QCheckBox(action.label(self.settings.language))
                    checkbox.setToolTip(action.description(self.settings.language))
                    checkbox.setAccessibleDescription(action.description(self.settings.language))
                    checkbox.setAccessibleName(self._action_accessible_name(action))
                    checkbox.stateChanged.connect(self._on_checkbox_state_changed)
                    self._action_checkboxes[action.id] = checkbox
                    row.addWidget(checkbox)
                    badge = QLabel(self._risk_text(action.risk))
                    badge.setObjectName("riskBadge")
                    badge.setToolTip(action.risk.value)
                    badge.setProperty("risk", action.risk.value)
                    row.addWidget(badge)
                    if not self.is_admin and preflight.profile_for([(module, action)]).needs_admin:
                        # Same predicate as the pre-flight "no_admin" blocker:
                        # say it here, not only after "Run".
                        admin_chip = QLabel(self._t("admin_chip"))
                        admin_chip.setObjectName("riskBadge")
                        admin_chip.setProperty("risk", "ADMIN")
                        admin_chip.setToolTip(self._t("admin_chip_tip"))
                        admin_chip.setAccessibleName(self._t("admin_chip_tip"))
                        row.addWidget(admin_chip)
                    if module.custom:
                        # Research G32: from UserModules/ - the shop's own
                        # action, not shipped with or checked by PortableFix.
                        custom_badge = QLabel(self._t("custom_badge"))
                        custom_badge.setObjectName("riskBadge")
                        custom_badge.setProperty("risk", "CUSTOM")
                        custom_badge.setToolTip(self._t("custom_badge_tip"))
                        custom_badge.setAccessibleName(self._t("custom_badge_tip"))
                        row.addWidget(custom_badge)
                    status_label = QLabel("")
                    status_label.setObjectName("actionStatus")
                    self._action_status_labels[action.id] = status_label
                    row.addWidget(status_label)
                    if action.check_command:
                        # Research G09: "already applied?" - filled in by the
                        # background check when this category is shown.
                        check_chip = QLabel("")
                        check_chip.setObjectName("actionStatus")
                        check_chip.setProperty("checkChip", True)
                        self._action_check_chips[action.id] = check_chip
                        row.addWidget(check_chip)
                        self._set_check_chip(action.id, self._check_results.get(action.id))
                    row.addStretch(1)
                    if action.preview_command:
                        # Only where a real preview exists - no guessed numbers
                        # for the rest (research-design-additions item 6).
                        preview_button = self._make_selection_button(
                            self._t("preview_button"), lambda a=action.id: self._on_preview_clicked(a)
                        )
                        preview_button.setToolTip(self._t("preview_tooltip"))
                        preview_button.setAccessibleName(
                            f"{self._t('preview_button')}: {action.label(self.settings.language)}"
                        )
                        preview_button.setEnabled(self._preview_runner is None)
                        self._preview_buttons[action.id] = preview_button
                        row.addWidget(preview_button)
                    detail_toggle, detail_panel = self._make_action_detail_toggle(action)
                    self._action_detail_toggles[action.id] = detail_toggle
                    self._action_detail_panels[action.id] = detail_panel
                    row.addWidget(detail_toggle)
                    row_container = self._wrap_row_with_detail_panel(row_widget, detail_panel)
                    card_layout.addWidget(row_container)
                    self._action_rows[action.id] = row_container
            self._category_groups[category] = card
            scroll_layout.addWidget(card)
        self._nav_row_order: list[QWidget] = [self._category_groups[c] for c in self._categories_order]

        self._risk_view_checkboxes: dict[str, QCheckBox] = {}
        self._risk_view_rows: dict[str, QWidget] = {}
        self._risk_view_detail_toggles: dict[str, QToolButton] = {}
        self._risk_view_detail_panels: dict[str, QWidget] = {}
        # The four risk cards repeat every row of the catalog - their rows
        # are built on the first visit (_ensure_risk_card), which took the
        # start-up build from ~10 s to a few.
        self._risk_card_layouts: dict[RiskLevel, QVBoxLayout] = {}
        for risk in self._risk_tabs_order:
            card = QFrame()
            card.setObjectName("actionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 10, 14, 10)
            card_layout.setSpacing(2)
            heading_row = QHBoxLayout()
            heading_row.setSpacing(6)
            heading = QLabel(f"{self._t('risk_tab_prefix')} {self._risk_text(risk)}")
            heading.setObjectName("cardHeading")
            heading_row.addWidget(heading)
            heading_row.addStretch(1)
            heading_row.addWidget(self._make_selection_button(
                self._t("select_all"), lambda r=risk: self._apply_selection(self._risk_action_ids[r], "all")
            ))
            heading_row.addWidget(self._make_selection_button(
                self._t("select_none"), lambda r=risk: self._apply_selection(self._risk_action_ids[r], "none")
            ))
            card_layout.addLayout(heading_row)
            self._risk_card_layouts[risk] = card_layout
            scroll_layout.addWidget(card)
            card.setHidden(True)
            self._nav_row_order.append(card)

        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        center_layout.addWidget(scroll, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("batchProgress")
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m")
        self.progress_bar.setVisible(False)
        center_layout.addWidget(self.progress_bar)

        run_row = QHBoxLayout()
        self.run_button = QPushButton(self._t("run_selected"))
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self.run_selected_actions)
        self._apply_run_button_dry_run_tint()
        run_row.addWidget(self.run_button, 1)

        self.cancel_button = QPushButton(self._t("cancel_batch"))
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.clicked.connect(lambda _checked=False: self._on_cancel_clicked())
        self.cancel_button.setEnabled(False)
        run_row.addWidget(self.cancel_button)
        center_layout.addLayout(run_row)
        body_layout.addLayout(center_layout, 2)
        body_layout.addWidget(self._build_sysinfo_panel(), 1)
        body_widget = QWidget()
        body_widget.setLayout(body_layout)

        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        # Unbounded, the console kept every line of every batch for the whole
        # session (DISM/SFC alone emit thousands) - memory only ever grew.
        # Full per-action output is still in the audit log and the report.
        self.console.setMaximumBlockCount(CONSOLE_MAX_LINES)
        self._console_window: QDialog | None = None
        self._console_fullscreen = False
        self._console_collapsed = False
        self._console_expanded_sizes: list[int] | None = None
        self._console_splitter_sizes: list[int] | None = None
        # High Contrast gets no stylesheet (style.stylesheet) - nor tinted lines.
        self._console_colors = {} if style.is_high_contrast() else style.CONSOLE_COLORS

        console_toolbar = QHBoxLayout()
        self.console_collapse_button = self._make_selection_button(
            f"▾ {self._t('console_collapse')}", lambda: self._on_console_collapse_toggled()
        )
        console_toolbar.addWidget(self.console_collapse_button)
        console_toolbar.addStretch(1)
        self.console_fullscreen_button = self._make_selection_button(
            "⤢", lambda: self._on_console_fullscreen_toggled()
        )
        self.console_fullscreen_button.setToolTip(self._t("console_fullscreen_toggle"))
        self.console_fullscreen_button.setAccessibleName(self._t("console_fullscreen_toggle"))
        console_toolbar.addWidget(self.console_fullscreen_button)
        self.console_popout_button = self._make_selection_button(
            "⧉", lambda: self._on_console_popout_clicked()
        )
        self.console_popout_button.setToolTip(self._t("console_popout"))
        self.console_popout_button.setAccessibleName(self._t("console_popout"))
        console_toolbar.addWidget(self.console_popout_button)

        self._console_container_layout = QVBoxLayout()
        self._console_container_layout.setContentsMargins(0, 0, 0, 0)
        self._console_container_layout.setSpacing(4)
        self._console_container_layout.addLayout(console_toolbar)
        self._console_container_layout.addWidget(self.console)
        self._console_panel = QWidget()
        self._console_panel.setLayout(self._console_container_layout)

        self._main_splitter = QSplitter(Qt.Orientation.Vertical)
        self._main_splitter.addWidget(body_widget)
        self._main_splitter.addWidget(self._console_panel)
        self._main_splitter.setStretchFactor(0, 3)
        self._main_splitter.setStretchFactor(1, 1)
        root_layout.addWidget(self._main_splitter, 1)

        self.category_list.currentRowChanged.connect(self._on_category_changed)
        if self._categories_order:
            self.category_list.setCurrentRow(0)
        self._update_status_bar()
        if self._preview_runner is not None:
            self.run_button.setEnabled(False)
        if self._batch_active:
            # A language toggle mid-batch rebuilds run_button/cancel_button/
            # progress_bar/console fresh - restore the in-flight state onto
            # the new widgets, otherwise a freshly-enabled run_button lets a
            # second click stomp on the still-running batch's queue/runner.
            # The running action's output keeps arriving: it is connected to
            # self._append_console, which writes to whichever console is current.
            self.run_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.language_button.setEnabled(False)
            self.dry_run_checkbox.setEnabled(False)
            self.restart_admin_button.setEnabled(False)
            self.progress_bar.setMaximum(self._queue_total)
            self.progress_bar.setValue(self._queue_total - len(self._queue))
            self.progress_bar.setVisible(True)
        if self._pending_update_info is not None:
            if self._update_in_progress:
                self.update_banner_label.setText(self._t(_UPDATE_PHASE_KEYS.get(self._update_phase, "update_downloading")))
                self.update_button.setEnabled(False)
                self.update_dismiss_button.setEnabled(False)
                # The rebuilt bar starts hidden - the download/stage/launch
                # still running would otherwise look finished.
                done, total = self._update_progress
                self.progress_bar.setFormat("%p%")
                self.progress_bar.setMaximum(total)
                self.progress_bar.setValue(done)
                self.progress_bar.setVisible(True)
            else:
                self.update_banner_label.setText(
                    self._t("update_available_banner").format(version=self._pending_update_info.version)
                )
            self.update_banner.setVisible(True)
