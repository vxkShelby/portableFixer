"""Disk health verdict for the pre-flight "image first" gate (research G13).

Qt-free. The verdict rules live in exactly one place - the SAFE catalog
action m03 `disk_health_verdict` - and this module runs that same command
and reads only its `VERDICT: disk=... status=...` lines, which the script
itself writes in fixed English tokens (never Windows' localized text).

The probe is meant to be cheap: one PowerShell launch with a short
timeout, made only when a real batch contains an action flagged
`stresses_disk: true`, once per review screen. Anything that goes wrong
(no PowerShell, timeout, not Windows, no VERDICT line) answers None =
"unknown", and pre-flight never blocks on an unknown. Tests inject the
runner (`probe(run=...)`) or replace `preflight.Probes.disk_health`.
"""

import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

OK = "OK"
WARNING = "WARNING"
FAILING = "FAILING"
UNKNOWN = "UNKNOWN"
_RANK = {OK: 0, UNKNOWN: 1, WARNING: 2, FAILING: 3}

ACTION_ID = "disk_health_verdict"
# Get-PhysicalDisk usually answers in 1-3 s; the review screen waits for
# the probe, so a storage stack that hangs (a dying disk often does) must
# not freeze it for long - a timeout is just "unknown".
PROBE_TIMEOUT_SEC = 20
# "?" = a failure prediction the script could not tie to a physical disk.
UNMAPPED_DISK = "?"

_VERDICT_RE = re.compile(
    r"^VERDICT: disk=(?P<disk>\S+) system=(?P<system>yes|no) "
    r"status=(?P<status>OK|WARNING|FAILING|UNKNOWN) reasons=(?P<reasons>\S+) name=(?P<name>.*)$"
)


@dataclass(frozen=True)
class DiskVerdict:
    disk: str
    status: str
    reasons: tuple[str, ...] = ()
    name: str = ""
    system: bool = False

    def describe(self) -> str:
        # Language-neutral: disk number, model and the rule codes that
        # decided - the same tokens the catalog action prints.
        why = ", ".join(self.reasons) or "-"
        label = f"#{self.disk}" if self.disk != UNMAPPED_DISK else "?"
        return f"{label} {self.name} ({self.status}: {why})".replace("  ", " ")


def parse_verdicts(output: str) -> list[DiskVerdict]:
    verdicts = []
    for line in output.splitlines():
        match = _VERDICT_RE.match(line.strip())
        if not match:
            continue
        reasons = match["reasons"]
        verdicts.append(DiskVerdict(
            disk=match["disk"],
            status=match["status"],
            reasons=() if reasons == "-" else tuple(reasons.split(",")),
            name="" if match["name"] == "-" else match["name"].strip(),
            system=match["system"] == "yes",
        ))
    return verdicts


def worst(verdicts: Iterable[DiskVerdict]) -> str:
    return max((v.status for v in verdicts), key=_RANK.__getitem__, default=UNKNOWN)


def gate_disks(verdicts: Iterable[DiskVerdict]) -> list[DiskVerdict]:
    """The disks a disk-stressing batch actually touches.

    Every stressing action in the catalog works on %SystemDrive%, so when
    the script found the system disk, only it counts - a dying USB stick
    must not block chkdsk of a healthy C:. A failure prediction the script
    could not map to a disk is kept (it may well be the system disk), and
    when the system disk is unknown, all disks count.
    """
    verdicts = list(verdicts)
    if any(v.system for v in verdicts):
        return [v for v in verdicts if v.system or v.disk == UNMAPPED_DISK]
    return verdicts


def catalog_command(modules_dir: Path | None = None) -> str | None:
    from .module_engine import ModuleLoadError, load_module
    from .paths import get_base_dir

    path = (modules_dir or get_base_dir() / "Modules") / "m03_disk" / "actions.yaml"
    try:
        module = load_module(path)
    except (OSError, ModuleLoadError, ValueError):
        return None
    return next((a.command for a in module.actions if a.id == ACTION_ID), None)


def _run_powershell(command: str, timeout: float) -> str | None:
    from .paths import powershell_executable

    try:
        result = subprocess.run(
            [powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return result.stdout


def probe(
    run: Callable[[str, float], str | None] = _run_powershell,
    command: str | None = None,
    timeout: float = PROBE_TIMEOUT_SEC,
) -> list[DiskVerdict] | None:
    """Per-disk verdicts, or None when the health cannot be told."""
    command = command if command is not None else catalog_command()
    if not command:
        return None
    output = run(command, timeout)
    if not output:
        return None
    return parse_verdicts(output) or None


def windows_probe() -> list[DiskVerdict] | None:
    # Off Windows there is nothing to ask (and a stray pwsh on a dev box
    # must not decide a test's pre-flight).
    if sys.platform != "win32":
        return None
    return probe()
