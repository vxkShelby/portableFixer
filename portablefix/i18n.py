_STRINGS = {
    "sk": {
        "app_title": "PortableFix",
        "readonly_banner": "Bez admin prav - len diagnostika (read-only).",
        "restart_as_admin": "Restartovat ako administrator",
        "dry_run_toggle": "DRY-RUN",
        "run_selected": "Spustit vybrane",
        "category_diagnostics": "Diagnostika",
        "category_cleanup": "Cistenie",
        "category_repair": "Oprava systemu",
        "category_security": "Zabezpecenie",
        "fallback_banner": "USB nedostupny na zapis - pouzivam %TEMP%\\PortableFix.",
        "integrity_warning": "Kontrola integrity zlyhala - subory boli zmenene.",
        "elevation_failed": "Restart ako administrator zlyhal alebo bol zruseny.",
        "confirm_risky_action": "Naozaj chcete spustit tuto akciu?",
        "confirm_destructive_action": "POZOR: Tato akcia je nevratna a nie je mozne ju vratit spat cez PortableFix. Naozaj pokracovat?",
        "restore_point_failed_confirm": "Nepodarilo sa vytvorit bod obnovenia (Windows to mozno obmedzuje). Pokracovat aj tak?",
    },
    "en": {
        "app_title": "PortableFix",
        "readonly_banner": "No admin rights - diagnostics only (read-only).",
        "restart_as_admin": "Restart as Administrator",
        "dry_run_toggle": "DRY-RUN",
        "run_selected": "Run selected",
        "category_diagnostics": "Diagnostics",
        "category_cleanup": "Cleanup",
        "category_repair": "System repair",
        "category_security": "Security",
        "fallback_banner": "USB not writable - using %TEMP%\\PortableFix instead.",
        "integrity_warning": "Integrity check failed - files were modified.",
        "elevation_failed": "Restart as administrator failed or was cancelled.",
        "confirm_risky_action": "Are you sure you want to run this action?",
        "confirm_destructive_action": "WARNING: This action is irreversible and cannot be undone through PortableFix. Continue anyway?",
        "restore_point_failed_confirm": "Could not create a System Restore Point (Windows may be limiting this). Continue anyway?",
    },
}


def translate(key: str, language: str) -> str:
    lang = language if language in _STRINGS else "sk"
    return _STRINGS[lang].get(key, key)
