"""Pre-flight check before a real (non-DRY-RUN) batch.

Qt-free on purpose: the GUI asks run_preflight() for blockers/warnings and
shows them on the batch review screen, the tests drive it with injected
probes. Every probe answers None ("unknown") when it cannot tell - an
unknown never blocks, because a check that fails on some odd machine must
not lock the technician out of the tool.
"""

import shutil
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .i18n import translate
from .models import ActionDef, ModuleCategory, ModuleDef, RiskLevel

BLOCKER = "blocker"
WARNING = "warning"

# Flyoobe's setup-preflight uses the same 5 GB floor: DISM /RestoreHealth,
# a restore point (VSS) and component-store work all need room on the
# system drive, and failing half-way through is worse than not starting.
MIN_FREE_BYTES = 5 * 1024**3
LOW_FREE_BYTES = 10 * 1024**3
# Below this on battery a long action (DISM, chkdsk, driver installs) or a
# reboot-requiring one can realistically die with the laptop mid-change.
BATTERY_BLOCK_PERCENT = 30

# An inactivity timeout / hard cap raised this far in the catalog is how an
# action says "I legitimately run for a long time" (DISM, chkdsk, SFC).
LONG_INACTIVITY_SEC = 600
LONG_HARD_CAP_SEC = 3600

# Pending-reboot sources (registry flags, never localized text).
REBOOT_CBS = "cbs"
REBOOT_WU = "wu"
REBOOT_FILE_RENAME = "file_rename"


def changes_system(action: ActionDef) -> bool:
    """Does the action change persistent system state (research G24)?

    Decided by the action's effect, not its module's category: the old
    category rule gave read-only SAFE checks in REPAIR a restore point and
    left the MODERATE m13 debloat changes (registry policies, removed apps,
    OneDrive) without one. The rule:
      - DESTRUCTIVE: always (the loader rejects `changes_system: false`);
      - otherwise the action's `changes_system` YAML field when it has one -
        `true` for a SAFE action that still changes the system, `false`
        for a non-SAFE one whose effect a restore point cannot cover
        (emptying caches or the Recycle Bin, a Defender scan);
      - otherwise non-SAFE = changes the system, SAFE = read-only.
    """
    if action.risk == RiskLevel.DESTRUCTIVE:
        return True
    if action.changes_system is not None:
        return action.changes_system
    return action.risk != RiskLevel.SAFE


def needs_restore_point(module: ModuleDef, action: ActionDef) -> bool:
    # module is kept in the signature: every caller has the pair at hand,
    # and the review screen and _run_next must keep asking the same question.
    return changes_system(action)


def is_long_action(action: ActionDef) -> bool:
    return (action.inactivity_timeout_sec or 0) >= LONG_INACTIVITY_SEC or (
        action.hard_cap_sec or 0
    ) >= LONG_HARD_CAP_SEC


@dataclass(frozen=True)
class BatchProfile:
    """What a batch is about to do - decides which findings matter."""

    # Anything that can change the system: a non-SAFE action or one guarded
    # by a restore point. A read-only batch gets no pre-flight at all.
    changes_system: bool = False
    # Non-SAFE actions: the read-only banner promises these need admin.
    needs_admin: bool = False
    # REQUIRES_REBOOT or long-running actions - what battery power endangers.
    long_or_reboot: bool = False
    # Servicing work (REPAIR category changes, REQUIRES_REBOOT): what a
    # pending reboot breaks - DISM/SFC on a half-applied update fail or lie.
    servicing: bool = False


def profile_for(items: Iterable[tuple[ModuleDef, ActionDef]]) -> BatchProfile:
    changes = admin = long_or_reboot = servicing = False
    for module, action in items:
        risky = action.risk != RiskLevel.SAFE
        if risky or needs_restore_point(module, action):
            changes = True
        if risky:
            admin = True
        if action.risk == RiskLevel.REQUIRES_REBOOT or (risky and is_long_action(action)):
            long_or_reboot = True
        if action.risk == RiskLevel.REQUIRES_REBOOT or (risky and module.category == ModuleCategory.REPAIR):
            servicing = True
    return BatchProfile(changes, admin, long_or_reboot, servicing)


@dataclass(frozen=True)
class PowerStatus:
    on_battery: bool | None
    percent: int | None


@dataclass
class Probes:
    """Each probe returns None when it cannot tell. Tests inject fakes."""

    power: Callable[[], PowerStatus | None] = lambda: None
    # List of REBOOT_* sources; [] = no reboot pending; None = unknown.
    pending_reboot: Callable[[], list[str] | None] = lambda: None
    system_free_bytes: Callable[[], int | None] = lambda: None
    is_admin: Callable[[], bool | None] = lambda: None
    # Names (already translated) of PortableFix jobs that are changing the
    # system right now - winget/uninstall panels, an update being applied.
    busy_tasks: Callable[[], list[str]] = lambda: []


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    params: dict = field(default_factory=dict)
    # A non-overridable blocker (another job busy) can never be confirmed
    # past: two system-changing runs at once is not a judgement call.
    overridable: bool = True

    def text(self, language: str) -> str:
        params = dict(self.params)
        if "sources" in params:
            # Kept as REBOOT_* codes (audit log, tests) and named only here.
            params["sources"] = ", ".join(translate(f"preflight_reboot_{s}", language) for s in params["sources"])
        for key, value in params.items():
            if isinstance(value, float):
                # Slovak writes a decimal comma.
                params[key] = f"{value:.1f}".replace(".", "," if language == "sk" else ".")
        return translate(f"preflight_{self.code}", language).format(**params)


@dataclass(frozen=True)
class PreflightResult:
    issues: tuple[Issue, ...] = ()

    @property
    def blockers(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == BLOCKER]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def hard_blocked(self) -> bool:
        return any(not i.overridable for i in self.blockers)

    def summary(self) -> str:
        # Language-neutral line for the audit log, like the other _system
        # events' output: codes, not translated sentences.
        if not self.issues:
            return "Pre-flight: no blockers, no warnings."
        blockers = ", ".join(i.code for i in self.blockers) or "none"
        warnings = ", ".join(i.code for i in self.warnings) or "none"
        return f"Pre-flight: blockers: {blockers}; warnings: {warnings}."


def _call(probe: Callable, default=None):
    # A probe that raises is an unknown, never a crash of the batch start.
    try:
        return probe()
    except Exception:  # noqa: BLE001 - any probe failure means "unknown"
        return default


def _gb(value: int) -> float:
    return round(value / 1024**3, 1)


def run_preflight(profile: BatchProfile, probes: Probes) -> PreflightResult:
    if not profile.changes_system:
        return PreflightResult()
    issues: list[Issue] = []

    busy = _call(probes.busy_tasks, []) or []
    if busy:
        issues.append(Issue("busy", BLOCKER, {"tasks": ", ".join(busy)}, overridable=False))

    if profile.needs_admin and _call(probes.is_admin) is False:
        issues.append(Issue("no_admin", BLOCKER))

    reboot = _call(probes.pending_reboot)
    if reboot:
        # A PendingFileRenameOperations entry alone is left behind by
        # countless installers and AV products and often survives several
        # reboots - blocking on it would block most PCs. CBS/WU are real
        # half-applied servicing that DISM/SFC must not run on top of.
        serious = any(s in (REBOOT_CBS, REBOOT_WU) for s in reboot)
        severity = BLOCKER if serious and profile.servicing else WARNING
        issues.append(Issue("pending_reboot", severity, {"sources": tuple(reboot)}))

    free = _call(probes.system_free_bytes)
    if isinstance(free, int):
        if free < MIN_FREE_BYTES:
            issues.append(Issue("low_disk", BLOCKER, {"free_gb": _gb(free), "min_gb": MIN_FREE_BYTES // 1024**3}))
        elif free < LOW_FREE_BYTES:
            issues.append(Issue("disk_tight", WARNING, {"free_gb": _gb(free)}))

    power = _call(probes.power)
    if isinstance(power, PowerStatus) and power.on_battery:
        percent = power.percent
        if profile.long_or_reboot and percent is not None and percent < BATTERY_BLOCK_PERCENT:
            issues.append(Issue("battery_low", BLOCKER, {"percent": percent, "min": BATTERY_BLOCK_PERCENT}))
        else:
            shown = "?" if percent is None else str(percent)
            issues.append(Issue("on_battery", WARNING, {"percent": shown}))

    return PreflightResult(tuple(issues))


# --- Real Windows probes ----------------------------------------------------
# Registry flags and Win32 calls only - no localized command output.


def _windows_power() -> PowerStatus | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _Status(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    status = _Status()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return None
    # ACLineStatus: 0 offline, 1 online, 255 unknown. BatteryFlag 128 = no
    # system battery (a desktop) - never "on battery" then.
    if status.BatteryFlag == 128 or status.ACLineStatus not in (0, 1):
        return PowerStatus(on_battery=False if status.BatteryFlag == 128 else None, percent=None)
    percent = None if status.BatteryLifePercent == 255 else int(status.BatteryLifePercent)
    return PowerStatus(on_battery=status.ACLineStatus == 0, percent=percent)


def _windows_pending_reboot() -> list[str] | None:
    if sys.platform != "win32":
        return None
    import winreg

    def key_exists(path: str) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path):
                return True
        except FileNotFoundError:
            return False

    sources = []
    try:
        if key_exists(r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"):
            sources.append(REBOOT_CBS)
        if key_exists(r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"):
            sources.append(REBOOT_WU)
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager"
            ) as key:
                value, _ = winreg.QueryValueEx(key, "PendingFileRenameOperations")
            if value and any(str(v).strip() for v in value):
                sources.append(REBOOT_FILE_RENAME)
        except FileNotFoundError:
            pass
    except OSError:
        return None
    return sources


def _windows_system_free_bytes() -> int | None:
    if sys.platform != "win32":
        return None
    import os

    drive = os.environ.get("SystemDrive") or "C:"
    try:
        return shutil.disk_usage(drive + "\\").free
    except OSError:
        return None


def system_probes(
    is_admin: Callable[[], bool | None], busy_tasks: Callable[[], list[str]]
) -> Probes:
    """The real probes; the caller supplies what only it knows (the
    process's elevation, its own running jobs)."""
    return Probes(
        power=_windows_power,
        pending_reboot=_windows_pending_reboot,
        system_free_bytes=_windows_system_free_bytes,
        is_admin=is_admin,
        busy_tasks=busy_tasks,
    )
