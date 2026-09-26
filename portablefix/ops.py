"""Declarative operations (research G10): `ops:` in actions.yaml instead of a
hand-written command.

    ops:
      - reg_set: {path: 'HKCU\\Software\\X', name: Foo, type: DWord, value: 1}
      - reg_delete: {path: 'HKLM\\SOFTWARE\\X', name: Bar}
      - service_start_type: {name: DiagTrack, start_type: disabled}
      - task_state: {path: '\\Microsoft\\Windows\\X\\Task', enabled: false}

From that list this module generates three PowerShell scripts:

* the command - reads the live state every op is about to change (value and
  registry type, or "did not exist"; which keys are missing; the service
  start type; whether the task is enabled) into a per-run JSON state file
  and only then applies the ops;
* the DRY-RUN preview - the same read, printed as "would set X from <now>
  to <new>", changing nothing;
* the undo step for undo.ps1 - generated in Python from the captured state
  after the run, so it puts back exactly what was there: the old value and
  type, deletes values that did not exist, removes keys the run created
  (only while empty) and restores the start type / task state.

Unlike a hand-written undo_command, which can only write a fixed Windows
default, the result returns this PC to how it was.

Safety: every name and path is checked against strict patterns at load time
(hives HKLM/HKCU/HKU/HKCR only, no wildcard characters because New-Item and
Get-ScheduledTask expand them), every value reaches PowerShell only through
the literal helpers below, and the undo generator takes key paths and value
names from the catalog, never from the state file - a tampered state file
can at worst change a value the action itself owns.

Qt-free on purpose: the loader, the GUI and the tests all use it.
"""

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path

STATE_VERSION = 1

# Target of the per-run state file; the executor defines this variable in
# front of the generated command (see executor.build_execution_plan).
STATE_VARIABLE = "$__pfOpsState"

HIVES = {
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKCU": "HKEY_CURRENT_USER",
    "HKU": "HKEY_USERS",
    "HKCR": "HKEY_CLASSES_ROOT",
}

# One registry key name: letters, digits, space and a few punctuation
# characters real tweak keys use ({GUID}, "Windows NT", "Control Panel").
# No wildcards (* ? [ ]) - New-Item -Path would expand them - no quotes, no
# $ or backtick, no backslash (the separator).
_KEY_SEGMENT = r"[A-Za-z0-9_.{}()\-](?:[A-Za-z0-9 _.{}()\-]{0,253}[A-Za-z0-9_.{}()\-])?"
_REG_PATH_RE = re.compile(r"^(HKLM|HKCU|HKU|HKCR)((?:\\" + _KEY_SEGMENT + r"){1,32})$")
_VALUE_NAME_RE = re.compile(r"^[A-Za-z0-9_.{}()\-](?:[A-Za-z0-9 _.{}()\-]{0,253}[A-Za-z0-9_.{}()\-])?$")
DEFAULT_VALUE_NAME = "(default)"
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")
_TASK_SEGMENT = r"[A-Za-z0-9_.{}()\-](?:[A-Za-z0-9 _.{}()\-]{0,230}[A-Za-z0-9_.{}()\-])?"
_TASK_PATH_RE = re.compile(r"^((?:\\" + _TASK_SEGMENT + r"){0,16})\\(" + _TASK_SEGMENT + r")$")
_SID_RE = re.compile(r"^S-1-[0-9]+(?:-[0-9]+){1,14}$")

REG_TYPES = ("String", "ExpandString", "MultiString", "Binary", "DWord", "QWord")
_REG_TYPE_BY_LOWER = {t.lower(): t for t in REG_TYPES}

# YAML start types -> (Start value in the Services key, delayed flag).
START_TYPES = {
    "automatic": (2, False),
    "automatic_delayed": (2, True),
    "manual": (3, False),
    "disabled": (4, False),
}
# Start value -> sc.exe "start=" argument; boot/system are never set by an
# op but can be captured and must then be restorable.
_SC_START = {0: "boot", 1: "system", 2: "auto", 3: "demand", 4: "disabled"}
_START_NAMES = {0: "boot", 1: "system", 2: "automatic", 3: "manual", 4: "disabled"}

_SERVICES_KEY = "Registry::HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Services\\"

_INT32 = (-(2**31), 2**31 - 1)
_INT64 = (-(2**63), 2**63 - 1)


class OpsError(ValueError):
    """A malformed `ops:` entry in actions.yaml."""


class OpsStateError(ValueError):
    """A state file that does not match its action - no undo is generated."""


@dataclass(frozen=True)
class RegSet:
    path: str  # HKLM\SOFTWARE\...
    name: str  # "(default)" = the key's default value
    type: str
    value: object


@dataclass(frozen=True)
class RegDelete:
    path: str
    name: str


@dataclass(frozen=True)
class ServiceStartType:
    name: str
    start_type: str


@dataclass(frozen=True)
class TaskState:
    path: str  # \Folder\Sub\TaskName
    enabled: bool


OP_KINDS = {"reg_set": RegSet, "reg_delete": RegDelete, "service_start_type": ServiceStartType, "task_state": TaskState}
_KIND_OF = {cls: kind for kind, cls in OP_KINDS.items()}


def op_kind(op) -> str:
    return _KIND_OF[type(op)]


# --- PowerShell literals -----------------------------------------------------

# PowerShell's tokenizer treats these typographic quotes like ' - doubling
# only the ASCII quote (as executor/restore_point do for paths) would let
# such a character end the string early.
_PS_QUOTE_LIKE = "‘’‚‛"


def ps_str(value: str) -> str:
    """A PowerShell expression for the exact string `value`, never interpolated.

    Plain text becomes a single-quoted literal with '' escaping (the same
    rule as executor.build_execution_plan). Anything a one-line command or a
    single-quoted string cannot carry verbatim - control characters,
    typographic quotes - goes in as base64 of its UTF-8 bytes, which has no
    special characters at all."""
    if any(ord(c) < 32 or ord(c) == 127 or c in _PS_QUOTE_LIKE for c in value):
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        return f"([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')))"
    return "'" + value.replace("'", "''") + "'"


def _ps_value(reg_type: str, value) -> str:
    """Typed PowerShell expression for a registry value (already validated)."""
    if reg_type == "DWord":
        return f"([int32]'{_signed(value, 32)}')"
    if reg_type == "QWord":
        return f"([int64]'{_signed(value, 64)}')"
    if reg_type == "Binary":
        return "([byte[]]@(" + ",".join(str(int(b)) for b in value) + "))"
    if reg_type == "MultiString":
        return "([string[]]@(" + ",".join(ps_str(s) for s in value) + "))"
    return ps_str(value)


def _signed(value: int, bits: int) -> int:
    # The registry APIs take DWord/QWord as signed Int32/Int64; 0xFFFFFFFF
    # in YAML is the same bits as -1.
    if value >= 2 ** (bits - 1):
        return value - 2**bits
    return value


def _ps_bool(value: bool) -> str:
    return "$true" if value else "$false"


# --- parsing and validation --------------------------------------------------

def _no_control(text: str) -> bool:
    return not any(ord(c) < 32 or ord(c) == 127 for c in text)


def _check_reg_path(path) -> str:
    if not isinstance(path, str) or not _REG_PATH_RE.match(path):
        raise OpsError(
            f"invalid registry path {path!r} (expected HKLM|HKCU|HKU|HKCR\\<key>\\..., "
            "key names of letters, digits, space and _ . - { } ( ) only)"
        )
    return path


def _check_value_name(name) -> str:
    if name == DEFAULT_VALUE_NAME:
        return name
    if not isinstance(name, str) or not _VALUE_NAME_RE.match(name):
        raise OpsError(
            f"invalid value name {name!r} (letters, digits, space and _ . - {{ }} ( ) only, "
            f"or '{DEFAULT_VALUE_NAME}' for the key's default value)"
        )
    return name


def _check_int(value, low: int, high: int, what: str) -> int:
    # bool is an int subclass - `true` must not become 1.
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise OpsError(f"invalid {what} value {value!r} (expected an integer {low}..{high})")
    return value


def _check_reg_value(reg_type: str, value):
    if reg_type == "DWord":
        return _check_int(value, 0, 2**32 - 1, "DWord")
    if reg_type == "QWord":
        return _check_int(value, 0, 2**64 - 1, "QWord")
    if reg_type in ("String", "ExpandString"):
        if not isinstance(value, str) or not _no_control(value) or len(value) > 4096:
            raise OpsError(f"invalid {reg_type} value {value!r} (expected one line of text)")
        return value
    if reg_type == "MultiString":
        if (
            not isinstance(value, list)
            or not all(isinstance(s, str) and _no_control(s) and len(s) <= 4096 for s in value)
            or len(value) > 256
        ):
            raise OpsError(f"invalid MultiString value {value!r} (expected a list of one-line strings)")
        return tuple(value)
    # Binary
    if not isinstance(value, list) or len(value) > 4096 or not all(
        isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in value
    ):
        raise OpsError(f"invalid Binary value {value!r} (expected a list of bytes 0..255)")
    return tuple(value)


def _fields(kind: str, spec, required: tuple[str, ...]) -> dict:
    if not isinstance(spec, dict):
        raise OpsError(f"{kind} must be a mapping with {', '.join(required)}")
    missing = [f for f in required if f not in spec]
    unknown = sorted(str(k) for k in spec if k not in required)
    if missing:
        raise OpsError(f"{kind} is missing {', '.join(missing)}")
    if unknown:
        raise OpsError(f"{kind} has unknown field(s) {', '.join(unknown)}")
    return spec


def parse_op(raw):
    if not isinstance(raw, dict) or len(raw) != 1:
        raise OpsError(f"each op must be a mapping with exactly one of {', '.join(OP_KINDS)}, got {raw!r}")
    ((kind, spec),) = raw.items()
    if kind == "reg_set":
        spec = _fields(kind, spec, ("path", "name", "type", "value"))
        reg_type = _REG_TYPE_BY_LOWER.get(str(spec["type"]).lower()) if isinstance(spec["type"], str) else None
        if reg_type is None:
            raise OpsError(f"invalid registry type {spec['type']!r} (expected one of {', '.join(REG_TYPES)})")
        return RegSet(
            _check_reg_path(spec["path"]), _check_value_name(spec["name"]), reg_type,
            _check_reg_value(reg_type, spec["value"]),
        )
    if kind == "reg_delete":
        spec = _fields(kind, spec, ("path", "name"))
        return RegDelete(_check_reg_path(spec["path"]), _check_value_name(spec["name"]))
    if kind == "service_start_type":
        spec = _fields(kind, spec, ("name", "start_type"))
        if not isinstance(spec["name"], str) or not _SERVICE_RE.match(spec["name"]):
            raise OpsError(f"invalid service name {spec['name']!r} (letters, digits and _ . - only)")
        if not isinstance(spec["start_type"], str) or spec["start_type"] not in START_TYPES:
            raise OpsError(
                f"invalid start_type {spec['start_type']!r} (expected one of {', '.join(START_TYPES)})"
            )
        return ServiceStartType(spec["name"], spec["start_type"])
    if kind == "task_state":
        spec = _fields(kind, spec, ("path", "enabled"))
        if not isinstance(spec["path"], str) or not _TASK_PATH_RE.match(spec["path"]):
            raise OpsError(
                f"invalid task path {spec['path']!r} (expected \\Folder\\TaskName; letters, digits, "
                "space and _ . - { } ( ) only)"
            )
        if not isinstance(spec["enabled"], bool):
            raise OpsError(f"invalid enabled {spec['enabled']!r} (expected true or false)")
        return TaskState(spec["path"], spec["enabled"])
    raise OpsError(f"unknown op {kind!r} (expected one of {', '.join(OP_KINDS)})")


# The generated command travels as one -Command argument: the Windows
# command line (32767 characters) also holds powershell.exe's path, its
# switches and the executor's prefix.
MAX_COMMAND_CHARS = 28000


def parse_ops(raw) -> list:
    if not isinstance(raw, list) or not raw:
        raise OpsError("'ops' must be a non-empty list")
    parsed = []
    for index, entry in enumerate(raw, start=1):
        try:
            parsed.append(parse_op(entry))
        except OpsError as exc:
            raise OpsError(f"op {index}: {exc}") from None
    return parsed


def check_message(message) -> str:
    if message is None:
        return ""
    if not isinstance(message, str) or not _no_control(message) or len(message) > 500:
        raise OpsError(f"invalid ops_message {message!r} (expected one line of text)")
    return message


# --- paths -------------------------------------------------------------------

def _provider_path(path: str) -> str:
    hive, _, rest = path.partition("\\")
    # Registry:: paths reach all four hives (the HKLM:/HKCU: drives do not
    # cover HKU and HKCR) and are never wildcard-expanded with -LiteralPath.
    return f"Registry::{HIVES[hive]}\\{rest}"


def _ancestors(path: str) -> list[str]:
    """Parent keys of `path`, nearest first, never the bare hive."""
    parts = path.split("\\")
    return ["\\".join(parts[:end]) for end in range(len(parts) - 1, 1, -1)]


def _task_split(path: str) -> tuple[str, str]:
    folder, _, name = path.rpartition("\\")
    return folder + "\\", name


def _display(op) -> str:
    if isinstance(op, (RegSet, RegDelete)):
        return f"{op.path}\\{op.name}"
    if isinstance(op, ServiceStartType):
        return f"service {op.name}"
    return f"task {op.path}"


def uses_hkcu(op_list) -> bool:
    return any(isinstance(op, (RegSet, RegDelete)) and op.path.startswith("HKCU\\") for op in op_list)


def describe(op_list) -> str:
    """Human-readable summary for the action detail panel."""
    lines = []
    for op in op_list:
        if isinstance(op, RegSet):
            value = list(op.value) if isinstance(op.value, tuple) else op.value
            lines.append(f"reg_set {op.path}\\{op.name} = {value!r} ({op.type})")
        elif isinstance(op, RegDelete):
            lines.append(f"reg_delete {op.path}\\{op.name}")
        elif isinstance(op, ServiceStartType):
            lines.append(f"service_start_type {op.name} -> {op.start_type}")
        else:
            lines.append(f"task_state {op.path} -> {'enabled' if op.enabled else 'disabled'}")
    # The note on how undo works is the GUI's (i18n "action_detail_ops_undo").
    return "\n".join(lines)


# --- generated PowerShell ----------------------------------------------------

def _ps_op(op) -> str:
    """One op as a PowerShell hashtable literal for the engine below."""
    if isinstance(op, (RegSet, RegDelete)):
        # vn: the name the .NET key API uses - "" for the default value,
        # which the registry provider calls "(default)".
        fields = [
            f"t = '{op_kind(op)}'",
            f"d = {ps_str(op.path)}",
            f"p = {ps_str(_provider_path(op.path))}",
            f"n = {ps_str(op.name)}",
            f"vn = {ps_str('' if op.name == DEFAULT_VALUE_NAME else op.name)}",
            "anc = @(" + ",".join(
                f"@{{ p = {ps_str(_provider_path(a))}; d = {ps_str(a)} }}" for a in _ancestors(op.path)
            ) + ")",
        ]
        if isinstance(op, RegSet):
            fields += [f"k = '{op.type}'", f"v = {_ps_value(op.type, op.value)}"]
        return "@{ " + "; ".join(fields) + " }"
    if isinstance(op, ServiceStartType):
        start, delayed = START_TYPES[op.start_type]
        return "@{ " + "; ".join([
            "t = 'service_start_type'",
            f"n = {ps_str(op.name)}",
            f"p = {ps_str(_SERVICES_KEY + op.name)}",
            f"st = {start}",
            f"dl = {_ps_bool(delayed)}",
            f"sc = '{'delayed-auto' if delayed else _SC_START[start]}'",
        ]) + " }"
    folder, name = _task_split(op.path)
    return "@{ " + "; ".join([
        "t = 'task_state'",
        f"d = {ps_str(op.path)}",
        f"tp = {ps_str(folder)}",
        f"tn = {ps_str(name)}",
        f"en = {_ps_bool(op.enabled)}",
    ]) + " }"


# Shared engine, one statement per list item (joined with "; " into the one
# line every catalog command is). Decisions use only exit codes, enum names
# and registry data - never localized text.
_ENGINE = [
    "$ErrorActionPreference = 'Stop'",
    "$pfKinds = @('String', 'ExpandString', 'MultiString', 'Binary', 'DWord', 'QWord')",
    "$pfStartNames = @{ 0 = 'boot'; 1 = 'system'; 2 = 'automatic'; 3 = 'manual'; 4 = 'disabled' }",
    # Culture-free rendering: a DWord is shown unsigned, binary as hex.
    "function pfShow($k, $v) { if ($k -eq 'DWord') { return ([string][BitConverter]::ToUInt32([BitConverter]::GetBytes([int32]$v), 0) + ' (DWord)') }; "
    "if ($k -eq 'QWord') { return ([string][BitConverter]::ToUInt64([BitConverter]::GetBytes([int64]$v), 0) + ' (QWord)') }; "
    "if ($k -eq 'Binary') { return ('hex:' + ((@($v) | ForEach-Object { '{0:x2}' -f [int]$_ }) -join ',') + ' (Binary)') }; "
    "if ($k -eq 'MultiString') { return ((ConvertTo-Json -Compress -InputObject @(@($v) | ForEach-Object { [string]$_ })) + ' (MultiString)') }; "
    "return (\"'\" + [string]$v + \"' (\" + $k + ')') }",
    "function pfStart($st, $dl) { if ($null -eq $st) { return 'not installed' }; $s = $pfStartNames[[int]$st]; if (-not $s) { $s = 'start ' + [string]$st }; if (([int]$st -eq 2) -and $dl) { $s = 'automatic (delayed)' }; return $s }",
    "function pfErr($r) { '(' + [string]$r.FullyQualifiedErrorId + ', HRESULT 0x' + ('{0:X8}' -f $r.Exception.HResult) + '): ' + $r.Exception.Message }",
    # Capture: what the op is about to change, exactly as it is now.
    "function pfCap($o, $i) { $e = [ordered]@{ i = $i; op = $o.t }; "
    "if ($o.t -eq 'reg_set' -or $o.t -eq 'reg_delete') { $e.key = $o.d; $e.name = $o.n; $e.existed = $false; $e.kind = $null; $e.value = $null; $e.missing_from = $null; "
    "if (Test-Path -LiteralPath $o.p) { $key = Get-Item -LiteralPath $o.p; if (@($key.GetValueNames()) -contains $o.vn) { $kind = [string]$key.GetValueKind($o.vn); "
    "if ($pfKinds -notcontains $kind) { throw ('value ' + $o.d + '\\' + $o.n + ' has registry type ' + $kind + ', which could not be restored exactly') }; "
    "$val = $key.GetValue($o.vn, $null, 'DoNotExpandEnvironmentNames'); if ($kind -eq 'Binary') { $val = @(@($val) | ForEach-Object { [int]$_ }) } elseif ($kind -eq 'MultiString') { $val = @(@($val) | ForEach-Object { [string]$_ }) }; "
    "$e.existed = $true; $e.kind = $kind; $e.value = $val } } "
    "else { $mf = $o.d; foreach ($a in @($o.anc)) { if (Test-Path -LiteralPath $a.p) { break }; $mf = $a.d }; $e.missing_from = $mf } } "
    "elseif ($o.t -eq 'service_start_type') { $e.name = $o.n; $e.existed = $false; $e.start = $null; $e.delayed = $false; "
    "if (Test-Path -LiteralPath $o.p) { $sk = Get-Item -LiteralPath $o.p; $names = @($sk.GetValueNames()); if ($names -contains 'Start') { $e.existed = $true; $e.start = [int]$sk.GetValue('Start'); "
    "$e.delayed = [bool](($names -contains 'DelayedAutostart') -and ([int]$sk.GetValue('DelayedAutostart') -eq 1)) } } } "
    "else { $e.path = $o.d; $e.existed = $false; $e.enabled = $null; $t = $null; "
    "try { $t = Get-ScheduledTask -TaskPath $o.tp -TaskName $o.tn -EA Stop } catch { if ([string]$_.FullyQualifiedErrorId -notlike 'CmdletizationQuery_NotFound*') { throw } }; "
    "if ($t) { $e.existed = $true; $e.enabled = ([string]$t.State -ne 'Disabled') } }; "
    "return $e }",
    # Current -> wanted, as text; $null when the op has nothing to do.
    "function pfPlan($o, $e) { if ($o.t -eq 'reg_set') { $new = pfShow $o.k $o.v; $old = 'absent'; if ($e.existed) { $old = pfShow $e.kind $e.value }; if ($old -eq $new) { return $null }; return @($old, $new) } "
    "elseif ($o.t -eq 'reg_delete') { if (-not $e.existed) { return $null }; return @((pfShow $e.kind $e.value), 'absent') } "
    "elseif ($o.t -eq 'service_start_type') { if (-not $e.existed) { return $null }; $old = pfStart $e.start $e.delayed; $new = pfStart $o.st $o.dl; if ($old -eq $new) { return $null }; return @($old, $new) } "
    "else { if (-not $e.existed) { return $null }; $old = $(if ($e.enabled) { 'enabled' } else { 'disabled' }); $new = $(if ($o.en) { 'enabled' } else { 'disabled' }); if ($old -eq $new) { return $null }; return @($old, $new) } }",
    "function pfWhat($o) { if ($o.t -eq 'service_start_type') { return ('Service ' + $o.n + ' start type') }; if ($o.t -eq 'task_state') { return ('Task ' + $o.d) }; return ($o.d + '\\' + $o.n) }",
    "function pfSkip($o, $e) { if (($o.t -eq 'service_start_type') -and (-not $e.existed)) { return ('Service ' + $o.n + ' is not installed - skipped.') }; "
    "if (($o.t -eq 'task_state') -and (-not $e.existed)) { return ('Task ' + $o.d + ' does not exist - skipped.') }; "
    "if (($o.t -eq 'reg_delete') -and (-not $e.existed)) { return ($o.d + '\\' + $o.n + ' does not exist - nothing to delete.') }; "
    "return ((pfWhat $o) + ' is already ' + $(if ($o.t -eq 'reg_set') { pfShow $o.k $o.v } elseif ($o.t -eq 'service_start_type') { pfStart $o.st $o.dl } elseif ($o.en) { 'enabled' } else { 'disabled' }) + ' - unchanged.') }",
]

_APPLY = [
    "function pfApply($o, $e) { if ($o.t -eq 'reg_set') { if (-not (Test-Path -LiteralPath $o.p)) { New-Item -Path $o.p -Force | Out-Null }; "
    "New-ItemProperty -LiteralPath $o.p -Name $o.n -Value $o.v -PropertyType $o.k -Force | Out-Null } "
    "elseif ($o.t -eq 'reg_delete') { Remove-ItemProperty -LiteralPath $o.p -Name $o.n } "
    # sc.exe, not Set-Service: Windows PowerShell 5.1 cannot set a delayed
    # start. Only its exit code is read - its text is localized.
    "elseif ($o.t -eq 'service_start_type') { $null = & sc.exe config $o.n start= $o.sc; if ($LASTEXITCODE -ne 0) { throw ('sc.exe config ' + $o.n + ' failed with exit code ' + $LASTEXITCODE) }; "
    # Read back what the service manager stored: exit code 0 alone does
    # not prove the delayed flag followed.
    "$after = pfCap $o 0; if (-not ((pfStart $after.start $after.delayed) -eq (pfStart $o.st $o.dl))) { throw ('the start type of ' + $o.n + ' is ' + (pfStart $after.start $after.delayed) + ' after sc.exe config') } } "
    "elseif ($o.en) { Enable-ScheduledTask -TaskPath $o.tp -TaskName $o.tn | Out-Null } else { Disable-ScheduledTask -TaskPath $o.tp -TaskName $o.tn | Out-Null } }",
]

_IDENTITY = "[Security.Principal.WindowsIdentity]::GetCurrent()"


def _prelude(op_list) -> list[str]:
    return ["$pfOps = @(" + ", ".join(_ps_op(op) for op in op_list) + ")"] + _ENGINE


def _capture_all(nothing_changed: str) -> str:
    return (
        "$pfEntries = New-Object System.Collections.ArrayList; "
        "try { for ($i = 0; $i -lt $pfOps.Count; $i++) { [void]$pfEntries.Add((pfCap $pfOps[$i] $i)) } } "
        f"catch {{ Write-Output ('Could not read the current state ' + (pfErr $_) + '. {nothing_changed}'); exit 1 }}"
    )


def _identity(required: bool) -> str:
    # HKCU is the hive of whoever PortableFix runs as - which is not the
    # signed-in customer when the technician elevated with their own
    # account. The SID goes into the state file so undo never writes these
    # values into a different user's hive.
    fail = (
        "Write-Output 'Could not identify the user PortableFix runs as - HKCU values could not be tied to a user. "
        "Nothing was changed.'; exit 1"
        if required else "$pfWho = $null"
    )
    return (
        f"$pfWho = $null; try {{ $pfId = {_IDENTITY}; $pfWho = @([string]$pfId.Name, [string]$pfId.User.Value) }} catch {{ }}; "
        f"if (-not ($pfWho -and $pfWho[1])) {{ {fail} }} "
        "else { Write-Output ('HKCU = registry hive of ' + $pfWho[0] + ' (' + $pfWho[1] + ') - the user PortableFix runs as.') }"
    )


def apply_script(action_id: str, op_list, message: str = "") -> str:
    """The action's command: capture everything, save it, then apply."""
    hkcu = uses_hkcu(op_list)
    statements = [
        f"if (-not {STATE_VARIABLE}) {{ Write-Output 'No state file was given, so the previous values could not be "
        "recorded for undo. Nothing was changed.'; exit 1 }"
    ]
    statements += _prelude(op_list) + _APPLY
    if hkcu:
        statements.append(_identity(required=True))
    statements.append(_capture_all("Nothing was changed."))
    who = "$pfWho[0]" if hkcu else "$null"
    sid = "$pfWho[1]" if hkcu else "$null"
    statements.append(
        # Remove-TypeData: Windows PowerShell 5.1 otherwise writes a wrapped
        # array (the entries, a Binary or MultiString value) as
        # {"value": [...], "Count": n}, which read_state rejects.
        "try { Remove-TypeData -TypeName System.Array -EA SilentlyContinue; "
        "[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName(" + STATE_VARIABLE + ")); "
        f"$pfJson = ConvertTo-Json -Depth 6 -InputObject ([ordered]@{{ version = {STATE_VERSION}; action = {ps_str(action_id)}; "
        f"user = {who}; sid = {sid}; entries = @($pfEntries) }}); "
        f"[IO.File]::WriteAllText({STATE_VARIABLE}, $pfJson, (New-Object Text.UTF8Encoding($false))) }} "
        f"catch {{ Write-Output ('Could not save the previous state to ' + {STATE_VARIABLE} + ' ' + (pfErr $_) + '. Nothing was changed.'); exit 1 }}"
    )
    # The saved capture stays the pre-run state undo restores, but whether
    # an op has anything to do is decided on a fresh read: an earlier op of
    # this same action may have changed the same value, service or task.
    statements.append(
        "for ($i = 0; $i -lt $pfOps.Count; $i++) { $o = $pfOps[$i]; $e = $null; $pl = $null; "
        "try { $e = pfCap $o $i; $pl = pfPlan $o $e; if ($null -ne $pl) { pfApply $o $e } } "
        "catch { Write-Output ('FAILED: ' + (pfWhat $o) + ' ' + (pfErr $_)); "
        f"Write-Output ('The previous state is saved in ' + {STATE_VARIABLE} + ' - undo.ps1 restores what was changed so far.'); exit 1 }}; "
        "if ($null -eq $pl) { Write-Output (pfSkip $o $e); continue }; "
        "$created = ''; if (($o.t -eq 'reg_set') -and $e.missing_from) { $created = ' (created key ' + $e.missing_from + ')' }; "
        "Write-Output ((pfWhat $o) + ': ' + $pl[0] + ' -> ' + $pl[1] + $created) }"
    )
    if message:
        statements.append(f"Write-Output {ps_str(message)}")
    statements.append(
        f"Write-Output ('Previous state saved to ' + {STATE_VARIABLE} + ' - undo.ps1 restores exactly this state.')"
    )
    return "; ".join(statements)


def build(action_id: str, op_list, message: str = "") -> tuple[str, str]:
    """(command, preview_command) for the loader."""
    command = apply_script(action_id, op_list, message)
    if len(command) > MAX_COMMAND_CHARS:
        raise OpsError(
            f"{len(op_list)} ops make a {len(command)}-character command (at most {MAX_COMMAND_CHARS}) - "
            "split them into several actions"
        )
    return command, preview_script(op_list)


def preview_script(op_list) -> str:
    """The DRY-RUN preview: the same capture, printed, nothing written."""
    statements = _prelude(op_list)
    if uses_hkcu(op_list):
        statements.append(_identity(required=False))
    statements.append(_capture_all("Nothing would be changed."))
    statements.append(
        "for ($i = 0; $i -lt $pfOps.Count; $i++) { $o = $pfOps[$i]; $e = $pfEntries[$i]; $pl = pfPlan $o $e; "
        "if ($null -eq $pl) { Write-Output ('Would skip: ' + (pfSkip $o $e)); continue }; "
        "$created = ''; if (($o.t -eq 'reg_set') -and $e.missing_from) { $created = ' (would create key ' + $e.missing_from + ')' }; "
        "Write-Output ('Would change ' + (pfWhat $o) + ' from ' + $pl[0] + ' to ' + $pl[1] + $created) }"
    )
    statements.append(
        "Write-Output 'The current values would be saved first, so undo.ps1 could restore exactly this state.'"
    )
    return "; ".join(statements)


def check_script(op_list) -> str:
    """The generated "already applied?" check (research G09): the same read
    as the preview, answering APPLIED when no op has anything left to do,
    NOT_APPLIED otherwise and UNKNOWN when the state cannot be read."""
    statements = _prelude(op_list)
    statements.append(
        "$pfEntries = New-Object System.Collections.ArrayList; "
        "try { for ($i = 0; $i -lt $pfOps.Count; $i++) { [void]$pfEntries.Add((pfCap $pfOps[$i] $i)) } } "
        "catch { Write-Output ('Could not read the current state ' + (pfErr $_)); Write-Output 'UNKNOWN'; exit 0 }"
    )
    statements.append(
        "$pfTodo = 0; for ($i = 0; $i -lt $pfOps.Count; $i++) { if ($null -ne (pfPlan $pfOps[$i] $pfEntries[$i])) { $pfTodo++ } }; "
        "if ($pfTodo -eq 0) { Write-Output 'APPLIED' } else { Write-Output 'NOT_APPLIED' }"
    )
    return "; ".join(statements)


# --- state file and undo -----------------------------------------------------

_SAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_.\-]")


def state_file_path(base_dir: Path, run_id: str, action_id: str) -> Path:
    """Backups/<run_id>/state/<action_id>.json - a fresh name per run of the
    action, so running it twice keeps both captures (undo.ps1 replays them
    newest first, back to the very first state)."""
    folder = Path(base_dir) / "Backups" / run_id / "state"
    stem = _SAFE_FILE_CHARS.sub("_", action_id) or "action"
    candidate = folder / f"{stem}.json"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}.json"
        counter += 1
    return candidate


def _is_ancestor_or_self(ancestor: str, path: str) -> bool:
    a, p = ancestor.lower(), path.lower()
    return p == a or p.startswith(a + "\\")


def _restored_value(reg_type, value):
    """Validate a captured value against its type; returns the value in the
    form _ps_value expects."""
    if reg_type == "DWord":
        if isinstance(value, bool) or not isinstance(value, int) or not _INT32[0] <= value <= 2**32 - 1:
            raise OpsStateError(f"invalid DWord {value!r}")
        return value % 2**32
    if reg_type == "QWord":
        if isinstance(value, bool) or not isinstance(value, int) or not _INT64[0] <= value <= 2**64 - 1:
            raise OpsStateError(f"invalid QWord {value!r}")
        return value % 2**64
    if reg_type in ("String", "ExpandString"):
        if not isinstance(value, str):
            raise OpsStateError(f"invalid {reg_type} {value!r}")
        return value
    if reg_type == "MultiString":
        # ConvertTo-Json writes a one-element array as a list, but an older
        # serializer quirk could flatten it - accept a bare string too.
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise OpsStateError(f"invalid MultiString {value!r}")
        return value
    if reg_type == "Binary":
        if isinstance(value, int) and not isinstance(value, bool):
            value = [value]
        if not isinstance(value, list) or not all(
            isinstance(b, int) and not isinstance(b, bool) and 0 <= b <= 255 for b in value
        ):
            raise OpsStateError(f"invalid Binary {value!r}")
        return value
    raise OpsStateError(f"unknown registry type {reg_type!r}")


def _show(reg_type: str, value) -> str:
    if reg_type == "Binary":
        return "hex:" + ",".join(f"{b:02x}" for b in value) + " (Binary)"
    if reg_type == "MultiString":
        return json.dumps(value, ensure_ascii=False) + " (MultiString)"
    if reg_type in ("DWord", "QWord"):
        return f"{value} ({reg_type})"
    return f"'{value}' ({reg_type})"


def _flat(text: str) -> str:
    # For comments and messages: a line break would end a comment and make
    # the rest executable.
    return " ".join(str(text).split())


def _undo_reg(op, entry) -> list[str]:
    p = ps_str(_provider_path(op.path))
    what = f"{op.path}\\{op.name}"
    if entry["existed"]:
        kind = entry.get("kind")
        if kind not in REG_TYPES:
            raise OpsStateError(f"{what}: unknown registry type {kind!r}")
        value = _restored_value(kind, entry.get("value"))
        return [
            f"if (-not (Test-Path -LiteralPath {p})) {{ New-Item -Path {p} -Force | Out-Null }}",
            f"New-ItemProperty -LiteralPath {p} -Name {ps_str(op.name)} -Value {_ps_value(kind, value)} "
            f"-PropertyType {kind} -Force | Out-Null",
            f"Write-Output {ps_str('Restored ' + what + ' = ' + _show(kind, value))}",
        ]
    vn = ps_str("" if op.name == DEFAULT_VALUE_NAME else op.name)
    return [
        f"if ((Test-Path -LiteralPath {p}) -and (@((Get-Item -LiteralPath {p}).GetValueNames()) -contains {vn})) "
        f"{{ Remove-ItemProperty -LiteralPath {p} -Name {ps_str(op.name)} }}",
        f"Write-Output {ps_str('Restored ' + what + ': deleted (it did not exist before)')}",
    ]


def _undo_service(op, entry) -> list[str]:
    if not entry["existed"]:
        return []
    start, delayed = entry.get("start"), entry.get("delayed")
    if isinstance(start, bool) or start not in _SC_START or not isinstance(delayed, bool):
        raise OpsStateError(f"service {op.name}: invalid start {start!r}/{delayed!r}")
    arg = "delayed-auto" if start == 2 and delayed else _SC_START[start]
    shown = "automatic (delayed)" if start == 2 and delayed else _START_NAMES[start]
    p = ps_str(_SERVICES_KEY + op.name)
    # The delayed flag only means something with Start = 2.
    delayed_check = f" -or ($pfDl -ne {_ps_bool(delayed)})" if start == 2 else ""
    return [
        f"$null = & sc.exe config {ps_str(op.name)} start= {arg}",
        f"if ($LASTEXITCODE -ne 0) {{ throw ('sc.exe config failed with exit code ' + $LASTEXITCODE) }}",
        # Read back, as the command does: exit code 0 alone does not prove
        # the delayed flag followed.
        f"$pfSk = Get-Item -LiteralPath {p}; $pfNames = @($pfSk.GetValueNames()); $pfSt = $null; "
        "if ($pfNames -contains 'Start') { $pfSt = [int]$pfSk.GetValue('Start') }; "
        "$pfDl = [bool](($pfNames -contains 'DelayedAutostart') -and ([int]$pfSk.GetValue('DelayedAutostart') -eq 1))",
        f"if (($pfSt -ne {start}){delayed_check}) {{ throw ('the start type is Start=' + [string]$pfSt + ', delayed=' + [string]$pfDl + ' after sc.exe config') }}",
        f"Write-Output {ps_str('Restored service ' + op.name + ' start type: ' + shown)}",
    ]


def _undo_task(op, entry) -> list[str]:
    if not entry["existed"]:
        return []
    enabled = entry.get("enabled")
    if not isinstance(enabled, bool):
        raise OpsStateError(f"task {op.path}: invalid enabled {enabled!r}")
    folder, name = _task_split(op.path)
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    return [
        f"{verb} -TaskPath {ps_str(folder)} -TaskName {ps_str(name)} | Out-Null",
        f"Write-Output {ps_str('Restored task ' + op.path + ': ' + ('enabled' if enabled else 'disabled'))}",
    ]


def _guarded(lines: list[str], what: str) -> str:
    # Each restore on its own: one that fails (access denied, a service
    # removed since) must not stop the rest of the action's undo - and
    # never the other actions' steps further down undo.ps1.
    body = "; ".join(lines)
    return (
        f"try {{ {body} }} catch {{ Write-Output ('FAILED to restore ' + {ps_str(what)} + ' ('"
        " + [string]$_.FullyQualifiedErrorId + ', HRESULT 0x' + ('{0:X8}' -f $_.Exception.HResult) + '): '"
        " + $_.Exception.Message) }"
    )


def undo_script(action_id: str, op_list, state: dict, state_path: Path | str | None = None) -> str:
    """undo.ps1 step restoring exactly what `state` (the parsed state file)
    captured. Raises OpsStateError when the state does not belong to this
    action's ops - then no undo is better than a wrong one."""
    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise OpsStateError("unknown state file format")
    if state.get("action") != action_id:
        raise OpsStateError(f"state file belongs to {state.get('action')!r}, not {action_id!r}")
    entries = state.get("entries")
    if not isinstance(entries, list) or len(entries) != len(op_list):
        raise OpsStateError("state file does not list one entry per op")
    hkcu = uses_hkcu(op_list)
    sid = state.get("sid")
    user = state.get("user")
    if hkcu and (not isinstance(sid, str) or not _SID_RE.match(sid)):
        raise OpsStateError(f"invalid user SID {sid!r}")
    user = _flat(user)[:200] if isinstance(user, str) else "?"

    plain: list[str] = []
    per_user: list[str] = []
    created: dict[str, str] = {}
    # Newest op first: when two ops touch the same value, the first one's
    # capture - the original - is written last and wins.
    for index in range(len(op_list) - 1, -1, -1):
        op, entry = op_list[index], entries[index]
        if not isinstance(entry, dict) or entry.get("i") != index or entry.get("op") != op_kind(op):
            raise OpsStateError(f"entry {index} does not match op {op_kind(op)}")
        if not isinstance(entry.get("existed"), bool):
            raise OpsStateError(f"entry {index}: invalid 'existed'")
        if isinstance(op, (RegSet, RegDelete)):
            # The key and name always come from the catalog; the state file
            # has to agree with them but never supplies them.
            if entry.get("key") != op.path or entry.get("name") != op.name:
                raise OpsStateError(f"entry {index} is for another value")
            lines = _undo_reg(op, entry)
            missing_from = entry.get("missing_from")
            if isinstance(op, RegSet) and missing_from is not None:
                if (
                    entry["existed"]
                    or not isinstance(missing_from, str)
                    or not _REG_PATH_RE.match(missing_from)
                    or not _is_ancestor_or_self(missing_from, op.path)
                ):
                    raise OpsStateError(f"entry {index}: invalid missing_from {missing_from!r}")
                depth = missing_from.count("\\")
                for key in [op.path] + _ancestors(op.path):
                    if key.count("\\") >= depth:
                        created.setdefault(key.lower(), key)
            target = per_user if op.path.startswith("HKCU\\") else plain
            target.append(_guarded(lines, f"{op.path}\\{op.name}"))
        elif isinstance(op, ServiceStartType):
            if entry.get("name") != op.name:
                raise OpsStateError(f"entry {index} is for another service")
            lines = _undo_service(op, entry)
            if lines:
                plain.append(_guarded(lines, f"service {op.name}"))
        else:
            if entry.get("path") != op.path:
                raise OpsStateError(f"entry {index} is for another task")
            lines = _undo_task(op, entry)
            if lines:
                plain.append(_guarded(lines, f"task {op.path}"))

    # Keys the run created go last, deepest first, and only while empty:
    # anything written there since belongs to someone else and stays.
    for key in sorted(created.values(), key=lambda k: k.count("\\"), reverse=True):
        p = ps_str(_provider_path(key))
        removal = [
            f"if (Test-Path -LiteralPath {p}) {{ $k = Get-Item -LiteralPath {p}; "
            f"if ((@($k.GetValueNames()).Count -eq 0) -and ([int]$k.SubKeyCount -eq 0)) {{ Remove-Item -LiteralPath {p}; "
            f"Write-Output {ps_str('Removed key ' + key + ' (it did not exist before)')} }} "
            f"else {{ Write-Output {ps_str('Kept key ' + key + ': it did not exist before, but now holds data PortableFix did not write.')} }} }}"
        ]
        target = per_user if key.startswith("HKCU\\") else plain
        target.append(_guarded(removal, key))

    where = _flat(state_path) if state_path is not None else "?"
    lines = [
        f"# {_flat(action_id)}: restores the state captured just before it ran ({where})",
        "& {",
        "$ErrorActionPreference = 'Stop'",
    ]
    lines.extend(plain)
    if per_user:
        lines.append(
            f"$pfSame = $false; try {{ $pfSame = ([string]({_IDENTITY}.User.Value) -eq {ps_str(sid)}) }} catch {{ }}"
        )
        lines.append(
            f"if (-not $pfSame) {{ Write-Output {ps_str('Skipped the HKCU values of ' + user + ' (' + sid + '): this undo runs as a different user, whose HKCU is another hive. Run undo.ps1 as ' + user + '.')} }}"
        )
        lines.append("if ($pfSame) {")
        lines.extend(per_user)
        lines.append("}")
    lines.append("}")
    return "\n".join(lines)


def undo_step(action_id: str, op_list, state_path: Path | None) -> str | None:
    """undo.ps1 step for one run of an ops action, or None when the run never
    got as far as saving its state (it refused before changing anything).
    Raises OpsStateError when the file is there but unusable."""
    if state_path is None or not Path(state_path).is_file():
        return None
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise OpsStateError(f"state file {state_path} could not be read: {exc}") from None
    return undo_script(action_id, op_list, state, state_path)
