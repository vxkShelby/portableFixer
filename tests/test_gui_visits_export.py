"""Settings menu entry "Export visits log..." (research N7): asks for a path
and writes the CSV of every PC's runs, masked with "Redact for the client"."""

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from portablefix.settings import Settings


def _write_report(tmp_path, host, run_id, client):
    reports = tmp_path / "Reports"
    reports.mkdir(exist_ok=True)
    data = {"run_id": run_id, "hostname": host, "generated_at": "2026-09-01T08:00:00+00:00",
            "job": {"client": client}, "actions": [{"exit_code": 0, "dry_run": False}]}
    (reports / f"{host}_{run_id}.json").write_text(json.dumps(data), encoding="utf-8")


def _entry(window):
    return next(a for a in window._settings_menu.actions() if a.text() == "Export visits log…")


def test_menu_entry_saves_the_visits_csv_of_all_pcs(tmp_path, monkeypatch, make_window):
    window = make_window(Settings(language="en"))
    _write_report(tmp_path, "OTHER-PC", "20260901T080000-aaaa", "Acme")
    dest = tmp_path / "out.csv"
    asked = {}

    def fake_dialog(parent, title, start, filter_):
        asked["start"] = Path(start)
        return str(dest), filter_

    monkeypatch.setattr(QFileDialog, "getSaveFileName", fake_dialog)
    _entry(window).trigger()
    assert asked["start"] == tmp_path / "Reports" / "visits.csv"
    text = dest.read_text(encoding="utf-8-sig")
    assert "OTHER-PC" in text and "Acme" in text


def test_menu_entry_masks_with_redact_for_client_and_cancel_writes_nothing(tmp_path, monkeypatch, make_window):
    window = make_window(Settings(language="en", redact_for_client=True))
    _write_report(tmp_path, "OTHER-PC", "20260901T080000-aaaa", "Acme")
    dest = tmp_path / "out.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: ("", ""))
    _entry(window).trigger()
    assert not dest.exists()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(dest), ""))
    _entry(window).trigger()
    text = dest.read_text(encoding="utf-8-sig")
    assert "<pc>" in text and "OTHER-PC" not in text and "Acme" not in text
