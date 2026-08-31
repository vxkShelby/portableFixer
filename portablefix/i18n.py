_STRINGS = {
    "sk": {
        "app_title": "PortableFix",
        "readonly_banner": "Bez admin prav - len diagnostika (read-only).",
        "restart_as_admin": "Restartovat ako administrator",
        "dry_run_toggle": "DRY-RUN",
        "run_selected": "Spustit vybrane",
        "category_diagnostics": "Diagnostika",
        "fallback_banner": "USB nedostupny na zapis - pouzivam %TEMP%\\PortableFix.",
        "integrity_warning": "Kontrola integrity zlyhala - subory boli zmenene.",
    },
    "en": {
        "app_title": "PortableFix",
        "readonly_banner": "No admin rights - diagnostics only (read-only).",
        "restart_as_admin": "Restart as Administrator",
        "dry_run_toggle": "DRY-RUN",
        "run_selected": "Run selected",
        "category_diagnostics": "Diagnostics",
        "fallback_banner": "USB not writable - using %TEMP%\\PortableFix instead.",
        "integrity_warning": "Integrity check failed - files were modified.",
    },
}


def translate(key: str, language: str) -> str:
    lang = language if language in _STRINGS else "sk"
    return _STRINGS[lang].get(key, key)
