import os
import subprocess
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

POWERSHELL_PREFIX = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]

# No output for this long means the command is hung (not just slow) -
# real progress (DISM, chkdsk, scans) redraws a line every few seconds.
INACTIVITY_TIMEOUT_SEC = 300
# Absolute ceiling regardless of activity, for commands that spin forever
# without ever exiting (e.g. waiting on a service that never responds).
HARD_CAP_SEC = 7200
WATCHDOG_POLL_SEC = 5

READ_CHUNK_SIZE = 4096


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


def _iter_output_segments(fd: int):
    """Read raw bytes and split on \\r or \\n.

    Tools like DISM/chkdsk redraw progress with a bare \\r instead of a
    newline; splitting on \\n only left that progress invisible for the
    whole run, which looked identical to a genuine hang.
    """
    buf = b""
    while True:
        chunk = os.read(fd, READ_CHUNK_SIZE)
        if not chunk:
            break
        buf += chunk
        while True:
            idx_n = buf.find(b"\n")
            idx_r = buf.find(b"\r")
            candidates = [i for i in (idx_n, idx_r) if i != -1]
            if not candidates:
                break
            idx = min(candidates)
            segment = buf[:idx]
            skip = 2 if buf[idx : idx + 2] == b"\r\n" else 1
            buf = buf[idx + skip :]
            yield segment.decode("utf-8", errors="replace")
    if buf:
        yield buf.decode("utf-8", errors="replace")


class ActionRunner(QThread):
    output_line = Signal(str)
    finished_with_code = Signal(int)

    TIMEOUT_EXIT_CODE = -2
    CANCELLED_EXIT_CODE = -3

    def __init__(self, plan: ExecutionPlan, parent=None):
        super().__init__(parent)
        self._plan = plan
        self.captured_output: list[str] = []
        self._process: subprocess.Popen | None = None
        self._cancel_requested = False
        self._timed_out = False
        self._last_activity = time.monotonic()
        self._watchdog_stop = threading.Event()
        self.finished.connect(self.deleteLater)

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:
                pass

    def _watchdog(self) -> None:
        start = time.monotonic()
        while not self._watchdog_stop.wait(WATCHDOG_POLL_SEC):
            now = time.monotonic()
            if now - self._last_activity > INACTIVITY_TIMEOUT_SEC or now - start > HARD_CAP_SEC:
                self._timed_out = True
                if self._process is not None:
                    try:
                        self._process.kill()
                    except OSError:
                        pass
                break

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
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._process = process
        watchdog = threading.Thread(target=self._watchdog, daemon=True)
        watchdog.start()
        try:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            for segment in _iter_output_segments(fd):
                self._last_activity = time.monotonic()
                line = _clean_line(segment)
                if not line:
                    continue
                self.captured_output.append(line)
                self.output_line.emit(line)
            process.wait()
            self._watchdog_stop.set()
            if self._timed_out:
                self.output_line.emit("[PortableFix] Action timed out and was terminated.")
                self.finished_with_code.emit(self.TIMEOUT_EXIT_CODE)
            elif self._cancel_requested:
                self.output_line.emit("[PortableFix] Action cancelled by user.")
                self.finished_with_code.emit(self.CANCELLED_EXIT_CODE)
            else:
                self.finished_with_code.emit(process.returncode)
        except Exception:
            self._watchdog_stop.set()
            process.kill()
            process.wait()
            self.finished_with_code.emit(-1)
