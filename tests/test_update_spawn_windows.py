"""How powershell.exe really behaves under the updater's spawn flags.

The in-app update never completed on any machine: the swap PowerShell was
created, lived 1-2 s and died without running line 1 of its script, and
without a trace in any Windows log. The suspected cause is DETACHED_PROCESS
(which apply_update has passed since the updater's first version): with no
console at all, PowerShell's console host cannot start and the process exits
quietly before it opens a runspace. Every other PowerShell the app starts
uses CREATE_NO_WINDOW and works.

These tests start the real powershell.exe on a one-line probe script (it
writes <script>.ran, so no path is written inside the script) under both flag
sets, and pin the difference down on a real Windows runner. They stay as a
guard against DETACHED_PROCESS ever coming back.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows process creation")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def _launch_probe(tmp_path: Path, flags: int, extra_args: list[str]) -> dict:
    from portablefix.executor import powershell_executable

    script = tmp_path / "probe.ps1"
    script.write_text("Set-Content -LiteralPath ($PSCommandPath + '.ran') -Value ok", encoding="utf-8-sig")
    ran = Path(str(script) + ".ran")
    log = tmp_path / "launch.log"
    argv = [
        powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        *extra_args, "-File", str(script),
    ]
    route = "breakaway"
    started = time.monotonic()
    with open(log, "wb") as out:
        popen_kwargs = dict(stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
        try:
            proc = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
        except PermissionError:
            # The runner's job forbids breakaway - the same fallback the
            # updater needs; record it rather than fail on it.
            route = "no-breakaway"
            proc = subprocess.Popen(argv, creationflags=flags & ~CREATE_BREAKAWAY_FROM_JOB, **popen_kwargs)
        exit_code = proc.wait(60)
    facts = {
        "exit_code": f"0x{exit_code & 0xFFFFFFFF:08X}",
        "script_ran": ran.exists(),
        "output_bytes": log.stat().st_size,
        "output": log.read_bytes()[:500],
        "elapsed_s": round(time.monotonic() - started, 2),
        "route": route,
    }
    print(f"flags=0x{flags:08X} args={extra_args}: {facts}")
    return facts


def test_powershell_create_no_window_runs_script(tmp_path):
    facts = _launch_probe(tmp_path, CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, [])
    assert facts["script_ran"], facts


def test_powershell_detached_process_never_runs_script(tmp_path):
    # The exact combination apply_update shipped with (<= 1.11.4).
    facts = _launch_probe(
        tmp_path,
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
        ["-WindowStyle", "Hidden"],
    )
    assert not facts["script_ran"], (
        "F1 falsified: DETACHED_PROCESS is not the cause - stop and re-investigate with launch logs", facts,
    )
