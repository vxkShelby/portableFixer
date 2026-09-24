import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_LANGUAGE = "sk"
SUPPORTED_LANGUAGES = ("sk", "en")
MAX_CUSTOM_PRESETS = 20
MAX_PRESET_NAME_LENGTH = 40
MAX_TECHNICIAN_NAME_LENGTH = 60


@dataclass
class Settings:
    language: str = DEFAULT_LANGUAGE
    dry_run: bool = True
    winget_ignored_ids: list[str] = field(default_factory=list)
    winget_auto_check_minutes: int = 0
    # User-saved action selections, name -> action ids (insertion-ordered).
    custom_presets: dict[str, list[str]] = field(default_factory=dict)
    # Remembered across runs (it's the same person on every client visit);
    # the client/ticket and note are per-run and deliberately not stored here.
    technician_name: str = ""


def settings_path(base_dir: Path) -> Path:
    return base_dir / "Data" / "settings.json"


def load_settings(base_dir: Path) -> Settings:
    path = settings_path(base_dir)
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    # Validate every field by type instead of trusting the file: a hand-edited
    # "dry_run": "false" is a truthy string, and a non-bool here must never
    # silently switch DRY-RUN off - fall back to the safe default instead.
    language = data.get("language")
    dry_run = data.get("dry_run")
    ignored = data.get("winget_ignored_ids")
    minutes = data.get("winget_auto_check_minutes")
    presets = data.get("custom_presets")
    technician = data.get("technician_name")
    return Settings(
        language=language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE,
        dry_run=dry_run if isinstance(dry_run, bool) else True,
        winget_ignored_ids=[i for i in ignored if isinstance(i, str)] if isinstance(ignored, list) else [],
        winget_auto_check_minutes=(
            minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes >= 0 else 0
        ),
        custom_presets=_valid_custom_presets(presets),
        technician_name=technician.strip()[:MAX_TECHNICIAN_NAME_LENGTH] if isinstance(technician, str) else "",
    )


def _valid_custom_presets(raw) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    presets: dict[str, list[str]] = {}
    for name, ids in raw.items():
        if len(presets) >= MAX_CUSTOM_PRESETS:
            break
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_PRESET_NAME_LENGTH:
            continue
        if not isinstance(ids, list):
            continue
        clean_ids = [i for i in ids if isinstance(i, str)]
        if clean_ids:
            presets[name] = clean_ids
    return presets


def save_settings(base_dir: Path, settings: Settings) -> None:
    path = settings_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a crash or a yanked USB stick mid-write leaves the
    # previous settings.json intact instead of a truncated file.
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
