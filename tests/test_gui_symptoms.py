"""GUI side of the symptom box (research G27): a complaint shows the top
symptoms as tiles, and their buttons only select actions. A module of its
own so it runs without a real batch."""

from pathlib import Path

from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from portablefix.gui.main_window import MainWindow
from portablefix.settings import Settings

REPO = Path(__file__).resolve().parent.parent

ACTIONS = (
    "module_id: m06_network\n"
    "actions:\n"
    "  - {id: ladder, label_sk: Rebrik, label_en: Ladder, risk: SAFE, command: \"Write-Output 1\"}\n"
    "  - {id: adapter, label_sk: Adapter, label_en: Adapter, risk: SAFE, command: \"Write-Output 1\"}\n"
    "  - {id: winsock, label_sk: Winsock, label_en: Winsock, risk: REQUIRES_REBOOT, command: \"Write-Output 1\"}\n"
    "  - {id: renew, label_sk: DHCP, label_en: DHCP, risk: MODERATE, command: \"Write-Output 1\"}\n"
)

SYMPTOMS = """
weak: [nejde]
symptoms:
  - id: no_internet
    title_sk: Nejde internet
    title_en: No internet
    why_sk: Test po krokoch.
    why_en: The step-by-step test.
    phrases_sk: [nejde internet]
    phrases_en: [no internet]
    diagnostics: [ladder, adapter]
    fixes: [renew, winsock]
  - id: bad_one
    title_sk: Zle
    title_en: Bad
    why_sk: x
    why_en: x
    phrases_sk: [zle veci]
    diagnostics: [winsock]
"""


def _window(qtbot, tmp_path, monkeypatch, symptoms_text=SYMPTOMS, language="en"):
    module_dir = tmp_path / "Modules" / "m06_network"
    module_dir.mkdir(parents=True)
    (module_dir / "actions.yaml").write_text(ACTIONS, encoding="utf-8")
    if symptoms_text is not None:
        (tmp_path / "Modules" / "symptoms.yaml").write_text(symptoms_text, encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text))
    window = MainWindow(
        assets_dir=tmp_path, state_dir=tmp_path, settings=Settings(language=language),
        is_admin=True, run_id="run_symptoms",
    )
    qtbot.addWidget(window)
    return window, warnings


def _tiles(window):
    layout = window._symptom_results_layout
    return [layout.itemAt(i).widget() for i in range(layout.count())]


def _checked(window):
    return {aid for aid, box in window._action_checkboxes.items() if box.isChecked()}


def test_a_complaint_shows_a_tile_with_title_why_and_matched_words(qtbot, tmp_path, monkeypatch):
    window, _ = _window(qtbot, tmp_path, monkeypatch)
    assert not window._symptom_results.isVisibleTo(window)
    window.symptom_box.setText("internet nejde")
    [tile] = _tiles(window)
    texts = [label.text() for label in tile.findChildren(QLabel)]
    assert texts == ["No internet", "The step-by-step test.", "Matched: internet, nejde"]
    assert window._symptom_results.isVisibleTo(window)
    window.symptom_box.clear()
    assert _tiles(window) == []
    assert not window._symptom_results.isVisibleTo(window)


def test_the_buttons_select_diagnostics_or_diagnostics_and_fixes(qtbot, tmp_path, monkeypatch):
    window, _ = _window(qtbot, tmp_path, monkeypatch)
    window.symptom_box.setText("no internet")
    diag_button, fix_button = _tiles(window)[0].findChildren(QPushButton)
    assert diag_button.text() == "Select diagnostics"
    diag_button.click()
    assert _checked(window) == {"ladder", "adapter"}
    assert "2 actions selected" in window.statusBar().currentMessage()
    fix_button.click()
    assert _checked(window) == {"ladder", "adapter", "renew", "winsock"}
    # Selecting is all it does - nothing started.
    assert not window._batch_active


def test_no_match_says_so(qtbot, tmp_path, monkeypatch):
    window, _ = _window(qtbot, tmp_path, monkeypatch)
    window.symptom_box.setText("tlaciaren")
    [label] = _tiles(window)
    assert isinstance(label, QLabel) and "No known problem" in label.text()


def test_a_non_safe_diagnostic_is_reported_at_startup(qtbot, tmp_path, monkeypatch):
    window, warnings = _window(qtbot, tmp_path, monkeypatch)
    assert any("bad_one" in w and "not SAFE" in w for w in warnings)
    window.symptom_box.setText("zle veci")
    assert isinstance(_tiles(window)[0], QLabel)  # skipped, so no tile


def test_without_a_symptoms_file_there_is_no_box(qtbot, tmp_path, monkeypatch):
    window, warnings = _window(qtbot, tmp_path, monkeypatch, symptoms_text=None)
    assert warnings == []
    assert not window.symptom_box.isVisibleTo(window)


def test_the_shipped_file_works_in_the_real_window(qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text))
    window = MainWindow(
        assets_dir=REPO, state_dir=tmp_path, settings=Settings(language="sk"), is_admin=True, run_id="run_real",
    )
    qtbot.addWidget(window)
    assert warnings == []
    window.symptom_box.setText("Outlook sa neotvára")
    tiles = _tiles(window)
    assert 1 <= len(tiles) <= 3
    assert tiles[0].findChildren(QLabel)[0].text() == "Outlook sa neotvára alebo padá"
    tiles[0].findChildren(QPushButton)[0].click()
    assert _checked(window) == {"office_addins_report", "office_ost_pst_report", "office_version_channel_report"}
