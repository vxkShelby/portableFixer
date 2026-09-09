import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_LANGUAGE = "sk"


@dataclass
class Settings:
    language: str = DEFAULT_LANGUAGE
    dry_run: bool = True
    winget_ignored_ids: list[str] = field(default_factory=list)
    winget_auto_check_minutes: int = 0


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
    return Settings(
        language=data.get("language", DEFAULT_LANGUAGE),
        dry_run=data.get("dry_run", True),
        winget_ignored_ids=list(data.get("winget_ignored_ids", [])),
        winget_auto_check_minutes=data.get("winget_auto_check_minutes", 0),
    )


def save_settings(base_dir: Path, settings: Settings) -> None:
    path = settings_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
