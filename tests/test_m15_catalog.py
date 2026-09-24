import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m15_boot_platform" / "actions.yaml"


def test_m15_catalog_loads_8_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m15_boot_platform"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 8


def test_m15_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
        "boot_secureboot_ca2023_status",
        "boot_winre_status",
    }
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "boot_clear_safe_mode_flag",
        "boot_enable_f8_legacy_recovery",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m15_catalog_reversible_actions_have_undo():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    for undoable in ("boot_enable_f8_legacy_recovery", "boot_clear_safe_mode_flag"):
        assert by_id[undoable].undo_command is not None, undoable
    for not_undoable in (
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
        "boot_secureboot_ca2023_status",
        "boot_winre_status",
    ):
        assert by_id[not_undoable].undo_command is None, not_undoable


def test_m15_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "boot_bcd_report",
        "boot_tpm_status",
        "boot_bitlocker_status",
        "boot_safe_mode_status",
        "boot_clear_safe_mode_flag",
        "boot_enable_f8_legacy_recovery",
        "boot_secureboot_ca2023_status",
        "boot_winre_status",
    }


def test_m15_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m15_catalog_clear_safe_mode_flag_does_not_overwrite_backup_on_second_run():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "boot_clear_safe_mode_flag")
    assert "if (-not (Test-Path $bk))" in action.command


def test_m15_catalog_clear_safe_mode_flag_locks_down_its_backup_folder():
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "boot_clear_safe_mode_flag")
    assert "icacls" in action.command
    assert "S-1-5-32-544" in action.command
    assert "S-1-5-18" in action.command
    assert "$env:USERDOMAIN" in action.command
    # Must be unconditional (icacls is idempotent) so an already-existing
    # unhardened root from an older install gets fixed too.
    assert "Test-Path $root" not in action.command


# --- G07 / G18: Secure Boot 2023 certificates and WinRE readiness ----------
#
# Both commands run below in PowerShell against stubbed tools: every cmdlet
# or executable they touch is shadowed by a function (functions win command
# lookup) and the script exits 97 unless each name really resolves to the
# stub, so a test run never queries the host's firmware or recovery setup.
# The stubbed native tools answer with Slovak labels on purpose - a command
# that keyed off English text would fail.

STUB_GUARD_EXIT = 97
SB_ID = "boot_secureboot_ca2023_status"
WINRE_ID = "boot_winre_status"
WINRE_GUID = "{3c9a8e1d-5b7f-11ef-9c2a-a4bb6d0e1f22}"
ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
WINRE_PATH = "\\\\?\\GLOBALROOT\\device\\harddisk0\\partition4\\Recovery\\WindowsRE"
SECUREBOOT_KEY = "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecureBoot"


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_bytes(data: bytes) -> str:
    return f"[Convert]::FromBase64String('{base64.b64encode(data).decode()}')"


def _run_ps(stubs: list, stubbed_names: list, command: str, extra_env=None):
    guard = (
        "foreach ($n in " + ", ".join(_ps_quote(n) for n in stubbed_names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + "; ".join(stubs + [guard]) + "; " + command
    env = dict(os.environ, **(extra_env or {}))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result


def _verdict(stdout: str) -> str:
    lines = [line for line in stdout.splitlines() if line.startswith("VERDICT: ")]
    assert len(lines) == 1, stdout
    return lines[0]


# Firmware db/KEK as Get-SecureBootUEFI returns them: EFI signature lists
# holding DER certificates, where the subject CN is plain ASCII between
# binary fields. The UTF-16LE variant covers the second encoding searched.
def _esl(*subjects: str, utf16: bool = False) -> bytes:
    out = b"\xa1\x59\xc0\xa5\xe4\x94\xa7\x4a\x87\xb5\xab\x15\x5c\x2b\xf0\x72"
    for subject in subjects:
        out += b"\x30\x82\x05\x13\x06\x03\x55\x04\x03\x13"
        out += subject.encode("utf-16-le") if utf16 else subject.encode("ascii")
        out += b"\x00\xff"
    return out


DB_2011 = _esl("Microsoft Windows Production PCA 2011", "Microsoft Corporation UEFI CA 2011")
DB_2023 = _esl("Microsoft Windows Production PCA 2011", "Windows UEFI CA 2023", "Microsoft UEFI CA 2023")
KEK_2011 = _esl("Microsoft Corporation KEK CA 2011")
KEK_2023 = _esl("Microsoft Corporation KEK CA 2011", "Microsoft Corporation KEK 2K CA 2023")
UPDATED = {"UEFICA2023Status": "Updated", "WindowsUEFICA2023Capable": 2}


def _run_secureboot(tmp_path, confirm="on", db=DB_2023, kek=KEK_2023, firmware_denied=False,
                    state=None, servicing=None, root=None, issuer="CN=Windows UEFI CA 2023, O=Microsoft Corporation"):
    """confirm: 'on' | 'off' | 'unsupported' | 'denied'. state/servicing/root:
    registry values under SecureBoot\\State, SecureBoot\\Servicing and
    SecureBoot (None = key absent)."""
    def reg(values):
        if values is None:
            return "$null"
        return "[pscustomobject]@{ " + "; ".join(
            f"{k} = {v if isinstance(v, int) else _ps_quote(v)}" for k, v in values.items()
        ) + " }"

    denied = "throw [System.UnauthorizedAccessException]::new('Prístup bol odmietnutý.')"
    confirm_body = {
        "on": "$true",
        "off": "$false",
        # Legacy BIOS: the real cmdlet throws "Cmdlet not supported on this
        # platform" as a PlatformNotSupportedException (localized message).
        "unsupported": "throw [System.PlatformNotSupportedException]::new('Rutina cmdlet nie je na tejto platforme podporovaná.')",
        "denied": denied,
    }[confirm]
    firmware = denied if firmware_denied else (
        f"if ($Name -eq 'db') {{ [pscustomobject]@{{ Bytes = {_ps_bytes(db)} }} }} "
        f"elseif ($Name -eq 'KEK') {{ [pscustomobject]@{{ Bytes = {_ps_bytes(kek)} }} }} "
        "else { throw 'Premenná neexistuje.' }"
    )
    stubs = [
        f"function Confirm-SecureBootUEFI {{ [CmdletBinding()] param() {confirm_body} }}",
        f"function Get-SecureBootUEFI {{ [CmdletBinding()] param([string] $Name) {firmware} }}",
        "function Get-ItemProperty { [CmdletBinding()] param([string] $Path) "
        f"switch ($Path) {{ {_ps_quote(SECUREBOOT_KEY)} {{ {reg(root)} }} "
        f"{_ps_quote(SECUREBOOT_KEY + chr(92) + 'Servicing')} {{ {reg(servicing)} }} "
        f"{_ps_quote(SECUREBOOT_KEY + chr(92) + 'State')} {{ {reg(state)} }} }} }}",
        "function Get-AuthenticodeSignature { [CmdletBinding()] param([string] $FilePath) "
        f"[pscustomobject]@{{ SignerCertificate = [pscustomobject]@{{ Issuer = {_ps_quote(issuer)} }} }} }}",
    ]
    names = ["Confirm-SecureBootUEFI", "Get-SecureBootUEFI", "Get-ItemProperty", "Get-AuthenticodeSignature"]
    return _run_ps(stubs, names, _action(SB_ID).command, extra_env={"SystemRoot": str(tmp_path / "Windows")})


def test_secureboot_legacy_bios_is_not_applicable(tmp_path):
    result = _run_secureboot(tmp_path, confirm="unsupported")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Secure Boot: unsupported" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: NOT APPLICABLE - legacy BIOS")


def test_secureboot_off_is_not_applicable(tmp_path):
    result = _run_secureboot(tmp_path, confirm="off")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Secure Boot: off" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: NOT APPLICABLE - Secure Boot is turned off")


def test_secureboot_certs_present_and_boot_manager_switched_is_ok(tmp_path):
    result = _run_secureboot(tmp_path, servicing=UPDATED, root={"AvailableUpdates": 0})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "db - Windows UEFI CA 2023: present" in result.stdout
    assert "db - Microsoft UEFI CA 2023: present" in result.stdout
    assert "KEK - Microsoft Corporation KEK 2K CA 2023: present" in result.stdout
    assert "UEFICA2023Status: Updated" in result.stdout
    assert "WindowsUEFICA2023Capable: 2" in result.stdout
    assert "AvailableUpdates: 0x0000" in result.stdout
    assert "signed by: CN=Windows UEFI CA 2023" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")


def test_secureboot_finds_certificate_names_stored_as_utf16(tmp_path):
    # Odd leading byte: the UTF-16 text sits at an odd offset in the buffer.
    result = _run_secureboot(
        tmp_path,
        db=b"\x01" + _esl("Windows UEFI CA 2023", utf16=True),
        kek=_esl("Microsoft Corporation KEK 2K CA 2023", utf16=True),
        servicing=UPDATED,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: OK")


def test_secureboot_missing_certs_needs_action_before_pca_2011_expiry(tmp_path):
    result = _run_secureboot(
        tmp_path, db=DB_2011, kek=KEK_2011,
        servicing={"UEFICA2023Status": "NotStarted", "UEFICA2023Error": 0},
        root={"AvailableUpdates": 0x5944},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    verdict = _verdict(result.stdout)
    assert verdict.startswith("VERDICT: ACTION NEEDED")
    assert "2026-10-19" in verdict
    assert "db - Windows UEFI CA 2023: MISSING" in result.stdout
    assert "KEK - Microsoft Corporation KEK 2K CA 2023: MISSING" in result.stdout
    assert "Windows UEFI CA 2023 is missing from the firmware db." in result.stdout
    assert "PC maker has to supply a BIOS/UEFI update" in result.stdout
    assert "AvailableUpdates: 0x5944" in result.stdout
    assert "Certificate updates are queued" in result.stdout
    assert "reported an error" not in result.stdout


def test_secureboot_db_updated_but_kek_missing_is_not_ok(tmp_path):
    # The typical stuck machine: Windows added the db certificate and even
    # switched the boot manager, but the PC maker never shipped the new KEK.
    result = _run_secureboot(tmp_path, db=DB_2023, kek=KEK_2011, servicing=UPDATED)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED")
    assert "KEK 2K CA 2023 is missing from KEK" in result.stdout
    assert "not yet booting with the 2023-signed boot manager" not in result.stdout


def test_secureboot_certs_present_but_boot_manager_not_switched_needs_action(tmp_path):
    result = _run_secureboot(
        tmp_path,
        # REG_DWORD above 0x7FFFFFFF arrives as a negative Int32.
        servicing={"UEFICA2023Status": "InProgress", "WindowsUEFICA2023Capable": 1,
                   "UEFICA2023Error": -2147024891, "UEFICA2023ErrorEvent": 1795},
        issuer="CN=Microsoft Windows Production PCA 2011, O=Microsoft Corporation",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED")
    assert "signed by: CN=Microsoft Windows Production PCA 2011" in result.stdout
    assert "not yet booting with the 2023-signed boot manager" in result.stdout
    assert "UEFICA2023Error: 0x80070005, UEFICA2023ErrorEvent: 1795" in result.stdout
    assert "reported an error" in result.stdout


def test_secureboot_not_elevated_falls_back_to_registry(tmp_path):
    # Confirm-SecureBootUEFI and Get-SecureBootUEFI need administrator rights;
    # the SecureBoot\State and Servicing registry values do not.
    result = _run_secureboot(
        tmp_path, confirm="denied", firmware_denied=True, state={"UEFISecureBootEnabled": 1}, servicing=UPDATED,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Secure Boot: on" in result.stdout
    assert "db/KEK: cannot be read" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")


def test_secureboot_not_elevated_and_not_updated_needs_action(tmp_path):
    result = _run_secureboot(
        tmp_path, confirm="denied", firmware_denied=True, state={"UEFISecureBootEnabled": 1},
        servicing={"UEFICA2023Status": "NotStarted"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED")


def test_secureboot_not_elevated_and_registry_off_is_not_applicable(tmp_path):
    result = _run_secureboot(tmp_path, confirm="denied", state={"UEFISecureBootEnabled": 0})
    assert result.returncode == 0, result.stdout + result.stderr
    assert _verdict(result.stdout).startswith("VERDICT: NOT APPLICABLE")


def test_secureboot_state_unreadable_fails_the_check(tmp_path):
    result = _run_secureboot(tmp_path, confirm="denied", state=None)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not determine the Secure Boot state" in result.stdout
    assert "VERDICT" not in result.stdout


def test_secureboot_firmware_unreadable_without_servicing_state_fails_the_check(tmp_path):
    result = _run_secureboot(tmp_path, firmware_denied=True, servicing=None)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT" not in result.stdout


# --- WinRE -----------------------------------------------------------------

def _reagent_xml(state: int) -> str:
    location = "\\Recovery\\WindowsRE" if state else ""
    return (
        "<?xml version='1.0' encoding='utf-8' standalone='yes'?>\n"
        "<WindowsRE version=\"2.0\">\n"
        f"  <WinreBCD id=\"{WINRE_GUID if state else ZERO_GUID}\"/>\n"
        f"  <WinreLocation path=\"{location}\" id=\"0\" offset=\"0\" guid=\"{ZERO_GUID}\"/>\n"
        f"  <ImageLocation path=\"\" id=\"0\" offset=\"0\" guid=\"{ZERO_GUID}\"/>\n"
        f"  <InstallState state=\"{state}\"/>\n"
        "</WindowsRE>\n"
    )


def _reagentc_info(enabled: bool) -> list:
    # Slovak labels on purpose: only the device path and the GUID may be used.
    return [
        "Konfigurácia prostredia Windows Recovery Environment (Windows RE) a obnovenia systému:",
        "",
        f"    Stav prostredia Windows RE:         {'Povolené' if enabled else 'Zakázané'}",
        f"    Umiestnenie prostredia Windows RE:  {WINRE_PATH if enabled else ''}",
        f"    Identifikátor BCD:                  {(WINRE_GUID if enabled else ZERO_GUID)[1:-1]}",
        "    Umiestnenie obrazu obnovenia:",
        "    Index obrazu obnovenia:             0",
        "REAGENTC.EXE: Operácia bola úspešná.",
    ]


QMR_XML = (
    "<?xml version='1.0' encoding='utf-8'?>\n\n<WindowsRE>\n    <WifiCredential>\n"
    "        <Wifi ssid=\"KlientWiFi\" password=\"TajneHeslo123\" />\n    </WifiCredential>\n"
    "    <CloudRemediation state=\"1\" />\n"
    "    <AutoRemediation state=\"0\" totalwaittime=\"2400\" waitinterval=\"120\"/>\n</WindowsRE>\n\n"
    "REAGENTC.EXE: Operácia bola úspešná.\n"
)


def _run_winre(tmp_path, enabled=True, xml_state=None, reagentc_exit=0, wim=False,
               part_free_mb=600, qmr=QMR_XML, qmr_exit=0):
    """xml_state: None = no ReAgent.xml, else its InstallState (1/0)."""
    recovery = tmp_path / "Windows" / "System32" / "Recovery"
    recovery.mkdir(parents=True)
    if xml_state is not None:
        (recovery / "ReAgent.xml").write_text(_reagent_xml(xml_state), encoding="utf-8")
    if wim:
        (recovery / "Winre.wim").write_bytes(b"MSWIM\x00")
    temp = tmp_path / "temp"
    temp.mkdir()
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    lg = _ps_quote(str(log))
    info = "; ".join(_ps_quote(line) for line in _reagentc_info(enabled))
    stubs = [
        "function reagentc.exe { "
        f"Add-Content -Path {lg} -Value ('reagentc ' + ($args -join ' ')); {info}; $global:LASTEXITCODE = {reagentc_exit} }}",
        "function Start-Process { [CmdletBinding()] param([string] $FilePath, [string[]] $ArgumentList, "
        "[string] $RedirectStandardOutput, [switch] $NoNewWindow, [switch] $Wait, [switch] $PassThru) "
        f"Add-Content -Path {lg} -Value ('Start-Process ' + $FilePath + ' ' + ($ArgumentList -join ' ')); "
        f"Set-Content -LiteralPath $RedirectStandardOutput -Value {_ps_quote(qmr)} -Encoding UTF8; "
        f"[pscustomobject]@{{ ExitCode = {qmr_exit} }} }}",
        "function Get-Partition { [CmdletBinding()] param([int] $DiskNumber, [int] $PartitionNumber) "
        f"Add-Content -Path {lg} -Value ('Get-Partition ' + $DiskNumber + ' ' + $PartitionNumber); "
        "[pscustomobject]@{ Type = 'Recovery'; Size = 750MB } }",
        "function Get-Volume { [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)] $InputObject) "
        f"process {{ [pscustomobject]@{{ SizeRemaining = {part_free_mb}MB }} }} }}",
    ]
    names = ["reagentc.exe", "Start-Process", "Get-Partition", "Get-Volume"]
    env = {"SystemRoot": str(tmp_path / "Windows"), "TMPDIR": str(temp), "TEMP": str(temp), "TMP": str(temp)}
    result = _run_ps(stubs, names, _action(WINRE_ID).command, extra_env=env)
    return result, log.read_text(encoding="utf-8-sig").splitlines(), temp


def test_winre_enabled_is_ok_with_partition_and_qmr_state(tmp_path):
    result, calls, _ = _run_winre(tmp_path, enabled=True, xml_state=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ReAgent.xml: InstallState=1, WinreBCD={WINRE_GUID}" in result.stdout
    assert "WinRE partition: disk 0, partition 4, type Recovery, size 750 MB, free 600 MB" in result.stdout
    assert "Get-Partition 0 4" in calls
    assert "Quick Machine Recovery: cloud remediation on, auto remediation off" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK - WinRE is enabled;")


def test_winre_qmr_settings_never_leak_the_wifi_password_and_temp_file_is_deleted(tmp_path):
    result, calls, temp = _run_winre(tmp_path, enabled=True, xml_state=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TajneHeslo123" not in result.stdout + result.stderr
    assert "KlientWiFi" not in result.stdout + result.stderr
    assert [c for c in calls if c.startswith("Start-Process reagentc.exe /getrecoverysettings")]
    assert list(temp.iterdir()) == []


def test_winre_low_free_space_on_partition_is_flagged(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=True, xml_state=1, part_free_mb=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "free 90 MB" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK - WinRE is enabled, but its partition has less than 250 MB free")


def test_winre_disabled_with_wim_present_needs_reagentc_enable(tmp_path):
    result, calls, _ = _run_winre(tmp_path, enabled=False, xml_state=0, wim=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Winre\.wim in .*: present", result.stdout)
    assert not [c for c in calls if c.startswith("Get-Partition")]
    verdict = _verdict(result.stdout)
    assert verdict.startswith("VERDICT: ACTION NEEDED - WinRE is disabled but Winre.wim is present")
    assert "reagentc /enable" in verdict


def test_winre_disabled_without_wim_needs_install_media(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=False, xml_state=0, wim=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"Winre\.wim in .*: MISSING", result.stdout)
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED - WinRE is disabled and Winre.wim is missing")


def test_winre_reagentc_decides_even_when_reagent_xml_is_missing(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=True, xml_state=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ReAgent.xml: not found" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")


def test_winre_falls_back_to_reagent_xml_when_reagentc_fails(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=True, xml_state=0, reagentc_exit=2, wim=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "using ReAgent.xml InstallState" in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: ACTION NEEDED - WinRE is disabled")


def test_winre_fails_the_check_when_neither_source_is_readable(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=True, xml_state=None, reagentc_exit=5)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Could not determine the WinRE state" in result.stdout
    assert "VERDICT" not in result.stdout


def test_winre_qmr_absent_on_older_builds(tmp_path):
    result, _, _ = _run_winre(tmp_path, enabled=True, xml_state=1, qmr="Neplatný parameter.\n", qmr_exit=87)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Quick Machine Recovery: not available on this Windows build." in result.stdout
    assert _verdict(result.stdout).startswith("VERDICT: OK")


# --- static checks -----------------------------------------------------------

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


def test_m15_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    module = load_module(CATALOG_PATH)
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in module.actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    env = dict(os.environ, PFSCRIPTS_FILE=str(scripts_file))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=env, capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


# The only patterns the two commands may match: a FullyQualifiedErrorId
# fragment, the reagentc device path, a GUID and the QMR XML element names -
# none of them is display-language text.
ALLOWED_PATTERNS = {
    "NotSupported",
    r"(?i)\\\\\?\\GLOBALROOT\\device\\harddisk(\d+)\\partition(\d+)\\[^\s]*",
    r"(?!\{?0{8}-0{4}-0{4}-0{4}-0{12})\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?",
    r'<CloudRemediation\s[^>]*state="(\d+)"',
    r'<AutoRemediation\s[^>]*state="(\d+)"',
}


@pytest.mark.parametrize("action_id", [SB_ID, WINRE_ID])
def test_secureboot_and_winre_never_match_localized_text(action_id):
    command = _action(action_id).command
    patterns = re.findall(r"-match '([^']*)'", command) + re.findall(r"\[regex\]::Match\(\$\w+, '([^']*)'\)", command)
    assert patterns
    assert set(patterns) <= ALLOWED_PATTERNS, set(patterns) - ALLOWED_PATTERNS
    # No other way of matching text, and exception messages (localized) are
    # never looked at - only exception types and FullyQualifiedErrorId.
    for forbidden in ("Select-String", "-like", "-imatch", "-cmatch", "-notmatch", "-replace", "-split",
                      "Where-Object", ".Message", "[regex]::Matches", "IndexOf"):
        assert forbidden not in command, f"{action_id}: {forbidden}"
    # The English reagentc labels must not reappear in any form.
    for label in ("Windows RE status", "Windows RE location", "Enabled", "Disabled", "Operation Successful",
                  "BCD) identifier", "Recovery image location"):
        assert label not in _action(WINRE_ID).command, label


def test_secureboot_and_winre_are_read_only():
    for action_id in (SB_ID, WINRE_ID):
        action = _action(action_id)
        assert action.risk == RiskLevel.SAFE
        assert action.undo_command is None and action.preview_command is None
        for verb in ("Set-", "New-Item", "reg add", "bcdedit", "reagentc.exe /enable", "'/enable'", "/disable", "Start-ScheduledTask",
                     "/setrecoverysettings", "/setreimage"):
            assert verb not in action.command, f"{action_id}: {verb}"
    # The one Remove-Item deletes the QMR temp file holding the Wi-Fi password.
    winre = _action(WINRE_ID).command
    assert winre.count("Remove-Item") == 1
    assert "finally { Remove-Item -LiteralPath $qf" in winre
