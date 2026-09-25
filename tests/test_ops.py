"""Declarative `ops:` (research G10, portablefix/ops.py): loader validation,
quoting, and capture -> apply -> undo round trips of the generated
PowerShell against the in-memory machine in tests/ops_rig.py."""
import json
import os
import subprocess
from pathlib import Path

import pytest

from ops_rig import OTHER, TECH, Machine, command_with_state, powershell_or_skip
from portablefix import ops
from portablefix.module_engine import ModuleLoadError, load_all_modules, load_module

HEADER = "module_id: m_test\ncategory: REPAIR\nactions:\n"


def _action_yaml(body: str, risk: str = "MODERATE", action_id: str = "a1") -> str:
    return (
        f"  - id: {action_id}\n    label_sk: \"Akcia\"\n    label_en: \"Action\"\n    risk: {risk}\n"
        + "".join(f"    {line}\n" for line in body.strip("\n").splitlines())
    )


def _load(tmp_path: Path, body: str, risk: str = "MODERATE"):
    path = tmp_path / "actions.yaml"
    path.write_text(HEADER + _action_yaml(body, risk), encoding="utf-8")
    return load_module(path).actions[0]


def _op_list(*raw):
    return ops.parse_ops(list(raw))


def reg_set(path, name, type_, value):
    return {"reg_set": {"path": path, "name": name, "type": type_, "value": value}}


# --- loader ------------------------------------------------------------------

VALID_OPS = r"""
ops:
  - reg_set: {path: 'HKLM\SOFTWARE\PortableFixTest', name: Level, type: DWord, value: 1}
  - reg_delete: {path: 'HKCU\Software\PortableFixTest', name: Old}
  - service_start_type: {name: DiagTrack, start_type: disabled}
  - task_state: {path: '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator', enabled: false}
ops_message: "Done."
"""


def test_loader_generates_command_preview_and_marks_undo(tmp_path):
    action = _load(tmp_path, VALID_OPS)
    assert [ops.op_kind(op) for op in action.ops] == ["reg_set", "reg_delete", "service_start_type", "task_state"]
    assert action.ops[0] == ops.RegSet("HKLM\\SOFTWARE\\PortableFixTest", "Level", "DWord", 1)
    assert action.ops[3] == ops.TaskState(
        "\\Microsoft\\Windows\\Customer Experience Improvement Program\\Consolidator", False,
    )
    assert action.command == ops.apply_script("a1", action.ops, "Done.")
    assert action.preview_command == ops.preview_script(action.ops)
    # Undo only exists after a run - generated from the captured state.
    assert action.undo_command is None
    assert action.has_undo


def test_loader_type_names_are_case_insensitive(tmp_path):
    action = _load(tmp_path, "ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: dword, value: 1}")
    assert action.ops[0].type == "DWord"


def test_command_actions_are_unchanged(tmp_path):
    action = _load(tmp_path, "command: \"Write-Output 'x'\"\nundo_command: \"Write-Output 'u'\"")
    assert action.ops == [] and action.undo_command == "Write-Output 'u'" and action.has_undo
    action = _load(tmp_path, "command: \"Write-Output 'x'\"")
    assert not action.has_undo


@pytest.mark.parametrize("body,fragment", [
    ("label_sk: x", "must have either 'command' or 'ops'"),
    ("command: x\nops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}", "must have either 'command' or 'ops'"),
    ("command: x\nops_message: hi", "has ops_message but no ops"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}\nundo_command: x", "its undo_command is generated"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}\npreview_command: x", "its preview_command is generated"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}\nchanges_system: false", "cannot be SAFE"),
    ("ops: []", "'ops' must be a non-empty list"),
    ("ops: {reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}}", "'ops' must be a non-empty list"),
    ("ops:\n  - reg_delete\n", "op 1: each op must be a mapping"),
    ("ops:\n  - {reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}, reg_set: {}}", "exactly one of"),
    ("ops:\n  - reg_copy: {path: 'HKLM\\SOFTWARE\\X', name: A}", "unknown op 'reg_copy'"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X'}", "reg_delete is missing name"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A, force: true}", "unknown field(s) force"),
    ("ops:\n  - reg_delete: 'HKLM\\SOFTWARE\\X'", "reg_delete must be a mapping"),
    ("ops:\n  - reg_delete: {path: 'HKLM:\\SOFTWARE\\X', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKEY_LOCAL_MACHINE\\SOFTWARE\\X', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKCC\\System\\X', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\*', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X[1]', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\a''b', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\$(calc)', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\\\X', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X\\', name: A}", "invalid registry path"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: \"a'b\"}", "invalid value name"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: ''}", "invalid value name"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: 5}", "invalid value name"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: REG_SZ, value: x}", "invalid registry type"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: DWord, value: 4294967296}", "invalid DWord value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: DWord, value: -1}", "invalid DWord value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: DWord, value: true}", "invalid DWord value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: DWord, value: '1'}", "invalid DWord value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: QWord, value: 18446744073709551616}", "invalid QWord value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: String, value: 1}", "invalid String value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: String, value: \"a\\nb\"}", "invalid String value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: MultiString, value: a}", "invalid MultiString value"),
    ("ops:\n  - reg_set: {path: 'HKLM\\SOFTWARE\\X', name: A, type: Binary, value: [1, 256]}", "invalid Binary value"),
    ("ops:\n  - service_start_type: {name: 'Diag Track', start_type: disabled}", "invalid service name"),
    ("ops:\n  - service_start_type: {name: 'MSSQL$X', start_type: disabled}", "invalid service name"),
    ("ops:\n  - service_start_type: {name: DiagTrack, start_type: auto}", "invalid start_type 'auto'"),
    ("ops:\n  - service_start_type: {name: DiagTrack, start_type: [disabled]}", "invalid start_type"),
    ("ops:\n  - task_state: {path: 'Microsoft\\Windows\\T', enabled: false}", "invalid task path"),
    ("ops:\n  - task_state: {path: '\\Microsoft\\*', enabled: false}", "invalid task path"),
    ("ops:\n  - task_state: {path: '\\Microsoft\\Windows\\', enabled: false}", "invalid task path"),
    ("ops:\n  - task_state: {path: '\\Microsoft\\T', enabled: 'false'}", "invalid enabled"),
    ("ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}\nops_message: \"a\\nb\"", "invalid ops_message"),
])
def test_loader_rejects_malformed_ops_with_a_clear_error(tmp_path, body, fragment):
    with pytest.raises(ModuleLoadError) as excinfo:
        _load(tmp_path, body)
    message = str(excinfo.value)
    assert fragment in message
    assert str(tmp_path / "actions.yaml") in message


def test_loader_error_names_the_action_and_the_op(tmp_path):
    body = "ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}\n  - reg_delete: {path: 'HKLM:\\X', name: A}"
    with pytest.raises(ModuleLoadError, match=r"action 'a1': op 2: invalid registry path 'HKLM:\\\\X'"):
        _load(tmp_path, body)


def test_loader_refuses_ops_on_a_safe_action(tmp_path):
    with pytest.raises(ModuleLoadError, match="cannot be SAFE"):
        _load(tmp_path, "ops:\n  - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: A}", risk="SAFE")


def test_loader_refuses_a_command_too_long_for_the_command_line(tmp_path):
    many = "".join(
        f"  - reg_set: {{path: 'HKLM\\SOFTWARE\\PortableFix\\Very Long Key Name Number {i}\\Deeper\\Still', "
        f"name: Value{i}, type: String, value: '{'x' * 200}'}}\n"
        for i in range(60)
    )
    with pytest.raises(ModuleLoadError, match="split them into several actions"):
        _load(tmp_path, "ops:\n" + many)


def test_load_all_modules_reports_a_malformed_ops_module_and_loads_the_rest(tmp_path):
    good = tmp_path / "m_good"
    bad = tmp_path / "m_bad"
    good.mkdir()
    bad.mkdir()
    (good / "actions.yaml").write_text(HEADER.replace("m_test", "m_good") + _action_yaml(VALID_OPS), encoding="utf-8")
    (bad / "actions.yaml").write_text(
        HEADER.replace("m_test", "m_bad")
        + _action_yaml("ops:\n  - service_start_type: {name: DiagTrack, start_type: sometimes}", action_id="b1"),
        encoding="utf-8",
    )
    modules, errors = load_all_modules(tmp_path)
    assert [m.module_id for m in modules] == ["m_good"]
    assert len(errors) == 1
    assert "m_bad" in errors[0] and "action 'b1': op 1: invalid start_type 'sometimes'" in errors[0]


def test_default_value_name_is_accepted(tmp_path):
    action = _load(tmp_path, "ops:\n  - reg_set: {path: 'HKCU\\Software\\X', name: '(default)', type: String, value: ''}")
    assert action.ops[0].name == "(default)"


# --- quoting -----------------------------------------------------------------

TRICKY = [
    "plain",
    "it's",
    "$env:USERNAME $(Remove-Item C:\\) `n @(1)",
    "'; Remove-Item -Recurse C:\\ ; '",
    "typographic \u2019 quote \u2018 and \u201a \u201b",
    "line\nbreak\r\nand\ttab",
    "Slovenčina: ľščťžýáíé",
    "",
]


def test_ps_str_never_leaves_an_interpolating_or_breakable_literal():
    for text in TRICKY:
        literal = ops.ps_str(text)
        if literal.startswith("'"):
            inner = literal[1:-1]
            assert inner.replace("''", "").count("'") == 0
            assert not any(c in literal for c in "\u2018\u2019\u201a\u201b\n\r")
        else:
            assert "FromBase64String" in literal


def test_ps_str_round_trips_in_powershell(tmp_path):
    expressions = "; ".join(
        f"[void]$out.Add([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]({ops.ps_str(t)}))))"
        for t in TRICKY
    )
    script = "$out = New-Object System.Collections.ArrayList; " + expressions + "; $out -join ','"
    result = subprocess.run(
        [powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    import base64

    decoded = [base64.b64decode(b).decode("utf-8") for b in result.stdout.strip().split(",")]
    assert decoded == TRICKY


def test_dword_and_qword_literals_use_the_signed_bit_pattern():
    command = ops.apply_script("a", _op_list(
        reg_set("HKLM\\SOFTWARE\\X", "A", "DWord", 0xFFFFFFFF),
        reg_set("HKLM\\SOFTWARE\\X", "B", "QWord", 2**64 - 1),
    ))
    assert "v = ([int32]'-1')" in command
    assert "v = ([int64]'-1')" in command


# --- parse check ---------------------------------------------------------------

# Target is Windows PowerShell 5.1: the scripts must parse, and none may use
# PS7-only syntax (??, ?., ternary, &&/||) that pwsh accepts but 5.1 rejects.
PARSE_CHECKER = (
    "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
    "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
    "Write-Output PARSE_DONE"
)

ALL_KINDS = [
    reg_set("HKLM\\SOFTWARE\\PortableFixTest", "D", "DWord", 7),
    reg_set("HKCU\\Software\\PortableFixTest", "Q", "QWord", 2**40),
    reg_set("HKU\\.DEFAULT\\Software\\PortableFixTest", "S", "String", "it's $x"),
    reg_set("HKCR\\PortableFixTest", "(default)", "ExpandString", "%SystemRoot%\\x"),
    reg_set("HKLM\\SOFTWARE\\PortableFixTest", "M", "MultiString", ["a", "b\u2019"]),
    reg_set("HKLM\\SOFTWARE\\PortableFixTest", "B", "Binary", [0, 255]),
    {"reg_delete": {"path": "HKLM\\SOFTWARE\\PortableFixTest", "name": "Old"}},
    {"service_start_type": {"name": "DiagTrack", "start_type": "automatic_delayed"}},
    {"task_state": {"path": "\\Root Task", "enabled": True}},
]


def _parse_errors(tmp_path, scripts: dict) -> list[str]:
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith("ERR")]


def test_generated_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    op_list = ops.parse_ops(ALL_KINDS)
    state = {
        "version": 1, "action": "a", "user": "PC\\t", "sid": TECH[1],
        "entries": [
            {"i": 0, "op": "reg_set", "key": op_list[0].path, "name": "D", "existed": True, "kind": "DWord", "value": -5, "missing_from": None},
            {"i": 1, "op": "reg_set", "key": op_list[1].path, "name": "Q", "existed": False, "kind": None, "value": None, "missing_from": "HKCU\\Software\\PortableFixTest"},
            {"i": 2, "op": "reg_set", "key": op_list[2].path, "name": "S", "existed": True, "kind": "MultiString", "value": ["x", "y\n'z"], "missing_from": None},
            {"i": 3, "op": "reg_set", "key": op_list[3].path, "name": "(default)", "existed": True, "kind": "Binary", "value": [1, 2], "missing_from": None},
            {"i": 4, "op": "reg_set", "key": op_list[4].path, "name": "M", "existed": True, "kind": "QWord", "value": -1, "missing_from": None},
            {"i": 5, "op": "reg_set", "key": op_list[5].path, "name": "B", "existed": True, "kind": "ExpandString", "value": "%TEMP%", "missing_from": None},
            {"i": 6, "op": "reg_delete", "key": op_list[6].path, "name": "Old", "existed": True, "kind": "String", "value": "o", "missing_from": None},
            {"i": 7, "op": "service_start_type", "name": "DiagTrack", "existed": True, "start": 3, "delayed": False},
            {"i": 8, "op": "task_state", "path": "\\Root Task", "existed": True, "enabled": False},
        ],
    }
    scripts = {
        "apply": ops.apply_script("a", op_list, "done"),
        "preview": ops.preview_script(op_list),
        "undo": ops.undo_script("a", op_list, state),
    }
    assert _parse_errors(tmp_path, scripts) == []
    # One line each, like every catalog command (one -Command argument).
    assert "\n" not in scripts["apply"] and "\n" not in scripts["preview"]


# --- round trips against the in-memory machine ---------------------------------

def _run_action(machine: Machine, action_id: str, op_list, state_path: Path, message: str = "", **env):
    command = ops.apply_script(action_id, op_list, message)
    return machine.run(command_with_state(command, state_path), **env)


def _undo(machine: Machine, action_id: str, op_list, state_path: Path, **env):
    step = ops.undo_step(action_id, op_list, state_path)
    assert step is not None
    return machine.run(step, **env)


@pytest.fixture
def state_path(tmp_path):
    return ops.state_file_path(tmp_path, "run1", "a1")


def test_state_file_lives_under_the_run_and_gets_a_fresh_name_per_run(tmp_path):
    first = ops.state_file_path(tmp_path, "run1", "tune_x")
    assert first == tmp_path / "Backups" / "run1" / "state" / "tune_x.json"
    first.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    second = ops.state_file_path(tmp_path, "run1", "tune_x")
    assert second.name == "tune_x-2.json"
    # No separator survives: the file always lands in the run's state folder.
    evil = ops.state_file_path(tmp_path, "run1", "..\\..\\evil/x")
    assert evil.parent == first.parent and evil.name == ".._.._evil_x.json"


def test_reg_set_new_value_round_trip_deletes_it_again(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"Keep": ("String", "k")}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "Level", "DWord", 3))
    result = _run_action(machine, "a1", op_list, state_path, message="Hotovo.")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKLM\\SOFTWARE\\App\\Level: absent -> 3 (DWord)" in result.stdout
    assert "Hotovo." in result.stdout and f"Previous state saved to {state_path}" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {"Keep": ("String", "k"), "Level": ("DWord", 3)}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["action"] == "a1" and state["sid"] is None
    assert state["entries"][0]["existed"] is False and state["entries"][0]["missing_from"] is None

    result = _undo(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Restored HKLM\\SOFTWARE\\App\\Level: deleted (it did not exist before)" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {"Keep": ("String", "k")}


def test_reg_set_restores_the_exact_previous_value_and_type(tmp_path, state_path):
    before = {
        "S": ("String", "1"), "E": ("ExpandString", "%SystemRoot%\\x"), "M": ("MultiString", ["a", "b"]),
        "M1": ("MultiString", ["only"]), "B": ("Binary", [0, 1, 255]), "Q": ("QWord", 2**40), "D": ("DWord", -1),
    }
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": dict(before)})
    op_list = _op_list(*(reg_set("HKLM\\SOFTWARE\\App", name, "DWord", 0) for name in before))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.values("HKLM\\SOFTWARE\\App") == {name: ("DWord", 0) for name in before}
    out = result.stdout
    assert "HKLM\\SOFTWARE\\App\\S: '1' (String) -> 0 (DWord)" in out
    # Unexpanded, as stored - never the expanded path.
    assert "HKLM\\SOFTWARE\\App\\E: '%SystemRoot%\\x' (ExpandString) -> 0 (DWord)" in out
    assert "HKLM\\SOFTWARE\\App\\B: hex:00,01,ff (Binary) -> 0 (DWord)" in out
    assert "HKLM\\SOFTWARE\\App\\D: 4294967295 (DWord) -> 0 (DWord)" in out
    assert 'HKLM\\SOFTWARE\\App\\M1: ["only"] (MultiString) -> 0 (DWord)' in out

    result = _undo(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.values("HKLM\\SOFTWARE\\App") == before


def test_reg_set_creates_missing_keys_and_undo_removes_exactly_those(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKCU\\Software\\Vendor": {}})
    op_list = _op_list(
        reg_set("HKCU\\Software\\Vendor\\PF\\Deep", "A", "DWord", 1),
        reg_set("HKCU\\Software\\Vendor\\PF", "B", "String", "x"),
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "(created key HKCU\\Software\\Vendor\\PF)" in result.stdout
    assert machine.values("HKCU\\Software\\Vendor\\PF\\Deep") == {"A": ("DWord", 1)}

    result = _undo(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    registry = machine.registry()
    assert "HKCU\\Software\\Vendor\\PF" not in registry and "HKCU\\Software\\Vendor\\PF\\Deep" not in registry
    assert registry["HKCU\\Software\\Vendor"] == {}
    assert "Removed key HKCU\\Software\\Vendor\\PF\\Deep (it did not exist before)" in result.stdout
    assert result.stdout.index("Deep (it did not exist") < result.stdout.index("Removed key HKCU\\Software\\Vendor\\PF (")


def test_undo_keeps_a_created_key_that_now_holds_foreign_data(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE": {}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\PF", "A", "DWord", 1))
    assert _run_action(machine, "a1", op_list, state_path).returncode == 0
    machine.run("New-ItemProperty -LiteralPath 'Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\PF' -Name Other -Value 5 -PropertyType DWord")
    result = _undo(machine, "a1", op_list, state_path)
    assert "Kept key HKLM\\SOFTWARE\\PF: it did not exist before, but now holds data" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\PF") == {"Other": ("DWord", 5)}


def test_default_value_round_trip(tmp_path, state_path):
    clsid = "HKCU\\Software\\Classes\\CLSID\\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\\InprocServer32"
    machine = Machine(tmp_path, registry={"HKCU\\Software\\Classes\\CLSID": {}})
    op_list = _op_list(reg_set(clsid, "(default)", "String", ""))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.values(clsid) == {"(default)": ("String", "")}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert set(machine.registry()) == {"HKCU\\Software", "HKCU\\Software\\Classes", "HKCU\\Software\\Classes\\CLSID"}


def test_reg_delete_round_trip_recreates_the_value(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"Blob": ("Binary", [9, 8]), "Keep": ("DWord", 1)}})
    op_list = _op_list(
        {"reg_delete": {"path": "HKLM\\SOFTWARE\\App", "name": "Blob"}},
        {"reg_delete": {"path": "HKLM\\SOFTWARE\\App", "name": "Missing"}},
        {"reg_delete": {"path": "HKLM\\SOFTWARE\\NoSuchKey", "name": "X"}},
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKLM\\SOFTWARE\\App\\Blob: hex:09,08 (Binary) -> absent" in result.stdout
    assert "HKLM\\SOFTWARE\\App\\Missing does not exist - nothing to delete." in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {"Keep": ("DWord", 1)}
    assert machine.values("HKLM\\SOFTWARE\\NoSuchKey") is None
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"Blob": ("Binary", [9, 8]), "Keep": ("DWord", 1)}
    # Undo never creates a key for a value that never existed.
    assert machine.values("HKLM\\SOFTWARE\\NoSuchKey") is None


def test_two_ops_on_the_same_value_undo_to_the_original(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"V": ("DWord", 5)}})
    op_list = _op_list(
        reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 6),
        reg_set("HKLM\\SOFTWARE\\App", "V", "String", "seven"),
    )
    assert _run_action(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("String", "seven")}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("DWord", 5)}


def test_later_op_on_the_same_value_sees_what_the_earlier_one_wrote(tmp_path, state_path):
    # Set, then set back: the second op must not be skipped against the
    # state captured before the first one ran.
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\T": {"X": ("DWord", 0)}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\T", "X", "DWord", 1), reg_set("HKLM\\SOFTWARE\\T", "X", "DWord", 0))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKLM\\SOFTWARE\\T\\X: 0 (DWord) -> 1 (DWord)" in result.stdout
    assert "HKLM\\SOFTWARE\\T\\X: 1 (DWord) -> 0 (DWord)" in result.stdout
    assert "unchanged" not in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\T") == {"X": ("DWord", 0)}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\T") == {"X": ("DWord", 0)}


def test_delete_after_set_of_a_new_value_deletes_it(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE": {}})
    op_list = _op_list(
        reg_set("HKLM\\SOFTWARE\\T", "X", "DWord", 1),
        {"reg_delete": {"path": "HKLM\\SOFTWARE\\T", "name": "X"}},
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HKLM\\SOFTWARE\\T\\X: absent -> 1 (DWord) (created key HKLM\\SOFTWARE\\T)" in result.stdout
    assert "HKLM\\SOFTWARE\\T\\X: 1 (DWord) -> absent" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\T") == {}
    # Undo goes back to before the run: no value, and the created key removed.
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\T") is None


def test_second_start_type_op_on_one_service_is_applied(tmp_path, state_path):
    machine = Machine(tmp_path, services={"WSearch": (3, False)})
    op_list = _op_list(
        {"service_start_type": {"name": "WSearch", "start_type": "disabled"}},
        {"service_start_type": {"name": "WSearch", "start_type": "manual"}},
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Service WSearch start type: disabled -> manual" in result.stdout
    assert machine.service("WSearch") == (3, False)


def test_running_an_action_twice_and_undoing_both_returns_the_first_state(tmp_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"V": ("DWord", 5)}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 6))
    steps = []
    for _ in range(2):
        path = ops.state_file_path(tmp_path, "run1", "a1")
        result = _run_action(machine, "a1", op_list, path)
        assert result.returncode == 0, result.stdout + result.stderr
        steps.append(ops.undo_step("a1", op_list, path))
    assert "is already 6 (DWord) - unchanged." in result.stdout
    # undo.ps1 order: newest step first.
    for step in reversed(steps):
        assert machine.run(step).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("DWord", 5)}


def test_already_set_value_is_left_alone(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"V": ("DWord", 6)}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 6))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0
    assert "HKLM\\SOFTWARE\\App\\V is already 6 (DWord) - unchanged." in result.stdout
    assert not [c for c in machine.calls() if c.startswith("New-")]


def test_service_start_type_round_trip_including_delayed_start(tmp_path, state_path):
    machine = Machine(tmp_path, services={"DiagTrack": (2, True), "WSearch": (3, False)})
    op_list = _op_list(
        {"service_start_type": {"name": "DiagTrack", "start_type": "disabled"}},
        {"service_start_type": {"name": "WSearch", "start_type": "automatic_delayed"}},
        {"service_start_type": {"name": "NotHere", "start_type": "disabled"}},
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Service DiagTrack start type: automatic (delayed) -> disabled" in result.stdout
    assert "Service WSearch start type: manual -> automatic (delayed)" in result.stdout
    assert "Service NotHere is not installed - skipped." in result.stdout
    assert machine.service("DiagTrack") == (4, False) and machine.service("WSearch") == (2, True)
    assert "sc.exe config WSearch start= delayed-auto" in machine.calls()

    result = _undo(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.service("DiagTrack") == (2, True) and machine.service("WSearch") == (3, False)
    assert not [c for c in machine.calls() if "NotHere" in c]


def test_service_change_that_fails_stops_the_action_with_undo_for_what_changed(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {}}, services={"DiagTrack": (3, False)})
    op_list = _op_list(
        reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1),
        {"service_start_type": {"name": "DiagTrack", "start_type": "disabled"}},
        reg_set("HKLM\\SOFTWARE\\App", "W", "DWord", 1),
    )
    result = _run_action(machine, "a1", op_list, state_path, PF_SC_EXIT=5)
    assert result.returncode == 1
    assert "FAILED: Service DiagTrack start type" in result.stdout and "exit code 5" in result.stdout
    assert "undo.ps1 restores what was changed so far" in result.stdout
    # Stopped at the failure: the third op never ran.
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("DWord", 1)}
    result = _undo(machine, "a1", op_list, state_path)
    assert machine.values("HKLM\\SOFTWARE\\App") == {}
    assert "FAILED to restore service DiagTrack" not in result.stdout  # nothing to fail: sc.exe works again


def test_service_undo_reads_the_start_type_back(tmp_path, state_path):
    machine = Machine(tmp_path, services={"DiagTrack": (2, False)})
    op_list = _op_list({"service_start_type": {"name": "DiagTrack", "start_type": "automatic_delayed"}})
    assert _run_action(machine, "a1", op_list, state_path).returncode == 0
    assert machine.service("DiagTrack") == (2, True)
    # sc.exe "succeeds" but leaves the delayed flag: undo must say so, not
    # claim the service is back to plain automatic.
    result = _undo(machine, "a1", op_list, state_path, PF_SC_KEEP_DELAYED=1)
    assert "FAILED to restore service DiagTrack" in result.stdout
    assert "Restored service DiagTrack" not in result.stdout
    result = _undo(machine, "a1", op_list, state_path)
    assert "Restored service DiagTrack start type: automatic" in result.stdout
    assert "FAILED" not in result.stdout
    assert machine.service("DiagTrack") == (2, False)


def test_task_state_round_trip(tmp_path, state_path):
    machine = Machine(tmp_path, tasks={"\\Microsoft\\Windows\\CEIP\\Consolidator": "Ready", "\\Off": "Disabled"})
    op_list = _op_list(
        {"task_state": {"path": "\\Microsoft\\Windows\\CEIP\\Consolidator", "enabled": False}},
        {"task_state": {"path": "\\Off", "enabled": True}},
        {"task_state": {"path": "\\Microsoft\\Gone", "enabled": False}},
    )
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Task \\Microsoft\\Windows\\CEIP\\Consolidator: enabled -> disabled" in result.stdout
    assert "Task \\Microsoft\\Gone does not exist - skipped." in result.stdout
    assert machine.tasks() == {"\\Microsoft\\Windows\\CEIP\\Consolidator": "Disabled", "\\Off": "Ready"}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.tasks() == {"\\Microsoft\\Windows\\CEIP\\Consolidator": "Ready", "\\Off": "Disabled"}


def test_task_scheduler_error_other_than_not_found_changes_nothing(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {}}, tasks={"\\T": "Ready"})
    op_list = _op_list(
        reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1),
        {"task_state": {"path": "\\T", "enabled": False}},
    )
    result = _run_action(machine, "a1", op_list, state_path, PF_TASK_BROKEN=1)
    assert result.returncode == 1
    assert "Could not read the current state" in result.stdout and "Nothing was changed." in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {} and not state_path.exists()
    assert ops.undo_step("a1", op_list, state_path) is None


def test_failed_write_keeps_the_state_so_undo_restores_the_part_that_changed(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"A": ("DWord", 0), "B": ("DWord", 0)}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "A", "DWord", 1), reg_set("HKLM\\SOFTWARE\\App", "B", "DWord", 1))
    result = _run_action(machine, "a1", op_list, state_path, PF_FAIL_NAME="B")
    assert result.returncode == 1
    assert "FAILED: HKLM\\SOFTWARE\\App\\B" in result.stdout and "HRESULT 0x80070005" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {"A": ("DWord", 1), "B": ("DWord", 0)}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"A": ("DWord", 0), "B": ("DWord", 0)}


def test_command_without_a_state_file_refuses_to_change_anything(tmp_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    result = machine.run(ops.apply_script("a1", op_list))
    assert result.returncode == 1
    assert "No state file was given" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {} and machine.calls() == []


def test_state_file_that_cannot_be_written_refuses_to_change_anything(tmp_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {}})
    blocker = tmp_path / "Backups"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    result = _run_action(machine, "a1", op_list, blocker / "run1" / "state" / "a1.json")
    assert result.returncode == 1
    assert "Could not save the previous state" in result.stdout and "Nothing was changed." in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {}


def test_value_of_a_type_that_cannot_be_restored_changes_nothing(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"V": ("Unknown", 1)}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "W", "DWord", 1), reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 1
    assert "has registry type Unknown, which could not be restored exactly" in result.stdout
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("Unknown", 1)}
    assert not state_path.exists()


def test_values_with_quotes_and_subexpressions_stay_literal(tmp_path, state_path):
    evil = "'; Remove-Item -Recurse C:\\; $(throw 'boom') \u2019; '"
    machine = Machine(tmp_path, registry={"HKLM\\SOFTWARE\\App": {"V": ("String", "x\n$(throw 'old')\u2018")}})
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "String", evil))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("String", evil)}
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("String", "x\n$(throw 'old')\u2018")}


# --- HKCU and the user it belongs to -----------------------------------------------

def test_hkcu_ops_record_the_user_and_undo_skips_them_for_another_user(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKCU\\Software\\App": {"V": ("DWord", 1)}, "HKLM\\SOFTWARE\\App": {"V": ("DWord", 1)}})
    op_list = _op_list(reg_set("HKCU\\Software\\App", "V", "DWord", 2), reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 2))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"HKCU = registry hive of {TECH[0]} ({TECH[1]})" in result.stdout
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert (state["user"], state["sid"]) == TECH

    machine.who = OTHER
    result = _undo(machine, "a1", op_list, state_path)
    assert f"Skipped the HKCU values of {TECH[0]} ({TECH[1]})" in result.stdout
    assert machine.values("HKCU\\Software\\App") == {"V": ("DWord", 2)}
    assert machine.values("HKLM\\SOFTWARE\\App") == {"V": ("DWord", 1)}

    machine.who = TECH
    assert _undo(machine, "a1", op_list, state_path).returncode == 0
    assert machine.values("HKCU\\Software\\App") == {"V": ("DWord", 1)}


def test_hkcu_ops_refuse_without_an_identity(tmp_path, state_path):
    machine = Machine(tmp_path, registry={"HKCU\\Software\\App": {}}, who=None)
    op_list = _op_list(reg_set("HKCU\\Software\\App", "V", "DWord", 2))
    result = _run_action(machine, "a1", op_list, state_path)
    assert result.returncode == 1
    assert "Could not identify the user" in result.stdout and "Nothing was changed." in result.stdout
    assert machine.values("HKCU\\Software\\App") == {} and not state_path.exists()


# --- preview -----------------------------------------------------------------------

def test_preview_describes_every_change_and_changes_nothing(tmp_path):
    machine = Machine(
        tmp_path, registry={"HKCU\\Software\\App": {"V": ("DWord", 0)}},
        services={"DiagTrack": (2, False)}, tasks={"\\T": "Ready"},
    )
    op_list = _op_list(
        reg_set("HKCU\\Software\\App", "V", "DWord", 2),
        reg_set("HKCU\\Software\\New\\Key", "N", "String", "x"),
        {"reg_delete": {"path": "HKCU\\Software\\App", "name": "Gone"}},
        {"service_start_type": {"name": "DiagTrack", "start_type": "manual"}},
        {"task_state": {"path": "\\T", "enabled": False}},
    )
    before = machine.registry()
    result = machine.run(ops.preview_script(op_list))
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Would change HKCU\\Software\\App\\V from 0 (DWord) to 2 (DWord)" in out
    assert "Would change HKCU\\Software\\New\\Key\\N from absent to 'x' (String) (would create key HKCU\\Software\\New)" in out
    assert "Would skip: HKCU\\Software\\App\\Gone does not exist - nothing to delete." in out
    assert "Would change Service DiagTrack start type from automatic to manual" in out
    assert "Would change Task \\T from enabled to disabled" in out
    assert machine.registry() == before and machine.tasks() == {"\\T": "Ready"}
    assert [c for c in machine.calls() if not c.startswith("Get-ScheduledTask")] == []


def test_preview_and_capture_never_use_writing_commands():
    op_list = ops.parse_ops(ALL_KINDS)
    preview = ops.preview_script(op_list)
    for verb in ("New-Item", "New-ItemProperty", "Remove-", "Set-", "sc.exe", "Enable-ScheduledTask",
                 "Disable-ScheduledTask", "WriteAllText"):
        assert verb not in preview.replace("pfApply", ""), verb


def test_generated_scripts_decide_nothing_by_localized_text():
    op_list = ops.parse_ops(ALL_KINDS)
    for script in (ops.apply_script("a", op_list), ops.preview_script(op_list)):
        for forbidden in ("-match", "Select-String", "Out-String", "whoami", "Get-Service"):
            assert forbidden not in script, forbidden


# --- undo from the state file ---------------------------------------------------------

def _state_for(tmp_path, machine_registry, op_list, state_path):
    machine = Machine(tmp_path, registry=machine_registry)
    assert _run_action(machine, "a1", op_list, state_path).returncode == 0
    return json.loads(state_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("tamper,fragment", [
    (lambda s: s.update(version=2), "unknown state file format"),
    (lambda s: s.update(action="other"), "belongs to 'other'"),
    (lambda s: s["entries"].pop(), "one entry per op"),
    (lambda s: s["entries"][0].update(key="HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"), "another value"),
    (lambda s: s["entries"][0].update(name="Other"), "another value"),
    (lambda s: s["entries"][0].update(op="reg_delete"), "does not match op"),
    (lambda s: s["entries"][0].update(i=1), "does not match op"),
    (lambda s: s["entries"][0].update(existed="yes"), "invalid 'existed'"),
    (lambda s: s["entries"][0].update(existed=True, kind="Link", value=1), "unknown registry type"),
    (lambda s: s["entries"][0].update(existed=True, kind="DWord", value="1"), "invalid DWord"),
    (lambda s: s["entries"][0].update(existed=True, kind="DWord", value=2**32), "invalid DWord"),
    (lambda s: s["entries"][0].update(existed=True, kind="Binary", value=[300]), "invalid Binary"),
    (lambda s: s["entries"][0].update(existed=True, kind="String", value=None), "invalid String"),
    (lambda s: s["entries"][0].update(missing_from="HKLM\\SOFTWARE\\Microsoft"), "invalid missing_from"),
    (lambda s: s["entries"][0].update(missing_from="HKLM"), "invalid missing_from"),
    (lambda s: s["entries"][0].update(missing_from="HKLM\\SOFTWARE\\AppX"), "invalid missing_from"),
    (lambda s: s["entries"][0].update(existed=True, kind="DWord", value=1), "invalid missing_from"),
])
def test_undo_refuses_a_state_that_does_not_belong_to_the_action(tmp_path, state_path, tamper, fragment):
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App\\Sub", "V", "DWord", 1))
    state = _state_for(tmp_path, {"HKLM\\SOFTWARE": {}}, op_list, state_path)
    assert state["entries"][0]["missing_from"] == "HKLM\\SOFTWARE\\App"
    tamper(state)
    with pytest.raises(ops.OpsStateError, match=fragment.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")):
        ops.undo_script("a1", op_list, state)


def test_undo_refuses_a_bad_sid_and_a_broken_file(tmp_path, state_path):
    op_list = _op_list(reg_set("HKCU\\Software\\App", "V", "DWord", 1))
    state = _state_for(tmp_path, {"HKCU\\Software\\App": {}}, op_list, state_path)
    state["sid"] = "S-1-5'; Remove-Item C:\\"
    with pytest.raises(ops.OpsStateError, match="invalid user SID"):
        ops.undo_script("a1", op_list, state)
    state_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ops.OpsStateError, match="could not be read"):
        ops.undo_step("a1", op_list, state_path)


def test_undo_step_is_none_without_a_state_file(tmp_path):
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    assert ops.undo_step("a1", op_list, None) is None
    assert ops.undo_step("a1", op_list, tmp_path / "missing.json") is None


def test_undo_takes_keys_and_names_only_from_the_catalog(tmp_path, state_path):
    # A tampered value can only change what the action's own value is set
    # back to - it is written as a literal, never run.
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    state = _state_for(tmp_path, {"HKLM\\SOFTWARE\\App": {"V": ("String", "a")}}, op_list, state_path)
    state["entries"][0]["value"] = "'; Remove-Item C:\\ -Recurse; '"
    state["user"] = "x'\n; Remove-Item C:\\"
    step = ops.undo_script("a1", op_list, state, state_path)
    assert "-Value '''; Remove-Item C:\\ -Recurse; '''" in step
    assert "Registry::HKEY_LOCAL_MACHINE\\SOFTWARE\\App'" in step
    assert all(line.startswith(("#", "&", "}", "$", "try", "if")) for line in step.splitlines())


def test_undo_header_names_the_action_and_state_file(tmp_path, state_path):
    op_list = _op_list(reg_set("HKLM\\SOFTWARE\\App", "V", "DWord", 1))
    _state_for(tmp_path, {"HKLM\\SOFTWARE\\App": {}}, op_list, state_path)
    step = ops.undo_step("a1", op_list, state_path)
    assert step.splitlines()[0] == f"# a1: restores the state captured just before it ran ({state_path})"


def test_describe_lists_the_ops_for_the_detail_panel():
    text = ops.describe(ops.parse_ops(ALL_KINDS[:1] + ALL_KINDS[-3:]))
    assert "reg_set HKLM\\SOFTWARE\\PortableFixTest\\D = 7 (DWord)" in text
    assert "service_start_type DiagTrack -> automatic_delayed" in text
    assert "task_state \\Root Task -> enabled" in text
    # Only the op lines: the (translated) note on undo is the GUI's.
    assert len(text.splitlines()) == 4


def test_executor_passes_the_state_file_path_as_a_literal(tmp_path):
    from portablefix.executor import build_execution_plan

    weird = tmp_path / "it's $HOME \u2019"
    plan = build_execution_plan("Write-Output x", dry_run=False, ops_state=weird)
    assert plan.argv[-1].startswith(f"$__pfOpsState = {ops.ps_str(str(weird))}; ")
    plain = build_execution_plan("Write-Output x", dry_run=False)
    assert "__pfOpsState" not in plain.argv[-1]
