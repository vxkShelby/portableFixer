import subprocess

from PySide6.QtCore import QThread, Signal

from .executor import POWERSHELL_PREFIX


def create_restore_point(description: str) -> tuple[bool, str]:
    safe_description = description.replace('"', "'")
    command = (
        'Enable-ComputerRestore -Drive "C:\\"; '
        f'Checkpoint-Computer -Description "{safe_description}" -RestorePointType MODIFY_SETTINGS'
    )
    try:
        result = subprocess.run(POWERSHELL_PREFIX + [command], capture_output=True, timeout=120)
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.decode("utf-8", errors="replace").strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


class RestorePointRunner(QThread):
    result_ready = Signal(bool, str)

    def __init__(self, description: str, parent=None):
        super().__init__(parent)
        self._description = description
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        success, detail = create_restore_point(self._description)
        self.result_ready.emit(success, detail)
