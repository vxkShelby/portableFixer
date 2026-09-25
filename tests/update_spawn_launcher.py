"""Plays the app for tests/test_update_spawn_windows.py: stages a release zip
and hands off to the real swap through update_swap.launch_swap, as the app
does, then exits. It runs as a separate process so each test can give it
the current directory, TEMP and Job Object of its case, and the swap
PowerShell inherits all three from it (unless the spawn deliberately
changes them).

    python update_spawn_launcher.py <case.json>

The case names the zip, the install, the PID of a helper process that
stands in for the rest of the app (the swap waits for it and for this
process), where to write the result, and whether to run inside a
kill-on-close Job Object.
"""
import ctypes
import json
import os
import sys
from pathlib import Path

from portablefix import update_swap


def _enter_kill_on_close_job():
    """A new Job Object that kills its members when its last handle closes
    (at this process's exit) and does not allow breakaway - what a hostile
    launcher (a terminal, a scheduler, a remote-support tool) may impose."""
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = update_swap._ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = update_swap._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(
        job, update_swap._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    # Returned and held by the caller: the job must stay open until exit.
    return job


def main() -> int:
    case = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    job = _enter_kill_on_close_job() if case.get("kill_on_close_job") else None
    install_dir = Path(case["install_dir"])
    out: dict = {"pid": os.getpid(), "cwd": os.getcwd(), "in_test_job": job is not None,
                 "facts_before": update_swap.job_facts()}
    try:
        staged = update_swap.stage_update(Path(case["zip"]), install_dir)
        result = update_swap.launch_swap(
            staged, install_dir, pids=[int(case["helper_pid"]), os.getpid()],
            # Bounds how long a leftover updater could wait if the test died
            # before its teardown learned the updater's PID.
            job_options={"max_wait_sec": 150},
        )
        out.update({
            "ok": result.ok, "reason": result.reason, "detail": result.detail, "exit_code": result.exit_code,
            "route": result.route, "child_pid": result.child_pid, "warn_job": result.warn_job,
            "diagnostics_path": str(result.diagnostics_path), "log_dir": str(result.log_dir),
        })
    except Exception as exc:  # reported to the test, which prints it
        out["error"] = repr(exc)
    Path(case["result"]).write_text(json.dumps(out, default=str), encoding="utf-8")
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
