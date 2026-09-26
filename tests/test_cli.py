"""Research G21: the headless run - presets, the risk gate, per-item ids,
restore point, the same audit/undo/report files as the window, exit codes.
The catalog is a tiny fake one; its commands run in the real powershell.exe."""

import json
from pathlib import Path

import pytest

from portablefix import cli, preflight
from portablefix.report import read_audit_entries
from portablefix.target_user import TargetUser

CATALOG = """module_id: m99_cli
category: REPAIR
actions:
  - id: cli_read
    label_sk: Čítaj
    label_en: Read
    risk: SAFE
    command: "Write-Output 'read ok'"
  - id: cli_change
    label_sk: Zmeň
    label_en: Change
    risk: MODERATE
    command: "Write-Output 'changed'"
    undo_command: "Write-Output 'undone'"
  - id: cli_wipe
    label_sk: Zmaž
    label_en: Wipe
    risk: DESTRUCTIVE
    command: "Write-Output 'wiped'"
  - id: cli_fail
    label_sk: Zlyhá
    label_en: Fails
    risk: SAFE
    command: "Write-Output 'nope'; exit 7"
  - id: cli_pick
    label_sk: Vyber
    label_en: Pick
    risk: MODERATE
    items_command: "Write-Output '{\\"id\\": \\"a1\\"}'; Write-Output '{\\"id\\": \\"b2\\"}'"
    command: "foreach ($i in $__pfItems) { Write-Output ('did ' + $i); Write-Output ('PFJSON:{\\"undo\\": {\\"id\\": \\"' + $i + '\\"}}') }"
    undo_command: "Write-Output ('undo ' + $__pfItem)"
  - id: cli_restart_first
    label_sk: Reštart potom
    label_en: Restart after
    risk: REQUIRES_REBOOT
    restart_before_next: true
    command: "Write-Output 'pending'"
"""


@pytest.fixture
def app(tmp_path):
    assets = tmp_path / "app"
    (assets / "Modules" / "m99_cli").mkdir(parents=True)
    (assets / "Modules" / "m99_cli" / "actions.yaml").write_text(CATALOG, encoding="utf-8")
    return assets


def _deps(lines, **overrides):
    rp_calls = []
    rp_result = overrides.pop("rp_result", (True, ""))

    def restore_point(description):
        rp_calls.append(description)
        return rp_result

    deps = cli.Deps(
        unsupported_os=lambda: False, running_from_temp=lambda app_dir: False, is_admin=lambda: True,
        target_user=TargetUser, probes=lambda: preflight.Probes(),
        create_restore_point=restore_point, take_snapshot=lambda checks=None: {"checks": checks or {}},
        out=lines.append,
    )
    for key, value in overrides.items():
        setattr(deps, key, value)
    deps.rp_calls = rp_calls
    return deps


def _preset(tmp_path, actions, items=None) -> str:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"name": "t", "actions": actions, "items": items or {}}), encoding="utf-8")
    return str(path)


def _run(app, tmp_path, *args, deps=None):
    lines = []
    deps = deps or _deps(lines)
    code = cli.run(["PortableFix.exe", *args, "--out", str(tmp_path / "out")], assets_dir=app, deps=deps)
    return code, lines if deps.out == lines.append else None, deps


def _run_id(lines) -> str:
    return next(line for line in lines if ", run " in line).rsplit(" ", 1)[1]


def test_wants_cli_only_for_the_headless_switches():
    assert cli.wants_cli(["x.exe", "--preset", "quick_clean"])
    assert cli.wants_cli(["x.exe", "--preset=a.json"])
    assert cli.wants_cli(["x.exe", "--export-preset", "a", "b.json"])
    assert not cli.wants_cli(["x.exe", "--post-update"])
    assert not cli.wants_cli(["x.exe"])


def test_dry_run_is_the_default_and_writes_audit_and_report(app, tmp_path):
    code, lines, deps = _run(app, tmp_path, "--preset", _preset(tmp_path, ["cli_read", "cli_wipe"]), "--job-client", "Acme")
    assert code == cli.EXIT_OK, lines
    assert any("DRY-RUN" in line for line in lines)
    run_id = _run_id(lines)
    entries = [e for e in read_audit_entries(tmp_path / "out", run_id) if e["module_id"] != "_system"]
    assert [(e["action_id"], e["dry_run"]) for e in entries] == [("cli_read", True), ("cli_wipe", True)]
    assert deps.rp_calls == []
    report = json.loads(next((tmp_path / "out" / "Reports").glob("*.json")).read_text(encoding="utf-8"))
    assert report["job"]["client"] == "Acme"


def test_live_refuses_actions_above_the_accepted_risk_before_running_anything(app, tmp_path):
    preset = _preset(tmp_path, ["cli_read", "cli_change", "cli_wipe"])
    code, lines, _ = _run(app, tmp_path, "--preset", preset, "--live")
    assert code == cli.EXIT_ERROR
    assert any("cli_change [MODERATE]" in line and "cli_wipe [DESTRUCTIVE]" in line for line in lines)
    assert not (tmp_path / "out" / "Logs").exists()
    code, lines, _ = _run(app, tmp_path, "--preset", preset, "--live", "--accept-risk", "MODERATE")
    assert code == cli.EXIT_ERROR and any("cli_wipe [DESTRUCTIVE]" in line for line in lines)


def test_live_run_makes_a_restore_point_and_writes_undo(app, tmp_path):
    code, lines, deps = _run(
        app, tmp_path, "--preset", _preset(tmp_path, ["cli_read", "cli_change"]), "--live", "--accept-risk", "MODERATE",
    )
    assert code == cli.EXIT_OK, lines
    assert len(deps.rp_calls) == 1
    run_id = _run_id(lines)
    undo = (tmp_path / "out" / "Backups" / run_id / "undo.ps1").read_text(encoding="utf-8-sig")
    assert "Write-Output 'undone'" in undo
    system = [e["action_id"] for e in read_audit_entries(tmp_path / "out", run_id) if e["module_id"] == "_system"]
    assert "preflight" in system and "restore_point" in system


def test_failed_restore_point_skips_the_guarded_actions_with_a_warning(app, tmp_path):
    lines = []
    deps = _deps(lines, rp_result=(False, "VSS off"))
    code, _, _ = _run(
        app, tmp_path, "--preset", _preset(tmp_path, ["cli_change", "cli_read"]), "--live", "--accept-risk", "MODERATE",
        deps=deps,
    )
    assert code == cli.EXIT_WARNING, lines
    ran = [e["action_id"] for e in read_audit_entries(tmp_path / "out", _run_id(lines)) if e["module_id"] != "_system"]
    assert ran == ["cli_read"]


def test_a_failed_action_is_exit_1_and_the_rest_still_runs(app, tmp_path):
    code, lines, _ = _run(app, tmp_path, "--preset", _preset(tmp_path, ["cli_fail", "cli_read"]), "--live")
    assert code == cli.EXIT_ERROR
    assert any("cli_fail: FAILED (exit 7)" in line for line in lines)
    assert any("cli_read: OK" in line for line in lines)


def test_per_item_action_gets_only_the_preset_ids_still_listed(app, tmp_path):
    preset = _preset(tmp_path, ["cli_pick"], items={"cli_pick": ["b2", "zz9"]})
    code, lines, _ = _run(app, tmp_path, "--preset", preset, "--live", "--accept-risk", "MODERATE")
    assert code == cli.EXIT_WARNING, lines  # zz9 is not listed any more
    assert "did b2" in lines and "did a1" not in lines
    run_id = _run_id(lines)
    entry = next(e for e in read_audit_entries(tmp_path / "out", run_id) if e["action_id"] == "cli_pick")
    assert entry["items"] == ["b2"]
    undo = (tmp_path / "out" / "Backups" / run_id / "undo.ps1").read_text(encoding="utf-8-sig")
    assert "$__pfItem = 'b2'" in undo and "'a1'" not in undo


def test_per_item_action_without_ids_in_the_preset_is_skipped(app, tmp_path):
    code, lines, _ = _run(app, tmp_path, "--preset", _preset(tmp_path, ["cli_pick", "cli_read"]))
    assert code == cli.EXIT_WARNING
    assert any("picks no items" in line for line in lines)


def test_preset_file_refuses_injected_item_ids(app, tmp_path):
    code, lines, _ = _run(app, tmp_path, "--preset", _preset(tmp_path, ["cli_pick"], items={"cli_pick": ["a;rm"]}))
    assert code == cli.EXIT_ERROR and any("invalid item id" in line for line in lines)


def test_restart_before_next_stops_the_run_with_exit_4(app, tmp_path):
    code, lines, _ = _run(
        app, tmp_path, "--preset", _preset(tmp_path, ["cli_restart_first", "cli_read"]), "--live",
        "--accept-risk", "MODERATE",
    )
    assert code == cli.EXIT_REBOOT_PENDING, lines
    assert not any("cli_read: OK" in line for line in lines)


@pytest.mark.parametrize("probes,expected", [
    # A non-SAFE REPAIR action is servicing: a half-applied update blocks it.
    (lambda: preflight.Probes(pending_reboot=lambda: [preflight.REBOOT_CBS]), cli.EXIT_REBOOT_PENDING),
    (lambda: preflight.Probes(is_admin=lambda: False), cli.EXIT_ERROR),
])
def test_preflight_blockers_refuse_the_live_run(app, tmp_path, probes, expected):
    lines = []
    code, _, _ = _run(app, tmp_path, "--preset", _preset(tmp_path, ["cli_change"]), "--live", "--accept-risk",
                      "MODERATE", deps=_deps(lines, probes=probes))
    assert code == expected and any("Pre-flight blocker" in line for line in lines), lines
    assert not any("changed" == line for line in lines)


def test_os_and_temp_guards(app, tmp_path):
    lines = []
    assert cli.run(["x", "--preset", "quick_clean"], assets_dir=app,
                   deps=_deps(lines, unsupported_os=lambda: True)) == cli.EXIT_UNSUPPORTED_OS
    assert cli.run(["x", "--preset", "quick_clean"], assets_dir=app,
                   deps=_deps(lines, running_from_temp=lambda d: True)) == cli.EXIT_FROM_TEMP


def test_bad_arguments_and_unknown_presets_are_exit_1(app, tmp_path):
    assert _run(app, tmp_path, "--preset", "no_such_preset")[0] == cli.EXIT_ERROR
    assert _run(app, tmp_path, "--preset", _preset(tmp_path, ["not_in_catalog"]))[0] == cli.EXIT_ERROR
    assert _run(app, tmp_path, "--preset", "x", "--accept-risk", "YOLO")[0] == cli.EXIT_ERROR


def test_export_then_import_a_builtin_preset(app, tmp_path):
    dest = tmp_path / "quick.json"
    code, lines, _ = _run(app, tmp_path, "--export-preset", "quick_clean", str(dest))
    assert code == cli.EXIT_OK, lines
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["name"] == "quick_clean" and "user_temp" in data["actions"]
    preset = cli.load_preset(str(dest), cli.Settings())
    assert preset.action_ids == data["actions"]


def test_real_catalog_presets_resolve():
    # Every built-in preset names actions the shipped catalog has.
    from portablefix.module_engine import load_all_modules
    from portablefix.settings import PRESETS

    modules, _ = load_all_modules(Path(__file__).resolve().parent.parent / "Modules")
    known = {a.id for m in modules for a in m.actions}
    for name, ids in PRESETS.items():
        assert set(ids) <= known, name
