"""Visits log (research N7): rows over every PC's reports, CSV/HTML export,
redaction, and the --visits switch of the headless CLI."""

import csv
import json
from datetime import date

import pytest

from portablefix import cli, visits


def _report(reports, host, run_id, *, actions=None, generated_at="2026-09-24T10:00:00+00:00", job=None, work=None,
            **extra):
    reports.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "hostname": host, "generated_at": generated_at, "job": job or {},
            "actions": [{"exit_code": 0, "dry_run": False}] if actions is None else actions, **extra}
    if work is not None:
        data["work_time"] = work
    (reports / f"{host}_{run_id}.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def reports(tmp_path):
    folder = tmp_path / "Reports"
    _report(folder, "SHOP-PC", "20260901T080000-aaaa", generated_at="2026-09-01T08:30:00+00:00", job={"technician": "Ján", "client": "Novák s.r.o."},
            actions=[{"exit_code": 0, "dry_run": False}, {"exit_code": 1, "dry_run": False}],
            work={"batch_seconds": 3725, "timer_seconds": 600})
    _report(folder, "CLIENT-2", "20260915T080000-bbbb", generated_at="2026-09-15T08:30:00+00:00",
            actions=[{"exit_code": 0, "dry_run": True, "duration_sec": 4.2}, {"exit_code": 0, "dry_run": True, "duration_sec": 6}])
    return folder


def test_collects_every_hostname_newest_first(reports):
    rows = visits.collect_visits(reports)
    assert [(r.hostname, r.run_id) for r in rows] == [("CLIENT-2", "20260915T080000-bbbb"), ("SHOP-PC", "20260901T080000-aaaa")]
    shop = rows[1]
    assert (shop.technician, shop.client, shop.actions, shop.failed, shop.dry_run) == ("Ján", "Novák s.r.o.", 2, 1, False)
    assert (shop.work_seconds, shop.timer_seconds) == (3725, 600)
    # No work_time block: summed from the actions' own timings; all dry-run.
    assert (rows[0].work_seconds, rows[0].timer_seconds, rows[0].dry_run) == (10, None, True)


def test_since_filters_by_local_date_and_drops_unreadable_dates(reports):
    _report(reports, "X-PC", "20260920T000000-cccc", generated_at="garbage")
    assert [r.hostname for r in visits.collect_visits(reports, since=date(2026, 9, 10))] == ["CLIENT-2"]
    assert len(visits.collect_visits(reports)) == 3


def test_corrupt_foreign_and_client_copies_are_skipped(reports):
    (reports / "BAD_1.json").write_text("{not json", encoding="utf-8")
    (reports / "LIST_1.json").write_text("[1]", encoding="utf-8")
    (reports / "NOACT_1.json").write_text(json.dumps({"run_id": "x", "actions": 5}), encoding="utf-8")
    (reports / "pc_20260915T080000-bbbb_client.json").write_text(
        json.dumps({"run_id": "20260915T080000-bbbb", "actions": []}), encoding="utf-8")
    (reports / "BINARY_1.json").write_bytes(b"\xff\xfe\x00bad")
    # Partial report: no hostname/job/date/work_time - host from the file name, junk values tolerated.
    (reports / "OLD-PC_r1.json").write_text(
        json.dumps({"run_id": "r1", "job": "x", "work_time": {"batch_seconds": "n/a"}, "actions": ["junk", {"exit_code": None}]}),
        encoding="utf-8")
    rows = visits.collect_visits(reports)
    assert len(rows) == 3
    old = next(r for r in rows if r.run_id == "r1")
    assert (old.hostname, old.started, old.actions, old.failed, old.work_seconds) == ("OLD-PC", None, 1, 1, None)
    assert visits.collect_visits(reports / "missing") == []


def test_redaction_masks_computer_and_customer(reports):
    rows = visits.collect_visits(reports, redact=True)
    assert {r.hostname for r in rows} == {"<pc>"}
    assert {r.client for r in rows} == {"<client>", ""}
    assert "Novák" not in visits.render_html(rows) and "SHOP-PC" not in visits.render_html(rows)
    assert "Ján" in {r.technician for r in rows}  # entered on purpose, kept like in the report


def test_csv_round_trip_bom_delimiter_and_quoting(reports, tmp_path):
    _report(reports, "Q-PC", "20260930T000000-dddb", job={"client": 'Smith; "Co"\nLtd', "technician": "=cmd|' /c calc'!A1"})
    dest = tmp_path / "v.csv"
    visits.write_csv(visits.collect_visits(reports), dest, "sk")
    raw = dest.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    with dest.open(encoding="utf-8-sig", newline="") as f:
        table = list(csv.reader(f, delimiter=";"))
    assert table[0][:3] == ["Dátum a čas", "Počítač", "Technik"]
    quoted = table[1]
    assert quoted[3] == 'Smith; "Co"\nLtd'
    assert quoted[2].startswith("'=")  # no formula in Excel
    shop = next(r for r in table if r[1] == "SHOP-PC")
    assert shop[2:] [:2] == ["Ján", "Novák s.r.o."] and shop[4:8] == ["2", "1", "nie", "1:02:05"] and shop[8] == "0:10:00"


def test_html_table_escapes(reports, tmp_path):
    _report(reports, "H-PC", "20261001T000000-eeee", job={"client": "<b>x</b> & y"})
    dest = visits.write_html(visits.collect_visits(reports), tmp_path / "v.html", "en")
    text = dest.read_text(encoding="utf-8")
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in text and "<b>x</b>" not in text
    assert text.count("<tr>") == 4 and "Visits log" in text


def _cli(tmp_path, *args, language="en"):
    lines = []
    deps = cli.Deps(unsupported_os=lambda: False, running_from_temp=lambda app_dir: False, out=lines.append)
    code = cli.run(["PortableFix.exe", "--visits", *args, "--out", str(tmp_path), "--language", language],
                   assets_dir=tmp_path / "app", deps=deps)
    return code, lines


def test_cli_visits_default_writes_both_files_next_to_the_reports(reports, tmp_path):
    assert cli.wants_cli(["x.exe", "--visits"])
    code, lines = _cli(tmp_path)
    assert code == cli.EXIT_OK and len(lines) == 2
    assert (reports / "visits.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert "CLIENT-2" in (reports / "visits.html").read_text(encoding="utf-8")


def test_cli_visits_paths_since_and_redaction(reports, tmp_path):
    dest = tmp_path / "only.csv"
    code, lines = _cli(tmp_path, "--csv", str(dest), "--since", "2026-09-10")
    assert code == cli.EXIT_OK and len(lines) == 1 and not (reports / "visits.html").exists()
    assert dest.read_bytes().count(b"\r\n") == 2  # header + one visit
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "settings.json").write_text(json.dumps({"redact_for_client": True}), encoding="utf-8")
    _cli(tmp_path, "--csv", str(dest))
    text = dest.read_text(encoding="utf-8-sig")
    assert "<pc>" in text and "SHOP-PC" not in text and "Novák" not in text


def test_cli_visits_refuses_a_bad_since_date(reports, tmp_path):
    code, lines = _cli(tmp_path, "--since", "yesterday")
    assert code == cli.EXIT_ERROR and "--since" in lines[0]
