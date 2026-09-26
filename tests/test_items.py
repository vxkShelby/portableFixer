"""Research G05: per-item selection - the id format, the items file the
command reads its ids from, the PFJSON records and the per-item undo steps.
The PowerShell tests run the real powershell.exe: what matters is that an id
reaches the command as data, byte for byte, never as code."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import action_service, items, pfjson
from portablefix.executor import PlanRun, build_execution_plan
from portablefix.models import ActionDef, RiskLevel
from portablefix.module_engine import ModuleLoadError, load_module


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _run_ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# --- ids and listing -----------------------------------------------------------

@pytest.mark.parametrize("item_id", [
    "KB5034441", "run-0a1b2c3d4e5f", "{8D8F4F83-3594-4F07-8369-FC3C3CAE4919}", "\\Vendor\\Updater Task",
    "Microsoft Print to PDF", "svc:Contoso.Service_2", "a", "HP LaserJet (Office #2)",
])
def test_item_id_accepts_real_world_names(item_id):
    assert items.check_ids([item_id]) == [item_id]


@pytest.mark.parametrize("item_id", [
    "a;b", "$env:x", "a`b", "*", "a'b", 'a"b', "a|b", "a&b", "a>b", "a\nb", "a\rb", " lead", "trail ", "",
    "x[1]", "what?", "é", "a\x00b", "x" * 201, 5, None,
])
def test_item_id_refuses_anything_a_shell_could_read_as_code(item_id):
    with pytest.raises(items.ItemsError):
        items.check_ids([item_id])


def test_check_ids_limits_the_choice_to_the_listed_items_and_drops_repeats():
    assert items.check_ids(["a", "b", "a"], allowed={"a", "b"}) == ["a", "b"]
    with pytest.raises(items.ItemsError):
        items.check_ids(["a", "c"], allowed={"a", "b"})
    with pytest.raises(items.ItemsError):
        items.check_ids("a")


def test_parse_items_reads_json_lines_and_skips_everything_else():
    lines = [
        "Collecting...",
        '{"id": "one", "label": "First", "detail": "C:\\\\x.exe --tray", "risk_hint": "MODERATE"}',
        '{"id": "bad;id", "label": "Injected"}',
        '{"id": "one", "label": "Repeat"}',
        '{"id": "two"}',
        "{not json",
        '["not", "an", "object"]',
        '{"id": "three", "label": "multi\\nline", "risk_hint": "CATASTROPHIC"}',
    ]
    parsed = items.parse_items(lines)
    assert [i.id for i in parsed] == ["one", "two", "three"]
    assert parsed[0] == items.Item("one", "First", "C:\\x.exe --tray", "MODERATE")
    assert parsed[1].label == "two"  # no label: the id stands in
    assert parsed[2].label == "multi line" and parsed[2].risk_hint == ""


def test_items_file_names_are_fresh_per_run_of_the_action(tmp_path):
    first = items.write_items_file(items.items_file_path(tmp_path, "run1", "act/x"), ["a", "b"])
    second = items.items_file_path(tmp_path, "run1", "act/x")
    assert first == tmp_path / "Backups" / "run1" / "items" / "act_x.txt"
    assert second.name == "act_x-2.txt"
    assert first.read_text(encoding="utf-8") == "a\nb\n"
    with pytest.raises(items.ItemsError):
        items.write_items_file(second, ["ok", "no;pe"])
    assert not second.exists()


def test_selected_ids_reach_the_command_as_data_not_code(tmp_path):
    ids = ["\\Vendor\\Updater Task", "{8D8F4F83-3594-4F07-8369-FC3C3CAE4919}", "HP (Office #2)", "a+b@c"]
    path = items.write_items_file(tmp_path / "ids.txt", ids)
    plan = build_execution_plan(
        "Write-Output ('count=' + $__pfItems.Count); foreach ($i in $__pfItems) { Write-Output ('<' + $i + '>') }",
        dry_run=False, items_file=path,
    )
    run = PlanRun(plan)
    assert run.run() == 0, run.captured_output
    assert run.captured_output == ["count=4"] + [f"<{i}>" for i in ids]


def test_dry_run_of_a_per_item_action_shows_the_ids_it_would_get(tmp_path):
    path = items.write_items_file(tmp_path / "ids.txt", ["one", "two"])
    plan = build_execution_plan("Do-It $__pfItems", dry_run=True, items_file=path)
    assert plan.display_command == "Do-It $__pfItems  # $__pfItems = one, two"


# --- PFJSON --------------------------------------------------------------------

def test_pfjson_lines_are_kept_out_of_the_console_and_parsed():
    plan = build_execution_plan(
        "Write-Output 'visible'; Write-Output 'PFJSON:{\"undo\": {\"id\": \"a\"}}'; "
        "Write-Output 'PFJSON:{broken'; Write-Output 'PFJSON:[1,2]'; Write-Output 'after'",
        dry_run=False,
    )
    run = PlanRun(plan)
    lines = []
    assert run.run(lines.append) == 0
    assert run.captured_output == ["visible", "after"]
    assert lines == ["visible", "after"]
    assert run.pfjson == [{"undo": {"id": "a"}}]


def test_pfjson_records_flatten_lists_and_the_powershell_single_object_quirk():
    payloads = [{"undo": [{"id": "a"}, {"id": "b"}]}, {"undo": {"id": "c"}}, {"other": 1},
                {"undo": {"value": [{"id": "d"}], "Count": 1}}, "junk"]
    assert [r["id"] for r in pfjson.records(payloads, "undo")] == ["a", "b", "c", "d"]
    assert pfjson.parse_line("no prefix") is None
    assert pfjson.parse_line("PFJSON:" + "x" * (pfjson.MAX_PAYLOAD_CHARS + 1)) is None


# --- per-item undo -----------------------------------------------------------------

def test_undo_records_only_for_selected_items_with_a_valid_prior():
    payloads = [{"undo": [
        {"id": "a", "prior": {"src": "user", "start": 2, "delayed": True, "gone": None}},
        {"id": "not-picked"},
        {"id": "b", "prior": {"bad key": 1}},
        {"id": "c", "prior": {"nested": {"x": 1}}},
        {"id": "d"},
        {"id": "a", "prior": {"src": "second record ignored"}},
    ]}]
    assert items.undo_records(payloads, ["a", "b", "c", "d"]) == [
        ("a", {"src": "user", "start": 2, "delayed": True, "gone": None}), ("d", {}),
    ]


def test_undo_step_passes_the_id_and_prior_as_literals(tmp_path):
    prior = {"name": "it's $env:USERNAME `n $(Get-Date)", "start": 4, "big": 2**64 - 1, "flag": False, "none": None,
             "ctl": "tab\there"}
    step = items.undo_step(
        "act", "Write-Output ($__pfItem + '|' + $__pfPrior.name + '|' + $__pfPrior.start + '|' + $__pfPrior.big + '|' "
        "+ $__pfPrior.flag + '|' + ($null -eq $__pfPrior.none) + '|' + $__pfPrior.ctl)",
        "run-0a1b2c3d4e5f", prior,
    )
    assert step.startswith("# act: item run-0a1b2c3d4e5f\n")
    result = _run_ps(step)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == (
        "run-0a1b2c3d4e5f|it's $env:USERNAME `n $(Get-Date)|4|18446744073709551615|False|True|tab\there"
    )


def test_undo_step_failure_is_reported_and_does_not_stop_the_script():
    step = items.undo_step("act", "throw 'boom'", "x1", {})
    result = _run_ps(step + "\nWrite-Output 'next step ran'")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAILED to restore x1" in result.stdout and "boom" in result.stdout
    assert "next step ran" in result.stdout


def _items_action(**kwargs) -> ActionDef:
    defaults = dict(
        id="pick", label_sk="Vyber", label_en="Pick", risk=RiskLevel.MODERATE, command="Do-It",
        items_command="List-It", undo_command="Undo-It $__pfItem",
    )
    defaults.update(kwargs)
    return ActionDef(**defaults)


def test_undo_outcome_of_a_per_item_action_is_one_step_per_reported_item():
    action = _items_action()
    payloads = [{"undo": [{"id": "a", "prior": {"n": 1}}, {"id": "zzz"}]}, {"undo": {"id": "b"}}]
    outcome = action_service.undo_outcome(action, 0, language="en", item_ids=["a", "b", "c"], payloads=payloads)
    assert len(outcome.steps) == 2 and outcome.irreversible == ""
    assert "$__pfItem = 'a'" in outcome.steps[0] and "$__pfPrior = @{ n = 1 }" in outcome.steps[0]
    assert "$__pfItem = 'b'" in outcome.steps[1]
    # Nothing reported: nothing changed, nothing to undo.
    assert action_service.undo_outcome(action, 0, language="en", item_ids=["a"], payloads=[]).steps == []
    # A failure that reported nothing may still have changed something.
    failed = action_service.undo_outcome(action, 1, language="en", item_ids=["a"], payloads=[])
    assert "no item reported what it changed" in failed.irreversible and "exit 1" in failed.irreversible
    # No undo_command at all: the items it ran on are named in undo.ps1.
    plain = action_service.undo_outcome(_items_action(undo_command=None), 0, language="en", item_ids=["a", "b"])
    assert plain.irreversible == "[MODERATE] Pick (pick) - items: a, b"


def test_prepare_plan_writes_the_items_file_under_backups(tmp_path):
    prepared = action_service.prepare_plan(
        _items_action(), dry_run=False, state_dir=tmp_path, run_id="r1", target_user=None, item_ids=["a", "b"],
    )
    assert prepared.items_file == tmp_path / "Backups" / "r1" / "items" / "pick.txt"
    assert prepared.items_file.read_text(encoding="utf-8") == "a\nb\n"
    assert "$__pfItemsFile = '" in prepared.plan.argv[-1]
    with pytest.raises(items.ItemsError):
        action_service.prepare_plan(
            _items_action(), dry_run=False, state_dir=tmp_path, run_id="r1", target_user=None, item_ids=["$(x)"],
        )


def test_list_items_runs_the_listing_and_fails_on_a_non_zero_exit():
    ok = _items_action(items_command="Write-Output 'noise'; Write-Output '{\"id\": \"k1\", \"label\": \"One\"}'")
    assert action_service.list_items(ok, None) == [items.Item("k1", "One")]
    assert action_service.list_items(_items_action(items_command="Write-Output '{\"id\": \"k1\"}'; exit 3"), None) is None


# --- loader --------------------------------------------------------------------------

def _load(tmp_path: Path, body: str):
    path = tmp_path / "actions.yaml"
    path.write_text(
        "module_id: m99\nactions:\n  - id: pick\n    label_sk: X\n    label_en: X\n    risk: MODERATE\n" + body,
        encoding="utf-8",
    )
    return load_module(path)


def test_loader_reads_items_command(tmp_path):
    action = _load(tmp_path, "    items_command: \"List-It\"\n    command: \"Do-It\"\n").actions[0]
    assert action.items_command == "List-It"
    assert _load(tmp_path, "    command: \"Do-It\"\n").actions[0].items_command is None


@pytest.mark.parametrize("body", [
    "    items_command: \"\"\n    command: \"Do-It\"\n",
    "    items_command: [1]\n    command: \"Do-It\"\n",
    "    items_command: \"List-It\"\n    ops:\n      - service_start_type: {name: X, start_type: manual}\n",
])
def test_loader_refuses_a_bad_items_command(tmp_path, body):
    with pytest.raises(ModuleLoadError):
        _load(tmp_path, body)
