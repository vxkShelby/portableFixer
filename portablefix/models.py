from dataclasses import dataclass, field
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    DESTRUCTIVE = "DESTRUCTIVE"
    REQUIRES_REBOOT = "REQUIRES_REBOOT"


class ModuleCategory(str, Enum):
    DIAGNOSTICS = "DIAGNOSTICS"
    CLEANUP = "CLEANUP"
    REPAIR = "REPAIR"
    SECURITY = "SECURITY"
    ANTIVIRUS = "ANTIVIRUS"
    DRIVER_UPDATES = "DRIVER_UPDATES"
    WINGET = "WINGET"
    UNINSTALLER = "UNINSTALLER"
    DASHBOARD = "DASHBOARD"


@dataclass
class ActionDef:
    id: str
    label_sk: str
    label_en: str
    risk: RiskLevel
    command: str
    description_sk: str = ""
    description_en: str = ""
    preview_command: str | None = None
    undo_command: str | None = None
    inactivity_timeout_sec: int | None = None
    hard_cap_sec: int | None = None
    exclude_from_select_all: bool = False
    # Does the action change persistent system state (registry, services,
    # drivers, installed software, system files)? Decides the restore point
    # (preflight.changes_system): None = derived from the risk tier, so only
    # an exception to "non-SAFE changes the system" needs the YAML field.
    changes_system: bool | None = None
    # Does the action put heavy I/O on the system disk (chkdsk /r surface
    # scan, defrag, online NTFS repair)? Pre-flight then asks the disk
    # health probe first and blocks on a failing disk (research G13).
    stresses_disk: bool = False
    # Restarts Windows the moment it succeeds (Defender Offline): the batch
    # runs it last, after the report and undo.ps1 are written (research G03).
    restarts_pc: bool = False
    # Leaves a change that only a restart completes (a pending uninstall, a
    # chkdsk scheduled for boot) which the rest of the batch must not run
    # against: when it succeeds, the batch stops and what is left is offered
    # again on the next start of PortableFix (research G03).
    restart_before_next: bool = False
    # Declarative operations (research G10, portablefix/ops.py) instead of a
    # hand-written command. When set, command and preview_command are
    # generated from them, and undo is generated after the run from the
    # state it captured - so undo_command stays None and has_undo is the
    # question to ask.
    ops: list = field(default_factory=list)
    # Per-item selection (research G05, portablefix/items.py): a SAFE
    # listing command printing one JSON object per item. When set, `command`
    # applies the change to the ids the technician picked ($__pfItems) and
    # `undo_command`, if any, restores ONE item ($__pfItem, $__pfPrior).
    items_command: str | None = None
    # "Already applied?" (research G09): a SAFE command whose last line is
    # APPLIED, NOT_APPLIED or UNKNOWN. A real batch run skips an APPLIED
    # action (and records the skip); generated for every `ops:` action.
    check_command: str | None = None

    @property
    def has_undo(self) -> bool:
        return bool(self.undo_command or self.ops)

    def label(self, language: str) -> str:
        return self.label_en if language == "en" else self.label_sk

    def description(self, language: str) -> str:
        return self.description_en if language == "en" else self.description_sk


@dataclass
class ModuleDef:
    module_id: str
    actions: list[ActionDef]
    category: ModuleCategory = ModuleCategory.DIAGNOSTICS
