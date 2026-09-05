import json

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
