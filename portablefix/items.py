"""Per-item selection for an action (research G05, the FRST "fixlist" model).

An action with `items_command:` in actions.yaml works on items the
technician picks, not on everything at once:

    items_command: <SAFE PowerShell printing one JSON object per line>
        {"id": "run-0a1b2c3d4e5f", "label": "Contoso Updater", "detail": "...", "risk_hint": "MODERATE"}
    command:       <applies the change to the ids in $__pfItems>
    undo_command:  <optional; restores ONE item, $__pfItem and $__pfPrior>

The selected ids never reach PowerShell by string interpolation into the
command: they are validated against ITEM_ID_RE, written one per line to a
per-run file under Backups/<run_id>/items/, and the executor puts two
assignments in front of the command - the file's path (a quoted literal)
and `$__pfItems = [string[]]@(...ReadAllLines...)`. The command then treats
them as data.

Undo is per item: for every item the command reports as changed, with a
`PFJSON:{"undo": {"id": ..., "prior": {...}}}` line, undo.ps1 gets one step
that sets $__pfItem (the id) and $__pfPrior (the reported previous state,
flat and validated, each value a PowerShell literal) and runs undo_command.
An item without such a record was not changed, so it gets no undo line.

Qt-free on purpose: the loader, the GUI, the CLI and the tests use it.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import pfjson
from .ops import ps_str

ITEMS_VARIABLE = "$__pfItems"
ITEMS_FILE_VARIABLE = "$__pfItemsFile"

# What an id may contain: enough for KB numbers, printer and task names,
# GUIDs and hashes - but no quote, $, backtick, semicolon, pipe, redirection,
# wildcard (* ? [ ]) or control character, and no leading/trailing space.
# The ids reach the command as data anyway; this is the second fence.
ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_.:{}()#@+\-\\](?:[A-Za-z0-9 _.:{}()#@+\-\\]{0,198}[A-Za-z0-9_.:{}()#@+\-\\])?$")
MAX_ITEMS = 500
_MAX_TEXT = 500
RISK_HINTS = ("SAFE", "MODERATE", "DESTRUCTIVE")

_PRIOR_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
_MAX_PRIOR_KEYS = 16
_MAX_PRIOR_TEXT = 8192


class ItemsError(ValueError):
    """An id or an undo record that does not follow the format."""


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    detail: str = ""
    risk_hint: str = ""


def _text(value, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def parse_items(lines) -> list[Item]:
    """The items an items_command printed: one JSON object per line, other
    lines (progress, warnings) ignored. An object with an invalid id is
    dropped rather than failing the whole list; so is a repeated id."""
    found: list[Item] = []
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str) or not ITEM_ID_RE.match(item_id) or item_id in seen:
            continue
        hint = raw.get("risk_hint")
        seen.add(item_id)
        found.append(Item(
            id=item_id,
            label=_text(raw.get("label")) or item_id,
            detail=_text(raw.get("detail")),
            risk_hint=hint if hint in RISK_HINTS else "",
        ))
        if len(found) >= MAX_ITEMS:
            break
    return found


def check_ids(ids, allowed=None) -> list[str]:
    """`ids` as a clean list: each must match ITEM_ID_RE and, when `allowed`
    is given, be one the items_command listed. Raises ItemsError."""
    if not isinstance(ids, (list, tuple)):
        raise ItemsError("items must be a list of ids")
    result: list[str] = []
    for item_id in ids:
        if not isinstance(item_id, str) or not ITEM_ID_RE.match(item_id):
            raise ItemsError(f"invalid item id {item_id!r}")
        if allowed is not None and item_id not in allowed:
            raise ItemsError(f"item {item_id!r} is not in the current list")
        if item_id not in result:
            result.append(item_id)
    if len(result) > MAX_ITEMS:
        raise ItemsError(f"at most {MAX_ITEMS} items per action")
    return result


_SAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def items_file_path(base_dir: Path, run_id: str, action_id: str) -> Path:
    """Backups/<run_id>/items/<action_id>.txt - a fresh name per run of the
    action, like the ops state files, so a second run keeps the first list."""
    folder = Path(base_dir) / "Backups" / run_id / "items"
    stem = _SAFE_FILE_CHARS.sub("_", action_id) or "action"
    candidate = folder / f"{stem}.txt"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}.txt"
        counter += 1
    return candidate


def write_items_file(path: Path, ids: list[str]) -> Path:
    ids = check_ids(ids)
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 without BOM, one id per line: ReadAllLines needs no parsing and
    # an id can hold no line break (ITEM_ID_RE).
    path.write_text("".join(f"{item_id}\n" for item_id in ids), encoding="utf-8")
    return path


def prelude(path: Path) -> str:
    """The assignments the executor puts in front of an items action."""
    return (
        f"{ITEMS_FILE_VARIABLE} = {ps_str(str(path))}; "
        f"{ITEMS_VARIABLE} = [string[]]@([IO.File]::ReadAllLines({ITEMS_FILE_VARIABLE}) | Where-Object {{ $_ }}); "
    )


# --- per-item undo -------------------------------------------------------------

def _clean_prior(prior) -> dict:
    if prior is None:
        return {}
    if not isinstance(prior, dict) or len(prior) > _MAX_PRIOR_KEYS:
        raise ItemsError("prior must be a small flat object")
    clean = {}
    for key, value in prior.items():
        if not isinstance(key, str) or not _PRIOR_KEY_RE.match(key):
            raise ItemsError(f"invalid prior key {key!r}")
        if value is None or isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, int):
            if not -(2**63) <= value < 2**64:
                raise ItemsError(f"prior {key}: integer out of range")
            clean[key] = value
        elif isinstance(value, str):
            if len(value) > _MAX_PRIOR_TEXT:
                raise ItemsError(f"prior {key}: text too long")
            clean[key] = value
        else:
            raise ItemsError(f"prior {key}: only text, integers, true/false and null")
    return clean


def undo_records(payloads, selected) -> list[tuple[str, dict]]:
    """(id, prior) for every item the command reported as changed. A record
    for an id that was not selected, or malformed, is dropped - undo.ps1
    must never touch an item the technician did not pick."""
    selected = set(selected)
    result: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for raw in pfjson.records(payloads, "undo"):
        if not isinstance(raw, dict):
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str) or item_id not in selected or item_id in seen:
            continue
        try:
            prior = _clean_prior(raw.get("prior"))
        except ItemsError:
            continue
        seen.add(item_id)
        result.append((item_id, prior))
    return result


def _ps_literal(value) -> str:
    if value is None:
        return "$null"
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, int):
        return str(value) if -(2**63) <= value < 2**63 else f"([decimal]'{value}')"
    return ps_str(value)


def _flat(text) -> str:
    return " ".join(str(text).split())


def undo_step(action_id: str, undo_command: str, item_id: str, prior: dict, user_prelude: str = "") -> str:
    """One undo.ps1 step restoring one item. The id and every prior value go
    in as literals assigned to variables; undo_command itself comes from the
    catalog. A failure restores nothing further for this item but never
    stops the rest of undo.ps1."""
    if not ITEM_ID_RE.match(item_id):
        raise ItemsError(f"invalid item id {item_id!r}")
    prior = _clean_prior(prior)
    prior_ps = "@{ " + "; ".join(f"{key} = {_ps_literal(value)}" for key, value in prior.items()) + " }"
    return (
        f"# {_flat(action_id)}: item {item_id}\n"
        f"{user_prelude}& {{ $ErrorActionPreference = 'Stop'; $__pfItem = {ps_str(item_id)}; $__pfPrior = {prior_ps}; "
        f"try {{ {undo_command} }} catch {{ Write-Output ('FAILED to restore ' + $__pfItem + ' ('"
        " + [string]$_.FullyQualifiedErrorId + '): ' + $_.Exception.Message) } }"
    )
