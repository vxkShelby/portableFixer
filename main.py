import sys
import uuid

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from portablefix import i18n
from portablefix.elevation import is_admin
from portablefix.gui.main_window import MainWindow
from portablefix.integrity import check_integrity
from portablefix.paths import get_base_dir, resolve_writable_base_dir
from portablefix.settings import load_settings, save_settings


def main() -> int:
    try:
        app = QApplication(sys.argv)

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

        mismatches = check_integrity(raw_base_dir)
        if mismatches:
            QMessageBox.warning(
                None,
                i18n.translate("app_title", settings.language),
                i18n.translate("integrity_warning", settings.language) + "\n" + "\n".join(mismatches),
            )

        run_id = uuid.uuid4().hex[:12]
        window = MainWindow(
            assets_dir=raw_base_dir,
            state_dir=base_dir,
            settings=settings,
            is_admin=is_admin(),
            run_id=run_id,
        )
        window.show()
        exit_code = app.exec()
        save_settings(base_dir, settings)
        return exit_code
    except Exception as exc:
        QMessageBox.critical(None, "PortableFix", f"Startup failed:\n{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
