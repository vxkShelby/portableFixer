import sys
import uuid
from datetime import datetime, timezone

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from portablefix import i18n
from portablefix.elevation import is_admin
from portablefix.gui import style
from portablefix.gui.main_window import MainWindow
from portablefix.integrity import IntegrityCheckRunner
from portablefix.paths import get_base_dir, resolve_writable_base_dir
from portablefix.settings import load_settings, save_settings


def main() -> int:
    try:
        app = QApplication(sys.argv)
        app.setStyleSheet(style.STYLE)

        raw_base_dir = get_base_dir()
        icon_path = raw_base_dir / "portablefix.ico"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
        base_dir, used_fallback = resolve_writable_base_dir(raw_base_dir)
        settings = load_settings(base_dir)

        if used_fallback:
            QMessageBox.warning(
                None,
                i18n.translate("app_title", settings.language),
                i18n.translate("fallback_banner", settings.language),
            )

        # Timestamp prefix makes Reports/Logs/Backups filenames sort
        # chronologically - a bare random id doesn't, which breaks the
        # "same technician, same machine, multiple visits" use case.
        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
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
                    i18n.translate("integrity_warning", settings.language) + "\n" + "\n".join(mismatches),
                )

        # Hashing every shipped file can take a visible moment on slow USB
        # media - runs after the window is already on screen instead of
        # stalling launch on a blank screen.
        integrity_runner = IntegrityCheckRunner(raw_base_dir, parent=window)
        integrity_runner.check_finished.connect(_on_integrity_checked)
        integrity_runner.start()

        exit_code = app.exec()
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
        QMessageBox.critical(None, "PortableFix", f"Startup failed:\n{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
