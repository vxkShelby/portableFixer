import ctypes
import sys
import uuid
from datetime import datetime, timezone

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from portablefix import i18n, updater
from portablefix.audit_log import append_entry, make_entry
from portablefix.diagnostics import install_excepthook, write_crash_log
from portablefix.elevation import is_admin
from portablefix.gui import style
from portablefix.gui.main_window import MainWindow
from portablefix.integrity import IntegrityCheckRunner, format_mismatches
from portablefix.paths import (
    get_base_dir,
    resolve_temp_root,
    resolve_windir_temp_root,
    resolve_writable_base_dir,
)
from portablefix.settings import load_settings, save_settings


def _write_startup_diagnostics(raw_base_dir, base_dir, used_fallback: bool, run_id: str, dry_run: bool) -> None:
    """Forensic breadcrumb: if the app's own folder ever gets wiped out from
    under it again, this is what it believed its paths were at the moment it
    started - answered by manual timestamp forensics last time, which
    shouldn't be necessary next time."""
    diag_entry = make_entry(
        "_system",
        "startup_diagnostics",
        "",
        0,
        f"base_dir={raw_base_dir} state_dir={base_dir} used_fallback={used_fallback} "
        f"temp_root={resolve_temp_root()} windir_temp_root={resolve_windir_temp_root()}",
        dry_run,
        run_id,
    )
    try:
        append_entry(base_dir, run_id, diag_entry)
    except OSError:
        pass


def _update_status_message(install_dir, language: str) -> str | None:
    """What to tell the user about an update that ran while no app was open
    to report on it (the detached swap script), or None. Recovery runs
    first, and before MainWindow loads Modules/: a swap interrupted by a
    pulled USB stick can leave Modules/ stranded as Modules.old."""
    restored = updater.recover_interrupted_swap(install_dir)
    status = updater.consume_update_status(install_dir)
    key = updater.update_status_message_key(status, restored)
    if key is None:
        return None
    return i18n.translate(key, language).format(log_dir=updater.update_log_dir() or "%TEMP%\\PortableFixUpdate")


_SINGLE_INSTANCE_MUTEX_NAME = "Global\\PortableFix_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance_lock() -> bool:
    """Named Win32 mutex, held for the process lifetime (never explicitly
    released - Windows tears it down on exit). Returns False when another
    instance already holds it, so a second launch can bail out before it
    locks the .exe file a running instance needs to be replaceable/updatable."""
    ctypes.windll.kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    return ctypes.windll.kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def main() -> int:
    if not _acquire_single_instance_lock():
        ctypes.windll.user32.MessageBoxW(
            None,
            "PortableFix uz bezi (skontroluj taskbar/tray).\nPortableFix is already running (check taskbar/tray).",
            "PortableFix",
            0x40,
        )
        return 0
    crash_log_dir = None
    try:
        app = QApplication(sys.argv)
        # The native Windows style (windowsvista/windows11) paints its own
        # chrome and ignores/misrenders QSS border-radius, gradients and
        # hover states on most widgets - Fusion is the standard Qt style
        # that actually respects a custom stylesheet.
        app.setStyle("Fusion")

        raw_base_dir = get_base_dir()
        # Segoe UI (the system fallback) reads as generic - Sora gives
        # headings/the wordmark the same distinct look as the approved
        # mockup. Bundled rather than loaded from Google Fonts since this
        # is an offline portable app with no guaranteed internet access.
        for font_file in ("Sora-SemiBold.ttf", "Sora-Bold.ttf"):
            font_path = raw_base_dir / "Vendor" / "Fonts" / font_file
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        app.setStyleSheet(style.STYLE)

        icon_path = raw_base_dir / "portablefix.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
        base_dir, used_fallback = resolve_writable_base_dir(raw_base_dir)
        crash_log_dir = base_dir
        install_excepthook(base_dir)
        settings = load_settings(base_dir)

        if used_fallback:
            QMessageBox.warning(
                None,
                i18n.translate("app_title", settings.language),
                # The fallback isn't always %TEMP% any more (see
                # resolve_writable_base_dir), so name the real folder.
                i18n.translate("fallback_banner_path", settings.language).format(path=base_dir),
            )
        update_message = _update_status_message(raw_base_dir, settings.language)

        # Timestamp prefix makes Reports/Logs/Backups filenames sort
        # chronologically - a bare random id doesn't, which breaks the
        # "same technician, same machine, multiple visits" use case.
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        _write_startup_diagnostics(raw_base_dir, base_dir, used_fallback, run_id, settings.dry_run)

        window = MainWindow(
            assets_dir=raw_base_dir,
            state_dir=base_dir,
            settings=settings,
            is_admin=is_admin(),
            run_id=run_id,
        )
        window.show()

        def _on_integrity_checked(mismatches: list) -> None:
            if mismatches:
                QMessageBox.warning(
                    window,
                    i18n.translate("app_title", settings.language),
                    i18n.translate("integrity_warning", settings.language)
                    + "\n"
                    + format_mismatches(mismatches, i18n.translate("integrity_more", settings.language)),
                )

        # Hashing every shipped file can take a visible moment on slow USB
        # media - runs after the window is already on screen instead of
        # stalling launch on a blank screen.
        integrity_runner = IntegrityCheckRunner(raw_base_dir, parent=window)
        integrity_runner.check_finished.connect(_on_integrity_checked)
        integrity_runner.start()
        # After the integrity runner is started, so reading this modal
        # doesn't also hold up the background check.
        if update_message:
            QMessageBox.warning(window, i18n.translate("app_title", settings.language), update_message)

        exit_code = app.exec()
        integrity_runner.stop()
        try:
            save_settings(base_dir, settings)
        except OSError:
            # Losing a language/dry-run preference is harmless; a write
            # failure here (e.g. the USB drive was pulled right at exit)
            # must not be reported as "Startup failed" for a session that
            # actually completed successfully.
            pass
        return exit_code
    except Exception as exc:
        if crash_log_dir is not None:
            write_crash_log(crash_log_dir, exc)
        QMessageBox.critical(None, "PortableFix", f"Startup failed:\n{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
