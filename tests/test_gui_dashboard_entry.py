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
    assert window._health_tiles["battery"][1].text() == "Unknown"
    assert not window._health_empty_label.isVisibleTo(window)


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
