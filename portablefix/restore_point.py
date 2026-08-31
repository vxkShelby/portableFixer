import subprocess

from .executor import POWERSHELL_PREFIX


def create_restore_point(description: str) -> bool:
    command = (
        'Enable-ComputerRestore -Drive "C:\\"; '
        f'Checkpoint-Computer -Description "{description}" -RestorePointType MODIFY_SETTINGS'
    )
    try:
        result = subprocess.run(POWERSHELL_PREFIX + [command], capture_output=True, timeout=120)
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False
