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
