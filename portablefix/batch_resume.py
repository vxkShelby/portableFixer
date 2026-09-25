"""Batches that span a restart (research G03) - Qt-free.

Three pieces the batch run path in gui/main_window.py builds on:

- ordering: an action that restarts Windows the moment it succeeds
  (`restarts_pc: true`, e.g. Defender Offline) runs last, so nothing else
  in the batch is cut off by it;
- the resume file: when a batch has to stop for a restart (after a
  `restart_before_next: true` action, or before a `restarts_pc` action with
  more queued behind it), what is left is saved to Data/pending_batch.json.
  The next start of PortableFix *offers* to continue it - nothing is ever
  registered to start with Windows, and the technician confirms the batch
  on the review screen again;
- keep-awake: SetThreadExecutionState holds off sleep while a batch runs
  (a laptop that sleeps mid-DISM ends the batch in a confusing state).
"""

import ctypes
import json
import os
import socket
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

RESUME_FILE_NAME = "pending_batch.json"
RESUME_FORMAT_VERSION = 1
# A saved batch is for "restart, then carry on" - a day later the PC may have
# been used, updated or repaired by someone else, and the plan is stale.
MAX_RESUME_AGE = timedelta(hours=24)

# winbase.h
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def resume_path(state_dir: Path) -> Path:
    # Next to settings.json: the writable state dir, which after a restart
    # resolves the same way even when the USB stick got another letter.
    return Path(state_dir) / "Data" / RESUME_FILE_NAME


def computer_name() -> str:
    # The same name the audit log and undo.ps1 record.
    return socket.gethostname()


def order_restarting_last(queue: list[str], restarts_pc: Callable[[str], bool]) -> list[str]:
    """The queue with every action that restarts the PC moved to the end,
    the relative order within both groups kept."""
    return [aid for aid in queue if not restarts_pc(aid)] + [aid for aid in queue if restarts_pc(aid)]


@dataclass(frozen=True)
class RestartSplit:
    # The action after (restart_before_next) or with (restarts_pc) which the
    # PC restarts; "" when the batch runs through without one.
    restart_action_id: str = ""
    # What runs only after the restart - on the next start of PortableFix.
    waiting_ids: tuple[str, ...] = ()


def plan_restart_split(
    queue: list[str], restarts_pc: Callable[[str], bool], restart_before_next: Callable[[str], bool],
) -> RestartSplit:
    """Where an (already ordered) batch will stop for a restart, for the
    review screen. A restart_before_next action only stops the batch when it
    succeeds - the review says so; a failed one needs no restart."""
    for index, action_id in enumerate(queue):
        if restarts_pc(action_id) or restart_before_next(action_id):
            return RestartSplit(action_id, tuple(queue[index + 1:]))
    return RestartSplit()


@dataclass
class PendingBatch:
    run_id: str
    action_ids: list[str]
    dry_run: bool = False
    # The action the restart is for (audit/report wording only).
    restart_after: str = ""
    # Report header job details (technician, client, note).
    job: dict = field(default_factory=dict)
    # undo.ps1 is rewritten from these lists for the same run_id - without
    # them the continued batch would overwrite the first half's undo steps.
    undo_steps: list[str] = field(default_factory=list)
    irreversible: list[str] = field(default_factory=list)
    hive_backups: list[str] = field(default_factory=list)
    # The "before" snapshot of the first half, so the one report compares
    # against the PC as it was when the technician started.
    snapshot_before: dict = field(default_factory=dict)
    computer: str = ""
    created: str = ""
    version: int = RESUME_FORMAT_VERSION

    def created_at(self) -> datetime | None:
        try:
            value = datetime.fromisoformat(self.created)
        except (TypeError, ValueError):
            return None
        return value if value.tzinfo is not None else None


def save_pending(state_dir: Path, pending: PendingBatch, *, now: datetime | None = None, computer: str | None = None) -> Path:
    """Writes the resume file atomically (a restart can come right after) and
    returns its path. Raises OSError - the caller tells the technician the
    batch can't be continued automatically."""
    pending.created = (now or datetime.now(timezone.utc)).isoformat()
    pending.computer = computer if computer is not None else computer_name()
    path = resume_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        data = json.dumps(asdict(pending), ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        # A snapshot value JSON can't hold must not cost the whole resume.
        pending.snapshot_before = {}
        data = json.dumps(asdict(pending), ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def discard_pending(state_dir: Path) -> None:
    for path in (resume_path(state_dir), resume_path(state_dir).with_suffix(".json.tmp")):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # A read-only stick: the file stays, and the next start drops it
            # once it is stale - never a reason to fail.
            pass


def _parse(raw) -> PendingBatch | None:
    if not isinstance(raw, dict) or raw.get("version") != RESUME_FORMAT_VERSION:
        return None
    run_id, action_ids = raw.get("run_id"), raw.get("action_ids")
    if not isinstance(run_id, str) or not run_id:
        return None
    if not isinstance(action_ids, list) or not action_ids or not all(isinstance(a, str) and a for a in action_ids):
        return None

    def str_list(key: str) -> list[str]:
        value = raw.get(key)
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    job = raw.get("job")
    snapshot = raw.get("snapshot_before")
    return PendingBatch(
        run_id=run_id,
        action_ids=list(action_ids),
        dry_run=raw.get("dry_run") is True,
        restart_after=str(raw.get("restart_after") or ""),
        job={k: v for k, v in job.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(job, dict) else {},
        undo_steps=str_list("undo_steps"),
        irreversible=str_list("irreversible"),
        hive_backups=str_list("hive_backups"),
        snapshot_before=snapshot if isinstance(snapshot, dict) else {},
        computer=str(raw.get("computer") or ""),
        created=str(raw.get("created") or ""),
    )


def load_pending(state_dir: Path, *, now: datetime | None = None, computer: str | None = None) -> PendingBatch | None:
    """The saved batch to offer on this start, or None. A file that is
    unreadable, older than MAX_RESUME_AGE, dated in the future or written on
    another computer (a USB stick moved to the next client's PC) is deleted
    here - it must never be offered, now or later."""
    path = resume_path(state_dir)
    if not path.exists():
        return None
    try:
        pending = _parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        pending = None
    now = now or datetime.now(timezone.utc)
    here = computer if computer is not None else computer_name()
    created = pending.created_at() if pending is not None else None
    if (
        pending is None
        or created is None
        # A small clock skew is fine; a file "from tomorrow" is not trusted.
        or created > now + timedelta(minutes=5)
        or now - created > MAX_RESUME_AGE
        or pending.computer.casefold() != here.casefold()
    ):
        discard_pending(state_dir)
        return None
    return pending


def _default_execution_state_setter() -> Callable[[int], int] | None:
    if sys.platform != "win32":
        return None
    try:
        func = ctypes.windll.kernel32.SetThreadExecutionState
    except (AttributeError, OSError):
        return None
    func.argtypes = [ctypes.c_uint32]
    func.restype = ctypes.c_uint32
    return func


class KeepAwake:
    """Holds off system sleep while a batch runs (the screen may still turn
    off). SetThreadExecutionState is per thread: acquire and release must
    both be called from the same (GUI) thread. `setter` is injectable for
    tests; off Windows the default is a no-op."""

    def __init__(self, setter: Callable[[int], int] | None = None):
        self._setter = setter if setter is not None else _default_execution_state_setter()
        self.active = False

    def _call(self, flags: int) -> None:
        if self._setter is None:
            return
        try:
            self._setter(flags)
        except Exception:
            # Staying awake is a convenience - a failed call must never
            # stop a batch or a close.
            pass

    def acquire(self) -> None:
        if self.active:
            return
        self._call(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        self.active = True

    def release(self) -> None:
        if not self.active:
            return
        # ES_CONTINUOUS alone clears the requirement set above.
        self._call(ES_CONTINUOUS)
        self.active = False
