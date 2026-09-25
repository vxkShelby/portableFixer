# tests/test_locale_free_actions.py
"""Run the real catalog commands of shadow_copies_oldest (m02),
tune_ultimate_performance_power_plan (m09) and drv_export_backup (m10) in
PowerShell against stubbed system tools.

All three used to decide what to do by matching English text in a native
tool's output ('No items found', 'Ultimate Performance', 'Total driver
packages:'), which is localized on non-English Windows. The stubs here answer
in Slovak on purpose, so a command that still keys off English labels fails.

Every system-changing tool (vssadmin, powercfg, pnputil, icacls,
Get-CimInstance) is replaced by a PowerShell function - functions win over
cmdlets and executables in command lookup - and the script refuses to run
(exit 97) unless each of those names really resolves to the stub, so a test
run can never touch the host's shadow copies, power plans or drivers.
"""
import os
import subprocess
from pathlib import Path

import pytest

from portablefix.module_engine import load_module

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"

VOL_C = "\\\\?\\Volume{c0c0c0c0-0000-0000-0000-000000000000}\\"
VOL_D = "\\\\?\\Volume{d0d0d0d0-0000-0000-0000-000000000000}\\"
ULTIMATE_TEMPLATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"
GUID_A = "aaaaaaaa-1111-2222-3333-444444444444"
GUID_B = "bbbbbbbb-5555-6666-7777-888888888888"
STUB_GUARD_EXIT = 97


def _powershell_or_skip() -> str:
    import shutil

    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _command(module_dir: str, action_id: str) -> str:
    module = load_module(MODULES_DIR / module_dir / "actions.yaml")
    return next(a for a in module.actions if a.id == action_id).command


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _stubs(log: Path, shadows=(), cim_fails=False, vss_exit=0, dup_output="", dup_exit=0, query_exit=0, pnputil_exit=0) -> str:
    """PowerShell prelude defining the stubs, on one line like every catalog
    command (the executor passes it as a single -Command argument).
    shadows: (volume, age_hours) pairs."""
    cim_fail_flag = "$true" if cim_fails else "$false"
    shadow_objs = ", ".join(
        f"[pscustomobject]@{{ ID = '{{s{i}}}'; VolumeName = {_ps_quote(vol)}; InstallDate = (Get-Date).AddHours(-{age}) }}"
        for i, (vol, age) in enumerate(shadows)
    )
    lg = _ps_quote(str(log))
    statements = [
        "function Get-CimInstance { [CmdletBinding()] param([Parameter(Position = 0)] [string] $ClassName, [string] $Filter); "
        f"Add-Content -Path {lg} -Value ('Get-CimInstance ' + $ClassName); "
        f"if ({cim_fail_flag}) {{ throw 'Prístup odmietnutý' }}; "
        f"if ($ClassName -eq 'Win32_Volume') {{ return [pscustomobject]@{{ DeviceID = {_ps_quote(VOL_C)} }} }}; "
        f"@({shadow_objs}) }}",
        f"function vssadmin {{ Add-Content -Path {lg} -Value ('vssadmin ' + ($args -join ' ')); "
        f"'Úspešne odstránená 1 tieňová kópia.'; $global:LASTEXITCODE = {vss_exit} }}",
        f"function powercfg {{ $a = @($args); Add-Content -Path {lg} -Value ('powercfg ' + ($a -join ' ')); "
        f"if ($a[0] -eq '-duplicatescheme') {{ {_ps_quote(dup_output)}; $global:LASTEXITCODE = {dup_exit} }} "
        f"elseif ($a[0] -eq '/query') {{ 'Schéma napájania: Maximálny výkon'; $global:LASTEXITCODE = {query_exit} }} "
        "else { $global:LASTEXITCODE = 0 } }",
        f"function pnputil {{ $a = @($args); Add-Content -Path {lg} -Value ('pnputil ' + ($a -join ' ')); "
        f"if ({pnputil_exit} -eq 0) {{ foreach ($n in 'a.inf_amd64_1', 'b.inf_amd64_2', 'c.inf_amd64_3') "
        "{ New-Item -ItemType Directory -Force -Path (Join-Path $a[2] $n) | Out-Null } }; "
        f"'Celkový počet balíkov ovládačov: 3'; 'Exportované balíky ovládačov: 3'; $global:LASTEXITCODE = {pnputil_exit} }}",
        "function icacls { $global:LASTEXITCODE = 0 }",
        "foreach ($n in 'Get-CimInstance', 'vssadmin', 'powercfg', 'pnputil', 'icacls') { "
        f"if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') {{ exit {STUB_GUARD_EXIT} }} }}",
    ]
    return "; ".join(statements) + "; "


def _run(tmp_path: Path, command: str, **stub_args):
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    program_data = tmp_path / "ProgramData"
    program_data.mkdir(exist_ok=True)
    script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + _stubs(log, **stub_args) + command
    env = dict(os.environ, ProgramData=str(program_data))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    calls = log.read_text(encoding="utf-8-sig").splitlines()
    return result, calls, program_data


# --- m02 shadow_copies_oldest ---------------------------------------------


def _shadow_run(tmp_path, **stub_args):
    return _run(tmp_path, _command("m02_cleanup", "shadow_copies_oldest"), **stub_args)


def test_shadow_copies_oldest_no_shadows_exits_zero_without_vssadmin(tmp_path):
    result, calls, _ = _shadow_run(tmp_path, shadows=())
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No shadow copies on C: - nothing to delete." in result.stdout
    assert not [c for c in calls if c.startswith("vssadmin")]


def test_shadow_copies_oldest_keeps_the_restore_point_this_run_just_created(tmp_path):
    # main_window creates a restore point right before the first DESTRUCTIVE
    # action - minutes later it is usually the only (so the oldest) shadow
    # copy on C:, and "/oldest" would delete it.
    result, calls, _ = _shadow_run(tmp_path, shadows=[(VOL_C, 0.1)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "less than 24 h old" in result.stdout
    assert "nothing deleted" in result.stdout
    assert not [c for c in calls if c.startswith("vssadmin")]


def test_shadow_copies_oldest_ignores_old_shadows_of_other_volumes(tmp_path):
    result, calls, _ = _shadow_run(tmp_path, shadows=[(VOL_D, 500)])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No shadow copies on C:" in result.stdout
    assert not [c for c in calls if c.startswith("vssadmin")]


@pytest.mark.parametrize("vss_exit", [0, 2])
def test_shadow_copies_oldest_deletes_when_oldest_is_over_24h_and_passes_exit_code_through(tmp_path, vss_exit):
    # Newest listed first on purpose: the command must sort by InstallDate,
    # not trust WMI's enumeration order. vssadmin's exit code is the result -
    # its (localized) text is never matched.
    result, calls, _ = _shadow_run(tmp_path, shadows=[(VOL_C, 0.1), (VOL_C, 72)], vss_exit=vss_exit)
    assert result.returncode == vss_exit, result.stdout + result.stderr
    assert [c for c in calls if c.startswith("vssadmin")] == ["vssadmin delete shadows /for=C: /oldest /quiet"]
    assert "Deleting the oldest shadow copy on C:" in result.stdout


def test_shadow_copies_oldest_query_failure_exits_one_without_vssadmin(tmp_path):
    result, calls, _ = _shadow_run(tmp_path, shadows=[(VOL_C, 72)], cim_fails=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "needs administrator" in result.stdout
    assert not [c for c in calls if c.startswith("vssadmin")]



@pytest.mark.parametrize(
    ("shadows", "expected"),
    [
        ((), "No shadow copies exist on C:"),
        (((VOL_C, 0.1),), "Would delete nothing: the oldest shadow copy on C: is less than 24 h old."),
        (((VOL_C, 0.1), (VOL_C, 72)), "Would delete only the oldest one, created "),
    ],
)
def test_shadow_copies_oldest_preview_matches_the_24h_rule(tmp_path, shadows, expected):
    module = load_module(MODULES_DIR / "m02_cleanup" / "actions.yaml")
    preview = next(a for a in module.actions if a.id == "shadow_copies_oldest").preview_command
    result, calls, _ = _run(tmp_path, preview, shadows=shadows)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    assert "younger than 24 h" in result.stdout
    assert not [c for c in calls if c.startswith("vssadmin")]

# --- m09 tune_ultimate_performance_power_plan -----------------------------

SK_DUP_OUTPUT = f"GUID schémy napájania: {GUID_B}  (Maximálny výkon)"


def _plan_run(tmp_path, **stub_args):
    return _run(tmp_path, _command("m09_tuning", "tune_ultimate_performance_power_plan"), **stub_args)


def _guid_file(program_data: Path) -> Path:
    return program_data / "PortableFix" / "ultimate_plan_guid.txt"


def _read_guid_file(program_data: Path) -> str:
    # Windows PowerShell writes a backslash path; pwsh on Linux turns the
    # same string into PortableFix/ultimate_plan_guid.txt - both land here.
    return _guid_file(program_data).read_text(encoding="utf-8-sig").strip()


def test_power_plan_first_run_parses_new_guid_from_localized_output_and_saves_it(tmp_path):
    result, calls, program_data = _plan_run(tmp_path, dup_output=SK_DUP_OUTPUT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"powercfg -duplicatescheme {ULTIMATE_TEMPLATE}" in calls
    assert f"powercfg /setactive {GUID_B}" in calls
    assert _read_guid_file(program_data) == GUID_B
    assert not [c for c in calls if c.startswith("powercfg /list")]


def test_power_plan_reuses_saved_guid_instead_of_duplicating_again(tmp_path):
    # The old command recognized "its" plan by the English name in
    # powercfg /list - on non-English Windows it never matched and every run
    # added another copy of the plan.
    program_data = tmp_path / "ProgramData"
    _guid_file(program_data).parent.mkdir(parents=True)
    _guid_file(program_data).write_text(GUID_A + "\n", encoding="ascii")
    result, calls, _ = _plan_run(tmp_path, dup_output=SK_DUP_OUTPUT, query_exit=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"powercfg /query {GUID_A}" in calls
    assert not [c for c in calls if c.startswith("powercfg -duplicatescheme")]
    assert f"powercfg /setactive {GUID_A}" in calls


def test_power_plan_recreates_when_saved_plan_was_deleted(tmp_path):
    program_data = tmp_path / "ProgramData"
    _guid_file(program_data).parent.mkdir(parents=True)
    _guid_file(program_data).write_text(GUID_A, encoding="ascii")
    result, calls, _ = _plan_run(tmp_path, dup_output=SK_DUP_OUTPUT, query_exit=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"powercfg -duplicatescheme {ULTIMATE_TEMPLATE}" in calls
    assert f"powercfg /setactive {GUID_B}" in calls
    assert f"powercfg /setactive {GUID_A}" not in calls
    assert _read_guid_file(program_data) == GUID_B


def test_power_plan_unsupported_device_fails_without_activating_the_template_guid(tmp_path):
    # An error message that echoes the template GUID must not be mistaken
    # for a newly created plan.
    error = f"Nie je možné vytvoriť novú schému napájania {ULTIMATE_TEMPLATE}."
    result, calls, program_data = _plan_run(tmp_path, dup_output=error, dup_exit=1)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not [c for c in calls if c.startswith("powercfg /setactive")]
    assert not _guid_file(program_data).exists()


# --- m10 drv_export_backup ------------------------------------------------


def test_driver_backup_counts_exported_package_folders_not_localized_labels(tmp_path):
    result, calls, program_data = _run(tmp_path, _command("m10_drivers", "drv_export_backup"))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Driver packages exported: 3" in result.stdout
    assert "Driver backup saved to: " in result.stdout
    assert [c for c in calls if c.startswith("pnputil /export-driver * ")]


def test_driver_backup_failure_still_exits_one(tmp_path):
    result, _, _ = _run(tmp_path, _command("m10_drivers", "drv_export_backup"), pnputil_exit=5)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Driver export failed" in result.stdout
    assert "Driver packages exported" not in result.stdout
