import json
import subprocess

from PySide6.QtCore import QThread, Signal

from .executor import POWERSHELL_PREFIX


def _ps_quote(value: str) -> str:
    # Single-quoted PowerShell strings never interpolate $variables or
    # subexpressions, unlike the double-quoted string this previously used -
    # see the identical helper (and its rationale) in updater.py.
    return "'" + value.replace("'", "''") + "'"


try:
    import winreg as _winreg
except ImportError:  # not Windows (tests, linting) - see _read_throttle
    _winreg = None


SYSTEM_RESTORE_SUBKEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
SYSTEM_RESTORE_KEY = "HKLM:\\" + SYSTEM_RESTORE_SUBKEY
FREQUENCY_VALUE = "SystemRestorePointCreationFrequency"
# Checkpoint-Computer waits for a VSS snapshot, which on a slow/fragmented
# HDD can take minutes - 120s cut real, still-progressing checkpoints off.
RESTORE_POINT_TIMEOUT_SEC = 300
# Prefix of the one stdout line that carries the created point as JSON - a
# marker, so stray output from any cmdlet can never be mistaken for it.
RESULT_MARKER = "PORTABLEFIX_RESTORE_POINT="


def build_restore_point_command(description: str) -> str:
    # Windows silently skips Checkpoint-Computer when another restore point
    # was created in the last 24h (SystemRestorePointCreationFrequency) -
    # it only writes a warning and still exits 0, so the batch went ahead
    # believing it had a fresh restore point. Lift the throttle for this one
    # call (restoring the previous value afterwards) and turn any remaining
    # warning into a failure, so the user is asked whether to continue.
    # $env:SystemDrive rather than a hard-coded C:\ - Windows isn't always on C:.
    # If powershell.exe is killed (timeout) the finally never runs -
    # create_restore_point's _ensure_throttle_restored is the backstop.
    #
    # The trailing lookup is only reached on success (the catch exits 1): it
    # finds the point just made - the newest one carrying our description -
    # so the audit log/report can name it by SequenceNumber, the same number
    # rstrui/Get-ComputerRestorePoint show when a result is disputed. It has
    # its own try/catch: failing to *look it up* must never turn a created
    # restore point into a failure.
    return (
        f"$key = {_ps_quote(SYSTEM_RESTORE_KEY)}; "
        f"$name = {_ps_quote(FREQUENCY_VALUE)}; "
        f"$desc = {_ps_quote(description)}; "
        "$old = (Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue).$name; "
        'Enable-ComputerRestore -Drive "$env:SystemDrive\\" -ErrorAction SilentlyContinue; '
        "try { "
        "New-ItemProperty -Path $key -Name $name -PropertyType DWord -Value 0 -Force -ErrorAction SilentlyContinue | Out-Null; "
        "Checkpoint-Computer -Description $desc -RestorePointType MODIFY_SETTINGS "
        "-ErrorAction Stop -WarningAction Stop "
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 } "
        "finally { "
        "if ($null -eq $old) { Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue } "
        "else { Set-ItemProperty -Path $key -Name $name -Value $old -ErrorAction SilentlyContinue } "
        "}; "
        "try { "
        "$rp = Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Where-Object { $_.Description -eq $desc } "
        "| Sort-Object SequenceNumber | Select-Object -Last 1; "
        "if ($null -ne $rp -and $null -ne $rp.SequenceNumber) { "
        f"Write-Output ({_ps_quote(RESULT_MARKER)} + "
        "(@{ sequence = [long]$rp.SequenceNumber; created = [string]$rp.CreationTime; "
        "description = [string]$rp.Description } | ConvertTo-Json -Compress)) "
        "} "
        "} catch { }"
    )


def parse_restore_point_output(stdout: str) -> dict:
    """Extract the created point's identity from the command's stdout.

    Returns {} when the marker line is missing or malformed (lookup failed,
    restore point list not readable) - callers treat that as "sequence
    number not recorded", never as a failed restore point."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith(RESULT_MARKER):
            continue
        try:
            data = json.loads(line[len(RESULT_MARKER):])
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        sequence = data.get("sequence")
        # bool is an int subclass - reject it explicitly.
        if not isinstance(sequence, int) or isinstance(sequence, bool):
            return {}
        return {
            "sequence_number": sequence,
            "creation_time": str(data.get("created") or ""),
            "description": str(data.get("description") or ""),
        }
    return {}


class RestorePointResult(tuple):
    """(success, detail) that also carries the created point's identity.

    A tuple subclass so every existing `success, detail = ...` caller (and
    every test stub returning a plain 2-tuple) keeps working unchanged; the
    extra facts ride along in `info` (parse_restore_point_output's dict)."""

    def __new__(cls, success: bool, detail: str, info: dict | None = None):
        self = super().__new__(cls, (success, detail))
        self.info = dict(info or {})
        return self

    @property
    def sequence_number(self) -> int | None:
        return self.info.get("sequence_number")


# --- throttle safety net ---
#
# The command's PowerShell `finally` puts SystemRestorePointCreationFrequency
# back - but only if PowerShell gets to run it. On a timeout subprocess.run
# kills powershell.exe (TerminateProcess), the finally never runs, and the
# client PC is left with the 24h throttle disabled for good. So Python
# snapshots the value before launching and, after the command ends however
# it ends, puts it back if it differs. Read/written with winreg (no second
# PowerShell start, and nothing to parse from a killed process's stdout).

def _open_throttle_key(access: int):
    # KEY_WOW64_64KEY: the real 64-bit HKLM\SOFTWARE that 64-bit PowerShell
    # writes, even if this Python process is 32-bit.
    return _winreg.OpenKey(
        _winreg.HKEY_LOCAL_MACHINE, SYSTEM_RESTORE_SUBKEY, 0, access | _winreg.KEY_WOW64_64KEY,
    )


def _read_throttle():
    """("absent",), ("value", data, reg_type), or None when it can't be read
    (not Windows, access denied) - None disables the safety net."""
    if _winreg is None:
        return None
    try:
        with _open_throttle_key(_winreg.KEY_READ) as key:
            data, reg_type = _winreg.QueryValueEx(key, FREQUENCY_VALUE)
    except FileNotFoundError:
        # Key or value missing - Windows then uses its 1440-minute default.
        return ("absent",)
    except OSError:
        return None
    return ("value", data, reg_type)


def _ensure_throttle_restored(snapshot) -> None:
    # Compare-then-repair: on the normal path the PowerShell finally already
    # restored the value, the comparison matches and nothing is written.
    if snapshot is None or _read_throttle() == snapshot:
        return
    try:
        with _open_throttle_key(_winreg.KEY_SET_VALUE) as key:
            if snapshot[0] == "absent":
                try:
                    _winreg.DeleteValue(key, FREQUENCY_VALUE)
                except FileNotFoundError:
                    pass
            else:
                _, data, reg_type = snapshot
                _winreg.SetValueEx(key, FREQUENCY_VALUE, 0, reg_type, data)
    except OSError:
        # Best effort - never let the safety net itself crash the batch.
        pass


def create_restore_point(description: str) -> RestorePointResult:
    command = build_restore_point_command(description)
    throttle_before = _read_throttle()
    try:
        result = subprocess.run(
            POWERSHELL_PREFIX + [command], capture_output=True, timeout=RESTORE_POINT_TIMEOUT_SEC,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            stdout = (result.stdout or b"").decode("utf-8", errors="replace")
            return RestorePointResult(True, "", parse_restore_point_output(stdout))
        return RestorePointResult(False, result.stderr.decode("utf-8", errors="replace").strip())
    except subprocess.TimeoutExpired:
        # str(exc) would dump the whole command line into the audit log.
        return RestorePointResult(False, f"Checkpoint-Computer timed out after {RESTORE_POINT_TIMEOUT_SEC}s.")
    except (subprocess.SubprocessError, OSError) as exc:
        return RestorePointResult(False, str(exc))
    finally:
        _ensure_throttle_restored(throttle_before)


class RestorePointRunner(QThread):
    # (success, detail, info) - info is parse_restore_point_output's dict,
    # {} when the created point could not be identified.
    result_ready = Signal(bool, str, object)

    def __init__(self, description: str, parent=None):
        super().__init__(parent)
        self._description = description
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        result = create_restore_point(self._description)
        success, detail = result
        # getattr: a stub returning a plain (success, detail) tuple has no info.
        self.result_ready.emit(success, detail, dict(getattr(result, "info", None) or {}))
