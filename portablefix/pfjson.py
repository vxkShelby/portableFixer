"""Structured lines in an action's output: `PFJSON:{...}`.

A catalog command prints ordinary text for the technician and, next to it,
machine-readable records on lines of their own:

    PFJSON:{"undo": {"id": "run-0a1b2c3d4e5f", "prior": {"src": "user"}}}
    PFJSON:{"findings": [{"id": "disk.smart", "severity": "critical", ...}]}

The executor takes these lines out of the console stream (they are neither
shown nor kept in the audit log's output text) and hands the parsed objects
to whoever ran the action: per-item undo records (research G05) and findings
(research G02) are read from them.

Qt-free on purpose: the executor, the GUI, the report and the CLI use it.
"""

import json
import re

PREFIX = "PFJSON:"

# One output line is at most a few KB; a payload far beyond that is not ours.
MAX_PAYLOAD_CHARS = 64_000


def parse_line(line: str) -> dict | None:
    """The object on a `PFJSON:` line, or None for any other line. A PFJSON
    line that is not a JSON object is None too - never an exception: a
    command's output must not be able to break the run that reads it."""
    if not line.startswith(PREFIX):
        return None
    text = line[len(PREFIX):].strip()
    if not text or len(text) > MAX_PAYLOAD_CHARS:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def is_pfjson(line: str) -> bool:
    return line.startswith(PREFIX)


def records(payloads, key: str) -> list:
    """Every entry under `key` across the payloads: a list is flattened, a
    single object counts as one entry (Windows PowerShell 5.1's
    ConvertTo-Json writes a one-element array as a bare object)."""
    found = []
    for payload in payloads or []:
        if not isinstance(payload, dict) or key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            found.extend(value)
        elif isinstance(value, dict) and set(value) == {"value", "Count"} and isinstance(value["value"], list):
            # The same 5.1 quirk for an array that went through Add-Member.
            found.extend(value["value"])
        else:
            found.append(value)
    return found


# --- findings (research G02) ------------------------------------------------

# Health areas of the dashboard, in tile order. "hardware" (devices, GPU
# driver, sensors) holds three of the five former problem_keywords rules.
AREAS = ("disk", "crashes", "security", "updates", "battery", "boot", "hardware")
# Worst last; "ok" is a finding too - "checked, nothing wrong" is what turns
# an Unknown tile into an OK one.
SEVERITIES = ("ok", "attention", "critical")
_FINDING_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_ACTION_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
MAX_FINDINGS = 50
_MAX_MESSAGE_CHARS = 500


def _one_line(text) -> str:
    return " ".join(str(text).split())[:_MAX_MESSAGE_CHARS]


def clean_finding(raw) -> dict | None:
    """A validated finding, or None when it does not follow the format. The
    messages are flattened to one line and capped; fix ids must look like
    action ids (whether they exist is checked where the catalog is known)."""
    if not isinstance(raw, dict):
        return None
    fid, severity, area = raw.get("id"), raw.get("severity"), raw.get("area")
    if not isinstance(fid, str) or not _FINDING_ID_RE.match(fid):
        return None
    if severity not in SEVERITIES or area not in AREAS:
        return None
    msg_sk, msg_en = raw.get("msg_sk"), raw.get("msg_en")
    if not isinstance(msg_en, str) or not msg_en.strip():
        return None
    if not isinstance(msg_sk, str) or not msg_sk.strip():
        msg_sk = msg_en
    fix = raw.get("fix", [])
    if isinstance(fix, str):
        fix = [fix]
    if not isinstance(fix, list):
        fix = []
    fix = [f for f in fix if isinstance(f, str) and _ACTION_ID_RE.match(f)][:10]
    return {
        "id": fid, "severity": severity, "area": area,
        "msg_sk": _one_line(msg_sk), "msg_en": _one_line(msg_en), "fix": fix,
    }


def findings(payloads) -> list[dict]:
    """The valid findings of one action's output, first occurrence of an id
    wins, at most MAX_FINDINGS."""
    result, seen = [], set()
    for raw in records(payloads, "findings"):
        finding = clean_finding(raw)
        if finding is None or finding["id"] in seen:
            continue
        seen.add(finding["id"])
        result.append(finding)
        if len(result) >= MAX_FINDINGS:
            break
    return result
