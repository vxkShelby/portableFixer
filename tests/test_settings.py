import json
from pathlib import Path

from portablefix.settings import Settings, load_settings, save_settings, settings_path


def test_load_settings_defaults_when_missing(tmp_path):
    settings = load_settings(tmp_path)
    assert settings == Settings(language="sk", dry_run=True)


def test_save_then_load_round_trip(tmp_path):
    save_settings(tmp_path, Settings(language="en", dry_run=False))
    loaded = load_settings(tmp_path)
    assert loaded == Settings(language="en", dry_run=False)


def test_load_settings_fills_missing_keys(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    loaded = load_settings(tmp_path)
    assert loaded == Settings(language="en", dry_run=True)
