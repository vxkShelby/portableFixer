"""Silent-uninstall planning (research G15) - Qt-free, registry-free."""

import subprocess
from types import SimpleNamespace

import pytest

from portablefix import uninstall_plan as up

GUID = "{12345678-9ABC-DEF0-1234-56789ABCDEF0}"
ENV = {"SystemRoot": r"C:\Windows", "ProgramFiles": r"C:\Program Files"}


def _entry(plain=None, quiet=None, key=None, **hints):
    return SimpleNamespace(
        name=hints.pop("name", "App"),
        uninstall_string=plain, quiet_uninstall_string=quiet,
        registry_path=rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{key or 'App'}",
        **hints,
    )


def _plan(entry, log_dir=None, nsis_files=(), system_files=()):
    return up.build_plan(
        entry, log_dir, environ=ENV, has_nsis_marker=lambda path: path in nsis_files,
        file_exists=lambda path: path.lower() in {f.lower() for f in system_files},
    )


# --- command-line parsing -----------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    (r'"C:\Program Files\App\uninst.exe" /x', [r"C:\Program Files\App\uninst.exe", "/x"]),
    (r"C:\Program Files\App\unins000.exe", [r"C:\Program Files\App\unins000.exe"]),
    (r"C:\Program Files\App\Uninstall.exe /S --keep", [r"C:\Program Files\App\Uninstall.exe", "/S", "--keep"]),
    ("cmd /c echo QUIET", ["cmd", "/c", "echo", "QUIET"]),
    (r"C:\Program Files\App\remove app.bat /q", [r"C:\Program Files\App\remove app.bat", "/q"]),
    (r"C:\my.company\u.exe /x", [r"C:\my.company\u.exe", "/x"]),
    (r'"%ProgramFiles%\App\u.exe" "a b" c\"d', [r"C:\Program Files\App\u.exe", "a b", 'c"d']),
    (r'x.exe "C:\dir\\" end', ["x.exe", "C:\\dir\\", "end"]),
    (r"x.exe %UNKNOWN_PF_VAR%", ["x.exe", "%UNKNOWN_PF_VAR%"]),
    ("", None),
    ("   ", None),
    (None, None),
])
def test_split_command_line_follows_the_windows_rules(text, expected):
    assert up.split_command_line(text, environ=ENV) == expected


def test_shell_metacharacters_stay_plain_argument_text():
    # No cmd.exe in between: "&" and "|" are just characters of an argument.
    argv = up.split_command_line(r'"C:\App\u.exe" /S & del C:\x | calc', environ=ENV)
    assert argv == [r"C:\App\u.exe", "/S", "&", "del", r"C:\x", "|", "calc"]


@pytest.mark.parametrize("registry, expected", [
    # InstallShield: a quote that starts inside the token.
    (r'C:\WINDOWS\IsUninst.exe -f"C:\Program Files\X\Uninst.isu" -c"C:\Program Files\X\a.dll"',
     r'C:\WINDOWS\IsUninst.exe -f"C:\Program Files\X\Uninst.isu" -c"C:\Program Files\X\a.dll"'),
    # rundll32 reads "<dll>",Entry from its raw command line.
    (r'"C:\Program Files\X\run.exe" "C:\Program Files\X\u.dll",Uninstall arg',
     r'"C:\Program Files\X\run.exe" "C:\Program Files\X\u.dll",Uninstall arg'),
    # MSI-style PROP="value" in a vendor string.
    (r'"C:\x\setup.exe" /uninstall /quiet INSTALLDIR="C:\Program Files\X"',
     r'C:\x\setup.exe /uninstall /quiet INSTALLDIR="C:\Program Files\X"'),
    # %VAR% is still expanded (cmd.exe used to), nothing else changes.
    (r'"%ProgramFiles%\App\u.exe"   a\"b  "c  d"', r'"C:\Program Files\App\u.exe" a\"b  "c  d"'),
])
def test_registry_arguments_reach_the_process_verbatim(registry, expected):
    plan = _plan(_entry(plain=registry))
    assert plan.kind == up.KIND_INTERACTIVE and plan.command == expected
    calls = []
    up.execute_plan(plan, None, run=_fake_run(0, calls=calls))
    # A string with shell=False: CreateProcess gets exactly this line.
    assert calls[0][0] == expected and calls[0][1]["shell"] is False


def test_silent_switches_are_appended_behind_the_verbatim_tail():
    plan = _plan(_entry(plain=r'"C:\P F\unins000.exe" /LOG="C:\a b\x.log"'))
    assert plan.kind == up.KIND_INNO
    assert plan.command == r'"C:\P F\unins000.exe" /LOG="C:\a b\x.log" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART'


def test_a_bare_system_program_is_pinned_to_system32_not_the_app_folder():
    system = {r"C:\Windows\System32\rundll32.exe", r"C:\Windows\System32\cmd.exe"}
    rundll = _plan(_entry(plain=r'RunDll32 "C:\Program Files\X\u.dll",Uninstall arg'), system_files=system)
    assert rundll.command == r'C:\Windows\System32\RunDll32.exe "C:\Program Files\X\u.dll",Uninstall arg'
    cmd = _plan(_entry(plain="cmd /c echo QUIET"), system_files=system)
    assert cmd.argv[0] == r"C:\Windows\System32\cmd.exe"
    # Not in System32 (or already a path): left to the normal search as before.
    assert _plan(_entry(plain="loud.exe"), system_files=system).command == "loud.exe"
    assert _plan(_entry(plain=r"D:\cmd.exe /c x"), system_files=system).argv[0] == r"D:\cmd.exe"


@pytest.mark.parametrize("registry", [
    r'"C:\x\u.bat" & calc',
    r"C:\x\remove.cmd /q | calc",
    r"cmd /c del x & calc",
    r'"C:\x\u.bat" ^& calc',
    r'"C:\x\u.bat" %COMSPEC%',
    r"C:\x\u.bat > C:\out.txt",
])
def test_cmd_interpreted_commands_with_metacharacters_never_run(registry):
    # CreateProcess runs a .bat/.cmd through cmd.exe, which would act on
    # "&" etc. - refused, in both the plain and the quiet string.
    for entry in (_entry(plain=registry), _entry(plain=r"C:\x\u.exe", quiet=registry)):
        plan = _plan(entry)
        assert plan.kind == up.KIND_UNSAFE and plan.argv == () and plan.command == "" and not plan.silent
        result = up.execute_plan(plan, 300, run=pytest.fail)
        assert not result.ok and result.outcome == up.OUTCOME_UNSAFE_COMMAND


def test_exe_and_plain_batch_commands_still_run():
    assert _plan(_entry(plain=r'"C:\x\u.exe" /S & calc')).command == r"C:\x\u.exe /S & calc"
    assert _plan(_entry(plain=r'"C:\x\u.bat" /q')).kind == up.KIND_INTERACTIVE
    assert _plan(_entry(plain="cmd /c echo QUIET")).kind == up.KIND_INTERACTIVE


def test_a_quote_inside_the_program_runs_nothing():
    assert up.parse_command(r'C:\a"b\u.exe /x', environ=ENV) is None


# --- installer type detection ------------------------------------------------


def test_msi_entry_uses_x_quiet_and_a_log_never_the_registry_i(tmp_path):
    plan = _plan(_entry(plain=f"MsiExec.exe /I{GUID}", windows_installer=True), log_dir=tmp_path)
    assert plan.kind == up.KIND_MSI and plan.silent and plan.product_code == GUID
    log = str(tmp_path / f"msi_uninstall_{GUID.strip('{}')}.log")
    assert plan.argv == (r"C:\Windows\System32\msiexec.exe", "/x", GUID, "/qn", "/norestart", "/l*v", log)
    assert plan.log_path == log
    assert "/I" not in plan.argv and not any(a.upper().startswith("/I") for a in plan.argv)


def test_msi_detected_from_msiexec_in_the_string_without_the_windows_installer_flag():
    plan = _plan(_entry(plain=f"MsiExec.exe /X {GUID.lower()}"))
    assert plan.kind == up.KIND_MSI and plan.product_code == GUID
    assert plan.argv[1:] == ("/x", GUID, "/qn", "/norestart")


def test_msi_takes_the_product_code_from_the_key_name_when_the_string_has_none():
    plan = _plan(_entry(plain=None, key=GUID.lower(), windows_installer=True))
    assert plan.kind == up.KIND_MSI and plan.product_code == GUID


def test_msi_wins_over_the_vendor_quiet_string():
    plan = _plan(_entry(plain=f"MsiExec.exe /I{GUID}", quiet=r"C:\x\setup.exe /uninstall /quiet", windows_installer=True))
    assert plan.kind == up.KIND_MSI


@pytest.mark.parametrize("bad", [
    "MsiExec.exe /I{1234}",
    "MsiExec.exe /I{12345678-9ABC-DEF0-1234-56789ABCDEF0}&calc",
    "MsiExec.exe /I 12345678-9ABC-DEF0-1234-56789ABCDEF0",
    "MsiExec.exe /I",
    "MsiExec.exe",
])
def test_msiexec_without_a_strict_guid_never_runs(bad):
    plan = _plan(_entry(plain=bad, key="NotAGuid", windows_installer=True))
    assert plan.kind == up.KIND_NONE and plan.argv == () and plan.command == ""


def test_msiexec_without_a_guid_falls_back_to_a_non_msiexec_quiet_string():
    plan = _plan(_entry(plain="MsiExec.exe /I{bad}", quiet=r"C:\x\u.exe /quiet", key="NotAGuid"))
    assert plan.kind == up.KIND_VENDOR_QUIET and plan.argv == (r"C:\x\u.exe", "/quiet")


def test_a_quiet_msiexec_string_without_a_guid_never_runs_even_beside_an_exe():
    plan = _plan(_entry(plain=r"C:\x\setup.exe /remove", quiet="MsiExec.exe /I{bad} /qn", key="Vendor"))
    assert plan.kind == up.KIND_INTERACTIVE and plan.argv == (r"C:\x\setup.exe", "/remove")


def test_windows_installer_flag_with_an_exe_bootstrapper_and_no_code_uses_that_exe():
    plan = _plan(_entry(plain=r'"C:\x\bootstrap.exe" /uninstall', key="Vendor", windows_installer=True))
    assert plan.kind == up.KIND_INTERACTIVE and plan.argv == (r"C:\x\bootstrap.exe", "/uninstall")


def test_inno_by_exe_name_adds_the_silent_switches_once():
    plan = _plan(_entry(plain=r'"C:\Program Files\App\unins000.exe" /verysilent'))
    assert plan.kind == up.KIND_INNO and plan.silent
    assert plan.argv == (r"C:\Program Files\App\unins000.exe", "/verysilent", "/SUPPRESSMSGBOXES", "/NORESTART")


def test_inno_by_registry_values():
    plan = _plan(_entry(plain=r"C:\App\remove.exe", inno_setup=True))
    assert plan.kind == up.KIND_INNO
    assert plan.argv == (r"C:\App\remove.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")


def test_nsis_by_file_marker_adds_s_and_no_underscore_question_mark():
    exe = r"C:\Program Files\App\Uninstall.exe"
    plan = _plan(_entry(plain=f'"{exe}"'), nsis_files={exe})
    assert plan.kind == up.KIND_NSIS and plan.silent and plan.argv == (exe, "/S")
    assert not any(a.startswith("_?=") for a in plan.argv)


def test_nsis_by_entry_marker_even_with_another_exe_name():
    plan = _plan(_entry(plain=r"C:\App\remove-app.exe", nsis_marker=True))
    assert plan.kind == up.KIND_NSIS and plan.argv == (r"C:\App\remove-app.exe", "/S")


def test_uninstall_exe_without_the_nsis_marker_is_not_guessed_silent():
    plan = _plan(_entry(plain=r"C:\App\Uninstall.exe"))
    assert plan.kind == up.KIND_INTERACTIVE and not plan.silent


def test_vendor_quiet_string_then_interactive_plain_string():
    quiet = _plan(_entry(plain=r"C:\App\remove.exe", quiet=r"C:\App\remove.exe /quiet"))
    assert quiet.kind == up.KIND_VENDOR_QUIET and quiet.silent
    assert quiet.argv == (r"C:\App\remove.exe", "/quiet")
    plain = _plan(_entry(plain=r"C:\App\remove.exe"))
    assert plain.kind == up.KIND_INTERACTIVE and not plain.silent
    assert _plan(_entry()).kind == up.KIND_NONE


def test_command_quotes_the_program_only_when_needed():
    plan = _plan(_entry(plain=r'"C:\Program Files\App\remove.exe" "a b"'))
    assert plan.command == r'"C:\Program Files\App\remove.exe" "a b"'
    assert _plan(_entry(plain=r'"C:\App\remove.exe"')).command == r"C:\App\remove.exe"
    # Plans we build ourselves (MSI) join their own argv.
    msi = _plan(_entry(plain=f"MsiExec.exe /I{GUID}", windows_installer=True))
    assert msi.command == subprocess.list2cmdline(list(msi.argv))


def test_file_has_nsis_marker_reads_the_header(tmp_path):
    nsis = tmp_path / "Uninstall.exe"
    nsis.write_bytes(b"MZ" + b"\0" * 70000 + b"\xef\xbe\xad\xdeNullsoftInst" + b"\0" * 10)
    other = tmp_path / "other.exe"
    other.write_bytes(b"MZ" + b"\0" * 1000)
    assert up.file_has_nsis_marker(str(nsis))
    assert not up.file_has_nsis_marker(str(other))
    assert not up.file_has_nsis_marker(str(tmp_path / "missing.exe"))


def test_file_has_nsis_marker_finds_a_signature_split_across_chunks(tmp_path):
    exe = tmp_path / "Uninstall.exe"
    exe.write_bytes(b"\0" * (1024 * 1024 - 5) + b"NullsoftInst")
    assert up.file_has_nsis_marker(str(exe))


def test_split_queues_puts_interactive_first_in_given_order():
    programs = [SimpleNamespace(name=n) for n in ("A", "B", "C", "D")]
    plans = {
        "A": up.UninstallPlan(up.KIND_MSI, ("m",), True),
        "B": up.UninstallPlan(up.KIND_INTERACTIVE, ("b",), False),
        "C": up.UninstallPlan(up.KIND_NONE, (), False),
        "D": up.UninstallPlan(up.KIND_INTERACTIVE, ("d",), False),
    }
    interactive, silent = up.split_queues(programs, plans)
    assert [p.name for p in interactive] == ["B", "D"]
    assert [p.name for p in silent] == ["A", "C"]


# --- exit codes and running ---------------------------------------------------

_MSI = up.UninstallPlan(up.KIND_MSI, (r"C:\Windows\System32\msiexec.exe", "/x", GUID, "/qn"), True, product_code=GUID)
_EXE = up.UninstallPlan(up.KIND_VENDOR_QUIET, (r"C:\App\u.exe", "/S"), True)


@pytest.mark.parametrize("code, expected", [
    (0, (True, up.OUTCOME_OK)),
    (1605, (True, up.OUTCOME_ALREADY_GONE)),
    (1641, (True, up.OUTCOME_REBOOT_INITIATED)),
    (3010, (True, up.OUTCOME_REBOOT_REQUIRED)),
    (1618, (False, up.OUTCOME_BUSY_RETRY)),
    (1602, (False, up.OUTCOME_CANCELLED)),
    (1603, (False, up.OUTCOME_FAILED)),
])
def test_interpret_msiexec_exit_codes(code, expected):
    assert up.interpret_exit_code(_MSI, code) == expected


def test_msi_codes_apply_to_a_vendor_string_that_is_msiexec_but_not_to_other_exes():
    vendor_msiexec = up.UninstallPlan(up.KIND_VENDOR_QUIET, ("MsiExec.exe", "/X", GUID, "/qn"), True)
    assert up.interpret_exit_code(vendor_msiexec, 3010) == (True, up.OUTCOME_REBOOT_REQUIRED)
    assert up.interpret_exit_code(vendor_msiexec, 1605) == (True, up.OUTCOME_ALREADY_GONE)
    # msiexec-only codes mean nothing from another exe.
    assert up.interpret_exit_code(_EXE, 1605) == (False, up.OUTCOME_FAILED)
    assert up.interpret_exit_code(_EXE, 1618) == (False, up.OUTCOME_FAILED)
    assert up.interpret_exit_code(_EXE, 0) == (True, up.OUTCOME_OK)


def test_reboot_codes_count_for_any_silent_plan_but_not_an_interactive_one():
    # WiX Burn / bootstrappers as QuietUninstallString relay 3010 and 1641.
    assert up.interpret_exit_code(_EXE, 3010) == (True, up.OUTCOME_REBOOT_REQUIRED)
    assert up.interpret_exit_code(_EXE, 1641) == (True, up.OUTCOME_REBOOT_INITIATED)
    interactive = up.UninstallPlan(up.KIND_INTERACTIVE, (r"C:\App\u.exe",), False)
    assert up.interpret_exit_code(interactive, 3010) == (False, up.OUTCOME_FAILED)


def _fake_run(returncode=0, stdout="", calls=None, exc=None):
    def run(args, **kwargs):
        if calls is not None:
            calls.append((args, kwargs))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    return run


def test_execute_plan_runs_the_argv_without_a_shell_and_passes_the_timeout():
    calls = []
    result = up.execute_plan(_EXE, 300, run=_fake_run(0, "bye", calls))
    args, kwargs = calls[0]
    assert args == r"C:\App\u.exe /S" and kwargs["shell"] is False and kwargs["timeout"] == 300
    assert result == up.UninstallResult(True, "bye", 0, up.OUTCOME_OK)


def test_execute_plan_interactive_waits_without_a_timeout():
    calls = []
    up.execute_plan(up.UninstallPlan(up.KIND_INTERACTIVE, ("u.exe",), False), None, run=_fake_run(0, calls=calls))
    assert calls[0][1]["timeout"] is None


def test_execute_plan_reports_reboot_and_busy_with_the_exit_code(tmp_path):
    log = tmp_path / "Logs" / "run_msi" / "msi.log"
    plan = up.UninstallPlan(up.KIND_MSI, _MSI.argv, True, GUID, str(log))
    reboot = up.execute_plan(plan, 300, run=_fake_run(3010))
    assert reboot.ok and reboot.outcome == up.OUTCOME_REBOOT_REQUIRED and reboot.exit_code == 3010
    assert "3010" in reboot.output and str(log) in reboot.output
    # msiexec does not create a missing log folder itself.
    assert log.parent.is_dir()
    busy = up.execute_plan(plan, 300, run=_fake_run(1618))
    assert not busy.ok and busy.outcome == up.OUTCOME_BUSY_RETRY and "try again" in busy.output
    gone = up.execute_plan(plan, 300, run=_fake_run(1605))
    assert gone.ok and gone.outcome == up.OUTCOME_ALREADY_GONE


def test_execute_plan_nsis_success_carries_the_background_note():
    plan = up.UninstallPlan(up.KIND_NSIS, (r"C:\App\Uninstall.exe", "/S"), True)
    result = up.execute_plan(plan, 300, run=_fake_run(0))
    assert result.ok and up.NSIS_BACKGROUND_NOTE in result.output


def test_execute_plan_timeout_launch_error_and_no_command():
    timeout = up.execute_plan(_EXE, 300, run=_fake_run(exc=subprocess.TimeoutExpired("u.exe", 300)))
    assert timeout == up.UninstallResult(False, "Uninstaller timed out.", None, up.OUTCOME_TIMEOUT)
    missing = up.execute_plan(_EXE, 300, run=_fake_run(exc=FileNotFoundError("gone")))
    assert not missing.ok and missing.outcome == up.OUTCOME_LAUNCH_ERROR and "gone" in missing.output
    none = up.execute_plan(up.UninstallPlan(up.KIND_NONE, (), False), 300, run=pytest.fail)
    assert not none.ok and none.outcome == up.OUTCOME_NO_COMMAND
    assert "No uninstall command" in none.output


def test_msiexec_path_uses_system_root():
    assert up.msiexec_path({"SystemRoot": r"D:\Win"}) == r"D:\Win\System32\msiexec.exe"
    assert up.msiexec_path({}) == r"C:\Windows\System32\msiexec.exe"


def test_module_is_qt_and_registry_free():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(up))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith(("PySide6", "winreg")) for name in imported)
