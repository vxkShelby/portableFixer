import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import pfjson
from .items import ITEMS_VARIABLE
from .items import prelude as items_prelude
from .ops import STATE_VARIABLE, ps_str

# Lives in paths.py so the Qt-free updater core can use it; re-exported
# here for the modules and tests that have always imported it from here.
from .paths import powershell_executable  # noqa: F401
from .target_user import TargetUser

POWERSHELL_PREFIX = [powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]

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


def build_execution_plan(
    command: str, dry_run: bool, temp_protect: Path | None = None, ops_state: Path | None = None,
    target_user: TargetUser | None = None, items_file: Path | None = None,
) -> ExecutionPlan:
    if dry_run:
        display = command
        if items_file is not None:
            # The ids the command would get - what a DRY-RUN of a per-item
            # action (research G05) is actually about.
            try:
                ids = [line for line in Path(items_file).read_text(encoding="utf-8").splitlines() if line]
            except OSError:
                ids = []
            display += f"  # {ITEMS_VARIABLE} = {', '.join(ids) or '-'}"
        return ExecutionPlan(mode="dry_run", display_command=display, argv=None)
    prefix = ""
    if items_file is not None:
        # The selected ids, read from their file - never pasted into the
        # command text (research G05, portablefix/items.py).
        prefix += items_prelude(items_file)
    if target_user is not None:
        # $__pfUserHive / $__pfUserSid (research G25): the signed-in user's
        # hive, which is not HKCU when the technician elevated with their
        # own account. Empty when detection did not work - commands then
        # fall back to HKCU: on their own.
        prefix += target_user.prelude()
    if temp_protect is not None:
        # Single-quote with doubled-quote escaping, not an f-string into double
        # quotes - the real path can contain $ or backticks PowerShell would expand.
        escaped = str(temp_protect).replace("'", "''")
        prefix += f"$__pfProtect = '{escaped}'; "
    if ops_state is not None:
        # Where an `ops:` command saves the state it captures (research G10);
        # without it the command refuses to change anything.
        prefix += f"{STATE_VARIABLE} = {ps_str(str(ops_state))}; "
    utf8_command = f"{prefix}[Console]::OutputEncoding=[Text.Encoding]::UTF8; {command}"
    return ExecutionPlan(mode="run", display_command=command, argv=POWERSHELL_PREFIX + [utf8_command])


def _clean_line(raw_line: str) -> str:
    return raw_line.replace("\x00", "").rstrip("\n")


def _iter_output_segments(fd: int):
    """Read raw bytes and split on \\r or \\n, yielding (text, is_real_line).

    Tools like DISM/chkdsk redraw progress with a bare \\r instead of a
    newline; splitting on \\n only left that progress invisible for the
    whole run, which looked identical to a genuine hang. A bare-\\r segment
    is a transient progress redraw (is_real_line=False) - callers still see
    it live but shouldn't treat it as a persistent line of output, or a
    single DISM run turns into hundreds of near-duplicate audit-log/report
    entries.
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
            if idx == idx_r and idx == len(buf) - 1:
                # Trailing \r with nothing after it yet - it may be the first
                # half of a \r\n split across two read() chunks. Wait for
                # more data instead of guessing wrong and losing the line.
                break
            segment = buf[:idx]
            is_real_line = buf[idx : idx + 1] == b"\n" or buf[idx : idx + 2] == b"\r\n"
            skip = 2 if buf[idx : idx + 2] == b"\r\n" else 1
            buf = buf[idx + skip :]
            yield segment.decode("utf-8", errors="replace"), is_real_line
    if buf:
        yield buf.decode("utf-8", errors="replace"), True


TIMEOUT_EXIT_CODE = -2
CANCELLED_EXIT_CODE = -3
POWERSHELL_NOT_FOUND_EXIT_CODE = -4


CHECK_STATES = ("APPLIED", "NOT_APPLIED", "UNKNOWN")
# A state check only reads - it must never hold a batch for long.
CHECK_INACTIVITY_SEC = 60
CHECK_HARD_CAP_SEC = 120


def parse_check_state(lines: list[str]) -> str:
    """The answer of a check_command (research G09): its last non-empty line
    when that is one of CHECK_STATES, otherwise UNKNOWN."""
    for line in reversed(lines):
        text = line.strip().upper()
        if text:
            return text if text in CHECK_STATES else "UNKNOWN"
    return "UNKNOWN"


class PlanRun:
    """Runs one ExecutionPlan to completion on the calling thread - the
    Qt-free core that ActionRunner puts on a QThread and the headless CLI
    (research G21) calls directly, so both paths run commands, watch for
    hangs and split out PFJSON lines the same way.

    captured_output holds the persistent lines (no progress redraws, no
    PFJSON lines); pfjson holds the parsed PFJSON objects (portablefix/pfjson.py).

    check_plan (research G09): the action's "already applied?" check, run
    first; when it answers APPLIED the plan itself never runs, skipped_applied
    is set and the run counts as a success."""

    def __init__(
        self, plan: ExecutionPlan, inactivity_timeout_sec: int | None = None, hard_cap_sec: int | None = None,
        check_plan: ExecutionPlan | None = None,
    ):
        self._plan = plan
        self._check_plan = check_plan
        self._check_run: "PlanRun | None" = None
        self.check_state: str | None = None
        self.skipped_applied = False
        self.captured_output: list[str] = []
        self.pfjson: list[dict] = []
        self.process: subprocess.Popen | None = None
        self.cancel_requested = False
        self._timed_out = False
        self._last_activity = time.monotonic()
        self._watchdog_stop = threading.Event()
        # None means "use the module default" - looked up live in _watchdog
        # (not snapshotted here) so tests can still patch INACTIVITY_TIMEOUT_SEC
        # after construction, same as they already do for WATCHDOG_POLL_SEC.
        self._inactivity_timeout_override = inactivity_timeout_sec
        self._hard_cap_override = hard_cap_sec

    def cancel(self) -> None:
        self.cancel_requested = True
        if self._check_run is not None:
            self._check_run.cancel()
        if self.process is not None:
            try:
                self.process.kill()
            except OSError:
                pass

    def _run_check(self, emit) -> bool:
        """True when the check says the change is already in place."""
        check = PlanRun(self._check_plan, inactivity_timeout_sec=CHECK_INACTIVITY_SEC, hard_cap_sec=CHECK_HARD_CAP_SEC)
        self._check_run = check
        code = check.run()
        self._check_run = None
        self.check_state = parse_check_state(check.captured_output) if code == 0 else "UNKNOWN"
        if self.check_state != "APPLIED":
            return False
        line = "[PortableFix] Already applied - nothing to do (state check: APPLIED). Skipped."
        self.captured_output.append(line)
        emit(line)
        return True

    def _watchdog(self) -> None:
        start = time.monotonic()
        while not self._watchdog_stop.wait(WATCHDOG_POLL_SEC):
            now = time.monotonic()
            timeout = (
                self._inactivity_timeout_override
                if self._inactivity_timeout_override is not None
                else INACTIVITY_TIMEOUT_SEC
            )
            hard_cap = self._hard_cap_override if self._hard_cap_override is not None else HARD_CAP_SEC
            if now - self._last_activity > timeout or now - start > hard_cap:
                self._timed_out = True
                if self.process is not None:
                    try:
                        self.process.kill()
                    except OSError:
                        pass
                break

    def run(self, emit=lambda line: None) -> int:
        """Runs the plan, passing every live line to emit(); returns the exit
        code (or one of the negative codes above)."""
        if self._plan.mode == "dry_run":
            line = f"[DRY-RUN] {self._plan.display_command}"
            self.captured_output.append(line)
            emit(line)
            return 0

        if self._check_plan is not None and self._plan.mode == "run":
            if self._run_check(emit):
                self.skipped_applied = True
                return 0
            if self.cancel_requested:
                emit("[PortableFix] Action cancelled by user.")
                return CANCELLED_EXIT_CODE

        try:
            process = subprocess.Popen(
                self._plan.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            emit("[PortableFix] PowerShell was not found on this system.")
            return POWERSHELL_NOT_FOUND_EXIT_CODE
        self.process = process
        self._last_activity = time.monotonic()
        watchdog = threading.Thread(target=self._watchdog, daemon=True)
        watchdog.start()
        try:
            assert process.stdout is not None
            fd = process.stdout.fileno()
            for segment, is_real_line in _iter_output_segments(fd):
                self._last_activity = time.monotonic()
                line = _clean_line(segment)
                if not line:
                    continue
                if is_real_line and pfjson.is_pfjson(line):
                    # Machine-readable records (research G02/G05) - kept
                    # out of the console and the audit log's output text.
                    payload = pfjson.parse_line(line)
                    if payload is not None:
                        self.pfjson.append(payload)
                    continue
                emit(line)
                if is_real_line:
                    self.captured_output.append(line)
            process.wait()
            self._watchdog_stop.set()
            if self._timed_out:
                emit("[PortableFix] Action timed out and was terminated.")
                return TIMEOUT_EXIT_CODE
            if self.cancel_requested:
                emit("[PortableFix] Action cancelled by user.")
                return CANCELLED_EXIT_CODE
            return process.returncode
        except Exception:
            self._watchdog_stop.set()
            process.kill()
            process.wait()
            return -1


class ActionRunner(QThread):
    output_line = Signal(str)
    finished_with_code = Signal(int)

    TIMEOUT_EXIT_CODE = TIMEOUT_EXIT_CODE
    CANCELLED_EXIT_CODE = CANCELLED_EXIT_CODE
    POWERSHELL_NOT_FOUND_EXIT_CODE = POWERSHELL_NOT_FOUND_EXIT_CODE

    def __init__(
        self,
        plan: ExecutionPlan,
        parent=None,
        inactivity_timeout_sec: int | None = None,
        hard_cap_sec: int | None = None,
        check_plan: ExecutionPlan | None = None,
    ):
        super().__init__(parent)
        self._core = PlanRun(
            plan, inactivity_timeout_sec=inactivity_timeout_sec, hard_cap_sec=hard_cap_sec, check_plan=check_plan,
        )
        self.finished.connect(self.deleteLater)

    @property
    def captured_output(self) -> list[str]:
        return self._core.captured_output

    @property
    def skipped_applied(self) -> bool:
        return self._core.skipped_applied

    @property
    def check_state(self) -> str | None:
        return self._core.check_state

    @property
    def pfjson(self) -> list[dict]:
        return self._core.pfjson

    @property
    def _process(self):
        return self._core.process

    @property
    def _cancel_requested(self) -> bool:
        return self._core.cancel_requested

    def cancel(self) -> None:
        self._core.cancel()

    def run(self) -> None:
        self.finished_with_code.emit(self._core.run(self.output_line.emit))

