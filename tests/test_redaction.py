"""redaction.py - masking personal data for the client (research G20)."""

import json

import pytest

from portablefix.redaction import IP, KEY, MAC, SERIAL, SSID, USER, redact_data, redact_text


@pytest.mark.parametrize(("text", "expected"), [
    (r"Deleted C:\Users\jnovak\AppData\Local\Temp\a.tmp", rf"Deleted C:\Users\{USER}\AppData\Local\Temp\a.tmp"),
    # Spaces and diacritics in the profile folder name.
    (r"C:\Users\Ján Novák\Desktop\x.txt", rf"C:\Users\{USER}\Desktop\x.txt"),
    # Other drive letter, lower case, forward slashes.
    (r"d:\users\eva\Documents", rf"d:\users\{USER}\Documents"),
    ("C:/Users/eva/Downloads/setup.exe", f"C:/Users/{USER}/Downloads/setup.exe"),
    # The profile folder itself, at the end of a line / before a quote.
    (r"Profile: C:\Users\jnovak", rf"Profile: C:\Users\{USER}"),
    ('"C:\\Users\\jnovak" removed', f'"C:\\Users\\{USER}" removed'),
    (r"C:\Users\jnovak and more text", rf"C:\Users\{USER} and more text"),
    # JSON-escaped backslashes in text that could not be parsed.
    (r'{"output": "C:\\Users\\jnovak\\AppData"}', rf'{{"output": "C:\\Users\\{USER}\\AppData"}}'),
])
def test_user_names_in_paths_are_masked(text, expected):
    assert redact_text(text) == expected


@pytest.mark.parametrize("text", [
    r"C:\Users\Public\Desktop\shortcut.lnk",
    r"C:\Users\Default\NTUSER.DAT",
    r"C:\Users\All Users\Microsoft",
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\ProgramData\Users\x",
    "Users: 3",
])
def test_shared_profiles_and_other_paths_stay(text):
    assert redact_text(text) == text


def test_ipv4_addresses_are_masked_but_not_loopback_masks_or_public_resolvers():
    text = ("IPv4 192.168.1.10, gateway 192.168.1.1. link-local 169.254.12.3, public 85.237.1.9\n"
            "mask 255.255.255.0, loopback 127.0.0.1, none 0.0.0.0, multicast 224.0.0.251, dns 8.8.8.8 1.1.1.1")
    assert redact_text(text) == (
        f"IPv4 {IP}, gateway {IP}. link-local {IP}, public {IP}\n"
        "mask 255.255.255.0, loopback 127.0.0.1, none 0.0.0.0, multicast 224.0.0.251, dns 8.8.8.8 1.1.1.1"
    )


def test_an_address_after_a_word_ending_in_ver_is_still_masked():
    # "Server" ends in "ver" - only a real version label keeps the number.
    out = redact_text("DNSServer : 192.168.1.1\nDNS Server: 10.0.0.2\nFileVersion : 6.3.9600.1")
    assert out == f"DNSServer : {IP}\nDNS Server: {IP}\nFileVersion : 6.3.9600.1"


@pytest.mark.parametrize("text", [
    # Windows and file versions - never addresses.
    "Microsoft Windows 10.0.26100.1",
    "OS build 10.0.19045.4291",
    "PowerShell 5.1.22621.4391",
    "Driver 31.0.101.5186 (1.2.3.4.5)",
    "App v1.2.3.4",
    "Version=2.0.0.0",
    "Not an address: 999.300.1.1",
    "KB5034441 installed 2026-09-25",
    # Hex hashes and GUIDs.
    "SHA256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "{6F9619FF-8B86-D011-B42D-00C04FC964FF}",
    "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f'",
    "0x80070005 HRESULT, Event ID 7031",
    # Times, timestamps and PowerShell's static member syntax.
    "Started 12:34:56, took 00:01:02",
    "2026-09-25T12:34:56.123456+00:00",
    "[System.Net.Dns]::GetHostEntry($ip); [Math]::Abs(-1); [Environment]::Exit(1)",
    # Computer names, product names and words with dashes.
    "DESKTOP-AB12CDE / LAPTOP-7Q2K9",
    "Wi-Fi, x64-based PC, UTF-8, ARM64",
    "Serial ports: 2",
    "12345-67890",
])
def test_no_false_positives(text):
    assert redact_text(text) == text


def test_ipv6_addresses_are_masked_but_not_loopback():
    text = ("Link-local fe80::1c2b:3d4e:5f60:7a8b%12\n"
            "Global 2001:db8:85a3::8a2e:370:7334 and 2a02:8308:a001:1d00:1c2b:3d4e:5f60:7a8b\n"
            "Loopback ::1")
    assert redact_text(text) == f"Link-local {IP}\nGlobal {IP} and {IP}\nLoopback ::1"


def test_mac_addresses_are_masked_in_both_notations():
    text = "Ethernet  Up  1 Gbps  00-1A-2B-3C-4D-5E\nBSSID : aa:bb:cc:dd:ee:ff"
    assert redact_text(text) == f"Ethernet  Up  1 Gbps  {MAC}\nBSSID : {MAC}"


def test_serial_numbers_are_masked_by_their_property_name():
    text = (
        "Manufacturer      : Dell Inc.\r\n"
        "SMBIOSBIOSVersion : 1.21.0\r\n"
        "SerialNumber      : 5CD1234XYZ\r\n"
        "ReleaseDate       : 01.02.2024\r\n"
    )
    out = redact_text(text)
    assert f"SerialNumber      : {SERIAL}\r\n" in out
    assert "5CD1234XYZ" not in out
    # Everything else, the BIOS version included, stays.
    assert "Dell Inc." in out and "1.21.0" in out and "01.02.2024" in out


@pytest.mark.parametrize(("text", "expected"), [
    ('{"SerialNumber": "WD-WX12A3456789"}', f'{{"SerialNumber": "{SERIAL}"}}'),
    ("SerialNumber=ABC123", f"SerialNumber={SERIAL}"),
    ("Disk Serial Number: S4EVNX0N123456", f"Disk Serial Number: {SERIAL}"),
    ("DiskSerialNumber : 0025_38B5_71B2_1234.", f"DiskSerialNumber : {SERIAL}"),
])
def test_serial_number_variants(text, expected):
    assert redact_text(text) == expected


def test_empty_serial_number_does_not_swallow_the_next_line():
    text = "SerialNumber : \r\nReleaseDate : 2024"
    assert redact_text(text) == text


def test_a_serial_number_is_masked_wherever_it_repeats():
    text = "SerialNumber : 5CD1234XYZ\nService tag 5CD1234XYZ on the sticker"
    assert redact_text(text) == f"SerialNumber : {SERIAL}\nService tag {SERIAL} on the sticker"


def test_product_keys_and_fragments_are_masked():
    text = (
        "Name              : Windows(R), Professional edition\n"
        "LicenseStatus     : 1\n"
        "PartialProductKey : 3V66T\n"
        "Full key VK7JG-NPHTM-C97JM-9MPGT-3V66T, masked XXXXX-XXXXX-XXXXX-XXXXX-3V66T, product ID 00330-80000-00000-AA946"
    )
    out = redact_text(text)
    assert f"PartialProductKey : {KEY}\n" in out
    assert out.endswith(f"Full key {KEY}, masked {KEY}, product ID {KEY}")
    assert "LicenseStatus     : 1" in out


def test_wifi_ssid_and_the_profile_naming_it_are_masked():
    text = (
        "    Name                   : Wi-Fi\r\n"
        "    SSID                   : Novakovci Home 5G\r\n"
        "    BSSID                  : aa:bb:cc:dd:ee:ff\r\n"
        "    Profile                : Novakovci Home 5G\r\n"
        "    Signal                 : 92%\r\n"
    )
    out = redact_text(text)
    assert "Novakovci" not in out
    assert f"SSID                   : {SSID}\r\n" in out
    assert f"Profile                : {SSID}\r\n" in out
    assert f"BSSID                  : {MAC}\r\n" in out
    assert "Name                   : Wi-Fi" in out and "92%" in out


def test_a_short_ssid_is_not_masked_everywhere():
    # A two-letter network name would eat ordinary words elsewhere.
    out = redact_text("SSID : AB\nABC and AB")
    assert out == f"SSID : {SSID}\nABC and AB"


def test_kept_values_are_never_masked():
    # A computer name can look like a product-key fragment, and the
    # technician/client typed their fields on purpose.
    text = "Computer ABCD1-EFGH2 checked, client ABC12-DEF34, other ZZZZ1-YYYY2"
    out = redact_text(text, keep=["abcd1-efgh2", "ABC12-DEF34"])
    assert out == f"Computer ABCD1-EFGH2 checked, client ABC12-DEF34, other {KEY}"


def test_redaction_is_idempotent():
    text = (r"C:\Users\jnovak\x 192.168.1.10 00-1A-2B-3C-4D-5E SerialNumber : ABC123 "
            "VK7JG-NPHTM-C97JM-9MPGT-3V66T fe80::1c2b:3d4e")
    once = redact_text(text)
    assert redact_text(once) == once
    for secret in ("jnovak", "192.168.1.10", "00-1A-2B", "ABC123", "VK7JG", "fe80"):
        assert secret not in once


@pytest.mark.parametrize("value", ["", None, 5])
def test_non_text_passes_through(value):
    assert redact_text(value) == value


def test_redact_data_walks_json_and_keeps_structure():
    data = {
        "run_id": "20260925T100000-abcd",
        "hostname": "DESKTOP-AB12C",
        "generated_at": "2026-09-25T10:00:00+00:00",
        "job": {"technician": "Ján", "client": r"C:\Users\ignored-on-purpose", "note": "192.168.1.10"},
        "snapshot_before": {"free_gb": 10.5, "temp_bytes": 123},
        "actions": [
            {"timestamp": "2026-09-25T10:00:01+00:00", "module_id": "m06_network", "action_id": "net_wifi_diagnostics",
             "exit_code": 0, "dry_run": False, "output": "SSID : CafeNet\nBSSID : aa:bb:cc:dd:ee:ff"},
            {"timestamp": "2026-09-25T10:00:02+00:00", "module_id": "m06_network", "action_id": "net_ip_config_report",
             "exit_code": 0, "dry_run": False, "output": "Connected to CafeNet with 10.0.0.23", "warned": None},
        ],
    }
    out = redact_data(data)
    assert out is not data
    # Untouched input: redaction never changes what it was given.
    assert data["actions"][0]["output"].startswith("SSID : CafeNet")
    assert out["run_id"] == data["run_id"] and out["hostname"] == data["hostname"]
    assert out["job"] == data["job"]
    assert out["snapshot_before"] == {"free_gb": 10.5, "temp_bytes": 123}
    assert out["actions"][0]["output"] == f"SSID : {SSID}\nBSSID : {MAC}"
    # The SSID named by key in one action is masked in the other one too.
    assert out["actions"][1]["output"] == f"Connected to {SSID} with {IP}"
    assert out["actions"][1]["warned"] is None and out["actions"][1]["exit_code"] == 0
    json.dumps(out)


def test_redact_data_keeps_the_hostname_inside_command_output():
    data = {"hostname": "PC-AB123-CD456", "output": "Computer PC-AB123-CD456 at 192.168.0.5"}
    out = redact_data(data, keep=["PC-AB123-CD456"])
    assert out["output"] == f"Computer PC-AB123-CD456 at {IP}"
