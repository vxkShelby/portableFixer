"""m24_scanners (research G28): the catalog, and scanners.ps1 run against
stubbed cmdlets - no download, no real scanner, no signature of a real file."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix import action_service, pfjson
from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

MODULE_DIR = Path(__file__).resolve().parent.parent / "Modules" / "m24_scanners"
CATALOG_PATH = MODULE_DIR / "actions.yaml"
LIB_PATH = MODULE_DIR / "scanners.ps1"
TOOLS = ("adwcleaner", "msert", "kvrt")


def _module():
    return load_module(CATALOG_PATH)


def _action(action_id):
    return next(a for a in _module().actions if a.id == action_id)


def test_m24_catalog_has_a_scan_and_a_separate_clean_per_tool():
    module = _module()
    assert module.module_id == "m24_scanners"
    assert module.category == ModuleCategory.ANTIVIRUS
    ids = [a.id for a in module.actions]
    assert sorted(ids) == sorted([f"{t}_{m}" for t in TOOLS for m in ("scan", "clean")])
    for tool in TOOLS:
        scan, clean = _action(f"{tool}_scan"), _action(f"{tool}_clean")
        assert scan.risk == RiskLevel.MODERATE and scan.changes_system is False
        assert "-Mode scan" in scan.command and "-Mode clean" not in scan.command
        assert clean.risk == RiskLevel.DESTRUCTIVE
        assert "-Mode clean" in clean.command
        for action in (scan, clean):
            assert action.exclude_from_select_all
            assert action.preview_command and "Show-PfScannerPlan" in action.preview_command
            assert action.undo_command is None
            assert action.inactivity_timeout_sec >= 300  # heartbeat every 60 s


def test_no_tdsskiller_anywhere():
    for path in (CATALOG_PATH, LIB_PATH):
        assert "tdss" not in path.read_text(encoding="utf-8-sig").lower()


def test_kvrt_descriptions_state_the_us_restriction():
    for action_id in ("kvrt_scan", "kvrt_clean"):
        action = _action(action_id)
        assert "USA" in action.description_sk and "USA" in action.description_en
        assert "2024" in action.description_en
    for action_id in ("adwcleaner_scan", "msert_scan"):
        assert "icens" in _action(action_id).description_en  # License / licensing note


def test_lib_has_a_bom_for_windows_powershell():
    # 5.1 reads a BOM-less .ps1 as ANSI - the Slovak finding text would break.
    assert LIB_PATH.read_bytes().startswith(b"\xef\xbb\xbf")


def test_scanner_variables_only_for_scanner_commands(tmp_path):
    variables = action_service.scanner_variables(_action("msert_scan"), tmp_path, "run-1", app_dir=Path("C:/PF"))
    assert variables == {
        "__pfScannersLib": str(Path("C:/PF") / "Modules" / "m24_scanners" / "scanners.ps1"),
        "__pfScannerCache": str(tmp_path / "ScannerCache"),
        "__pfJobDir": str(tmp_path / "Backups" / "run-1" / "scanners" / "msert_scan"),
    }
    other = load_module(MODULE_DIR.parent / "m23_antivirus" / "actions.yaml").actions[0]
    assert action_service.scanner_variables(other, tmp_path, "run-1") == {}


def test_prepare_plan_passes_the_folders_to_the_command(tmp_path):
    prepared = action_service.prepare_plan(_action("adwcleaner_scan"), dry_run=False, state_dir=tmp_path,
                                           run_id="run-1", target_user=None)
    script = prepared.plan.argv[-1]
    assert "$__pfScannerCache = " in script and "$__pfJobDir = " in script
    assert script.index("$__pfScannersLib = ") < script.index(". $__pfScannersLib;")


# --- scanners.ps1 against stubs ----------------------------------------------

STUB_GUARD_EXIT = 97


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


LOGS = {
    "adwcleaner_clean_log": "# Mode: Scan\n# Scanned: 31915\n# Detected: 0\n",
    "adwcleaner_dirty_log": ("# Mode: Scan\n# Detected: 2\n\n***** [ Registry ] *****\n\n"
                             "PUP.Optional.Legacy, HKLM\\Software\\Foo\nPUP.Optional.BundleInstaller, C:\\x\n"),
    "msert_clean_log": "Results Summary:\n----------------\nNo infection found.\nReturn code: 0 (0x0)\n",
    "msert_dirty_log": ("Quick Scan Results:\n-------------------\nThreat Detected: Trojan:Win32/Wacatac.B!ml, not removed.\n"
                        "    file://C:\\Users\\x\\a.exe\nResults Summary:\n----------------\n"
                        "Found Trojan:Win32/Wacatac.B!ml, not removed.\n"),
}


def _run(tmp_path, command, *, signature="Valid", signer="Malwarebytes Inc", cached=False, download_error=False,
         tool_log="", tool_exit=0, arch="AMD64", cache_age_days=0, old_msert_log=""):
    """Runs `command` after dot-sourcing scanners.ps1 with Get-AuthenticodeSignature,
    Invoke-WebRequest and Start-Process stubbed. The fake tool 'writes' tool_log
    where the real one would."""
    calls = tmp_path / "calls.txt"
    cache = tmp_path / "ScannerCache"
    job = tmp_path / "Backups" / "run-1" / "scanners" / "job"
    sysroot = tmp_path / "Windows"
    (sysroot / "debug").mkdir(parents=True, exist_ok=True)
    if old_msert_log:
        (sysroot / "debug" / "msert.log").write_text(old_msert_log, encoding="utf-8")
    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    if cached:
        cache.mkdir(parents=True, exist_ok=True)
        for name in ("adwcleaner.exe", "msert.exe", "KVRT.exe"):
            (cache / name).write_bytes(b"MZ cached")
    log_file = tmp_path / "tool_log.txt"
    log_file.write_text(tool_log, encoding="utf-8")
    stubs = {
        "Get-AuthenticodeSignature": (
            "$LiteralPath",
            "$c = [pscustomobject]@{}; $c | Add-Member -MemberType ScriptMethod -Name GetNameInfo -Value "
            f"{{ param($t, $i) {_q(signer)} }}; [pscustomobject]@{{ Status = {_q(signature)}; SignerCertificate = $c }}",
        ),
        "Invoke-WebRequest": (
            "$Uri, $OutFile, [switch]$UseBasicParsing",
            "throw [System.Net.WebException]::new('Názov sa nepodarilo rozpoznať.')" if download_error
            else "[IO.File]::WriteAllBytes($OutFile, [byte[]](77, 90))",
        ),
        "Start-Process": (
            "$FilePath, $ArgumentList, [switch]$PassThru, $WindowStyle",
            # The fake tool: write its log where the real one would, then 'exit'.
            "$src = $env:PF_TOOL_LOG; $argsText = [string]$ArgumentList; "
            "if ($argsText.Contains('/path')) { $d = Join-Path $__pfJobDir 'AdwCleaner\\Logs'; New-Item -ItemType Directory -Force $d | Out-Null; "
            "Copy-Item $src (Join-Path $d 'AdwCleaner[S00].txt') } "
            "elseif ($argsText.Contains('-accepteula')) { $d = Join-Path $__pfJobDir 'kvrt_data\\Reports'; New-Item -ItemType Directory -Force $d | Out-Null; "
            "Copy-Item $src (Join-Path $d 'report.txt') } "
            "else { Add-Content -LiteralPath $__pfMsertLog -Value (Get-Content $src -Raw) }; "
            "$p = [pscustomobject]@{ ExitCode = [int]$env:PF_TOOL_EXIT; Waits = 0 }; "
            "$p | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($ms) $this.Waits++; $this.Waits -ge 2 }; $p",
        ),
    }
    functions = [
        f"function {name} {{ [CmdletBinding()] param({params}) "
        f"Add-Content -LiteralPath $env:PF_CALLS -Value ('{name} ' + (($PSBoundParameters.Keys | Where-Object {{ $_ -ne 'ErrorAction' }} | Sort-Object | "
        "ForEach-Object { $_ + '=' + ($PSBoundParameters[$_] -join ' ') }) -join ' ')); "
        f"{body} }}"
        for name, (params, body) in stubs.items()
    ]
    guard = ("foreach ($n in " + ", ".join(_q(n) for n in stubs) + ") { "
             "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
             f"{{ exit {STUB_GUARD_EXIT} }} }}")
    env_values = {
        "PF_CALLS": str(calls), "PF_TOOL_LOG": str(log_file), "PF_TOOL_EXIT": str(tool_exit),
        "TEMP": str(temp), "PROCESSOR_ARCHITECTURE": arch, "PROCESSOR_ARCHITEW6432": "",
    }
    setup = [f"$env:{k} = {_q(v)}" for k, v in env_values.items()] + [
        f"$__pfScannersLib = {_q(str(LIB_PATH))}", f"$__pfScannerCache = {_q(str(cache))}", f"$__pfJobDir = {_q(str(job))}", f"$__pfMsertLog = {_q(str(sysroot / 'debug' / 'msert.log'))}",
    ]
    if cached and cache_age_days:
        setup.append(f"Get-ChildItem {_q(str(cache))} | ForEach-Object {{ $_.LastWriteTime = (Get-Date).AddDays(-{cache_age_days}) }}")
    script = "; ".join(["[Console]::OutputEncoding=[Text.Encoding]::UTF8"] + setup + functions + [guard, command])
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a cmdlet was not stubbed - refusing to run the real one"
    called = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, called, job


def _named(calls, name):
    return [c for c in calls if c.split(" ", 1)[0] == name]


def _findings(stdout):
    return pfjson.findings([pfjson.parse_line(line) for line in stdout.splitlines() if pfjson.is_pfjson(line)])


def test_adwcleaner_scan_downloads_verifies_runs_and_reports_ok(tmp_path):
    result, calls, job = _run(tmp_path, _action("adwcleaner_scan").command, tool_log=LOGS["adwcleaner_clean_log"])
    assert result.returncode == 0, result.stdout + result.stderr
    names = [c.split(" ", 1)[0] for c in calls]
    assert names == ["Invoke-WebRequest", "Get-AuthenticodeSignature", "Start-Process"]
    start = _named(calls, "Start-Process")[0]
    assert "/eula /scan /noreboot /path" in start and "/clean" not in start
    # The verified temp copy runs, not the cache file.
    assert "ScannerCache" not in start.split("FilePath=")[1].split(" ")[0]
    assert (tmp_path / "ScannerCache" / "adwcleaner.exe").exists()
    assert (job / "logs" / "AdwCleaner[S00].txt").exists()
    assert _findings(result.stdout) == [{
        "id": "security.scanner.adwcleaner", "severity": "ok", "area": "security",
        "msg_sk": "Malwarebytes AdwCleaner nič nenašiel.", "msg_en": "Malwarebytes AdwCleaner found nothing.", "fix": [],
    }]
    assert not list((tmp_path / "temp").iterdir()), "the temp copy must be removed"


def test_adwcleaner_detections_are_attention_with_the_clean_action_as_fix(tmp_path):
    result, _, _ = _run(tmp_path, _action("adwcleaner_scan").command, tool_log=LOGS["adwcleaner_dirty_log"], cached=True)
    assert result.returncode == 0, result.stdout + result.stderr
    [finding] = _findings(result.stdout)
    assert finding["severity"] == "attention" and finding["fix"] == ["adwcleaner_clean"]
    assert "PUP.Optional.Legacy" in finding["msg_en"] and "2 detection(s)" in finding["msg_en"]


@pytest.mark.parametrize("signature,signer", [("NotSigned", ""), ("HashMismatch", "Malwarebytes Inc"),
                                              ("Valid", "Evil Corp")])
def test_bad_signature_is_refused_and_nothing_runs(tmp_path, signature, signer):
    result, calls, _ = _run(tmp_path, _action("adwcleaner_scan").command, signature=signature, signer=signer,
                            tool_log=LOGS["adwcleaner_clean_log"])
    assert result.returncode == 5, result.stdout + result.stderr
    assert "REFUSED" in result.stdout
    assert _named(calls, "Start-Process") == []
    assert not (tmp_path / "ScannerCache" / "adwcleaner.exe").exists(), "a bad download must not be cached"
    assert _findings(result.stdout) == []


def test_bad_technician_copy_is_refused_but_left_in_place(tmp_path):
    result, calls, _ = _run(tmp_path, _action("adwcleaner_scan").command, cached=True, signature="NotSigned")
    assert result.returncode == 5
    assert "not the genuine tool" in result.stdout
    assert _named(calls, "Invoke-WebRequest") == [] and _named(calls, "Start-Process") == []
    assert (tmp_path / "ScannerCache" / "adwcleaner.exe").exists()


def test_download_failure_exits_6_with_the_manual_route(tmp_path):
    result, calls, _ = _run(tmp_path, _action("msert_scan").command, download_error=True)
    assert result.returncode == 6
    assert "ScannerCache" in result.stdout and "msert.exe" in result.stdout
    assert _named(calls, "Start-Process") == []


def test_msert_scan_is_detect_only_and_reads_only_this_runs_log(tmp_path):
    result, calls, job = _run(tmp_path, _action("msert_scan").command, signer="Microsoft Corporation",
                              tool_log=LOGS["msert_clean_log"], old_msert_log=LOGS["msert_dirty_log"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ArgumentList=/N /Q" in _named(calls, "Start-Process")[0]
    assert [f["severity"] for f in _findings(result.stdout)] == ["ok"]
    saved = (job / "logs" / "msert.log").read_text(encoding="utf-8-sig")
    assert "Wacatac" not in saved and "No infection found" in saved


def test_msert_detection_names_the_threat(tmp_path):
    result, _, _ = _run(tmp_path, _action("msert_scan").command, signer="Microsoft Corporation",
                        tool_log=LOGS["msert_dirty_log"])
    [finding] = _findings(result.stdout)
    assert finding["severity"] == "attention" and finding["fix"] == ["msert_clean"]
    assert "Trojan:Win32/Wacatac.B!ml" in finding["msg_en"]


def test_msert_clean_removes_and_leaves_the_verdict_to_the_next_scan(tmp_path):
    result, calls, _ = _run(tmp_path, _action("msert_clean").command, signer="Microsoft Corporation",
                            tool_log=LOGS["msert_dirty_log"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ArgumentList=/Q" in _named(calls, "Start-Process")[0]
    assert "/N" not in _named(calls, "Start-Process")[0]
    assert _findings(result.stdout) == []
    assert "scan again" in result.stdout


def test_msert_cached_copy_older_than_10_days_is_downloaded_again(tmp_path):
    result, calls, _ = _run(tmp_path, _action("msert_scan").command, signer="Microsoft Corporation", cached=True,
                            cache_age_days=11, tool_log=LOGS["msert_clean_log"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert _named(calls, "Invoke-WebRequest")


def test_msert_refuses_arm64(tmp_path):
    result, calls, _ = _run(tmp_path, _action("msert_scan").command, arch="ARM64")
    assert result.returncode == 4
    assert calls == []


def test_kvrt_scan_is_interactive_and_never_silent(tmp_path):
    result, calls, job = _run(tmp_path, _action("kvrt_scan").command, signer="AO Kaspersky Lab",
                              tool_log="Detected: 0\n")
    assert result.returncode == 0, result.stdout + result.stderr
    start = _named(calls, "Start-Process")[0]
    assert "-accepteula -dontencrypt -d" in start
    assert "-silent" not in start and "WindowStyle" not in start
    assert (job / "logs" / "report.txt").exists()
    assert [f["severity"] for f in _findings(result.stdout)] == ["ok"]


def test_kvrt_unreadable_report_gives_no_finding(tmp_path):
    result, _, _ = _run(tmp_path, _action("kvrt_scan").command, signer="AO Kaspersky Lab", tool_log="<klr/>\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _findings(result.stdout) == []
    assert "could not read the result" in result.stdout


def test_kvrt_clean_runs_silent(tmp_path):
    result, calls, _ = _run(tmp_path, _action("kvrt_clean").command, signer="AO Kaspersky Lab", tool_log="x\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "-silent -processlevel 2" in _named(calls, "Start-Process")[0]


def test_no_log_means_the_tool_did_not_finish(tmp_path):
    result, _, _ = _run(tmp_path, _action("adwcleaner_scan").command, tool_log="")
    # An empty log file is still copied - but empty text means no result.
    assert result.returncode == 3
    assert _findings(result.stdout) == []


@pytest.mark.parametrize("action_id", [f"{t}_{m}" for t in TOOLS for m in ("scan", "clean")])
def test_preview_downloads_and_runs_nothing(tmp_path, action_id):
    result, calls, _ = _run(tmp_path, _action(action_id).preview_command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == []
    assert "Would run:" in result.stdout or "Would refuse" in result.stdout


def test_lib_parses_without_powershell_7_only_syntax(tmp_path):
    checker = (
        "$t = $null; $e = $null; "
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({_q(str(LIB_PATH))}, [ref]$t, [ref]$e); "
        "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $x.Message) } }; "
        "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
        "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
        "if ($ps7.Count) { Write-Output 'ERR PowerShell 7-only syntax' }; Write-Output PARSE_DONE"
    )
    result = subprocess.run([_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", checker],
                            capture_output=True, text=True, timeout=120,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []
