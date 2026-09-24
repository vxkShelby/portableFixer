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


def test_load_settings_returns_defaults_on_corrupted_file(tmp_path):
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe{not json at all")
    loaded = load_settings(tmp_path)
    assert loaded == Settings()


def test_winget_ignored_and_auto_check_round_trip(tmp_path):
    save_settings(tmp_path, Settings(winget_ignored_ids=["Vendor.App"], winget_auto_check_minutes=30))
    loaded = load_settings(tmp_path)
    assert loaded.winget_ignored_ids == ["Vendor.App"]
    assert loaded.winget_auto_check_minutes == 30


def _write_raw(tmp_path, payload: str):
    path = settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def test_load_settings_returns_defaults_when_top_level_is_not_an_object(tmp_path):
    _write_raw(tmp_path, "[1, 2, 3]")
    assert load_settings(tmp_path) == Settings()


def test_load_settings_string_dry_run_keeps_dry_run_on(tmp_path):
    # "false" is a truthy string; a malformed value must never turn DRY-RUN off.
    _write_raw(tmp_path, json.dumps({"dry_run": "false"}))
    assert load_settings(tmp_path).dry_run is True


def test_load_settings_sanitizes_invalid_field_types(tmp_path):
    _write_raw(
        tmp_path,
        json.dumps(
            {
                "language": "de",
                "winget_ignored_ids": ["Vendor.App", 42, None],
                "winget_auto_check_minutes": "30",
            }
        ),
    )
    loaded = load_settings(tmp_path)
    assert loaded.language == "sk"
    assert loaded.winget_ignored_ids == ["Vendor.App"]
    assert loaded.winget_auto_check_minutes == 0


def test_load_settings_rejects_negative_or_bool_auto_check_minutes(tmp_path):
    _write_raw(tmp_path, json.dumps({"winget_auto_check_minutes": -5}))
    assert load_settings(tmp_path).winget_auto_check_minutes == 0
    _write_raw(tmp_path, json.dumps({"winget_auto_check_minutes": True}))
    assert load_settings(tmp_path).winget_auto_check_minutes == 0


def test_save_settings_leaves_no_temp_file_behind(tmp_path):
    save_settings(tmp_path, Settings(language="en"))
    files = sorted(p.name for p in settings_path(tmp_path).parent.iterdir())
    assert files == ["settings.json"]


def test_custom_presets_round_trip(tmp_path):
    presets = {"Môj servis": ["user_temp", "recycle_bin"], "Rýchla kontrola": ["os_info"]}
    save_settings(tmp_path, Settings(custom_presets=presets))
    loaded = load_settings(tmp_path)
    assert loaded.custom_presets == presets
    assert list(loaded.custom_presets) == ["Môj servis", "Rýchla kontrola"]


def test_custom_presets_are_sanitized(tmp_path):
    _write_raw(
        tmp_path,
        json.dumps(
            {
                "custom_presets": {
                    "ok": ["a", 1, None, "b"],
                    "": ["a"],
                    "   ": ["a"],
                    "x" * 41: ["a"],
                    "not_a_list": "a",
                    "empty": [],
                }
            }
        ),
    )
    assert load_settings(tmp_path).custom_presets == {"ok": ["a", "b"]}
    _write_raw(tmp_path, json.dumps({"custom_presets": ["a"]}))
    assert load_settings(tmp_path).custom_presets == {}


def test_custom_presets_are_capped(tmp_path):
    _write_raw(tmp_path, json.dumps({"custom_presets": {f"p{i}": ["a"] for i in range(50)}}))
    assert len(load_settings(tmp_path).custom_presets) == 20
