"""G13: the m03 disk_health_verdict script (run against stubs) and the Qt-free
probe/parser the pre-flight gate uses."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import disk_health
from portablefix.disk_health import FAILING, OK, UNKNOWN, WARNING, DiskVerdict
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m03_disk" / "actions.yaml"
STUB_GUARD_EXIT = 97
STUBBED = ["Get-PhysicalDisk", "Get-Partition", "Get-CimInstance", "Get-StorageReliabilityCounter"]


def _command() -> str:
    return next(a.command for a in load_module(CATALOG_PATH).actions if a.id == disk_health.ACTION_ID)


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps(value) -> str:
    if value is None:
        return "$null"
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "@(" + ", ".join(_ps(v) for v in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def _obj(values: dict) -> str:
    return "[pscustomobject]@{ " + "; ".join(f"{k} = {_ps(v)}" for k, v in values.items()) + " }"


def _disk(device_id="0", name="WDC WD10EZEX", health=0, opstatus=(2,), media=3, bus=11, **counters):
    return {
        "disk": {"DeviceId": device_id, "FriendlyName": name, "HealthStatus": health,
                 "OperationalStatus": list(opstatus), "MediaType": media, "BusType": bus, "Size": 1000204886016},
        "counters": counters or None,
        "pnp": f"SCSI\\DISK&VEN_WDC&PROD_{device_id}\\4&1A2B3C&0&00000{device_id}",
    }


def _run(disks, system_disk="0", predict=None, wmi_denied=False, counters_denied=False, no_disks=False):
    """predict: {device_id: bool} for MSStorageDriver_FailurePredictStatus;
    the key "orphan" adds an entry no Win32_DiskDrive matches."""
    predict = predict or {}
    physical = "@()" if no_disks else "@(" + ", ".join(_obj(d["disk"]) for d in disks) + ")"
    drives = "@(" + ", ".join(
        _obj({"Index": int(d["disk"]["DeviceId"]), "PNPDeviceID": d["pnp"]}) for d in disks
    ) + ")"
    fp_entries = []
    for key, value in predict.items():
        if key == "orphan":
            instance = "USBSTOR\\DISK&VEN_X\\ORPHAN_0"
        else:
            instance = next(d["pnp"] for d in disks if d["disk"]["DeviceId"] == key) + "_0"
        # Upper vs lower case on purpose: WMI does not keep PNPDeviceID's case.
        fp_entries.append(_obj({"InstanceName": instance.lower(), "PredictFailure": value, "Reason": 0}))
    fps = "@(" + ", ".join(fp_entries) + ")"
    denied = "throw [System.UnauthorizedAccessException]::new('Prístup bol odmietnutý.')"
    counter_cases = " ".join(
        f"{_ps(d['disk']['DeviceId'])} {{ {_obj(d['counters'])} }}" for d in disks if d["counters"]
    )
    stubs = [
        "function Get-PhysicalDisk { [CmdletBinding()] param() " + physical + " }",
        "function Get-Partition { [CmdletBinding()] param([string] $DriveLetter) "
        + (f"if ($DriveLetter -eq 'C') {{ [pscustomobject]@{{ DiskNumber = {system_disk} }} }} "
           "else { throw 'Nenájdené.' }" if system_disk is not None else "throw 'Oddiel sa nenašiel.'") + " }",
        "function Get-CimInstance { [CmdletBinding()] param([string] $Namespace, [string] $ClassName) "
        "if ($ClassName -eq 'Win32_DiskDrive') { " + drives + " } "
        "elseif ($ClassName -eq 'MSStorageDriver_FailurePredictStatus' -and $Namespace -eq 'root\\wmi') { "
        + (denied if wmi_denied else fps) + " } else { throw ('unexpected class ' + $ClassName) } }",
        "function Get-StorageReliabilityCounter { [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)] $PhysicalDisk) "
        "process { " + (denied if counters_denied else f"switch ([string]$PhysicalDisk.DeviceId) {{ {counter_cases} default {{ }} }}")
        + " } }",
    ]
    guard = (
        "foreach ($n in " + ", ".join(_ps(n) for n in STUBBED) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    # SystemDrive is set inside the script, never in the child environment.
    script = "; ".join(["[Console]::OutputEncoding=[Text.Encoding]::UTF8", "$env:SystemDrive = 'C:'"] + stubs + [guard, _command()])
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system cmdlet was not stubbed - refusing to run the real one"
    return result


def _only(result) -> DiskVerdict:
    verdicts = disk_health.parse_verdicts(result.stdout)
    assert len(verdicts) == 1, result.stdout + result.stderr
    return verdicts[0]


# --- the catalog script, one test per verdict ------------------------------


def test_healthy_disk_is_ok_and_shows_every_value():
    result = _run([_disk(Wear=3, Temperature=38, ReadErrorsUncorrected=0, PowerOnHours=1234)], predict={"0": False})
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = _only(result)
    assert (verdict.disk, verdict.status, verdict.system, verdict.reasons) == ("0", OK, True, ())
    assert verdict.name == "WDC WD10EZEX"
    assert "MediaType: 3 | BusType: 11 | Size: 932 GB" in result.stdout
    assert "HealthStatus: 0 | OperationalStatus: 2 | PredictFailure: False" in result.stdout
    assert "Wear: 3 % | Temperature: 38 C | ReadErrorsUncorrected: 0 | PowerOnHours: 1234" in result.stdout


def test_enum_names_are_read_like_numbers():
    # The Storage module may hand back enum names; they are code
    # identifiers, identical on every Windows language.
    result = _run([_disk(health="Warning", opstatus=("OK",))])
    assert _only(result).status == WARNING
    assert _only(result).reasons == ("health_warning",)
    result = _run([_disk(health="Healthy", opstatus=("Predictive Failure",))])
    assert _only(result).status == FAILING
    assert _only(result).reasons == ("opstatus_predictive_failure",)


@pytest.mark.parametrize("disk, reason", [
    (_disk(health=2), "health_unhealthy"),
    (_disk(opstatus=(2, 6)), "opstatus_error"),
    (_disk(opstatus=(7,)), "opstatus_non_recoverable_error"),
])
def test_unhealthy_or_error_status_is_failing(disk, reason):
    assert _only(_run([disk])).status == FAILING
    assert reason in _only(_run([disk])).reasons


def test_smart_predict_failure_is_failing_even_when_windows_says_healthy():
    result = _run([_disk(Wear=0, ReadErrorsUncorrected=0)], predict={"0": True})
    verdict = _only(result)
    assert verdict.status == FAILING and verdict.reasons == ("predict_failure",)
    assert "PredictFailure: True" in result.stdout


@pytest.mark.parametrize("disk, reason", [
    (_disk(health=1), "health_warning"),
    (_disk(opstatus=(3,)), "opstatus_degraded"),
    (_disk(ReadErrorsUncorrected=12), "read_errors_uncorrected=12"),
    (_disk(media=4, bus=17, Wear=93), "wear=93"),
])
def test_early_signs_are_warning(disk, reason):
    verdict = _only(_run([disk]))
    assert verdict.status == WARNING and verdict.reasons == (reason,)


def test_unreadable_health_is_unknown_and_missing_rights_are_explained():
    # A USB bridge or VM: no health, no counters, no failure prediction.
    result = _run([_disk(health=5)], wmi_denied=True, counters_denied=True)
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = _only(result)
    assert verdict.status == UNKNOWN and verdict.reasons == ("health_unknown",)
    assert "PredictFailure (root\\wmi): not readable" in result.stdout
    assert "Reliability counters: not readable" in result.stdout
    assert "PredictFailure: n/a" in result.stdout


def test_no_readable_disk_exits_1_with_an_unknown_verdict():
    result = _run([], no_disks=True)
    assert result.returncode == 1
    assert _only(result).status == UNKNOWN


def test_one_verdict_per_disk_and_the_system_disk_is_marked():
    result = _run([_disk("0", "Samsung SSD 980", media=4, bus=17), _disk("1", "USB Flash", health=2, bus=7)],
                  system_disk="0")
    verdicts = disk_health.parse_verdicts(result.stdout)
    assert [(v.disk, v.status, v.system) for v in verdicts] == [("0", OK, True), ("1", FAILING, False)]
    assert disk_health.worst(disk_health.gate_disks(verdicts)) == OK


def test_unknown_system_disk_marks_none():
    result = _run([_disk("0"), _disk("1")], system_disk=None)
    assert [v.system for v in disk_health.parse_verdicts(result.stdout)] == [False, False]


def test_failure_prediction_not_tied_to_a_disk_still_gets_a_failing_verdict():
    result = _run([_disk("0")], predict={"0": False, "orphan": True})
    verdicts = disk_health.parse_verdicts(result.stdout)
    assert [(v.disk, v.status) for v in verdicts] == [("0", OK), ("?", FAILING)]
    # It may be the system disk - the gate keeps it.
    assert disk_health.worst(disk_health.gate_disks(verdicts)) == FAILING


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


def test_m03_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in load_module(CATALOG_PATH).actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


def test_verdict_script_reads_no_localized_text():
    # Only numeric codes and enum identifiers decide - no -match/-like on
    # the output of a tool or cmdlet.
    command = _command()
    for forbidden in ("-match", "-like", "Select-String", "Out-String"):
        assert forbidden not in command


# --- Qt-free parser and probe ----------------------------------------------

SAMPLE = """Disk 0: Samsung SSD 980 | system disk: yes | MediaType: 4 | BusType: 17 | Size: 932 GB
VERDICT: disk=0 system=yes status=WARNING reasons=wear=93,read_errors_uncorrected=2 name=Samsung SSD 980
VERDICT: disk=1 system=no status=OK reasons=- name=-
VERDICT: something unexpected
"""


def test_parse_verdicts_reads_only_well_formed_lines():
    assert disk_health.parse_verdicts(SAMPLE) == [
        DiskVerdict("0", WARNING, ("wear=93", "read_errors_uncorrected=2"), "Samsung SSD 980", True),
        DiskVerdict("1", OK, (), "", False),
    ]
    assert disk_health.parse_verdicts("") == []


def test_worst_and_gate_disks():
    sys_ok = DiskVerdict("0", OK, system=True)
    other_bad = DiskVerdict("1", FAILING)
    assert disk_health.worst([]) == UNKNOWN
    assert disk_health.worst([sys_ok, DiskVerdict("1", UNKNOWN)]) == UNKNOWN
    assert disk_health.worst([sys_ok, other_bad, DiskVerdict("2", WARNING)]) == FAILING
    # A known system disk: other disks do not count.
    assert disk_health.gate_disks([sys_ok, other_bad]) == [sys_ok]
    # System disk unknown: every disk counts.
    assert disk_health.gate_disks([DiskVerdict("0", OK), other_bad]) == [DiskVerdict("0", OK), other_bad]


def test_describe_is_language_neutral():
    assert DiskVerdict("0", FAILING, ("predict_failure",), "WDC X", True).describe() == "#0 WDC X (FAILING: predict_failure)"
    assert DiskVerdict("?", FAILING, ("predict_failure",)).describe() == "? (FAILING: predict_failure)"


def test_probe_runs_the_catalog_command_once_with_the_timeout():
    calls = []

    def fake_run(command, timeout):
        calls.append((command, timeout))
        return SAMPLE

    verdicts = disk_health.probe(run=fake_run)
    assert [v.status for v in verdicts] == [WARNING, OK]
    assert calls == [(_command(), disk_health.PROBE_TIMEOUT_SEC)]


@pytest.mark.parametrize("output", [None, "", "no verdict here"])
def test_probe_failure_is_unknown(output):
    assert disk_health.probe(run=lambda command, timeout: output, command="x") is None


def test_probe_without_catalog_is_unknown(tmp_path):
    assert disk_health.catalog_command(tmp_path) is None
    assert disk_health.catalog_command(CATALOG_PATH.parent.parent) == _command()


def test_real_powershell_runner_times_out_to_none(monkeypatch):
    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(disk_health.subprocess, "run", slow)
    assert disk_health._run_powershell("x", 1) is None
    monkeypatch.setattr(disk_health.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("missing")))
    assert disk_health._run_powershell("x", 1) is None


def test_windows_probe_is_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(disk_health.sys, "platform", "linux")
    monkeypatch.setattr(disk_health, "probe", lambda: pytest.fail("must not launch PowerShell off Windows"))
    assert disk_health.windows_probe() is None
