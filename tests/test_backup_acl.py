"""The PortableFix ProgramData backup folder is locked down before any real
run that references it (portablefix/backup_acl.py) - a standard user must not
be able to pre-plant a backup that an elevated undo later imports."""
import os
import shutil
import subprocess

import pytest

from portablefix import undo
from portablefix.backup_acl import HARDEN_PS, references_backup_dir
from portablefix.executor import POWERSHELL_PREFIX, build_execution_plan

IMPORT = r"netsh advfirewall import $env:ProgramData\PortableFix\firewall_backup.wfw"


def _script(plan) -> str:
    return plan.argv[-1]


def test_real_run_referencing_the_folder_gets_the_hardening_prelude_first():
    script = _script(build_execution_plan(IMPORT, dry_run=False))
    assert script.startswith(HARDEN_PS)
    assert script.endswith("[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + IMPORT)
    assert script.count(HARDEN_PS) == 1


def test_percent_form_and_case_are_recognised():
    assert references_backup_dir(r"copy %ProgramData%\PortableFix\x.json y")
    assert references_backup_dir(r"$env:PROGRAMDATA + '\portablefix\x'")
    assert not references_backup_dir(r"Get-ChildItem C:\ProgramData\Microsoft\Windows\WER")


def test_dry_run_never_changes_the_folder():
    plan = build_execution_plan(IMPORT, dry_run=True)
    assert plan.argv is None and plan.display_command == IMPORT


def test_read_only_callers_can_opt_out():
    plan = build_execution_plan(IMPORT, dry_run=False, harden_backups=False)
    assert plan.argv == POWERSHELL_PREFIX + ["[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + IMPORT]


def test_commands_without_the_folder_are_byte_identical_to_before():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False)
    assert plan.argv == POWERSHELL_PREFIX + ["[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output 'hi'"]


def test_prelude_uses_sids_not_names_and_fails_closed():
    for sid in ("S-1-5-18", "S-1-5-32-544"):
        assert sid in HARDEN_PS
    for name in ("Administrators'", "SYSTEM'", "BUILTIN", "NT AUTHORITY", "Users"):
        assert name not in HARDEN_PS
    assert "SetAccessRuleProtection($true, $false)" in HARDEN_PS
    assert "ContainerInherit,ObjectInherit" in HARDEN_PS
    assert "exit 1" in HARDEN_PS
    assert ".untrusted" in HARDEN_PS
    assert "\n" not in HARDEN_PS


def test_prelude_text_is_stable():
    first = build_execution_plan(IMPORT, dry_run=False).argv
    assert build_execution_plan(IMPORT, dry_run=False).argv == first
    assert "Get-Date" not in HARDEN_PS and "NewGuid" not in HARDEN_PS


def test_undo_script_starts_with_the_hardening_step_when_it_imports_from_the_folder(tmp_path):
    path = undo.create_undo_script(tmp_path, "run1", steps=[IMPORT])
    text = path.read_text(encoding="utf-8-sig")
    assert text.index(HARDEN_PS.rstrip()) < text.index(IMPORT)


def test_undo_script_without_folder_steps_is_unchanged(tmp_path):
    path = undo.create_undo_script(tmp_path, "run1", steps=["reg import 'C:/x.reg'"])
    assert HARDEN_PS.rstrip() not in path.read_text(encoding="utf-8-sig")


def _powershell():
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe or os.name != "nt":
        pytest.skip("Windows PowerShell needed")
    return exe


def test_hardening_fails_closed_or_locks_the_folder(tmp_path):
    """Runs the real step against a scratch ProgramData. Not elevated it must
    stop with exit 1 before the guarded command; elevated it must succeed."""
    exe = _powershell()
    folder = tmp_path / "PortableFix"
    folder.mkdir()
    run = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", HARDEN_PS + "Write-Output 'GUARDED-RAN'"],
        env=dict(os.environ, ProgramData=str(tmp_path)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    import ctypes

    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if not elevated:
        assert run.returncode == 1 and "GUARDED-RAN" not in run.stdout
        assert "Could not secure the backup folder" in run.stdout
        return
    # Elevated (CI runner): the lock path must really work, no fail-closed excuse.
    assert run.returncode == 0 and "GUARDED-RAN" in run.stdout, run.stdout
    read = (
        "$d = New-Object IO.DirectoryInfo $env:PF_DIR; $a = $d.GetAccessControl(); "
        "'PROTECTED=' + $a.AreAccessRulesProtected; "
        "'OWNER=' + $a.GetOwner([Security.Principal.SecurityIdentifier]).Value; "
        "foreach ($r in $a.Access) { 'RULE=' + $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value + ' inherited=' + $r.IsInherited }"
    )
    check = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", read],
        env=dict(os.environ, PF_DIR=str(folder)), capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    msg = f"stdout={check.stdout!r} stderr={check.stderr!r} rc={check.returncode}"
    lines = [ln.strip() for ln in check.stdout.splitlines() if ln.strip()]
    assert "PROTECTED=True" in lines and "OWNER=S-1-5-32-544" in lines, msg
    rules = [ln for ln in lines if ln.startswith("RULE=")]
    assert {r.split()[0] for r in rules} == {"RULE=S-1-5-18", "RULE=S-1-5-32-544"}, msg
    assert all(r.endswith("inherited=False") for r in rules), msg
