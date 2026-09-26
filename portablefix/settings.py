import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import branding

DEFAULT_LANGUAGE = "sk"
SUPPORTED_LANGUAGES = ("sk", "en")
MAX_CUSTOM_PRESETS = 20
MAX_PRESET_NAME_LENGTH = 40
MAX_TECHNICIAN_NAME_LENGTH = 60

# Built-in presets - the window's preset buttons and the headless
# `--preset <name>` (research G21) share them.
PRESETS: dict[str, list[str]] = {
    "quick_clean": [
        "user_temp", "system_temp", "recycle_bin", "prefetch", "wer_reports",
        "thumbnail_cache", "directx_shader_cache", "browser_cache_sweep",
    ],
    "full_diagnostic": [
        "os_info", "computer_info", "bios_info", "cpu_info", "memory_info",
        "volumes", "physical_disks", "recent_hotfixes", "pending_reboot",
        "eventlog_critical_7d", "bsod_summary", "crash_bugcheck_triage",
        "whea_hardware_errors", "disk_reliability_counters",
        "defender_status", "top_cpu_processes", "sec_defender_status",
        "sec_firewall_status", "sec_uac_status",
    ],
    "privacy_debloat": [
        "debloat_disable_telemetry", "debloat_disable_suggestions",
        "debloat_disable_web_search", "debloat_disable_copilot",
        "debloat_disable_widgets", "debloat_disable_advertising_id",
        "debloat_disable_diagtrack", "debloat_disable_ceip_tasks",
    ],
}


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
    # Quiet/offline mode (research G33): no network traffic the technician
    # did not click for - no periodic ping, no VPN polling, no update check
    # at start and no automatic winget scan. Off by default so an upgrade
    # keeps today's behaviour; on a client's corporate network a ping to a
    # public IP every few seconds can trip EDR alerts.
    quiet_mode: bool = False
    # "Redact for the client" (research G20): the report and the handoff
    # package's own text files mask user names in paths, addresses, serial
    # numbers, key fragments and SSIDs. Off by default - the technician's
    # own copy should be complete unless they ask otherwise.
    redact_for_client: bool = False
    # The technician's branding in the report header (research G20), all
    # optional. The logo is the image itself as base64 (PNG/JPEG, at most
    # branding.MAX_LOGO_BYTES), not a path - see branding.py.
    branding_company: str = ""
    branding_company_id: str = ""
    branding_contact: str = ""
    branding_logo: str = ""

    def branding_info(self) -> dict:
        """The branding as report.py takes it (see branding.clean_branding)."""
        return branding.clean_branding({
            "company": self.branding_company,
            "company_id": self.branding_company_id,
            "contact": self.branding_contact,
            "logo": self.branding_logo,
        })


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
    quiet_mode = data.get("quiet_mode")
    redact = data.get("redact_for_client")
    logo = branding.decode_logo(data.get("branding_logo"))
    return Settings(
        language=language if language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE,
        dry_run=dry_run if isinstance(dry_run, bool) else True,
        winget_ignored_ids=[i for i in ignored if isinstance(i, str)] if isinstance(ignored, list) else [],
        winget_auto_check_minutes=(
            minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes >= 0 else 0
        ),
        custom_presets=_valid_custom_presets(presets),
        technician_name=technician.strip()[:MAX_TECHNICIAN_NAME_LENGTH] if isinstance(technician, str) else "",
        # Strictly a bool: a hand-edited "quiet_mode": "false" must not be
        # read as truthy and silently change what the app sends on the wire.
        quiet_mode=quiet_mode if isinstance(quiet_mode, bool) else False,
        redact_for_client=redact if isinstance(redact, bool) else False,
        branding_company=branding.clean_text(data.get("branding_company"), branding.MAX_COMPANY_LENGTH),
        branding_company_id=branding.clean_text(data.get("branding_company_id"), branding.MAX_COMPANY_ID_LENGTH),
        branding_contact=branding.clean_text(data.get("branding_contact"), branding.MAX_CONTACT_LENGTH),
        # A logo that is not a valid PNG/JPEG within the limit (hand-edited,
        # cut short) is dropped rather than put into every report.
        branding_logo=logo[1] if logo is not None else "",
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
