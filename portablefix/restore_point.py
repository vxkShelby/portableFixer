import subprocess

from PySide6.QtCore import QThread, Signal

from .executor import POWERSHELL_PREFIX


def _ps_quote(value: str) -> str:
    # Single-quoted PowerShell strings never interpolate $variables or
    # subexpressions, unlike the double-quoted string this previously used -
    # see the identical helper (and its rationale) in updater.py.
    return "'" + value.replace("'", "''") + "'"


SYSTEM_RESTORE_KEY = r"HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore"
FREQUENCY_VALUE = "SystemRestorePointCreationFrequency"


def build_restore_point_command(description: str) -> str:
    # Windows silently skips Checkpoint-Computer when another restore point
    # was created in the last 24h (SystemRestorePointCreationFrequency) -
    # it only writes a warning and still exits 0, so the batch went ahead
    # believing it had a fresh restore point. Lift the throttle for this one
    # call (restoring the previous value afterwards) and turn any remaining
    # warning into a failure, so the user is asked whether to continue.
    # $env:SystemDrive rather than a hard-coded C:\ - Windows isn't always on C:.
    return (
        f"$key = {_ps_quote(SYSTEM_RESTORE_KEY)}; "
        f"$name = {_ps_quote(FREQUENCY_VALUE)}; "
        "$old = (Get-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue).$name; "
        'Enable-ComputerRestore -Drive "$env:SystemDrive\\" -ErrorAction SilentlyContinue; '
        "try { "
        "New-ItemProperty -Path $key -Name $name -PropertyType DWord -Value 0 -Force -ErrorAction SilentlyContinue | Out-Null; "
        f"Checkpoint-Computer -Description {_ps_quote(description)} -RestorePointType MODIFY_SETTINGS "
        "-ErrorAction Stop -WarningAction Stop "
        "} catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 } "
        "finally { "
        "if ($null -eq $old) { Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue } "
        "else { Set-ItemProperty -Path $key -Name $name -Value $old -ErrorAction SilentlyContinue } "
        "}"
    )


def create_restore_point(description: str) -> tuple[bool, str]:
    command = build_restore_point_command(description)
    try:
        result = subprocess.run(
            POWERSHELL_PREFIX + [command], capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, ""
        return False, result.stderr.decode("utf-8", errors="replace").strip()
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)


class RestorePointRunner(QThread):
    result_ready = Signal(bool, str)

    def __init__(self, description: str, parent=None):
        super().__init__(parent)
        self._description = description
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        success, detail = create_restore_point(self._description)
        self.result_ready.emit(success, detail)
