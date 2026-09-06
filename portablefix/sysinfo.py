import ctypes
import socket
import subprocess
import sys
import time
import urllib.request
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal


@dataclass
class StaticInfo:
    os_name: str
    cpu_name: str
    cpu_cores: int
    local_ip: str
    ram_speed_mhz: int | None


@dataclass
class LiveStats:
    cpu_load_percent: float | None
    cpu_clock_mhz: float | None
    ram_used_gb: float
    ram_total_gb: float
    gpu_name: str | None
    gpu_load_percent: float | None
    gpu_temp_c: float | None
    gpu_clock_mhz: float | None
    gpu_vram_used_gb: float | None
    gpu_vram_total_gb: float | None


def _read_reg(hive, path: str, name: str) -> str | None:
    try:
        with winreg.OpenKey(hive, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value).strip()
    except OSError:
        return None


def get_static_info() -> StaticInfo:
    os_name = (
        _read_reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName")
        or "Windows"
    )
    display_version = _read_reg(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "DisplayVersion"
    )
    if display_version:
        os_name = f"{os_name} {display_version}"

    cpu_name = (
        _read_reg(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            "ProcessorNameString",
        )
        or "Unknown CPU"
    )
    cpu_name = " ".join(cpu_name.split())

    local_ip = "N/A"
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        local_ip = probe.getsockname()[0]
    except OSError:
        pass
    finally:
        probe.close()

    import os

    return StaticInfo(
        os_name=os_name,
        cpu_name=cpu_name,
        cpu_cores=os.cpu_count() or 1,
        local_ip=local_ip,
        ram_speed_mhz=_get_ram_speed_mhz(),
    )


def _get_ram_speed_mhz() -> int | None:
    # RAM speed has no registry source - a one-off CIM query at startup
    # (this value never changes while running) matches how every other
    # PortableFix action already shells out to PowerShell for hardware info.
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_PhysicalMemory | Select-Object -First 1 -ExpandProperty Speed)"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return int(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


def _filetime_to_int(ft: _FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


class CpuLoadSampler:
    """Delta-based CPU load via GetSystemTimes - no subprocess, no WMI."""

    def __init__(self):
        self._prev_idle: int | None = None
        self._prev_kernel: int | None = None
        self._prev_user: int | None = None

    def sample(self) -> float | None:
        idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            return None
        idle_i, kernel_i, user_i = _filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user)
        if self._prev_idle is None:
            self._prev_idle, self._prev_kernel, self._prev_user = idle_i, kernel_i, user_i
            return None
        idle_delta = idle_i - self._prev_idle
        # kernel time includes idle time on Windows - total busy time is
        # (kernel + user) - idle, not kernel + user.
        total_delta = (kernel_i - self._prev_kernel) + (user_i - self._prev_user)
        self._prev_idle, self._prev_kernel, self._prev_user = idle_i, kernel_i, user_i
        if total_delta <= 0:
            return 0.0
        busy = total_delta - idle_delta
        return max(0.0, min(100.0, (busy / total_delta) * 100.0))


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("sullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def get_ram_usage_gb() -> tuple[float, float]:
    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    total_gb = stat.ullTotalPhys / (1024**3)
    used_gb = (stat.ullTotalPhys - stat.ullAvailPhys) / (1024**3)
    return round(used_gb, 1), round(total_gb, 1)


_hw_computer = None
_hw_init_attempted = False
_hw_init_error: str | None = None
_hw_init_assets_dir: Path | None = None


def _vendor_dir(assets_dir: Path) -> Path:
    return assets_dir / "Vendor" / "LibreHardwareMonitor"


def init_hardware_monitor(assets_dir: Path):
    """Lazily loads LibreHardwareMonitorLib via pythonnet. Returns the opened
    Computer object, or None if unavailable - every caller must treat every
    sensor as optional and degrade to 'N/A' rather than crash."""
    global _hw_computer, _hw_init_attempted, _hw_init_error, _hw_init_assets_dir
    if _hw_init_attempted and _hw_init_assets_dir == assets_dir:
        return _hw_computer
    _hw_init_attempted = True
    _hw_init_assets_dir = assets_dir
    try:
        vendor = _vendor_dir(assets_dir)
        if not (vendor / "LibreHardwareMonitorLib.dll").exists():
            _hw_init_error = "LibreHardwareMonitorLib.dll not bundled"
            return None
        if str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        import pythonnet

        pythonnet.load("netfx")
        import clr

        clr.AddReference("LibreHardwareMonitorLib")
        from LibreHardwareMonitor.Hardware import Computer

        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = True
        computer.Open()
        _hw_computer = computer
        return computer
    except Exception as exc:  # pythonnet/CLR failures are too varied to enumerate
        _hw_init_error = str(exc)
        return None


def hardware_monitor_error() -> str | None:
    return _hw_init_error


def read_hardware_sensors(assets_dir: Path) -> dict:
    """CPU temp/clock and full GPU stats, best-effort. Every key is present
    but any value may be None if that sensor/hardware isn't available."""
    result = {
        "cpu_clock_mhz": None,
        "gpu_name": None,
        "gpu_load_percent": None,
        "gpu_temp_c": None,
        "gpu_clock_mhz": None,
        "gpu_vram_used_gb": None,
        "gpu_vram_total_gb": None,
    }
    computer = init_hardware_monitor(assets_dir)
    if computer is None:
        return result
    # Each GPU's fields are collected into its own dict first - on a laptop
    # with an iGPU+dGPU, writing straight into `result` per-sensor would mix
    # e.g. one GPU's name with another GPU's temperature. Only the busiest
    # GPU's complete, consistent set of fields is published.
    gpu_candidates = []
    try:
        for hw in computer.Hardware:
            hw.Update()
            type_name = str(hw.HardwareType)
            if type_name == "Cpu":
                clocks = [
                    s.Value
                    for s in hw.Sensors
                    if str(s.SensorType) == "Clock" and s.Value is not None and "Core" in s.Name
                ]
                if clocks:
                    result["cpu_clock_mhz"] = round(max(clocks), 0)
            elif type_name.startswith("Gpu"):
                gpu = {
                    "gpu_name": hw.Name,
                    "gpu_load_percent": None,
                    "gpu_temp_c": None,
                    "gpu_clock_mhz": None,
                    "gpu_vram_used_gb": None,
                    "gpu_vram_total_gb": None,
                }
                for s in hw.Sensors:
                    sensor_type, name, value = str(s.SensorType), s.Name, s.Value
                    if value is None:
                        continue
                    if sensor_type == "Load" and name == "GPU Core":
                        gpu["gpu_load_percent"] = round(value, 0)
                    elif sensor_type == "Temperature" and name == "GPU Core":
                        gpu["gpu_temp_c"] = round(value, 0)
                    elif sensor_type == "Clock" and name == "GPU Core":
                        gpu["gpu_clock_mhz"] = round(value, 0)
                    elif sensor_type == "SmallData" and name == "D3D Dedicated Memory Used":
                        gpu["gpu_vram_used_gb"] = round(value / 1024, 1)
                    elif sensor_type == "SmallData" and name == "D3D Dedicated Memory Total":
                        gpu["gpu_vram_total_gb"] = round(value / 1024, 1)
                gpu_candidates.append(gpu)
    except Exception:
        pass
    if gpu_candidates:
        result.update(max(gpu_candidates, key=lambda g: g["gpu_load_percent"] or -1))
    return result


def ping_once(host: str = "8.8.8.8", timeout_ms: int = 1000) -> float | None:
    try:
        result = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), host],
            capture_output=True, text=True, timeout=(timeout_ms / 1000) + 2,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if "time=" in line:
            try:
                return float(line.split("time=")[1].split("ms")[0].strip("<="))
            except (IndexError, ValueError):
                return None
        if "time<" in line:
            return 1.0
    return None


_VPN_ADAPTER_PATTERN = (
    "WireGuard|OpenVPN|TAP-Windows|Wintun|NordLynx|NordVPN|ExpressVPN|ProtonVPN|"
    "Mullvad|Tailscale|ZeroTier|Netbird|Hamachi|Cisco AnyConnect|GlobalProtect|"
    "Surfshark|IPVanish|Private Internet Access|FortiClient"
)


def check_vpn_status() -> str | None:
    """Best-effort VPN detection, no admin rights needed. Returns the VPN's
    name if one looks active (a connected native Windows VPN profile, or an
    up virtual adapter matching a known VPN client), "" if none was found,
    or None if the check itself failed (subprocess error/timeout - shown as
    N/A, never guessed as either connected or not)."""
    script = (
        "$native = Get-VpnConnection -EA SilentlyContinue | "
        "Where-Object { $_.ConnectionStatus -eq 'Connected' } | "
        "Select-Object -First 1 -ExpandProperty Name; "
        "if ($native) { Write-Output $native; exit 0 }; "
        f"$pattern = '{_VPN_ADAPTER_PATTERN}'; "
        "$adapter = Get-NetAdapter -EA SilentlyContinue | "
        "Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match $pattern } | "
        "Select-Object -First 1 -ExpandProperty InterfaceDescription; "
        "Write-Output $adapter"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_speed_test(size_bytes: int = 25_000_000, timeout: float = 20.0) -> float | None:
    """Downloads a fixed-size payload from Cloudflare's public speed-test
    endpoint and returns the measured throughput in Mbps, or None on failure."""
    url = f"https://speed.cloudflare.com/__down?bytes={size_bytes}"
    # Cloudflare's speed-test endpoint 403s requests with Python's default
    # User-Agent - a plain browser-style one is enough to pass.
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        start = time.monotonic()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            downloaded = len(resp.read())
        elapsed = time.monotonic() - start
        if elapsed <= 0 or downloaded == 0:
            return None
        return round((downloaded * 8) / elapsed / 1_000_000, 1)
    except Exception:
        return None


class StaticInfoRunner(QThread):
    """One-shot: static info includes a subprocess call (RAM speed), so it
    runs off the GUI thread even though it only happens once at startup."""

    static_info_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.static_info_ready.emit(get_static_info())


class HardwareSensorRunner(QThread):
    """Runs read_hardware_sensors() off the GUI thread - the very first call
    cold-starts the CLR + loads the DLL (~1.5s measured), and every call
    queries live hardware/driver state, neither of which belongs on a timer
    tick that fires directly on the UI thread."""

    sensors_ready = Signal(object)

    def __init__(self, assets_dir: Path, parent=None):
        super().__init__(parent)
        self._assets_dir = assets_dir
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.sensors_ready.emit(read_hardware_sensors(self._assets_dir))


class PingRunner(QThread):
    """One-shot: ping shells out to ping.exe, which can block for up to
    ~1s - runs off the GUI thread so the timer tick never stalls the UI."""

    ping_ready = Signal(object)

    def __init__(self, host: str = "8.8.8.8", parent=None):
        super().__init__(parent)
        self._host = host
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.ping_ready.emit(ping_once(self._host))


class VpnStatusRunner(QThread):
    """One-shot: VPN detection shells out to PowerShell (Get-VpnConnection +
    Get-NetAdapter), which can take a moment - runs off the GUI thread like
    every other subprocess-backed sysinfo check."""

    vpn_status_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.vpn_status_ready.emit(check_vpn_status())


class SpeedTestRunner(QThread):
    speed_test_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        self.speed_test_ready.emit(run_speed_test())
