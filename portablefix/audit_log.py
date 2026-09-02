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
    )


def audit_log_path(base_dir: Path, run_id: str) -> Path:
    return base_dir / "Logs" / f"{run_id}.jsonl"


def append_entry(base_dir: Path, run_id: str, entry: AuditEntry) -> None:
    path = audit_log_path(base_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
