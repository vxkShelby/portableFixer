"""The Dashboard as the landing page (review B6, B13, B14): a plain "what is
not working?" box with one-click complaints, a dismissible first-run hint,
"not checked yet" instead of seven identical Unknown tiles, and one
settings menu that forwards to the existing handlers."""

from PySide6.QtWidgets import QMessageBox, QPushButton

from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings, load_settings, save_settings

from test_gui_symptoms import _window as _symptom_window

ACTIONS = (
    "module_id: m01_diagnostics\n"
    "actions:\n"
    "  - {id: hello, label_sk: Ahoj, label_en: Hello, risk: SAFE, command: \"Write-Output 1\"}\n"
)


def _plain_window(qtbot, tmp_path, **settings):
    module_dir = tmp_path / "Modules" / "m01_diagnostics"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(ACTIONS, encoding="utf-8")
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language="en", **settings),
        is_admin=True, run_id="run_entry",
    )
    qtbot.addWidget(window)
    return window


def _toggle_language(qtbot, window):
    # The winget scan the panel starts with the window locks the toggle.
    from portablefix.gui.main_window import _thread_running

    qtbot.waitUntil(lambda: not _thread_running(window._winget_scan_runner), timeout=90_000)
    window._on_toggle_language()


def _menu_action(window, text):
    return next(a for a in window._settings_menu.actions() if a.text() == text)


def test_the_dashboard_opens_with_the_symptom_box_and_one_click_complaints(make_window, monkeypatch):
    window, _ = _symptom_window(make_window, monkeypatch)
    window.show()
    assert window.symptom_box.isVisibleTo(window)
    # Only the chips whose symptom exists in the file: no_internet here.
    chips = [b.text() for b in window.centralWidget().findChildren(QPushButton) if b.text() == "No internet"]
    assert chips == ["No internet"]
    next(b for b in window.centralWidget().findChildren(QPushButton) if b.text() == "No internet").click()
    assert window.symptom_box.text() == "No internet"
    assert window._symptom_results.isVisibleTo(window)


def test_first_run_hint_shows_until_dismissed_and_stays_dismissed(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path)
    assert window.first_run_banner.isVisibleTo(window)
    window._dismiss_first_run_hint()
    assert not window.first_run_banner.isVisibleTo(window)
    assert load_settings(tmp_path).first_run_seen is True
    # A language switch rebuilds the window; the hint must not come back.
    window._on_toggle_language()
    assert not window.first_run_banner.isVisibleTo(window)


def test_first_run_flag_round_trips_and_a_junk_value_means_not_seen(tmp_path):
    save_settings(tmp_path, Settings(first_run_seen=True))
    assert load_settings(tmp_path).first_run_seen is True
    (tmp_path / "Data" / "settings.json").write_text('{"first_run_seen": "true"}', encoding="utf-8")
    assert load_settings(tmp_path).first_run_seen is False


def test_unchecked_health_tiles_say_so_once_instead_of_repeating_unknown(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path)
    window.show()
    tiles = window._health_tiles.values()
    assert all(state.text() == "Not checked yet" for _, state, _, _ in tiles)
    assert all(not reason.isVisibleTo(window) for _, _, reason, _ in tiles)
    assert window._health_empty_label.isVisibleTo(window)

    window._findings = {
        "disk.health": {"id": "disk.health", "severity": "ok", "area": "disk", "msg_sk": "s", "msg_en": "Healthy",
                        "fix": []},
    }
    window._refresh_dashboard()
    _, state, reason, _ = window._health_tiles["disk"]
    assert state.text() == "OK" and reason.isVisibleTo(window)
    assert window._health_tiles["battery"][1].text() == "Not checked"
    assert not window._health_empty_label.isVisibleTo(window)


def test_first_run_dashboard_is_one_hero_and_tiles_appear_worst_first_after_analysis(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path)
    window.show()
    # Before the first analysis: the Analyze button and one sentence, no tiles.
    assert window.dashboard_analyze_button.text() == "Analyze this PC"
    assert window._health_empty_label.isVisibleTo(window)
    assert "nothing is changed" in window._health_empty_label.text()
    assert all(not tile.isVisibleTo(window) for tile, _, _, _ in window._health_tiles.values())

    window._findings = {
        "disk.health": {"id": "disk.health", "severity": "ok", "area": "disk", "msg_sk": "s", "msg_en": "Healthy",
                        "fix": []},
        "battery.wear": {"id": "battery.wear", "severity": "critical", "area": "battery", "msg_sk": "s",
                         "msg_en": "Worn out", "fix": []},
    }
    window._refresh_dashboard()
    assert all(tile.isVisibleTo(window) for tile, _, _, _ in window._health_tiles.values())
    assert not window._health_empty_label.isVisibleTo(window)
    # Critical first, then the unchecked areas, the healthy one last.
    assert window._health_order[0] == "battery" and window._health_order[-1] == "disk"
    first_cell = window._health_grid.itemAtPosition(0, 0).widget()
    assert first_cell is window._health_tiles["battery"][0]


def test_dashboard_tile_columns_follow_the_card_width(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path)
    window.show()
    for width, columns in ((1000, 4), (700, 3), (420, 2), (200, 2)):
        window._on_dashboard_resized(width)
        assert window._dashboard_columns == columns
        placed = [window._category_grid.getItemPosition(i)[1] for i in range(window._category_grid.count())]
        assert max(placed) < columns


def test_settings_menu_forwards_to_the_existing_settings(qtbot, tmp_path, monkeypatch):
    window = _plain_window(qtbot, tmp_path, redact_for_client=True)
    window._settings_menu.aboutToShow.emit()
    # The check marks mirror the live state each time the menu opens.
    assert _menu_action(window, "Redact for the client").isChecked()
    assert not _menu_action(window, "Quiet mode (no background network)").isChecked()

    _menu_action(window, "Quiet mode (no background network)").setChecked(True)
    assert window.settings.quiet_mode is True and window.quiet_mode_checkbox.isChecked()
    assert load_settings(tmp_path).quiet_mode is True

    _menu_action(window, "Redact for the client").setChecked(False)
    assert load_settings(tmp_path).redact_for_client is False

    _menu_action(window, "Automatic check for program updates (winget)").setChecked(True)
    assert window.winget_auto_check_checkbox.isChecked()
    assert window.settings.winget_auto_check_minutes == 15

    opened = []
    monkeypatch.setattr(window, "_open_job_dialog", lambda: opened.append("job"))
    monkeypatch.setattr(window, "_open_branding_dialog", lambda: opened.append("branding"))
    _menu_action(window, "Technician and job details…").trigger()
    _menu_action(window, "Report branding…").trigger()
    assert opened == ["job", "branding"]


def test_appearance_menu_switches_the_theme_live_and_remembers_it(qtbot, tmp_path):
    from portablefix.gui import style

    try:
        window = _plain_window(qtbot, tmp_path, theme="dark")
        window._settings_menu.aboutToShow.emit()
        theme_menu = _menu_action(window, "Appearance").menu()
        assert theme_menu is not None
        by_text = {a.text(): a for a in theme_menu.actions()}
        assert list(by_text) == ["Follow system", "Light", "Dark"]
        assert by_text["Dark"].isChecked() and not by_text["Light"].isChecked()

        by_text["Light"].trigger()
        assert window.settings.theme == "light"
        assert load_settings(tmp_path).theme == "light"
        assert window.styleSheet() == style.build_stylesheet("light")
        window._settings_menu.aboutToShow.emit()
        assert by_text["Light"].isChecked() and not by_text["Dark"].isChecked()

        # "auto" has no OS scheme under the offscreen platform: dark.
        by_text["Follow system"].trigger()
        assert window.settings.theme == "auto"
        assert window.styleSheet() == style.build_stylesheet("dark")
    finally:
        style.set_theme("dark")


def test_console_starts_folded_and_a_batch_unfolds_it(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path, dry_run=True)
    window.show()
    assert window._console_collapsed and window.console.isHidden()
    assert window.console_collapse_button.text() == "▸ Show output"
    # Folded to its toolbar row: the action list gets the height.
    assert window._main_splitter.sizes()[1] <= window.console_collapse_button.sizeHint().height() + 20

    window._action_checkboxes["hello"].setChecked(True)
    window.run_selected_actions()
    assert not window._console_collapsed and not window.console.isHidden()
    assert window.console_collapse_button.text() == "▾ Hide output"
    assert window._main_splitter.sizes()[1] > window.console_collapse_button.sizeHint().height() + 20
    # The batch ends with the selection cleared: Run goes back to neutral.
    qtbot.waitUntil(lambda: not window._batch_start_blocked(), timeout=15000)
    assert window.run_button.text() == "Run preview" and not window.run_button.isEnabled()


def test_a_language_toggle_keeps_the_console_as_the_technician_left_it(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path)
    window.show()
    _toggle_language(qtbot, window)
    assert window._console_collapsed
    window.console_collapse_button.click()
    assert not window._console_collapsed
    _toggle_language(qtbot, window)
    assert not window._console_collapsed and not window.console.isHidden()


def test_presets_and_my_presets_share_one_row_next_to_the_select_menu(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path, custom_presets={"Mine": ["hello"]})
    window.show()
    built_in = window._preset_buttons["quick_clean"]
    custom = window._preset_buttons["custom:Mine"]
    assert built_in.geometry().center().y() == custom.geometry().center().y()
    assert window.save_preset_button.geometry().center().y() == built_in.geometry().center().y()
    # The search box moved up beside the Select menu, off the presets row.
    assert window.search_box.geometry().center().y() == window.global_select_button.geometry().center().y()


def test_run_button_names_the_count_and_mode_and_is_neutral_at_zero(qtbot, tmp_path):
    window = _plain_window(qtbot, tmp_path, dry_run=True)
    assert window.run_button.text() == "Run preview" and not window.run_button.isEnabled()

    window._action_checkboxes["hello"].setChecked(True)
    assert window.run_button.text() == "Run 1 selected (DRY-RUN)" and window.run_button.isEnabled()
    window.dry_run_checkbox.setChecked(False)
    assert window.run_button.text() == "Run 1 selected" and window.run_button.property("dryrun") == "false"
    window._action_checkboxes["hello"].setChecked(False)
    assert window.run_button.text() == "Run selected" and not window.run_button.isEnabled()

    _toggle_language(qtbot, window)
    window._action_checkboxes["hello"].setChecked(True)
    assert window.run_button.text() == "Spustiť vybrané: 1"


def test_system_panel_groups_its_rows_under_headings_with_one_line_per_disk(qtbot, tmp_path):
    from PySide6.QtWidgets import QLabel

    from portablefix import sysinfo

    window = _plain_window(qtbot, tmp_path)
    headings = [label.text() for label in window.findChildren(QLabel) if label.objectName() == "sysinfoGroup"]
    assert headings == ["System", "CPU", "GPU", "Storage", "Network"]
    window._on_static_info_ready(sysinfo.StaticInfo(
        os_name="Windows", cpu_name="CPU", cpu_cores=2, local_ip="10.0.0.5", ram_speed_mhz=None,
        disk_health_summary="Disk A: Healthy\nDisk B: Warning",
    ))
    assert window._sysinfo_labels["disk_health"].text().splitlines() == ["Disk A: Healthy", "Disk B: Warning"]


def test_text_size_setting_scales_every_font_size_in_the_stylesheet():
    import re

    from portablefix.gui import style

    assert style.system_text_scale(lambda: 150) == 1.5
    for junk in (None, 99, 300, "150", True):
        assert style.system_text_scale(lambda junk=junk: junk) == 1.0

    def unreadable():
        raise OSError

    assert style.system_text_scale(unreadable) == 1.0
    try:
        style.set_text_scale(1.5)
        sheet = style.build_stylesheet("dark")
        sizes = [float(pt) for pt in re.findall(r"font-size: ([\d.]+)pt", sheet)]
        assert min(sizes) >= 13.5 and 15.0 in sizes  # 9pt -> 13.5, 10pt -> 15
        style.set_text_scale(9)
        assert style.text_scale() == style.TEXT_SCALE_MAX
    finally:
        style.set_text_scale(1.0)
    assert style.build_stylesheet("dark") == style.STYLE
