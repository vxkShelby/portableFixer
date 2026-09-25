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
        "sequence": None, "subject": "", "subject_label": "", "subjects": [], "panel": False,
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


def _system_event(tmp_path, run_id, action_id, exit_code, output, **fields):
    append_entry(tmp_path, run_id, make_entry("_system", action_id, "", exit_code, output, False, run_id, **fields))


def _safety_items(content: str) -> str:
    start = content.index('<ul class="events">')
    return content[start:content.index("</ul>", start)]


def test_hive_backup_and_panel_safety_events_reach_the_reports_safety_section(tmp_path):
    # G24/G01: the full registry backup, a refused protected program and an
    # "uninstall anyway while it runs" answer are safety facts on record -
    # in the report's language, not as the raw event kind.
    folder = r"D:\PortableFix\Backups\run_hive\hives-20260924-100000"
    _system_event(tmp_path, "run_hive", "hive_backup", 0,
                  f"Registry hive backup saved: {folder} (SOFTWARE.hiv, SYSTEM.hiv).", subject="m02_cleanup/user_temp")
    _system_event(tmp_path, "run_hive", "protected_program", None,
                  "Uninstall refused - protected program (gpu_driver).", risk="DESTRUCTIVE",
                  subject="_uninstaller/NVIDIA Graphics Driver 555.85")
    _system_event(tmp_path, "run_hive", "running_programs_decision", 0,
                  "Technician chose to continue while the program was still running.", risk="DESTRUCTIVE",
                  warned=True, warning_text="7-Zip: 7zFM.exe (PID 42)", subject="_uninstaller/7-Zip 24.08",
                  decision="proceed")
    content = generate_report(tmp_path, "run_hive", _fixture_modules(), "en", {}, {})[0].read_text(encoding="utf-8")
    items = _safety_items(content)
    assert "Registry backup (SOFTWARE and SYSTEM hives): saved" in items
    assert "before Temp files" in items
    assert f'<div class="warn-text">{folder} (SOFTWARE.hiv, SYSTEM.hiv).</div>' in items
    assert ("Uninstall refused - protected program: <strong>NVIDIA Graphics Driver 555.85</strong> "
            '<span class="mod">(graphics driver – removing it can leave a black screen)</span>') in items
    assert ("Continued while the program was still running: <strong>Uninstall: 7-Zip 24.08</strong> "
            '<span class="mod">[DESTRUCTIVE]</span>') in items
    assert "7-Zip: 7zFM.exe (PID 42)" in items
    for raw in ("hive_backup", "protected_program", "running_programs_decision", "Registry hive backup saved"):
        assert raw not in items


def test_new_safety_events_are_slovak(tmp_path):
    _system_event(tmp_path, "run_hsk", "hive_backup", 1, "Registry hive backup failed: reg.exe exit 1",
                  subject="m02_cleanup/user_temp")
    _system_event(tmp_path, "run_hsk", "hive_backup_decision", 0, "Technician declined ...", decision="skip",
                  subject="m02_cleanup/user_temp")
    _system_event(tmp_path, "run_hsk", "protected_program", None, "Uninstall refused - protected program (self).",
                  subject="_uninstaller/PortableFix")
    _system_event(tmp_path, "run_hsk", "running_programs_decision", 0, "continue", decision="proceed",
                  subject="_winget/7zip.7zip")
    items = _safety_items(generate_report(tmp_path, "run_hsk", _fixture_modules(), "sk", {}, {})[0]
                          .read_text(encoding="utf-8"))
    assert 'Záloha registra (úly SOFTWARE a SYSTEM): <span class="rp-fail">NEPODARILA SA</span>' in items
    assert "pred akciou Docasne subory" in items
    assert '<div class="warn-text">reg.exe exit 1</div>' in items
    assert "technik odmietol pokračovať - akcie DESTRUCTIVE boli vynechané" in items
    assert "Odinštalovanie odmietnuté - chránený program: <strong>PortableFix</strong>" in items
    assert "samotný PortableFix" in items
    assert "Pokračovanie napriek bežiacemu programu: <strong>Aktualizácia cez winget: 7zip.7zip</strong>" in items


def test_hive_backup_decision_proceed_and_unknown_protected_reason(tmp_path):
    _system_event(tmp_path, "run_hp", "hive_backup_decision", 0, "go", decision="proceed")
    # A reason code this version has no text for is shown bare, not dropped.
    _system_event(tmp_path, "run_hp", "protected_program", None, "Uninstall refused - protected program (future_kind).",
                  subject="_uninstaller/X")
    # An output without a code adds no empty "()".
    _system_event(tmp_path, "run_hp", "protected_program", None, "Uninstall refused.", subject="_uninstaller/Y")
    items = _safety_items(generate_report(tmp_path, "run_hp", [], "en", {}, {})[0].read_text(encoding="utf-8"))
    assert "Registry backup (SOFTWARE and SYSTEM hives): technician confirmed continuing without the registry backup" in items
    assert '<strong>X</strong> <span class="mod">(future_kind)</span>' in items
    assert "<strong>Y</strong></li>" in items


@pytest.mark.parametrize(("output", "shown"), [
    ("Pre-flight: ok. Technician confirmed the batch on the review screen. "
     "Full registry hive backup requested before the first DESTRUCTIVE action.", True),
    ("Pre-flight: ok. Technician confirmed the batch on the review screen.", False),
])
def test_batch_review_says_when_a_registry_backup_was_requested(tmp_path, output, shown):
    _system_event(tmp_path, "run_brh", "batch_review", 0, output, decision="confirmed")
    items = _safety_items(generate_report(tmp_path, "run_brh", [], "en", {}, {})[0].read_text(encoding="utf-8"))
    assert ("full registry backup requested before the first DESTRUCTIVE action" in items) is shown


def test_report_sentences_match_what_main_window_logs():
    # report.py recognises main_window's own fixed English audit sentences
    # (display only) - pin them so a rewording there can't silently turn
    # the translated lines back into raw English.
    from pathlib import Path

    from portablefix import report

    source = (Path(report.__file__).parent / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert '"Registry hive backup saved: ' in source
    assert '"Registry hive backup failed: ' in source and '"Registry hive backup failed."' in source
    assert f'" {report._HIVE_REQUESTED} before the first DESTRUCTIVE action."' in source
    assert '"Uninstall refused - protected program ({protected[program.name]})."' in source
    for prefix in (report._UNINSTALL_SUBJECT, report._LEFTOVER_SUBJECT, report._WINGET_SUBJECT):
        assert f'f"{prefix}' in source


@pytest.mark.parametrize(("subject", "language", "label"), [
    ("_uninstaller/7-Zip 24.08", "en", "Uninstall: 7-Zip 24.08"),
    ("_uninstaller/orphan_cleanup:Old App", "sk", "Vyčistenie zvyškov v registri: Old App"),
    ("_winget/Mozilla.Firefox", "en", "winget update: Mozilla.Firefox"),
    ("_winget/Mozilla.Firefox", "sk", "Aktualizácia cez winget: Mozilla.Firefox"),
    ("m02_cleanup/user_temp", "en", "Temp files"),
    ("m99/unknown", "en", "unknown"),
    ("", "en", ""),
])
def test_event_subject_labels_name_panel_targets(tmp_path, subject, language, label):
    _system_event(tmp_path, "run_lbl", "risk_declined", None, "declined", subject=subject, decision="declined")
    data = build_report_data(tmp_path, "run_lbl", _fixture_modules(), language, {}, {})
    assert data["events"][0]["subject_label"] == label


def test_panel_declined_confirmation_names_the_program_not_the_raw_subject(tmp_path):
    _system_event(tmp_path, "run_pdecl", "risk_declined", None, "declined", risk="DESTRUCTIVE",
                  subject="_uninstaller/orphan_cleanup:Old <App>", decision="declined")
    items = _safety_items(generate_report(tmp_path, "run_pdecl", [], "en", {}, {})[0].read_text(encoding="utf-8"))
    assert "<strong>Registry leftover cleanup: Old &lt;App&gt;</strong>" in items
    assert "orphan_cleanup" not in items


def _rp(tmp_path, run_id, exit_code, subject, sequence=None):
    output = "System Restore Point created." if exit_code == 0 else "System Restore Point creation failed: disabled"
    _system_event(tmp_path, run_id, "restore_point", exit_code, output, subject=subject,
                  restore_point_sequence=sequence)


def test_panel_restore_point_is_not_presented_as_the_batchs(tmp_path):
    # G01: the uninstaller made a restore point, the batch did not - the
    # header must not read "Restore point: created" as if the batch had one.
    _rp(tmp_path, "run_prp", 0, "_uninstaller/7-Zip 24.08", sequence=77)
    html_path, json_path = generate_report(tmp_path, "run_prp", [], "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    [point] = data["restore_points"]
    assert point["panel"] is True
    assert point["subject"] == "_uninstaller/7-Zip 24.08"
    assert point["subject_label"] == "Uninstall: 7-Zip 24.08"
    content = html_path.read_text(encoding="utf-8")
    assert "Restore point: created" not in content
    # Header and safety log both name what it guarded.
    assert content.count("Restore point (Uninstall: 7-Zip 24.08): created (#77) (") == 2


def test_batch_restore_point_keeps_its_plain_header_line(tmp_path):
    _rp(tmp_path, "run_brp", 0, "m02_cleanup/user_temp", sequence=5)
    html_path, json_path = generate_report(tmp_path, "run_brp", _fixture_modules(), "sk", {}, {})
    [point] = json.loads(json_path.read_text(encoding="utf-8"))["restore_points"]
    assert point["panel"] is False
    assert html_path.read_text(encoding="utf-8").count("Bod obnovenia: vytvorený (#5) (") == 2


def test_panel_restore_point_decision_pairs_with_its_own_point(tmp_path):
    # The batch's point failed and was answered; later a winget update's
    # point failed and was declined - each answer stays with its own point,
    # and the panel's "No" reads "nothing was changed", not "high-risk
    # actions were skipped".
    _rp(tmp_path, "run_pair", 1, "m02_cleanup/user_temp")
    _system_event(tmp_path, "run_pair", "restore_point_decision", 0, "go", decision="proceed",
                  subject="m02_cleanup/user_temp")
    _rp(tmp_path, "run_pair", 1, "_winget/Mozilla.Firefox")
    _system_event(tmp_path, "run_pair", "restore_point_decision", 0, "stop", decision="skip",
                  subject="_winget/Mozilla.Firefox")
    html_path, json_path = generate_report(tmp_path, "run_pair", _fixture_modules(), "en", {}, {})
    batch, panel = json.loads(json_path.read_text(encoding="utf-8"))["restore_points"]
    assert (batch["panel"], batch["decision"]) == (False, "proceed")
    assert (panel["panel"], panel["decision"]) == (True, "skip")
    content = html_path.read_text(encoding="utf-8")
    assert "technician confirmed continuing without a restore point" in content
    assert ("Restore point (winget update: Mozilla.Firefox): <span class=\"rp-fail\">FAILED</span>"
            in content)
    assert "technician declined to continue - nothing was changed" in content
    assert "high-risk actions were skipped" not in content


def test_decision_for_another_subject_does_not_attach_to_the_wrong_point(tmp_path):
    _rp(tmp_path, "run_other", 1, "_uninstaller/A")
    _system_event(tmp_path, "run_other", "restore_point_decision", 0, "stop", decision="skip",
                  subject="_uninstaller/B")
    [point] = build_report_data(tmp_path, "run_other", [], "en", {}, {})["restore_points"]
    assert point["decision"] is None


def test_render_html_old_report_restore_point_without_panel_keys(tmp_path):
    # Report JSON written before panel restore points existed.
    from portablefix.report import _render_html

    data = build_report_data(tmp_path, "run_oldrp", [], "en", {}, {})
    data["restore_points"] = [{"timestamp": "2026-01-01T10:00:00+00:00", "created": False,
                               "detail": "x", "decision": "skip", "sequence": None}]
    content = _render_html(data)
    assert "Restore point: <span class=\"rp-fail\">FAILED</span>" in content
    assert "high-risk actions were skipped" in content


def test_panel_restore_point_names_every_program_it_guarded(tmp_path):
    # One restore point before uninstalling three programs at once - the
    # report must not read as if only the first one had a way back.
    _system_event(tmp_path, "run_multi", "restore_point", 0, "System Restore Point created.",
                  subject="_uninstaller/A", subjects=["_uninstaller/A", "_uninstaller/B <x>", "_uninstaller/C"],
                  restore_point_sequence=9)
    html_path, json_path = generate_report(tmp_path, "run_multi", [], "en", {}, {})
    [point] = json.loads(json_path.read_text(encoding="utf-8"))["restore_points"]
    assert point["subject"] == "_uninstaller/A"
    assert point["subjects"] == ["_uninstaller/A", "_uninstaller/B <x>", "_uninstaller/C"]
    assert point["subject_label"] == "Uninstall: A, B <x>, C"
    content = html_path.read_text(encoding="utf-8")
    assert content.count("Restore point (Uninstall: A, B &lt;x&gt;, C): created (#9) (") == 2


@pytest.mark.parametrize("subject, subjects, language, label", [
    ("_winget/Mozilla.Firefox", ["_winget/Mozilla.Firefox", "_winget/7zip.7zip"], "sk",
     "Aktualizácia cez winget: Mozilla.Firefox, 7zip.7zip"),
    ("_uninstaller/orphan_cleanup:Old", ["_uninstaller/orphan_cleanup:Old", "_uninstaller/orphan_cleanup:Gone"], "en",
     "Registry leftover cleanup: Old, Gone"),
    # An uninstall list never swallows a leftover-cleanup subject.
    ("_uninstaller/A", ["_uninstaller/A", "_uninstaller/orphan_cleanup:X"], "en", "Uninstall: A"),
    # One subject, a catalog subject, or no list at all.
    ("_uninstaller/A", ["_uninstaller/A"], "en", "Uninstall: A"),
    ("m02_cleanup/user_temp", ["m02_cleanup/user_temp", "m02_cleanup/other"], "en", "Temp files"),
    ("_uninstaller/A", None, "en", "Uninstall: A"),
])
def test_restore_point_subjects_label(tmp_path, subject, subjects, language, label):
    fields = {"subjects": subjects} if subjects is not None else {}
    _system_event(tmp_path, "run_subj", "restore_point", 0, "ok", subject=subject, **fields)
    [point] = build_report_data(tmp_path, "run_subj", _fixture_modules(), language, {}, {})["restore_points"]
    assert point["subject_label"] == label


def test_restore_point_subjects_tolerates_a_corrupted_value(tmp_path):
    from dataclasses import asdict

    path = audit_log_path(tmp_path, "run_badsubj")
    entry = asdict(make_entry("_system", "restore_point", "", 0, "ok", False, "run_badsubj", subject="_uninstaller/A"))
    entry["subjects"] = "not a list"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    [point] = build_report_data(tmp_path, "run_badsubj", [], "en", {}, {})["restore_points"]
    assert point["subject_label"] == "Uninstall: A" and point["subjects"] == []


# --- Restart / resume events (research G03) --------------------------------


def _restart_modules():
    def action(action_id, sk, en, risk=RiskLevel.SAFE):
        return ActionDef(id=action_id, label_sk=sk, label_en=en, risk=risk, command="x")

    return [ModuleDef(module_id="m03_repair", actions=[
        action("dism_restore", "Oprava obrazu", "Image repair", RiskLevel.REQUIRES_REBOOT),
        action("sfc_scan", "Kontrola súborov", "File check"),
        action("chkdsk_now", "Kontrola disku", "Disk check"),
    ])]


def _restart_events(tmp_path, run_id):
    # The exact sentences main_window / main.py write (pinned below).
    _system_event(tmp_path, run_id, "restart_pending", 0,
                  "dism_restore succeeded and needs a restart before the rest of the batch - batch stopped. "
                  "Saved to continue after the restart: sfc_scan, chkdsk_now.",
                  risk="REQUIRES_REBOOT", subject="m03_repair/dism_restore")
    _system_event(tmp_path, run_id, "resume_skipped", None,
                  "Not in this version's catalog, not continued: old_gone.")
    _system_event(tmp_path, run_id, "resumed_after_reboot", 0,
                  "Continuing the batch after a restart (2 action(s): sfc_scan, chkdsk_now).",
                  subject="m03_repair/dism_restore")


@pytest.mark.parametrize(("language", "expected"), [
    ("en", [
        'Windows restart: <strong>Image repair</strong> <span class="mod">[REQUIRES_REBOOT]</span> &mdash; '
        "succeeded and needs a restart before the rest of the batch - batch stopped"
        '<div class="warn-text">Saved to continue after the restart: File check, Disk check</div>',
        "Not continued after the restart - not in this version&#x27;s catalog</span>: old_gone",
        "Batch continued after the restart (after <strong>Image repair</strong>): File check, Disk check",
    ]),
    ("sk", [
        'Reštart Windows: <strong>Oprava obrazu</strong> <span class="mod">[REQUIRES_REBOOT]</span> &mdash; '
        "po úspešnom behu vyžaduje reštart pred zvyškom dávky - dávka zastavená"
        '<div class="warn-text">Uložené na pokračovanie po reštarte: Kontrola súborov, Kontrola disku</div>',
        "Po reštarte nepokračovali - nie sú v katalógu tejto verzie</span>: old_gone",
        "Dávka pokračovala po reštarte (po akcii <strong>Oprava obrazu</strong>): Kontrola súborov, Kontrola disku",
    ]),
])
def test_restart_and_resume_events_reach_the_safety_section_translated(tmp_path, language, expected):
    _restart_events(tmp_path, "run_rs")
    html_path, json_path = generate_report(tmp_path, "run_rs", _restart_modules(), language, {}, {})
    items = _safety_items(html_path.read_text(encoding="utf-8"))
    for text in expected:
        assert text in items
    # Never the raw event kind or main_window's English sentence.
    for raw in ("restart_pending:", "resumed_after_reboot:", "resume_skipped:", "batch stopped. Saved"):
        assert raw not in items
    # Not actions: the counts and chips stay clean.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["actions"] == []
    kinds = {e["kind"]: e for e in data["events"]}
    assert kinds["restart_pending"]["action_labels"] == [
        "File check" if language == "en" else "Kontrola súborov",
        "Disk check" if language == "en" else "Kontrola disku",
    ]
    assert kinds["resume_skipped"]["action_labels"] == ["old_gone"]
    # The audit sentence itself is kept verbatim for the record.
    assert kinds["resumed_after_reboot"]["output"].startswith("Continuing the batch after a restart")


def test_restart_that_restarts_at_once_and_could_not_save_the_rest_is_flagged(tmp_path):
    _system_event(tmp_path, "run_rs2", "restart_pending", 0,
                  "dism_restore restarts Windows immediately - report and undo.ps1 written before it runs. "
                  "Could not save the rest of the batch (sfc_scan) - start it again by hand after the restart.",
                  risk="REQUIRES_REBOOT", subject="m03_repair/dism_restore")
    # Nothing was queued behind it: no second line at all.
    _system_event(tmp_path, "run_rs2", "restart_pending", 0,
                  "chkdsk_now restarts Windows immediately - report and undo.ps1 written before it runs.",
                  risk="SAFE", subject="m03_repair/chkdsk_now")
    items = _safety_items(
        generate_report(tmp_path, "run_rs2", _restart_modules(), "en", {}, {})[0].read_text(encoding="utf-8")
    )
    assert ("restarts Windows at once - the report and undo.ps1 were written before it ran"
            '<div class="warn-text"><span class="rp-fail">The rest of the batch could not be saved - '
            "start it by hand after the restart</span>: File check</div>") in items
    assert ("<strong>Disk check</strong> <span class=\"mod\">[SAFE]</span> &mdash; restarts Windows at once - "
            "the report and undo.ps1 were written before it ran</li>") in items


@pytest.mark.parametrize(("language", "expected"), [
    ("en", "Technician declined to continue the batch after the restart: File check, Disk check"),
    ("sk", "Technik po reštarte odmietol pokračovať v dávke: Kontrola súborov, Kontrola disku"),
])
def test_declined_resume_is_on_the_first_halfs_report(tmp_path, language, expected):
    _system_event(tmp_path, "run_rd", "resume_declined", None,
                  "Technician declined to continue the batch after the restart: sfc_scan, chkdsk_now.",
                  decision="declined")
    items = _safety_items(
        generate_report(tmp_path, "run_rd", _restart_modules(), language, {}, {})[0].read_text(encoding="utf-8")
    )
    assert expected in items


def test_missing_first_half_hive_backup_is_flagged(tmp_path):
    _system_event(tmp_path, "run_rh", "resume_hive_backup_missing", None,
                  r"Registry hive backup of the first half not found: E:\PortableFix\Backups\r\hives-1.")
    items = _safety_items(generate_report(tmp_path, "run_rh", [], "sk", {}, {})[0].read_text(encoding="utf-8"))
    assert ('<span class="rp-fail">Záloha registra z prvej časti dávky sa nenašla</span>: '
            r"E:\PortableFix\Backups\r\hives-1</li>") in items


def test_resume_event_with_an_unrecognised_sentence_still_shows_it(tmp_path):
    # A reworded (future) sentence: the event is still shown, verbatim.
    _system_event(tmp_path, "run_ru", "resume_declined", None, "Declined <for> some reason", decision="declined")
    _system_event(tmp_path, "run_ru", "resumed_after_reboot", 0, "Continued.")
    items = _safety_items(generate_report(tmp_path, "run_ru", [], "en", {}, {})[0].read_text(encoding="utf-8"))
    assert "Technician declined to continue the batch after the restart: Declined &lt;for&gt; some reason" in items
    assert "Batch continued after the restart: Continued.</li>" in items


def test_report_json_from_before_action_labels_still_renders():
    from portablefix.report import _render_event

    event = {"timestamp": "2026-09-25T10:00:00+00:00", "kind": "resume_skipped", "exit_code": None,
             "output": "Not in this version's catalog, not continued: a, b.", "subject": ""}
    assert "Not in this version&#x27;s catalog, not continued: a, b." in _render_event(event, "en")


def test_restart_sentences_match_what_main_window_and_main_log():
    # report.py reads the action ids back out of these fixed sentences -
    # pin them so a rewording can't silently drop the names from the report.
    from pathlib import Path

    from portablefix import report

    root = Path(report.__file__).parent
    window = (root / "gui" / "main_window.py").read_text(encoding="utf-8")
    main = (root.parent / "main.py").read_text(encoding="utf-8")
    assert f"{{action.id}} {report._RESTART_IMMEDIATE} - " in window
    assert '" Saved to continue after the restart: {waiting}."' in window
    assert '" Could not save the rest of the batch ({waiting}) - start it again by hand after the restart."' in window
    assert '"Continuing the batch after a restart ({len(queue)} action(s): {\', \'.join(queue)})."' in window
    assert '"Not in this version\'s catalog, not continued: {\', \'.join(missing)}."' in window
    assert '"Registry hive backup of the first half not found: {\', \'.join(missing_hives)}."' in window
    assert '"Technician declined to continue the batch after the restart: {\', \'.join(pending.action_ids)}."' in main
    for kind in ("restart_pending", "resumed_after_reboot", "resume_skipped", "resume_hive_backup_missing"):
        assert f'"{kind}"' in window
    assert '"resume_declined"' in main


# --- Redact for the client (research G20) ----------------------------------


_PERSONAL_OUTPUT = (
    "SerialNumber : 5CD1234XYZ\n"
    "Cleaned C:\\Users\\jnovak\\AppData\\Local\\Temp\n"
    "IPv4 192.168.1.23 MAC 00-1A-2B-3C-4D-5E\n"
    "SSID : Novakovci\n"
    "Windows 10.0.26100.1"
)


@pytest.mark.parametrize(("language", "banner"), [
    ("sk", "Redigované pre klienta"),
    ("en", "Redacted for the client"),
])
def test_redacted_report_masks_personal_data_and_says_so(tmp_path, language, banner):
    import socket

    append_entry(tmp_path, "run_red", make_entry("m02_cleanup", "user_temp", "cmd", 0, _PERSONAL_OUTPUT, False, "run_red"))
    log_before = audit_log_path(tmp_path, "run_red").read_bytes()
    job = {"technician": "Ján Technik", "client": "Firma 192.168.1.23", "note": "Pomalý štart"}
    html_path, json_path = generate_report(
        tmp_path, "run_red", _fixture_modules(), language, {}, {}, job=job, redact=True,
    )

    content = html_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["redacted"] is True
    assert f'<div class="banner redacted" role="note"><strong>{banner}</strong>' in content
    assert data["actions"][0]["output"] == (
        "SerialNumber : <serial>\nCleaned C:\\Users\\<user>\\AppData\\Local\\Temp\n"
        "IPv4 <ip> MAC <mac>\nSSID : <ssid>\nWindows 10.0.26100.1"
    )
    for secret in ("5CD1234XYZ", "jnovak", "00-1A-2B-3C-4D-5E", "Novakovci", "IPv4 192.168.1.23"):
        assert secret not in content
    # Escaped like any other text on the page, never markup.
    assert "C:\\Users\\&lt;user&gt;\\AppData" in content
    # Entered on purpose: the computer name and the job fields stay.
    assert data["hostname"] == socket.gethostname()
    assert data["job"] == job
    assert "Firma 192.168.1.23" in content
    # The audit log itself is never touched.
    assert audit_log_path(tmp_path, "run_red").read_bytes() == log_before
    assert b"5CD1234XYZ" in log_before


def test_report_is_not_redacted_by_default(tmp_path):
    append_entry(tmp_path, "run_nored", make_entry("m02_cleanup", "user_temp", "cmd", 0, _PERSONAL_OUTPUT, False, "run_nored"))
    html_path, json_path = generate_report(tmp_path, "run_nored", _fixture_modules(), "en", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert "redacted" not in data
    assert data["actions"][0]["output"] == _PERSONAL_OUTPUT
    content = html_path.read_text(encoding="utf-8")
    assert "Redacted for the client" not in content and "5CD1234XYZ" in content


def test_redacted_events_keep_their_translated_rendering(tmp_path):
    # Masking runs over the event outputs too, and must not break how the
    # safety section reads them.
    _system_event(tmp_path, "run_redev", "resume_hive_backup_missing", None,
                  "Registry hive backup of the first half not found: C:\\Users\\jnovak\\AppData\\Local\\Temp\\PF\\hives-1.")
    _restart_events(tmp_path, "run_redev")
    html_path, _ = generate_report(tmp_path, "run_redev", _restart_modules(), "en", {}, {}, redact=True)
    items = _safety_items(html_path.read_text(encoding="utf-8"))
    assert "C:\\Users\\&lt;user&gt;\\AppData\\Local\\Temp\\PF\\hives-1</li>" in items
    assert "Saved to continue after the restart: File check, Disk check" in items


def test_redacted_report_masks_this_pcs_profile_names_without_a_path(tmp_path, monkeypatch):
    # m04 ProfileList check prints the account as COMPUTER\name; the name
    # is known from the profile folders of the PC the report is made on.
    from portablefix import redaction

    monkeypatch.setattr(redaction, "local_profile_names", lambda users_dir=None: ["Jan Novak"])
    output = "User : DESKTOP-X\\Jan Novak\nLocalPath : C:\\Users\\Jan Novak"
    append_entry(tmp_path, "run_prof", make_entry("m04_integrity", "profile_list", "cmd", 0, output, False, "run_prof"))
    html_path, json_path = generate_report(tmp_path, "run_prof", _fixture_modules(), "en", {}, {}, redact=True)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["actions"][0]["output"] == "User : DESKTOP-X\\<user>\nLocalPath : C:\\Users\\<user>"
    assert "Novak" not in html_path.read_text(encoding="utf-8")


def test_report_runner_passes_redact_through(tmp_path, monkeypatch):
    from portablefix import report

    captured = {}
    monkeypatch.setattr(report, "generate_report", lambda *a, **kw: captured.update(kw) or (tmp_path / "r.html", None))
    runner = report.ReportRunner(tmp_path, "run_x", [], "en", {}, {}, redact=True)
    runner.run()
    assert captured["redact"] is True


# --- Intake / outtake, work time and branding (research G20) ---------------

_PNG_LOGO = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x01" * 32


def _g20_run(tmp_path, run_id):
    from portablefix import intake

    intake_form = intake.IntakeForm(
        problem="Pomalý štart <b>", condition="Škrabanec na veku",
        condition_flags=["scratches", "liquid_damage"], accessories="nabíjačka",
        backup="waiver", password_handling="given_by_client",
    )
    outtake_form = intake.OuttakeForm(
        checks={"wifi": "pass", "sound": "fail", "camera": "na"}, handed_to="Ján Novák", handed_at="2026-09-25 16:30",
    )
    append_entry(tmp_path, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, run_id))
    _system_event(tmp_path, run_id, intake.INTAKE_EVENT, 0, intake.form_to_output(intake_form))
    _system_event(tmp_path, run_id, intake.OUTTAKE_EVENT, 0, intake.form_to_output(outtake_form))
    _system_event(tmp_path, run_id, intake.BATCH_DURATION_EVENT, 0, intake.batch_duration_output(600))
    _system_event(tmp_path, run_id, intake.BATCH_DURATION_EVENT, 0, intake.batch_duration_output(125))


@pytest.mark.parametrize(("language", "texts"), [
    ("sk", ["Pri prevzatí", "Nahlásený problém", "škrabance, poškodenie tekutinou; Škrabanec na veku",
            "<strong>Klient zálohu odmieta a riziko straty dát berie na seba</strong>", "Zadal ho klient",
            "Pri odovzdaní", "V poriadku", "Chyba", "Odovzdané", "Čas práce: dávky 12 min"]),
    ("en", ["Intake", "Reported problem", "scratches, liquid damage; Škrabanec na veku",
            "<strong>The client declines a backup and accepts the risk of data loss</strong>", "Given by the client",
            "Hand-over", "Pass", "Fail", "Handed over to", "Work time: batches 12 min"]),
])
def test_report_renders_intake_outtake_and_work_time_in_both_languages(tmp_path, language, texts):
    _g20_run(tmp_path, "run_g20")
    html_path, json_path = generate_report(tmp_path, "run_g20", _fixture_modules(), language, {}, {})
    content = html_path.read_text(encoding="utf-8")
    for text in texts:
        assert text in content, text
    # Free text is escaped, never markup.
    assert "Pomalý štart &lt;b&gt;" in content
    assert '<td class="check-pass">' in content and '<td class="check-fail">' in content
    assert "<strong>Ján Novák</strong> &middot; 2026-09-25 16:30" in content
    # Neither form is a safety fact - nothing of them in the safety log.
    assert '<ul class="events">' not in content

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["intake"]["backup"] == "waiver"
    assert data["intake"]["password_handling"] == "given_by_client"
    assert data["intake"]["condition_flags"] == ["scratches", "liquid_damage"]
    assert data["intake"]["timestamp"]
    assert [c["key"] for c in data["outtake"]["checks"]] == ["wifi", "sound", "camera"]
    assert data["outtake"]["handed_to"] == "Ján Novák"
    assert data["work_time"] == {"batch_seconds": 725, "batch_count": 2, "timer_seconds": 0, "timer_running": False}
    assert data["events"] == []
    # The labels in the report's language, next to the codes.
    assert data["intake"]["backup_label"] in content


def test_report_without_forms_has_no_new_sections(tmp_path):
    append_entry(tmp_path, "run_plain", make_entry("m02_cleanup", "user_temp", "cmd", 0, "done", False, "run_plain"))
    html_path, json_path = generate_report(tmp_path, "run_plain", _fixture_modules(), "en", {}, {})
    content = html_path.read_text(encoding="utf-8")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for key in ("intake", "outtake", "work_time", "branding"):
        assert key not in data
    for marker in ("pf-h-intake", "pf-h-outtake", "Work time", 'class="brand"'):
        assert marker not in content


def test_report_shows_the_manual_timer_and_a_running_one(tmp_path):
    from datetime import datetime, timedelta, timezone

    from portablefix import intake

    run = "run_timer"
    start = datetime.now(timezone.utc) - timedelta(minutes=20)
    for decision, when in (("start", start), ("stop", start + timedelta(minutes=5)), ("start", start + timedelta(minutes=10))):
        entry = make_entry("_system", intake.WORK_TIMER_EVENT, "", 0, "", False, run, decision=decision)
        entry.timestamp = when.isoformat()
        append_entry(tmp_path, run, entry)
    html_path, json_path = generate_report(tmp_path, run, _fixture_modules(), "sk", {}, {})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["work_time"]["timer_running"] is True
    # 5 closed minutes + ~10 running ones.
    assert 15 * 60 <= data["work_time"]["timer_seconds"] < 16 * 60
    content = html_path.read_text(encoding="utf-8")
    assert "Čas práce: ručný časovač 15 min (stále beží)" in content
    # No batch ran - no batch part.
    assert "dávky" not in content


def test_report_header_shows_branding_with_the_logo_inline(tmp_path):
    import base64

    logo = base64.b64encode(_PNG_LOGO).decode("ascii")
    brand = {"company": "Servis <s.r.o.>", "company_id": "12345678", "contact": "0900 123 456\nservis@example.sk", "logo": logo}
    html_path, json_path = generate_report(tmp_path, "run_brand", _fixture_modules(), "sk", {}, {}, branding=brand)
    content = html_path.read_text(encoding="utf-8")
    assert f'<img class="brand-logo" src="data:image/png;base64,{logo}" alt="Servis &lt;s.r.o.&gt;">' in content
    assert "<strong>Servis &lt;s.r.o.&gt;</strong>" in content
    assert "IČO: 12345678" in content
    assert "servis@example.sk" in content
    # Before the title, so it heads the page and the printout.
    assert content.index('class="brand"') < content.index("<h1>")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["branding"]["logo_mime"] == "image/png"
    assert data["branding"]["company_id"] == "12345678"


def test_report_branding_refuses_a_forged_logo_when_re_rendered(tmp_path):
    from portablefix.report import render_report_html

    html_path, json_path = generate_report(tmp_path, "run_forge", _fixture_modules(), "en", {}, {},
                                           branding={"company": "Servis"})
    data = json.loads(json_path.read_text(encoding="utf-8"))
    # A hand-edited report.json must not put anything else into <img src>.
    data["branding"]["logo"] = 'x" onerror="alert(1)'
    data["branding"]["logo_mime"] = "text/html"
    content = render_report_html(data)
    assert "<img" not in content and "onerror" not in content
    assert "<strong>Servis</strong>" in content
    assert "Company ID" not in content


def test_report_invalid_logo_alone_adds_no_branding(tmp_path):
    import base64

    html_path, json_path = generate_report(tmp_path, "run_badlogo", _fixture_modules(), "en", {}, {},
                                           branding={"logo": base64.b64encode(b"GIF89a....").decode()})
    assert "branding" not in json.loads(json_path.read_text(encoding="utf-8"))
    assert 'class="brand"' not in html_path.read_text(encoding="utf-8")


def test_redacted_report_masks_form_text_but_keeps_branding_and_the_hand_over_name(tmp_path, monkeypatch):
    import base64

    from portablefix import intake, redaction

    # The client's profile name equals the hand-over name - the name
    # entered on purpose still stays.
    monkeypatch.setattr(redaction, "local_profile_names", lambda users_dir=None: ["Jan Novak"])
    run = "run_g20red"
    form = intake.IntakeForm(problem="Nejde sieť na 192.168.1.23, SerialNumber : 5CD1234XYZ")
    _system_event(tmp_path, run, intake.INTAKE_EVENT, 0, intake.form_to_output(form))
    _system_event(tmp_path, run, intake.OUTTAKE_EVENT, 0, intake.form_to_output(
        intake.OuttakeForm(checks={"wifi": "pass"}, handed_to="Jan Novak")))
    # The logo's base64 holds "/5CD1234XYZ/" - the serial collected above,
    # between two non-word characters: masking it would break the image.
    logo = base64.b64encode(_PNG_LOGO).decode("ascii") + "/5CD1234XYZ/"
    brand = {"company": "Servis 10.0.0.5", "contact": "Jan Novak, 0900", "logo": logo}
    html_path, json_path = generate_report(tmp_path, run, _fixture_modules(), "en", {}, {}, redact=True, branding=brand)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["intake"]["problem"] == "Nejde sieť na <ip>, SerialNumber : <serial>"
    assert data["outtake"]["handed_to"] == "Jan Novak"
    assert data["branding"]["company"] == "Servis 10.0.0.5"
    assert data["branding"]["contact"] == "Jan Novak, 0900"
    assert data["branding"]["logo"] == logo
    content = html_path.read_text(encoding="utf-8")
    assert "192.168.1.23" not in content
    assert "SerialNumber : 5CD1234XYZ" not in content
    assert f"base64,{logo}" in content
    assert "<strong>Jan Novak</strong>" in content


def test_report_runner_passes_branding_through(tmp_path, monkeypatch):
    from portablefix import report

    captured = {}
    monkeypatch.setattr(report, "generate_report", lambda *a, **kw: captured.update(kw) or (tmp_path / "r.html", None))
    report.ReportRunner(tmp_path, "run_x", [], "en", {}, {}, branding={"company": "Servis"}).run()
    assert captured["branding"] == {"company": "Servis"}
