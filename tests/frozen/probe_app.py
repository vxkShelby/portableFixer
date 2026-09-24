"""A stand-in for PortableFix.exe in the frozen end-to-end update test
(tests/test_frozen_update_e2e.py, CI job frozen-update-e2e).

It is built with the same PyInstaller onefile bootloader as the app and
imports only the Qt-free update core, so the test exercises exactly what a
frozen app hands to the swap: its own PID and the bootloader parent's, the
cleaned environment, the working directory and the relaunch - the parts no
unfrozen test can reach.

    PortableFix.exe --probe-apply <zip>   stage_update + launch_swap, then
                                          write Data\\probe_launch.json
    PortableFix.exe --post-update         (the swap's relaunch) write
                                          Data\\relaunched.json

The version (1 or 2) comes from probe_version.txt bundled into the exe.
Built with --noconsole like the app, so it reports only through files.
"""
import json
import os
import sys
from pathlib import Path

from portablefix import update_swap


def _install_dir() -> Path:
    return Path(sys.executable).parent.parent


def _bundled_version() -> str:
    try:
        return (Path(sys._MEIPASS) / "probe_version.txt").read_text(encoding="ascii").strip()
    except (AttributeError, OSError):
        return "unknown"


def _facts() -> dict:
    return {
        "version": _bundled_version(),
        "meipass": getattr(sys, "_MEIPASS", None),
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "argv": sys.argv,
        "reset_environment": os.environ.get("PYINSTALLER_RESET_ENVIRONMENT"),
    }


def _write(name: str, data: dict) -> None:
    # Written to a temp name first: the test polls for the file and must
    # never read it half-written.
    target = _install_dir() / "Data" / name
    tmp = target.with_name(name + ".tmp")
    tmp.write_text(json.dumps(data, default=str, indent=1), encoding="utf-8")
    os.replace(tmp, target)


def probe_apply(zip_path: str) -> int:
    install_dir = _install_dir()
    out = _facts()
    out["parent_pid"] = update_swap.onefile_parent_pid()
    try:
        staged = update_swap.stage_update(Path(zip_path), install_dir)
        result = update_swap.launch_swap(staged, install_dir)
        out.update({
            "ok": result.ok, "reason": result.reason, "detail": result.detail, "exit_code": result.exit_code,
            "route": result.route, "child_pid": result.child_pid, "warn_job": result.warn_job,
            "diagnostics_path": str(result.diagnostics_path), "log_dir": str(result.log_dir),
        })
    except Exception as exc:  # reported through the file - there is no console
        out["ok"] = False
        out["error"] = repr(exc)
    _write("probe_launch.json", out)
    return 0 if out["ok"] else 1


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--probe-apply":
        return probe_apply(argv[2])
    if "--post-update" in argv[1:]:
        _write("relaunched.json", _facts())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
