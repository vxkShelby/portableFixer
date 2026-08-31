import subprocess
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

POWERSHELL_PREFIX = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]


@dataclass
class ExecutionPlan:
    mode: str
    display_command: str
    argv: list[str] | None


def build_execution_plan(command: str, dry_run: bool) -> ExecutionPlan:
    if dry_run:
        return ExecutionPlan(mode="dry_run", display_command=command, argv=None)
    utf8_command = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {command}"
    return ExecutionPlan(mode="run", display_command=command, argv=POWERSHELL_PREFIX + [utf8_command])


def _clean_line(raw_line: str) -> str:
    return raw_line.replace("\x00", "").rstrip("\n")


class ActionRunner(QThread):
    output_line = Signal(str)
    finished_with_code = Signal(int)

    def __init__(self, plan: ExecutionPlan, parent=None):
        super().__init__(parent)
        self._plan = plan
        self.captured_output: list[str] = []
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        if self._plan.mode == "dry_run":
            line = f"[DRY-RUN] {self._plan.display_command}"
            self.captured_output.append(line)
            self.output_line.emit(line)
            self.finished_with_code.emit(0)
            return

        process = subprocess.Popen(
            self._plan.argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = _clean_line(raw_line)
                self.captured_output.append(line)
                self.output_line.emit(line)
            process.wait()
            self.finished_with_code.emit(process.returncode)
        except Exception:
            process.kill()
            process.wait()
            self.finished_with_code.emit(-1)
