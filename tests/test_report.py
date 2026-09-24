import json

import pytest

from portablefix.audit_log import append_entry, audit_log_path, make_entry
from portablefix.models import ActionDef, ModuleDef, RiskLevel
from portablefix.report import build_report_data, generate_report


def _fixture_modules():
    action = ActionDef(
        id="user_temp",
        label_sk="Docasne subory",
        label_en="Temp files",
        risk=RiskLevel.SAFE,
        command="Remove-Item $env:TEMP",
    )
    return [ModuleDef(module_id="m02_cleanup", actions=[action])]


def test_build_report_data_joins_audit_entries_with_catalog(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "Remove-Item $env:TEMP", 0, "done", False, "run1")
    append_entry(tmp_path, "run1", entry)

    data = build_report_data(
        tmp_path, "run1", modules, "en",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 12.0},
    )

    assert data["run_id"] == "run1"
    assert data["snapshot_before"]["free_gb"] == 10.0
    assert data["snapshot_after"]["free_gb"] == 12.0
    assert len(data["actions"]) == 1
    assert data["actions"][0]["label"] == "Temp files"
    assert data["actions"][0]["risk"] == "SAFE"
    assert data["actions"][0]["exit_code"] == 0
    assert data["generated_at"]


def test_build_report_data_unknown_action_falls_back_to_id(tmp_path):
    entry = make_entry("m02_cleanup", "not_in_catalog", "cmd", 0, "out", False, "run2")
    append_entry(tmp_path, "run2", entry)
    data = build_report_data(tmp_path, "run2", [], "en", {}, {})
    assert data["actions"][0]["label"] == "not_in_catalog"
    assert data["actions"][0]["risk"] == "UNKNOWN"


def test_generate_report_writes_html_and_json(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "Remove-Item $env:TEMP", 0, "done", False, "run3")
    append_entry(tmp_path, "run3", entry)

    html_path, json_path = generate_report(
        tmp_path, "run3", modules, "en",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 12.0},
    )

    assert html_path.exists()
    assert json_path.exists()
    assert html_path.parent == tmp_path / "Reports"
    html_content = html_path.read_text(encoding="utf-8")
    assert "Temp files" in html_content
    assert "SAFE" in html_content
    assert "Generated:" in html_content

    json_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert json_data["run_id"] == "run3"
    assert len(json_data["actions"]) == 1


def test_render_html_escapes_unsafe_characters(tmp_path):
    from portablefix.audit_log import append_entry, make_entry

    entry = make_entry("m02_cleanup", "<script>alert(1)</script>", "cmd", 0, "out", False, "run_xss")
    append_entry(tmp_path, "run_xss", entry)

    html_path, _ = generate_report(tmp_path, "run_xss", [], "en", {}, {})
    content = html_path.read_text(encoding="utf-8")

    assert "<script>alert(1)</script>" not in content
    assert "&lt;script&gt;" in content


def test_build_report_data_includes_captured_output(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "Skipped locked items: 3", False, "run_out")
    append_entry(tmp_path, "run_out", entry)

    data = build_report_data(
        tmp_path, "run_out", modules, "en",
        snapshot_before={}, snapshot_after={},
    )

    assert data["actions"][0]["output"] == "Skipped locked items: 3"


def test_html_report_has_collapsible_escaped_output_and_failure_marker(tmp_path):
    modules = _fixture_modules()
    append_entry(tmp_path, "run_html", make_entry("m02_cleanup", "user_temp", "cmd", 0, "<b>raw & output</b>", False, "run_html"))
    append_entry(tmp_path, "run_html", make_entry("m02_cleanup", "user_temp", "cmd", 1, "boom", False, "run_html"))

    html_path, _ = generate_report(
        tmp_path, "run_html", modules, "en",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 11.0},
    )
    content = html_path.read_text(encoding="utf-8")

    assert "<details>" in content
    assert "&lt;b&gt;raw &amp; output&lt;/b&gt;" in content
    assert "<b>raw & output</b>" not in content
    assert 'class="card fail"' in content
    assert "FAILED" in content


def test_build_report_data_skips_corrupted_audit_line(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_bad")
    append_entry(tmp_path, "run_bad", entry)
    path = audit_log_path(tmp_path, "run_bad")
    with path.open("a", encoding="utf-8") as f:
        f.write('{"truncated": tr\n')

    data = build_report_data(tmp_path, "run_bad", modules, "en", {}, {})

    assert len(data["actions"]) == 1
    assert data["actions"][0]["action_id"] == "user_temp"


def test_build_report_data_includes_comparison_with_previous_report(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    old_report = {
        "run_id": "old_run",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_after": {"free_gb": 10.0},
        "actions": [{"action_id": "a"}, {"action_id": "b"}],
    }
    (reports_dir / f"{hostname}_old_run.json").write_text(json.dumps(old_report), encoding="utf-8")

    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_compare")
    append_entry(tmp_path, "run_compare", entry)

    data = build_report_data(
        tmp_path, "run_compare", modules, "en",
        snapshot_before={"free_gb": 11.0}, snapshot_after={"free_gb": 12.0},
    )

    comparison = data["previous_comparison"]
    assert comparison["previous_run_id"] == "old_run"
    assert comparison["free_gb_delta"] == 2.0
    assert comparison["previous_action_count"] == 2
    assert comparison["action_count"] == 1


def test_build_report_data_comparison_is_none_without_a_previous_report(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_solo")
    append_entry(tmp_path, "run_solo", entry)
    data = build_report_data(tmp_path, "run_solo", modules, "en", {}, {})
    assert data["previous_comparison"] is None


def test_build_report_data_degrades_gracefully_on_a_malformed_previous_report(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    # Syntactically valid JSON, but not the dict shape this tool ever writes
    # (e.g. a hand-edited or corrupted file) - must not crash report generation.
    (reports_dir / f"{hostname}_bad.json").write_text("[1, 2, 3]", encoding="utf-8")

    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_bad_prev")
    append_entry(tmp_path, "run_bad_prev", entry)

    data = build_report_data(
        tmp_path, "run_bad_prev", modules, "en",
        snapshot_before={"free_gb": 11.0}, snapshot_after={"free_gb": 12.0},
    )
    assert data["previous_comparison"] is None


def test_build_report_data_tolerates_a_previous_report_with_wrong_shaped_snapshot(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    # snapshot_after here is a list instead of the expected dict.
    old_report = {
        "run_id": "old_run3",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_after": ["not", "a", "dict"],
        "actions": [{"action_id": "a"}],
    }
    (reports_dir / f"{hostname}_old_run3.json").write_text(json.dumps(old_report), encoding="utf-8")

    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_bad_shape")
    append_entry(tmp_path, "run_bad_shape", entry)

    data = build_report_data(
        tmp_path, "run_bad_shape", modules, "en",
        snapshot_before={"free_gb": 11.0}, snapshot_after={"free_gb": 12.0},
    )
    comparison = data["previous_comparison"]
    assert comparison["previous_run_id"] == "old_run3"
    assert comparison["free_gb_delta"] is None


def test_html_report_shows_since_last_visit_section_when_a_previous_report_exists(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    old_report = {
        "run_id": "old_run2",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "snapshot_after": {"free_gb": 10.0},
        "actions": [],
    }
    (reports_dir / f"{hostname}_old_run2.json").write_text(json.dumps(old_report), encoding="utf-8")

    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_compare2")
    append_entry(tmp_path, "run_compare2", entry)

    html_path, _ = generate_report(
        tmp_path, "run_compare2", modules, "en",
        snapshot_before={"free_gb": 11.0}, snapshot_after={"free_gb": 12.0},
    )
    content = html_path.read_text(encoding="utf-8")
    assert "Since last visit" in content
    assert "old_run2" in content
    # The top summary line must still show THIS run's own free-space delta
    # (11.0 -> 12.0 GB), not get clobbered by the comparison section's delta.
    assert "Free space: 11.0 GB &rarr; 12.0 GB (+1.0 GB)" in content


def test_build_report_data_skips_valid_json_line_missing_required_fields(tmp_path):
    modules = _fixture_modules()
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_missing")
    append_entry(tmp_path, "run_missing", entry)
    path = audit_log_path(tmp_path, "run_missing")
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"module_id": "m02_cleanup"}) + "\n")

    data = build_report_data(tmp_path, "run_missing", modules, "en", {}, {})

    assert len(data["actions"]) == 1
    assert data["actions"][0]["action_id"] == "user_temp"


def test_html_report_is_localized_to_slovak(tmp_path):
    modules = _fixture_modules()
    append_entry(tmp_path, "run_sk", make_entry("m02_cleanup", "user_temp", "cmd", 1, "boom", False, "run_sk"))
    html_path, _ = generate_report(
        tmp_path, "run_sk", modules, "sk",
        snapshot_before={"free_gb": 10.0}, snapshot_after={"free_gb": 11.0},
    )
    content = html_path.read_text(encoding="utf-8")
    assert '<html lang="sk">' in content
    assert "ZLYHALO" in content
    assert "Voľné miesto" in content
    assert "Výstup" in content
    assert "FAILED" not in content
    assert "Free space" not in content


def test_html_report_is_english_when_language_is_en(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_en", [], "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert '<html lang="en">' in content
    assert "Free space" in content


def test_previous_report_with_non_list_actions_does_not_crash(tmp_path):
    import socket

    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    hostname = socket.gethostname()
    old = {"run_id": "old", "generated_at": "x", "snapshot_after": {}, "actions": 7}
    (reports_dir / f"{hostname}_old.json").write_text(json.dumps(old), encoding="utf-8")
    data = build_report_data(tmp_path, "run_new", [], "en", {}, {})
    assert data["previous_comparison"]["previous_action_count"] == 0


def test_html_report_shows_readable_utc_timestamps(tmp_path):
    entry = make_entry("m02_cleanup", "user_temp", "cmd", 0, "", False, "run_ts")
    entry.timestamp = "2026-09-24T12:54:03.410593+00:00"
    append_entry(tmp_path, "run_ts", entry)
    html_path, json_path = generate_report(tmp_path, "run_ts", _fixture_modules(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "2026-09-24 12:54:03 UTC" in content
    assert "12:54:03.410593" not in content
    # The machine-readable JSON keeps the full ISO timestamp.
    assert json.loads(json_path.read_text(encoding="utf-8"))["actions"][0]["timestamp"] == entry.timestamp


def _two_module_fixture():
    from portablefix.models import ModuleCategory

    cleanup = ModuleDef(
        module_id="m02_cleanup",
        actions=[ActionDef(id="user_temp", label_sk="Dočasné súbory", label_en="Temp files",
                           risk=RiskLevel.SAFE, command="c")],
        category=ModuleCategory.CLEANUP,
    )
    repair = ModuleDef(
        module_id="m03_repair",
        actions=[ActionDef(id="sfc", label_sk="Kontrola systémových súborov", label_en="System file check",
                           risk=RiskLevel.MODERATE, command="sfc /scannow")],
        category=ModuleCategory.REPAIR,
    )
    return [cleanup, repair]


def _mixed_run(tmp_path, run_id):
    # 1: cleanup ok, 2: cleanup dry-run ok, 3: repair failed, 4: repair ok
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "c", 0, "done", False, run_id))
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "c", 0, "", True, run_id))
    append_entry(tmp_path, run_id, make_entry("m03_repair", "sfc", "sfc", 5, "boom", False, run_id))
    append_entry(tmp_path, run_id, make_entry("m03_repair", "sfc", "sfc", 0, "ok", False, run_id))


def test_build_report_data_adds_category_and_module_summary(tmp_path):
    _mixed_run(tmp_path, "run_sum")
    data = build_report_data(tmp_path, "run_sum", _two_module_fixture(), "en", {}, {})

    assert [a["category"] for a in data["actions"]] == ["CLEANUP", "CLEANUP", "REPAIR", "REPAIR"]
    assert data["module_summary"] == [
        {"module_id": "m02_cleanup", "category": "CLEANUP", "total": 2, "ok": 2, "failed": 0, "dry_run": 1},
        {"module_id": "m03_repair", "category": "REPAIR", "total": 2, "ok": 1, "failed": 1, "dry_run": 0},
    ]
    # Existing keys stay in place for JSON consumers.
    for key in ("actions", "requires_restart", "previous_comparison", "snapshot_before", "snapshot_after"):
        assert key in data


def test_module_summary_marks_unknown_modules(tmp_path):
    append_entry(tmp_path, "run_unk", make_entry("mXX", "a", "c", 0, "", False, "run_unk"))
    data = build_report_data(tmp_path, "run_unk", [], "en", {}, {})
    assert data["actions"][0]["category"] == "UNKNOWN"
    assert data["module_summary"][0]["category"] == "UNKNOWN"


def test_html_report_renders_module_summary_table(tmp_path):
    _mixed_run(tmp_path, "run_tbl")
    html_path, _ = generate_report(tmp_path, "run_tbl", _two_module_fixture(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "Summary by category" in content
    assert '<table class="summary">' in content
    assert "<td>m02_cleanup</td>" in content
    assert '<td class="cat">System repair</td>' in content
    assert '<td class="n fail-n">1</td>' in content


def test_html_report_lists_failed_actions_with_anchors(tmp_path):
    _mixed_run(tmp_path, "run_failed")
    html_path, _ = generate_report(tmp_path, "run_failed", _two_module_fixture(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "Failed actions (1)" in content
    assert '<a href="#action-3">System file check</a>' in content
    assert 'id="action-3"' in content
    # Cards keep chronological DOM order.
    assert content.index('id="action-1"') < content.index('id="action-2"') < content.index('id="action-3"')
    # The failed list sits above the action log.
    assert content.index('href="#action-3"') < content.index('id="action-1"')


def test_html_report_omits_failed_list_without_failures(tmp_path):
    append_entry(tmp_path, "run_allok", make_entry("m02_cleanup", "user_temp", "c", 0, "", False, "run_allok"))
    html_path, _ = generate_report(tmp_path, "run_allok", _fixture_modules(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "Failed actions" not in content
    assert 'href="#action-' not in content


def test_html_report_cards_carry_filter_data_attributes(tmp_path):
    _mixed_run(tmp_path, "run_attr")
    html_path, _ = generate_report(tmp_path, "run_attr", _two_module_fixture(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert '<div class="card ok" id="action-1" data-status="ok" data-dry="0"' in content
    assert '<div class="card ok" id="action-2" data-status="ok" data-dry="1"' in content
    assert '<div class="card fail" id="action-3" data-status="fail" data-dry="0"' in content
    assert 'data-search="system file check m03_repair sfc"' in content


def test_html_report_has_filter_bar_print_css_and_is_self_contained(tmp_path):
    _mixed_run(tmp_path, "run_bar")
    html_path, _ = generate_report(tmp_path, "run_bar", _two_module_fixture(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "<main" in content and "</main>" in content
    # Toolbar is hidden until the inline script reveals it (no-JS = full log).
    assert 'id="pf-toolbar" hidden' in content
    assert '<button type="button" data-filter="all" aria-pressed="true">All</button>' in content
    assert 'data-filter="fail" aria-pressed="false">Failed only</button>' in content
    assert 'data-filter="changes" aria-pressed="false">Changes only (no dry-run)</button>' in content
    assert 'type="search" id="pf-search"' in content
    assert "window.print()" in content
    assert "Print / save as PDF" in content
    assert "@media print" in content
    assert "break-inside: avoid" in content
    # Offline, single file: no external resources.
    assert "http://" not in content and "https://" not in content
    assert "<script src" not in content and "<link" not in content


def test_html_report_escapes_labels_in_new_sections(tmp_path):
    evil = '<img src=x onerror=alert(1)>"'
    append_entry(tmp_path, "run_xss2", make_entry(evil, evil, "c", 1, "", False, "run_xss2"))
    html_path, _ = generate_report(tmp_path, "run_xss2", [], "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "<img src=x" not in content
    assert "&lt;img src=x onerror=alert(1)&gt;" in content
    # Failed list link, summary table row and data-search attribute are all escaped.
    assert '<a href="#action-1">&lt;img src=x onerror=alert(1)&gt;&quot;</a>' in content
    assert "<td>&lt;img src=x onerror=alert(1)&gt;&quot;</td>" in content
    assert 'data-search="&lt;img src=x onerror=alert(1)&gt;&quot;' in content


def test_html_report_new_sections_are_localized_to_slovak(tmp_path):
    _mixed_run(tmp_path, "run_sk2")
    html_path, _ = generate_report(tmp_path, "run_sk2", _two_module_fixture(), "sk", {}, {})
    content = html_path.read_text(encoding="utf-8")
    for text in ("Prehľad podľa kategórií", "Zlyhané akcie (1)", "Všetky", "Len zlyhané",
                 "Tlačiť / uložiť ako PDF", "Kontrola systémových súborov", "Kategória", "Spolu"):
        assert text in content
    for english in ("Failed only", "Summary by category", "Print / save as PDF", "Failed actions"):
        assert english not in content


def test_html_report_without_actions_shows_empty_note_and_no_toolbar(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_empty", [], "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert "No actions were run during this session." in content
    assert 'id="pf-toolbar"' not in content
    assert "Summary by category" not in content


def test_module_summary_shows_localized_category_names(tmp_path):
    modules = _fixture_modules()
    append_entry(tmp_path, "run_cat", make_entry("m02_cleanup", "user_temp", "cmd", 0, "", False, "run_cat"))
    append_entry(tmp_path, "run_cat", make_entry("m99_gone", "ghost", "cmd", 0, "", False, "run_cat"))
    html_path, _ = generate_report(tmp_path, "run_cat", modules, "sk", {}, {})
    content = html_path.read_text(encoding="utf-8")
    category = modules[0].category.value
    from portablefix.i18n import translate

    assert f'<td class="cat">{translate("category_" + category.lower(), "sk")}</td>' in content
    assert '<td class="cat">UNKNOWN</td>' in content


def test_report_header_shows_job_details_escaped(tmp_path):
    job = {"technician": "Ján", "client": "<b>Firma</b>", "note": "riadok 1\nriadok 2", "extra": "x"}
    html_path, json_path = generate_report(tmp_path, "run_job", [], "sk", {}, {}, job=job)
    content = html_path.read_text(encoding="utf-8")
    assert "Technik: <strong>Ján</strong>" in content
    assert "&lt;b&gt;Firma&lt;/b&gt;" in content and "<b>Firma</b>" not in content
    assert "riadok 1\nriadok 2" in content
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["job"] == {"technician": "Ján", "client": "<b>Firma</b>", "note": "riadok 1\nriadok 2"}


def test_report_without_job_has_no_job_block(tmp_path):
    html_path, json_path = generate_report(tmp_path, "run_nojob", [], "en", {}, {}, job={"technician": "  "})
    assert 'class="job"' not in html_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["job"] == {}


def test_report_risk_is_frozen_from_audit_entry_not_current_catalog(tmp_path):
    # research-reporting.md F11: the catalog now says SAFE, but the action
    # ran (and was confirmed) as DESTRUCTIVE - the report must keep that.
    modules = _fixture_modules()
    append_entry(tmp_path, "run_frozen", make_entry(
        "m02_cleanup", "user_temp", "cmd", 0, "", False, "run_frozen", risk="DESTRUCTIVE", warned=True,
    ))
    data = build_report_data(tmp_path, "run_frozen", modules, "en", {}, {})
    assert data["actions"][0]["risk"] == "DESTRUCTIVE"


def test_report_old_entry_without_new_fields_still_renders(tmp_path):
    # Backward compat: a JSONL line written before risk/warned/elevated and
    # the safety fields existed must still render, risk from the catalog.
    modules = _fixture_modules()
    path = audit_log_path(tmp_path, "run_old")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "timestamp": "2026-09-01T14:27:15.925813+00:00", "module_id": "m02_cleanup", "action_id": "user_temp",
        "command": "cmd", "exit_code": 0, "output": "done", "output_hash": "abc", "dry_run": False,
    }) + "\n", encoding="utf-8")
    html_path, json_path = generate_report(tmp_path, "run_old", modules, "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["actions"][0]["risk"] == "SAFE"
    assert data["actions"][0]["warned"] is None
    assert data["elevated"] is None
    assert data["restore_points"] == [] and data["events"] == []
    content = html_path.read_text(encoding="utf-8")
    assert "Temp files" in content
    assert "Run as administrator" not in content
    assert "Confirmed after warning" not in content


def test_report_shows_warning_confirmation_with_exact_text(tmp_path):
    # research-reporting.md F2: proof the technician was warned, in the
    # document the client actually receives.
    modules = _fixture_modules()
    append_entry(tmp_path, "run_warn", make_entry(
        "m02_cleanup", "user_temp", "cmd", 0, "", False, "run_warn",
        risk="MODERATE", warned=True, warning_text="[MODERATE] Temp files\n\nAre you <sure>?",
    ))
    content = generate_report(tmp_path, "run_warn", modules, "en", {}, {})[0].read_text(encoding="utf-8")
    assert "Confirmed after warning" in content
    assert "Are you &lt;sure&gt;?" in content


def test_report_lists_declined_confirmation_in_safety_log_not_as_action(tmp_path):
    modules = _fixture_modules()
    append_entry(tmp_path, "run_decl", make_entry(
        "_system", "risk_declined", "", None, "Technician declined", False, "run_decl",
        risk="DESTRUCTIVE", warned=True, warning_text="WARNING: irreversible",
        subject="m02_cleanup/user_temp", decision="declined",
    ))
    html_path, json_path = generate_report(tmp_path, "run_decl", modules, "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # Not counted as a (failed) action.
    assert data["actions"] == []
    assert data["events"][0]["kind"] == "risk_declined"
    content = html_path.read_text(encoding="utf-8")
    assert "Safety log" in content
    assert "Declined after risk warning (not run): <strong>Temp files</strong>" in content
    assert "WARNING: irreversible" in content


@pytest.mark.parametrize(
    ("language", "decision", "expected"),
    [
        ("en", "override", "Pre-run batch review: <span class=\"rp-fail\">technician confirmed the batch despite blocking findings</span>"),
        ("sk", "override", "Kontrola pred spustením dávky: <span class=\"rp-fail\">technik dávku potvrdil napriek blokujúcim zisteniam</span>"),
        ("en", "confirmed", "Pre-run batch review: <span>technician confirmed the batch</span>"),
        ("sk", "cancelled", "Kontrola pred spustením dávky: <span>technik dávku zrušil - nič sa nespustilo</span>"),
    ],
)
def test_report_renders_batch_review_decision_translated_with_the_quoted_findings(tmp_path, language, decision, expected):
    # G12: an override "is recorded in the report" - in the report's own
    # language and with the findings the technician overrode, not the raw
    # English audit line.
    append_entry(tmp_path, "run_review", make_entry(
        "_system", "batch_review", "", 0, "Pre-flight: blockers: low_disk; warnings: none. Technician confirmed.",
        False, "run_review", warned=True, warning_text="Only 2.0 GB <free>", decision=decision,
    ))
    content = generate_report(tmp_path, "run_review", [], language, {}, {})[0].read_text(encoding="utf-8")
    assert expected in content
    assert "Only 2.0 GB &lt;free&gt;" in content
    assert "batch_review:" not in content


def test_report_shows_restore_point_created(tmp_path):
    # research-reporting.md F1: restore-point outcome visible in the report.
    append_entry(tmp_path, "run_rp", make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 0, "System Restore Point created.", False, "run_rp",
        elevated=True,
    ))
    html_path, json_path = generate_report(tmp_path, "run_rp", [], "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["restore_points"][0]["created"] is True
    assert data["actions"] == []
    content = html_path.read_text(encoding="utf-8")
    assert "Restore point: created" in content
    assert "Run as administrator: yes" in content


def test_report_shows_restore_point_sequence_number(tmp_path):
    # research-reporting.md F1: name *which* restore point was created, in
    # both the header line and the safety log.
    append_entry(tmp_path, "run_rpseq", make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 0, "System Restore Point created (#123).", False,
        "run_rpseq", restore_point_sequence=123, restore_point_created="20260924101530.123456-000",
    ))
    html_path, json_path = generate_report(tmp_path, "run_rpseq", [], "sk", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["restore_points"][0]["sequence"] == 123
    content = html_path.read_text(encoding="utf-8")
    assert content.count("Bod obnovenia: vytvorený (#123) (") == 2


def test_report_restore_point_without_sequence_renders_as_before(tmp_path):
    # Logs written before the field existed have no restore_point_sequence key.
    log = tmp_path / "Logs" / "run_rpold.jsonl"
    log.parent.mkdir(parents=True)
    old_entry = {
        "timestamp": "2026-01-01T10:00:00+00:00", "module_id": "_system", "action_id": "restore_point",
        "command": "Checkpoint-Computer", "exit_code": 0, "output": "System Restore Point created.",
        "dry_run": False, "hostname": "pc", "run_id": "run_rpold",
    }
    log.write_text(json.dumps(old_entry) + "\n", encoding="utf-8")
    html_path, json_path = generate_report(tmp_path, "run_rpold", [], "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["restore_points"][0]["created"] is True
    assert data["restore_points"][0]["sequence"] is None
    content = html_path.read_text(encoding="utf-8")
    assert "Restore point: created (" in content
    assert "(#" not in content


def test_report_shows_failed_restore_point_and_proceed_decision(tmp_path):
    # research-reporting.md F3: "continue without a restore point" is an
    # explicit, visible decision - not inferred from timestamps.
    append_entry(tmp_path, "run_rpf", make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 1, "System Restore Point creation failed: disabled",
        False, "run_rpf",
    ))
    append_entry(tmp_path, "run_rpf", make_entry(
        "_system", "restore_point_decision", "", 0, "proceed", False, "run_rpf", decision="proceed",
    ))
    html_path, json_path = generate_report(tmp_path, "run_rpf", [], "sk", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["restore_points"] == [{
        "timestamp": data["restore_points"][0]["timestamp"], "created": False,
        "detail": "System Restore Point creation failed: disabled", "decision": "proceed",
        "sequence": None,
    }]
    content = html_path.read_text(encoding="utf-8")
    assert "NEPODARIL SA" in content
    assert "technik potvrdil pokračovanie bez bodu obnovenia" in content


def test_report_shows_storage_fallback_banner(tmp_path):
    # research-reporting.md F4.
    html_path, json_path = generate_report(tmp_path, "run_fb", [], "en", {}, {}, storage_fallback=True)
    assert json.loads(json_path.read_text(encoding="utf-8"))["storage_fallback"] is True
    assert 'class="banner"' in html_path.read_text(encoding="utf-8")
    html_path, _ = generate_report(tmp_path, "run_nofb", [], "en", {}, {})
    assert 'class="banner"' not in html_path.read_text(encoding="utf-8")


def test_report_elevation_not_admin(tmp_path):
    # research-reporting.md F8.
    modules = _fixture_modules()
    append_entry(tmp_path, "run_el", make_entry(
        "m02_cleanup", "user_temp", "cmd", 0, "", False, "run_el", elevated=False,
    ))
    content = generate_report(tmp_path, "run_el", modules, "en", {}, {})[0].read_text(encoding="utf-8")
    assert "Run as administrator: no" in content


def test_second_batch_of_a_session_does_not_compare_the_run_with_itself(tmp_path):
    # Report files are per run_id and rewritten after each batch; the
    # comparison must skip the current run's own earlier report.
    import socket

    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    hostname = socket.gethostname()
    older = {"run_id": "20260901T000000-old", "generated_at": "2026-09-01T00:00:00+00:00",
             "snapshot_after": {"free_gb": 5.0}, "actions": []}
    (reports_dir / f"{hostname}_20260901T000000-old.json").write_text(json.dumps(older), encoding="utf-8")
    append_entry(tmp_path, "20260924T000000-now", make_entry("m02_cleanup", "user_temp", "cmd", 0, "", False, "20260924T000000-now"))
    generate_report(tmp_path, "20260924T000000-now", _fixture_modules(), "en", {}, {"free_gb": 8.0})
    data = build_report_data(tmp_path, "20260924T000000-now", _fixture_modules(), "en", {}, {"free_gb": 9.0})
    assert data["previous_comparison"]["previous_run_id"] == "20260901T000000-old"
    assert data["previous_comparison"]["free_gb_delta"] == 4.0


def test_report_runner_emits_the_html_path_from_its_thread(qtbot, tmp_path):
    # research-app-performance.md 4.3: batch-end report runs off the GUI thread.
    from portablefix.report import ReportRunner

    runner = ReportRunner(tmp_path, "run_thread", [], "en", {}, {}, job={"technician": "T"})
    with qtbot.waitSignal(runner.result_ready, timeout=10000) as blocker:
        runner.start()
    html_path, write_failed = blocker.args
    assert write_failed is False
    assert html_path.exists() and html_path.name.endswith("_run_thread.html")
    runner.wait(5000)


def test_report_runner_reports_an_oserror_as_write_failed(qtbot, tmp_path, monkeypatch):
    from portablefix import report

    def failing(*args, **kwargs):
        raise OSError("USB unplugged")

    monkeypatch.setattr(report, "generate_report", failing)
    runner = report.ReportRunner(tmp_path, "run_fail", [], "en", {}, {})
    with qtbot.waitSignal(runner.result_ready, timeout=10000) as blocker:
        runner.start()
    assert blocker.args == [None, True]
    runner.wait(5000)


def test_report_runner_still_answers_when_generation_hits_a_bug(qtbot, tmp_path, monkeypatch):
    # Without an answer the GUI would keep Run disabled forever.
    import sys

    from portablefix import report

    def buggy(*args, **kwargs):
        raise KeyError("bug")

    surfaced = []
    monkeypatch.setattr(report, "generate_report", buggy)
    monkeypatch.setattr(sys, "excepthook", lambda *exc: surfaced.append(exc[0]))
    runner = report.ReportRunner(tmp_path, "run_bug", [], "en", {}, {})
    with qtbot.waitSignal(runner.result_ready, timeout=10000) as blocker:
        runner.start()
    assert blocker.args == [None, False]
    assert surfaced == [KeyError]
    runner.wait(5000)


# --- "Before / after" snapshot table ------------------------------------------

_SNAP_BEFORE = {
    "free_gb": 40.0, "total_gb": 237.0,
    "temp_user_mb": 2600.0, "temp_user_files": 41000, "temp_user_complete": False,
    "temp_windows_mb": 310.0, "temp_windows_complete": True,
    "recycle_bin_mb": 850.0, "startup_entries": 11, "mem_available_mb": 5100,
}
_SNAP_AFTER = {
    "free_gb": 43.8, "total_gb": 237.0,
    "temp_user_mb": 12.5, "temp_user_files": 30, "temp_user_complete": True,
    "temp_windows_mb": 320.0, "temp_windows_complete": True,
    "recycle_bin_mb": 0.0, "startup_entries": 11, "mem_available_mb": None,
}


def _snapshot_section(content: str) -> str:
    start = content.index('<section aria-labelledby="pf-h-snapshot">')
    return content[start:content.index("</section>", start)]


def test_html_report_renders_before_after_table_with_deltas(tmp_path):
    html_path, json_path = generate_report(tmp_path, "run_snap", [], "en", _SNAP_BEFORE, _SNAP_AFTER)
    section = _snapshot_section(html_path.read_text(encoding="utf-8"))

    assert "Before / after" in section
    assert "Free space on the system drive" in section
    assert "+3.8 GB" in section
    # lower-bound "before", exact "after" -> "at most" delta, good direction
    assert "≥ 2.54 GB" in section
    assert 'class="n delta good"' in section
    assert "≤ −" in section
    assert "the real value is higher" in section
    # windows temp grew -> bad; startup unchanged -> same
    assert 'class="n delta bad"' in section
    assert 'class="n delta same"' in section
    # memory unknown after -> row omitted rather than shown as None
    assert "Available memory" not in section
    assert "None" not in section

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["snapshot_before"] == _SNAP_BEFORE
    assert data["snapshot_after"]["temp_user_mb"] == 12.5


def test_html_report_before_after_table_is_slovak(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_snap_sk", [], "sk", _SNAP_BEFORE, _SNAP_AFTER)
    section = _snapshot_section(html_path.read_text(encoding="utf-8"))
    assert "Pred / po" in section
    assert "Kôš" in section
    assert "zlepšenie" in section
    assert "Before" not in section


def test_html_report_old_snapshot_without_new_keys_renders_as_before(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_old", [], "en", {"free_gb": 11.0}, {"free_gb": 12.0})
    content = html_path.read_text(encoding="utf-8")
    assert "Free space: 11.0 GB &rarr; 12.0 GB (+1.0 GB)" in content
    assert "pf-h-snapshot" not in content


def test_html_report_handles_unknown_free_space(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_none", [], "en", {"free_gb": None}, {"free_gb": None})
    content = html_path.read_text(encoding="utf-8")
    assert "Free space: ? GB &rarr; ? GB" in content


def test_render_html_old_report_json_without_new_keys(tmp_path):
    # Report data shaped like a JSON written by an older version, re-rendered.
    from portablefix.report import _render_html

    data = build_report_data(tmp_path, "run_legacy", [], "en", {"free_gb": 5.0}, {"free_gb": 6.0})
    data["snapshot_before"] = {"free_gb": 5.0, "total_gb": 50.0}
    data["snapshot_after"] = {"free_gb": 6.0, "total_gb": 50.0}
    content = _render_html(data)
    assert "+1.0 GB" in content
    assert "pf-h-snapshot" not in content


def test_before_after_table_escapes_values(tmp_path, monkeypatch):
    from portablefix import report

    evil = "<script>alert(1)</script>"
    monkeypatch.setattr(report, "compare_snapshots", lambda b, a: [{
        "key": "temp_user_mb", "label_key": "snapshot_temp_user", "before": evil, "after": evil,
        "delta": evil, "delta_value": -1.0, "trend": "good", "lower_bound": False,
    }])
    html_path, _ = generate_report(tmp_path, "run_esc", [], "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert evil not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in _snapshot_section(content)


def test_before_after_table_has_print_styles(tmp_path):
    html_path, _ = generate_report(tmp_path, "run_print", [], "en", _SNAP_BEFORE, _SNAP_AFTER)
    content = html_path.read_text(encoding="utf-8")
    print_css = content[content.index("@media print"):]
    assert "table.snapshot td.delta.good" in print_css
    assert "table.snapshot td.delta.bad" in print_css


# --- Robustness: legacy-encoded files, real reboots, sentinel exit codes -----


def _write_cp1250_report(reports_dir, name):
    # An older build / another tool saved the report in the Windows ANSI code
    # page: "Ján" in cp1250 is b"J\xe1n", which is not valid UTF-8.
    body = json.dumps({
        "run_id": name, "generated_at": "2026-09-01T10:00:00+00:00",
        "snapshot_after": {"free_gb": 1.0}, "actions": [], "job": {"technician": "@@"},
    }).encode("ascii").replace(b"@@", "Ján".encode("cp1250"))
    (reports_dir / f"{name}.json").write_bytes(body)


def test_non_utf8_previous_report_does_not_stop_the_report(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    _write_cp1250_report(reports_dir, f"{hostname}_20260901T100000-aaaaaaaa")
    run_id = "20260924T090000-bbbbbbbb"
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "", False, run_id))

    html_path, json_path = generate_report(tmp_path, run_id, _fixture_modules(), "en", {}, {"free_gb": 9.0})

    assert html_path.exists() and json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["run_id"] == run_id
    assert data["previous_comparison"] is None


def test_non_utf8_previous_report_falls_back_to_an_older_valid_one(tmp_path):
    import socket

    hostname = socket.gethostname()
    reports_dir = tmp_path / "Reports"
    reports_dir.mkdir()
    older = {"run_id": "20260815T100000-cccccccc", "generated_at": "2026-08-15T10:00:00+00:00",
             "snapshot_after": {"free_gb": 5.0}, "actions": [{"action_id": "a"}]}
    (reports_dir / f"{hostname}_20260815T100000-cccccccc.json").write_text(json.dumps(older), encoding="utf-8")
    _write_cp1250_report(reports_dir, f"{hostname}_20260901T100000-aaaaaaaa")
    run_id = "20260924T090000-bbbbbbbb"
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "", False, run_id))

    html_path, json_path = generate_report(tmp_path, run_id, _fixture_modules(), "en", {}, {"free_gb": 9.0})

    comparison = json.loads(json_path.read_text(encoding="utf-8"))["previous_comparison"]
    assert comparison["previous_run_id"] == "20260815T100000-cccccccc"
    assert comparison["free_gb_delta"] == 4.0
    assert comparison["previous_action_count"] == 1
    assert "20260815T100000-cccccccc" in html_path.read_text(encoding="utf-8")


def test_audit_line_with_invalid_utf8_does_not_drop_the_other_entries(tmp_path):
    run_id = "run_enc"
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "first", False, run_id))
    path = audit_log_path(tmp_path, run_id)
    # A complete entry except for its encoding - only the decode can reject it.
    bad = json.dumps({
        "timestamp": "2026-09-24T10:00:00+00:00", "module_id": "m02_cleanup", "action_id": "user_temp",
        "command": "cmd", "exit_code": 0, "output": "@@", "dry_run": False,
    }).encode("ascii").replace(b"@@", "Ján".encode("cp1250"))
    with path.open("ab") as f:
        f.write(bad + b"\n")
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "second", False, run_id))

    html_path, json_path = generate_report(tmp_path, run_id, _fixture_modules(), "en", {}, {})

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert [a["output"] for a in data["actions"]] == ["first", "second"]
    content = html_path.read_text(encoding="utf-8")
    assert "first" in content and "second" in content


def test_requires_restart_lists_only_real_successful_reboot_actions(tmp_path):
    run_id = "run_reboot"
    # Dry-run and failed runs changed nothing that a restart would apply.
    append_entry(tmp_path, run_id, make_entry(
        "m02_cleanup", "user_temp", "cmd", 0, "", True, run_id, risk="REQUIRES_REBOOT"))
    append_entry(tmp_path, run_id, make_entry(
        "m02_cleanup", "user_temp", "cmd", 5, "boom", False, run_id, risk="REQUIRES_REBOOT"))
    data = build_report_data(tmp_path, run_id, _fixture_modules(), "en", {}, {})
    assert data["requires_restart"] == []
    content = generate_report(tmp_path, run_id, _fixture_modules(), "en", {}, {})[0].read_text(encoding="utf-8")
    assert "Requires restart" not in content

    append_entry(tmp_path, run_id, make_entry(
        "m02_cleanup", "user_temp", "cmd", 0, "applied", False, run_id, risk="REQUIRES_REBOOT"))
    data = build_report_data(tmp_path, run_id, _fixture_modules(), "en", {}, {})
    assert [(a["exit_code"], a["dry_run"], a["output"]) for a in data["requires_restart"]] == [(0, False, "applied")]


def test_report_sentinel_codes_match_the_executor():
    # report.py mirrors these instead of importing executor.py (see the
    # comment there) - this keeps the two from drifting apart.
    from portablefix.executor import ActionRunner
    from portablefix.report import _SENTINEL_EXIT_KEYS

    assert _SENTINEL_EXIT_KEYS == {
        ActionRunner.TIMEOUT_EXIT_CODE: "report_exit_timeout",
        ActionRunner.CANCELLED_EXIT_CODE: "report_exit_cancelled",
        ActionRunner.POWERSHELL_NOT_FOUND_EXIT_CODE: "report_exit_no_powershell",
    }


def _run_with_exit_codes(tmp_path, run_id, codes):
    for code in codes:
        append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", code, "", False, run_id))


def test_report_explains_timeout_exit_code_in_slovak(tmp_path):
    import html as html_mod

    from portablefix.i18n import translate

    _run_with_exit_codes(tmp_path, "run_sent_sk", [-2, 5])
    html_path, json_path = generate_report(tmp_path, "run_sent_sk", _fixture_modules(), "sk", {}, {})
    content = html_path.read_text(encoding="utf-8")
    timeout_text = html_mod.escape(translate("report_exit_timeout", "sk"))
    # Both the action card and the failed-actions list explain it.
    assert content.count(timeout_text) == 2
    assert "kód -2" not in content
    # A real exit code is still shown as a code.
    assert content.count("kód 5") == 2
    # The JSON keeps the raw code for machines.
    assert [a["exit_code"] for a in json.loads(json_path.read_text(encoding="utf-8"))["actions"]] == [-2, 5]


def test_report_explains_cancelled_and_missing_powershell_in_english(tmp_path):
    import html as html_mod

    from portablefix.i18n import translate

    _run_with_exit_codes(tmp_path, "run_sent_en", [-3, -4, 5])
    content = generate_report(tmp_path, "run_sent_en", _fixture_modules(), "en", {}, {})[0].read_text(encoding="utf-8")
    for key in ("report_exit_cancelled", "report_exit_no_powershell"):
        assert content.count(html_mod.escape(translate(key, "en"))) == 2
    assert "exit -3" not in content and "exit -4" not in content
    assert content.count("exit 5") == 2


def test_failed_restore_point_shows_only_the_reason_not_the_english_prefix(tmp_path):
    output = "System Restore Point creation failed: disabled"
    append_entry(tmp_path, "run_rpx", make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 1, output, False, "run_rpx",
    ))
    html_path, json_path = generate_report(tmp_path, "run_rpx", [], "sk", {}, {})
    content = html_path.read_text(encoding="utf-8")
    assert '<div class="warn-text">disabled</div>' in content
    assert "System Restore Point creation failed" not in content
    # Only the rendering changes - the JSON keeps the logged text verbatim.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["events"][0]["output"] == output
    assert data["restore_points"][0]["detail"] == output


def test_failed_restore_point_without_a_reason_adds_no_detail_line(tmp_path):
    append_entry(tmp_path, "run_rpy", make_entry(
        "_system", "restore_point", "Checkpoint-Computer", 1, "System Restore Point creation failed.", False, "run_rpy",
    ))
    content = generate_report(tmp_path, "run_rpy", [], "en", {}, {})[0].read_text(encoding="utf-8")
    assert 'Restore point: <span class="rp-fail">FAILED</span>' in content
    assert "System Restore Point creation failed" not in content
    assert '<div class="warn-text">' not in content


@pytest.mark.parametrize("output, reason", [
    ("System Restore Point creation failed: disabled", "disabled"),
    ("System Restore Point creation failed.", ""),
    ("System Restore Point creation failed: .NET error", ".NET error"),
    ("Something else entirely", "Something else entirely"),
])
def test_restore_point_failure_reason_strips_only_the_english_prefix(output, reason):
    from portablefix.report import _restore_point_failure_reason

    assert _restore_point_failure_reason(output) == reason
