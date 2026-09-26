"""The health verdict from structured findings (research G02), instead of a
score formula: one tile per area (portablefix/pfjson.py AREAS), each OK /
Attention / Critical / Unknown with the finding that decided it and the
actions that fix it, plus the client summary at the top of the report.

Qt-free: the dashboard and the report both use it."""

from .pfjson import AREAS, SEVERITIES

UNKNOWN = "unknown"
_RANK = {severity: index for index, severity in enumerate(SEVERITIES)}


def latest_findings(entries) -> dict[str, dict]:
    """finding id -> the newest finding with that id, over audit entries or
    report actions in chronological order (a later run's "ok" replaces an
    earlier "critical" - the problem was fixed in between)."""
    latest: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("dry_run"):
            continue
        for finding in entry.get("findings") or []:
            if isinstance(finding, dict) and isinstance(finding.get("id"), str):
                latest.pop(finding["id"], None)
                latest[finding["id"]] = finding
    return latest


def area_verdicts(findings: dict[str, dict]) -> dict[str, dict]:
    """area -> {"state", "findings"} for every area: the worst severity of
    its findings (worst first), UNKNOWN when nothing reported on it."""
    verdicts = {area: {"state": UNKNOWN, "findings": []} for area in AREAS}
    for finding in findings.values():
        area = finding.get("area")
        if area not in verdicts or finding.get("severity") not in _RANK:
            continue
        verdicts[area]["findings"].append(finding)
    for verdict in verdicts.values():
        verdict["findings"].sort(key=lambda f: -_RANK[f["severity"]])
        if verdict["findings"]:
            verdict["state"] = verdict["findings"][0]["severity"]
    return verdicts


def problems(findings: dict[str, dict]) -> list[dict]:
    """The findings that need something done (attention or critical)."""
    return [f for f in findings.values() if f.get("severity") in ("attention", "critical")]


def recommended_fixes(findings: dict[str, dict], known_ids=None) -> list[str]:
    """Fix action ids of every problem, in order, once each - only ids the
    catalog knows when `known_ids` is given."""
    result: list[str] = []
    for finding in problems(findings):
        for action_id in finding.get("fix") or []:
            if action_id not in result and (known_ids is None or action_id in known_ids):
                result.append(action_id)
    return result


def client_summary(actions: list[dict], language: str, label_of=None) -> dict:
    """Found / Fixed / Recommended for the top of report.html.

    found: the problems diagnostics reported (their latest state);
    fixed: system-changing actions that ran for real and succeeded (not
    skipped as already applied); recommended: fixes of problems still open
    whose action did not run successfully in this run."""
    findings = latest_findings(actions)
    message_key = "msg_en" if language == "en" else "msg_sk"
    found = [
        {"id": f["id"], "severity": f["severity"], "area": f["area"], "message": f.get(message_key) or f.get("msg_en", "")}
        for f in sorted(problems(findings), key=lambda f: -_RANK[f["severity"]])
    ]
    succeeded = {a.get("action_id") for a in actions if a.get("exit_code") == 0 and not a.get("dry_run")}
    fixed = [
        {"action_id": a.get("action_id"), "label": a.get("label") or a.get("action_id")}
        for a in actions
        if a.get("exit_code") == 0 and not a.get("dry_run") and not a.get("already_applied")
        and a.get("risk") not in ("SAFE", "UNKNOWN")
    ]
    labels = {a.get("action_id"): a.get("label") for a in actions}
    recommended = [
        {"action_id": aid, "label": labels.get(aid) or (label_of(aid) if label_of else None) or aid}
        for aid in recommended_fixes(findings) if aid not in succeeded
    ]
    return {"found": found, "fixed": fixed, "recommended": recommended}
