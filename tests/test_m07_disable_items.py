"""Research G04 step 2: autoruns_disable_items - a per-item action (G05) that
disables the Run values, scheduled tasks and services the technician picks,
each with an exact inverse in undo.ps1.

Everything runs in the real powershell.exe. Run values live in a throw-away
key under HKCU\\Software\\PortableFixTest, reached through $__pfUserHive
exactly as the signed-in user's hive would be; scheduled tasks, the services
key and sc.exe are shadowed by functions (functions win command lookup) and
the script exits 97 unless every name really resolves to the stub, so a test
never disables anything real. HKLM Run keys are only read."""

import json
import os
import shutil
import subprocess
import uuid
import winreg
from pathlib import Path
from types import SimpleNamespace

import pytest

from portablefix import action_service, items
from portablefix.executor import PlanRun, build_execution_plan
from portablefix.models import RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m07_autoruns" / "actions.yaml"
STUB_GUARD_EXIT = 97
SVC_ROOT = "Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services"


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action():
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == "autoruns_disable_items")


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _pfh(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()[:12]


@pytest.fixture
def hive():
    """A fake user hive: HKCU\\Software\\PortableFixTest\\<random>."""
    _powershell_or_skip()
    rel = f"Software\\PortableFixTest\\{uuid.uuid4().hex}"
    run = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rel + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Run")
    winreg.SetValueEx(run, "Contoso Tray", 0, winreg.REG_EXPAND_SZ, '"%ProgramFiles%\\Contoso\\tray.exe" --min')
    winreg.SetValueEx(run, "Weird*Name[1]", 0, winreg.REG_SZ, "C:\\w.exe")
    winreg.SetValueEx(run, "Weird", 0, winreg.REG_SZ, "C:\\keep-me.exe")
    winreg.CloseKey(run)
    yield {"rel": rel, "hive": "Registry::HKEY_CURRENT_USER\\" + rel}
    _delete_tree(winreg.HKEY_CURRENT_USER, rel)


def _delete_tree(root, rel):
    try:
        key = winreg.OpenKey(root, rel, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    with key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(root, rel + "\\" + child)
    winreg.DeleteKey(root, rel)


def _values(rel: str) -> dict:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, rel)
    except FileNotFoundError:
        return {}
    with key:
        result = {}
        for index in range(winreg.QueryInfoKey(key)[1]):
            name, data, kind = winreg.EnumValue(key, index)
            result[name] = (data, kind)
        return result


def _stubs(log: Path, tasks, services) -> str:
    """tasks: [(TaskPath, TaskName, State, Execute)]; services: {name: {Start, Type, ImagePath, ...}}."""
    task_objs = ", ".join(
        f"[pscustomobject]@{{ TaskPath = {_q(tp)}; TaskName = {_q(tn)}; State = {_q(st)}; "
        f"Actions = @([pscustomobject]@{{ Execute = {_q(ex)}; Arguments = '/bg' }}) }}"
        for tp, tn, st, ex in tasks
    )
    svc = "; ".join(
        f"$FakeSvc[{_q(name)}] = [pscustomobject]@{{ " + "; ".join(
            f"{k} = {v if isinstance(v, int) else _q(v)}" for k, v in props.items()) + " }"
        for name, props in services.items()
    )
    logq = _q(str(log))
    lines = [
        "$FakeSvc = @{}", svc or "$null",
        f"function Get-ScheduledTask {{ [CmdletBinding()] param() @({task_objs}) }}",
        "function Disable-ScheduledTask { [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)] $InputObject) "
        f"process {{ Microsoft.PowerShell.Management\\Add-Content -LiteralPath {logq} -Value ('disable ' + $InputObject.TaskPath + $InputObject.TaskName) }} }}",
        "function Enable-ScheduledTask { [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)] $InputObject) "
        f"process {{ Microsoft.PowerShell.Management\\Add-Content -LiteralPath {logq} -Value ('enable ' + $InputObject.TaskPath + $InputObject.TaskName) }} }}",
        "function Get-ChildItem { [CmdletBinding()] param([string] $LiteralPath) "
        f"if ($LiteralPath -ne {_q(SVC_ROOT)}) {{ exit 96 }}; @($FakeSvc.Keys | Sort-Object | ForEach-Object {{ [pscustomobject]@{{ PSChildName = $_ }} }}) }}",
        "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath) "
        f"$pre = {_q(SVC_ROOT + chr(92))}; if (-not $LiteralPath.StartsWith($pre)) {{ exit 96 }}; $FakeSvc[$LiteralPath.Substring($pre.Length)] }}",
        "function sc.exe { Microsoft.PowerShell.Management\\Add-Content -LiteralPath " + logq + " -Value ('sc ' + ($args -join ' ')); $global:LASTEXITCODE = 0 }",
    ]
    names = ["Get-ScheduledTask", "Disable-ScheduledTask", "Enable-ScheduledTask", "Get-ChildItem", "Get-ItemProperty", "sc.exe"]
    lines.append(
        "foreach ($n in " + ", ".join(_q(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    return "; ".join(lines)


TASKS = [("\\Contoso\\", "Updater", "Ready", "C:\\c\\up.exe"), ("\\Microsoft\\Windows\\X\\", "MsTask", "Ready", "x.exe"),
         ("\\Contoso\\", "Old", "Disabled", "old.exe")]
SERVICES = {
    "ContosoSvc": {"Start": 2, "Type": 16, "ImagePath": "C:\\Program Files\\Contoso\\svc.exe", "DisplayName": "Contoso Service",
                   "DelayedAutostart": 1},
    "WinSvc": {"Start": 2, "Type": 32, "ImagePath": "%SystemRoot%\\System32\\svchost.exe -k netsvcs"},
    "ManualSvc": {"Start": 3, "Type": 16, "ImagePath": "C:\\m.exe"},
    "Driver": {"Start": 2, "Type": 1, "ImagePath": "C:\\d.sys"},
}


def _run(command: str, hive, tmp_path: Path, items_file: Path | None = None):
    log = tmp_path / "calls.log"
    script = _stubs(log, TASKS, SERVICES) + f"; $__pfUserHive = {_q(hive['hive'])}; " + command
    run = PlanRun(build_execution_plan(script, dry_run=False, items_file=items_file))
    code = run.run()
    assert code not in (STUB_GUARD_EXIT, 96), run.captured_output
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return code, run, calls


def _target(hive):
    # undo.ps1 must restore into the same user's hive: the per-item undo
    # carries the target user's prelude, here pointing at the fake hive.
    return SimpleNamespace(prelude=lambda: f"$__pfUserHive = {_q(hive['hive'])}; ")


def _listed(hive, tmp_path):
    code, run, _ = _run(_action().items_command, hive, tmp_path)
    assert code == 0, run.captured_output
    return {i.label: i for i in items.parse_items(run.captured_output)}


def test_disable_items_is_a_moderate_per_item_action_with_undo():
    action = _action()
    assert action.risk == RiskLevel.MODERATE
    assert action.items_command and action.undo_command and action.has_undo
    # The listing only reads.
    for verb in ("SetValue", "DeleteValue", "Disable-ScheduledTask", "sc.exe", "CreateSubKey($r)"):
        assert verb not in action.items_command.replace("return $b.CreateSubKey($r)", ""), verb


def test_listing_offers_user_run_values_third_party_tasks_and_services(hive, tmp_path):
    listed = _listed(hive, tmp_path)
    user = {label: i for label, i in listed.items() if label.startswith("Run (user)")}
    assert set(user) == {"Run (user): Contoso Tray", "Run (user): Weird*Name[1]", "Run (user): Weird"}
    tray = user["Run (user): Contoso Tray"]
    # Stable id over source and name; the data is shown unexpanded.
    assert tray.id == "run-" + _pfh("user|Contoso Tray")
    assert tray.detail == '"%ProgramFiles%\\Contoso\\tray.exe" --min'
    assert listed["Task: \\Contoso\\Updater"].id == "task-" + _pfh("\\Contoso\\Updater")
    assert listed["Task: \\Contoso\\Updater"].detail == "C:\\c\\up.exe /bg"
    assert listed["Service: Contoso Service (ContosoSvc)"].id == "svc-" + _pfh("ContosoSvc")
    labels = set(listed)
    for absent in ("Task: \\Microsoft\\Windows\\X\\MsTask", "Task: \\Contoso\\Old"):
        assert absent not in labels
    assert not any("WinSvc" in label or "ManualSvc" in label or "Driver" in label for label in labels)


def test_disable_and_undo_round_trip_restores_exactly_what_was_there(hive, tmp_path):
    action = _action()
    listed = _listed(hive, tmp_path)
    picked = [listed[label].id for label in (
        "Run (user): Contoso Tray", "Run (user): Weird*Name[1]", "Task: \\Contoso\\Updater",
        "Service: Contoso Service (ContosoSvc)")]
    items_file = items.write_items_file(tmp_path / "ids.txt", picked)
    run_rel = hive["rel"] + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
    off_rel = hive["rel"] + "\\Software\\PortableFix\\AutorunsDisabled\\Run"
    before = _values(run_rel)

    code, run, calls = _run(action.command, hive, tmp_path, items_file)
    assert code == 0, run.captured_output
    # Moved, with the value type kept - and the wildcard-looking name did
    # not take "Weird" with it.
    assert _values(run_rel) == {"Weird": before["Weird"]}
    assert _values(off_rel) == {"Contoso Tray": before["Contoso Tray"], "Weird*Name[1]": before["Weird*Name[1]"]}
    assert calls == ["disable \\Contoso\\Updater", "sc config ContosoSvc start= disabled"]
    outcome = action_service.undo_outcome(
        action, code, language="en", item_ids=picked, payloads=run.pfjson, target_user=_target(hive),
    )
    assert len(outcome.steps) == 4 and outcome.irreversible == ""

    (tmp_path / "calls.log").unlink()
    undo_script = "\n".join(outcome.steps)
    code, undo_run, calls = _run(undo_script, hive, tmp_path)
    assert code == 0, undo_run.captured_output
    assert not any("FAILED" in line for line in undo_run.captured_output), undo_run.captured_output
    assert _values(run_rel) == before
    assert _values(off_rel) == {}
    assert sorted(calls) == ["enable \\Contoso\\Updater", "sc config ContosoSvc start= delayed-auto"]


def test_an_item_gone_since_the_list_is_skipped_and_nothing_else_changes(hive, tmp_path):
    items_file = items.write_items_file(tmp_path / "ids.txt", ["run-000000000000"])
    code, run, calls = _run(_action().command, hive, tmp_path, items_file)
    assert code == 0
    assert any("Not found any more" in line for line in run.captured_output)
    assert run.pfjson == [] and calls == []


def test_undo_refuses_a_record_that_does_not_match_its_item_id(hive, tmp_path):
    action = _action()
    listed = _listed(hive, tmp_path)
    tray = listed["Run (user): Contoso Tray"].id
    code, run, _ = _run(action.command, hive, tmp_path, items.write_items_file(tmp_path / "ids.txt", [tray]))
    assert code == 0
    # A tampered record pointing the undo at another value.
    forged = [{"undo": {"id": tray, "prior": {"src": "user", "name": "Weird"}}}]
    [step] = action_service.undo_outcome(
        action, 0, language="en", item_ids=[tray], payloads=forged, target_user=_target(hive),
    ).steps
    code, undo_run, _ = _run(step, hive, tmp_path)
    assert any("does not match the item id" in line for line in undo_run.captured_output)
    run_rel = hive["rel"] + "\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"
    assert "Contoso Tray" not in _values(run_rel)
