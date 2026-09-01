import hashlib
import json
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
    output_hash: str
    dry_run: bool


def make_entry(
    module_id: str, action_id: str, command: str, exit_code: int | None, output: str, dry_run: bool
) -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        module_id=module_id,
        action_id=action_id,
        command=command,
        exit_code=exit_code,
        output=output,
        output_hash=hashlib.sha256(output.encode("utf-8")).hexdigest(),
        dry_run=dry_run,
    )


def audit_log_path(base_dir: Path, run_id: str) -> Path:
    return base_dir / "Logs" / f"{run_id}.jsonl"


def append_entry(base_dir: Path, run_id: str, entry: AuditEntry) -> None:
    path = audit_log_path(base_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
