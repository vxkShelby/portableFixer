import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from portablefix import sysinfo


def test_cpu_load_sampler_first_call_returns_none_then_a_percentage():
    sampler = sysinfo.CpuLoadSampler()
    assert sampler.sample() is None
    time.sleep(0.2)
    value = sampler.sample()
    assert value is not None
    assert 0.0 <= value <= 100.0


def test_get_ram_usage_gb_used_is_less_than_total():
    used, total = sysinfo.get_ram_usage_gb()
    assert 0 < used < total


def test_get_static_info_returns_populated_fields():
    info = sysinfo.get_static_info()
    assert info.os_name
    assert info.cpu_name
    assert info.cpu_cores >= 1


def test_read_hardware_sensors_degrades_gracefully_without_vendored_dll(tmp_path):
    sysinfo._hw_init_attempted = False
    sysinfo._hw_computer = None
    sysinfo._hw_init_error = None
    try:
        result = sysinfo.read_hardware_sensors(tmp_path)
        error = sysinfo.hardware_monitor_error()
    finally:
        sysinfo._hw_init_attempted = False
        sysinfo._hw_computer = None
        sysinfo._hw_init_error = None
    assert result == {
        "cpu_clock_mhz": None,
        "gpu_name": None,
        "gpu_load_percent": None,
        "gpu_temp_c": None,
        "gpu_clock_mhz": None,
        "gpu_vram_used_gb": None,
        "gpu_vram_total_gb": None,
    }
    assert error is not None


class _FakeSensor:
    def __init__(self, sensor_type, name, value):
        self.SensorType = sensor_type
        self.Name = name
        self.Value = value


class _FakeHardware:
    def __init__(self, hardware_type, name, sensors):
        self.HardwareType = hardware_type
        self.Name = name
        self.Sensors = sensors

    def Update(self):
        pass


class _FakeComputer:
    def __init__(self, hardware):
        self.Hardware = hardware


def test_read_hardware_sensors_does_not_mix_fields_from_different_gpus(tmp_path):
    igpu = _FakeHardware("GpuIntel", "Intel Iris Xe", [
        _FakeSensor("Load", "GPU Core", 5.0),
        _FakeSensor("Temperature", "GPU Core", 40.0),
    ])
    dgpu = _FakeHardware("GpuNvidia", "NVIDIA RTX 4070", [
        _FakeSensor("Load", "GPU Core", 80.0),
        _FakeSensor("Temperature", "GPU Core", 70.0),
        _FakeSensor("Clock", "GPU Core", 1800.0),
    ])
    fake_computer = _FakeComputer([igpu, dgpu])
    with patch("portablefix.sysinfo.init_hardware_monitor", return_value=fake_computer):
        result = sysinfo.read_hardware_sensors(tmp_path)
    # The busier GPU's fields must all come from that same GPU, never a mix.
    assert result["gpu_name"] == "NVIDIA RTX 4070"
    assert result["gpu_load_percent"] == 80.0
    assert result["gpu_temp_c"] == 70.0
    assert result["gpu_clock_mhz"] == 1800.0


def test_init_hardware_monitor_retries_with_a_different_assets_dir(tmp_path):
    sysinfo._hw_init_attempted = False
    sysinfo._hw_computer = None
    sysinfo._hw_init_error = None
    sysinfo._hw_init_assets_dir = None
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    try:
        sysinfo.init_hardware_monitor(tmp_path)
        first_error = sysinfo.hardware_monitor_error()
        sysinfo.init_hardware_monitor(other_dir)
        second_error = sysinfo.hardware_monitor_error()
    finally:
        sysinfo._hw_init_attempted = False
        sysinfo._hw_computer = None
        sysinfo._hw_init_error = None
        sysinfo._hw_init_assets_dir = None
    # Both attempts genuinely ran (no vendored DLL in either dir) rather than
    # the second silently reusing the first attempt's cached failure.
    assert "not bundled" in first_error
    assert "not bundled" in second_error


def test_ping_once_parses_time_from_ping_output():
    fake = MagicMock()
    fake.stdout = (
        "Pinging 8.8.8.8 with 32 bytes of data:\n"
        "Reply from 8.8.8.8: bytes=32 time=21ms TTL=115\n"
    )
    with patch("portablefix.sysinfo.subprocess.run", return_value=fake):
        assert sysinfo.ping_once() == 21.0


def test_ping_once_returns_none_on_timeout():
    with patch("portablefix.sysinfo.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("ping", 2)):
        assert sysinfo.ping_once() is None


def test_check_vpn_status_returns_adapter_name_when_present():
    fake = MagicMock()
    fake.stdout = "WireGuard Tunnel\n"
    with patch("portablefix.sysinfo.subprocess.run", return_value=fake):
        assert sysinfo.check_vpn_status() == "WireGuard Tunnel"


def test_check_vpn_status_returns_empty_string_when_nothing_found():
    fake = MagicMock()
    fake.stdout = "\n"
    with patch("portablefix.sysinfo.subprocess.run", return_value=fake):
        assert sysinfo.check_vpn_status() == ""


def test_check_vpn_status_returns_none_on_timeout():
    with patch("portablefix.sysinfo.subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("powershell", 10)):
        assert sysinfo.check_vpn_status() is None


def test_check_vpn_status_script_is_valid_powershell_and_checks_native_and_adapter_vpns():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return MagicMock(stdout="")

    with patch("portablefix.sysinfo.subprocess.run", side_effect=fake_run):
        sysinfo.check_vpn_status()
    script = calls[0][-1]
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"[scriptblock]::Create(@'\n{script}\n'@) | Out-Null; Write-Output 'OK'"],
        capture_output=True, text=True, timeout=15,
    )
    assert "OK" in result.stdout, result.stderr
    assert "Get-VpnConnection" in script
    assert "Get-NetAdapter" in script


def test_run_speed_test_computes_mbps_from_elapsed_time():
    fake_resp = MagicMock()
    fake_resp.read.return_value = b"x" * 1_000_000
    fake_resp.__enter__.return_value = fake_resp

    def fake_urlopen(req, timeout):
        time.sleep(0.1)
        return fake_resp

    with patch("portablefix.sysinfo.urllib.request.urlopen", side_effect=fake_urlopen):
        mbps = sysinfo.run_speed_test(size_bytes=1_000_000)
    assert mbps is not None
    assert mbps > 0


def test_run_speed_test_returns_none_on_failure():
    with patch("portablefix.sysinfo.urllib.request.urlopen", side_effect=OSError("network down")):
        assert sysinfo.run_speed_test() is None
