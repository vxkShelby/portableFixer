"""Research G02: per-area verdicts and the client summary from findings."""

from portablefix import health


def _f(fid, severity, area="disk", fix=()):
    return {"id": fid, "severity": severity, "area": area, "msg_sk": fid + " sk", "msg_en": fid + " en", "fix": list(fix)}


def test_latest_finding_per_id_wins_and_dry_runs_do_not_count():
    entries = [
        {"dry_run": False, "findings": [_f("disk.health", "critical", fix=["chk"])]},
        {"dry_run": True, "findings": [_f("disk.health", "ok")]},
        {"dry_run": False, "findings": [_f("disk.health", "attention")]},
        {"dry_run": False},
        "junk",
    ]
    assert health.latest_findings(entries)["disk.health"]["severity"] == "attention"


def test_area_verdict_is_the_worst_finding_and_unknown_without_any():
    findings = {f["id"]: f for f in (
        _f("disk.health", "ok"), _f("disk.smart", "critical", fix=["chk"]), _f("security.av", "attention", "security"),
        _f("x.bad", "catastrophic"), _f("y.bad", "ok", "moon"),
    )}
    verdicts = health.area_verdicts(findings)
    assert verdicts["disk"]["state"] == "critical"
    assert [f["id"] for f in verdicts["disk"]["findings"]] == ["disk.smart", "disk.health"]
    assert verdicts["security"]["state"] == "attention"
    assert verdicts["battery"] == {"state": "unknown", "findings": []}
    assert set(verdicts) == {"disk", "crashes", "security", "updates", "battery", "boot", "hardware"}


def test_recommended_fixes_come_from_problems_only_and_known_actions():
    findings = {f["id"]: f for f in (
        _f("a", "critical", fix=["fix1", "fix2"]), _f("b", "attention", fix=["fix2", "ghost"]), _f("c", "ok", fix=["fix3"]),
    )}
    assert health.recommended_fixes(findings) == ["fix1", "fix2", "ghost"]
    assert health.recommended_fixes(findings, known_ids={"fix1", "fix2", "fix3"}) == ["fix1", "fix2"]


def test_client_summary_found_fixed_recommended():
    actions = [
        {"action_id": "diag", "label": "Diag", "risk": "SAFE", "exit_code": 0, "dry_run": False,
         "findings": [_f("disk.smart", "critical", fix=["chk", "fix_ran"]), _f("boot.safe", "ok", "boot")]},
        {"action_id": "fix_ran", "label": "Fix that ran", "risk": "MODERATE", "exit_code": 0, "dry_run": False},
        {"action_id": "skipped", "label": "Already", "risk": "MODERATE", "exit_code": 0, "dry_run": False, "already_applied": True},
        {"action_id": "broke", "label": "Broke", "risk": "MODERATE", "exit_code": 1, "dry_run": False},
        {"action_id": "preview", "label": "Preview", "risk": "MODERATE", "exit_code": 0, "dry_run": True},
    ]
    summary = health.client_summary(actions, "en", label_of=lambda aid: {"chk": "Check disk"}.get(aid))
    assert summary["found"] == [{"id": "disk.smart", "severity": "critical", "area": "disk", "message": "disk.smart en"}]
    assert summary["fixed"] == [{"action_id": "fix_ran", "label": "Fix that ran"}]
    assert summary["recommended"] == [{"action_id": "chk", "label": "Check disk"}]
    assert health.client_summary(actions, "sk")["found"][0]["message"] == "disk.smart sk"
