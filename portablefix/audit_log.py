import json
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class AuditEntry:
    timestamp: str
    module_id: str
    action_id: str
    command: str
    exit_code: int | None
    output: str
    dry_run: bool
    hostname: str
    run_id: str
    risk: str = ""
    warned: bool = False
    elevated: bool = False
    # Fields below were added after logs already existed in the field - they
    # all have defaults so older entries (and readers of older logs) keep
    # working; report.py treats a missing key as "not recorded".
    # The exact confirmation text the technician was shown and answered -
    # proof of *what* they were warned about, not just that a dialog fired.
    warning_text: str = ""
    # "module_id/action_id" a _system event is about (e.g. the action a
    # declined confirmation or a restore point was guarding).
    subject: str = ""
    # Technician's answer to a safety prompt: "declined" (risk warning),
    # "proceed" / "skip" (restore point failed - continue without one?).
    decision: str = ""


def make_entry(
    module_id: str,
    action_id: str,
    command: str,
    exit_code: int | None,
    output: str,
    dry_run: bool,
    run_id: str,
    risk: str = "",
    warned: bool = False,
    elevated: bool = False,
    warning_text: str = "",
    subject: str = "",
    decision: str = "",
) -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        module_id=module_id,
        action_id=action_id,
        command=command,
        exit_code=exit_code,
        output=output,
        dry_run=dry_run,
        hostname=socket.gethostname(),
        run_id=run_id,
        risk=risk,
        warned=warned,
        elevated=elevated,
        warning_text=warning_text,
        subject=subject,
        decision=decision,
    )


def audit_log_path(base_dir: Path, run_id: str) -> Path:
    return base_dir / "Logs" / f"{run_id}.jsonl"


def append_entry(base_dir: Path, run_id: str, entry: AuditEntry) -> None:
    path = audit_log_path(base_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
