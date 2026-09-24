import json

from portablefix.history import recent_runs


def _write_report(reports_dir, hostname, run_id, actions, generated_at="2026-09-24T10:00:00+00:00", html=True):
    reports_dir.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "generated_at": generated_at, "actions": actions}
    (reports_dir / f"{hostname}_{run_id}.json").write_text(json.dumps(data), encoding="utf-8")
    if html:
        (reports_dir / f"{hostname}_{run_id}.html").write_text("<html></html>", encoding="utf-8")


def test_recent_runs_newest_first_with_counts(tmp_path):
    reports = tmp_path / "Reports"
    _write_report(reports, "PC1", "20260901T100000-aaaa", [{"exit_code": 0, "dry_run": True}])
    _write_report(
        reports,
        "PC1",
        "20260924T100000-bbbb",
        [{"exit_code": 0, "dry_run": False}, {"exit_code": 1, "dry_run": False}],
    )
    runs = recent_runs(reports, "PC1")
    assert [r.run_id for r in runs] == ["20260924T100000-bbbb", "20260901T100000-aaaa"]
    assert (runs[0].action_count, runs[0].failed_count, runs[0].dry_run) == (2, 1, False)
    assert (runs[1].action_count, runs[1].failed_count, runs[1].dry_run) == (1, 0, True)
    assert runs[0].html_path is not None and runs[0].html_path.name.endswith(".html")


def test_recent_runs_filters_other_hosts_and_respects_limit(tmp_path):
    reports = tmp_path / "Reports"
    for i in range(7):
        _write_report(reports, "PC1", f"2026090{i}T000000-x", [])
    _write_report(reports, "OTHER", "20261231T000000-z", [])
    # A hostname containing glob metacharacters must not break matching.
    _write_report(reports, "PC[1]", "20261231T000000-y", [])
    runs = recent_runs(reports, "PC1", limit=5)
    assert len(runs) == 5
    assert all(r.run_id.startswith("2026090") for r in runs)
    assert [r.run_id for r in recent_runs(reports, "PC[1]")] == ["20261231T000000-y"]


def test_recent_runs_skips_corrupt_or_foreign_json(tmp_path):
    reports = tmp_path / "Reports"
    reports.mkdir()
    (reports / "PC1_bad.json").write_text("{not json", encoding="utf-8")
    (reports / "PC1_list.json").write_text("[1, 2]", encoding="utf-8")
    (reports / "PC1_noactions.json").write_text(json.dumps({"run_id": "x", "actions": 5}), encoding="utf-8")
    _write_report(reports, "PC1", "good", [{"exit_code": 0, "dry_run": False}], html=False)
    runs = recent_runs(reports, "PC1")
    assert [r.run_id for r in runs] == ["good"]
    assert runs[0].html_path is None


def test_recent_runs_missing_dir_and_display_date(tmp_path):
    assert recent_runs(tmp_path / "nope", "PC1") == []
    reports = tmp_path / "Reports"
    _write_report(reports, "PC1", "r", [], generated_at="garbage")
    assert recent_runs(reports, "PC1")[0].display_date() == "garbage"
    _write_report(reports, "PC1", "s", [], generated_at="2026-09-24T10:00:00+00:00")
    assert recent_runs(reports, "PC1", exclude_run_id="r")[0].display_date().startswith("2026-09-24")


def test_display_date_survives_out_of_range_dates(tmp_path):
    reports = tmp_path / "Reports"
    _write_report(reports, "PC1", "r", [], generated_at="0001-01-01T00:00:00+00:00")
    assert recent_runs(reports, "PC1")[0].display_date()  # no exception
