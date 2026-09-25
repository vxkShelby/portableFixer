"""Silent-uninstall planning for the Uninstaller panel (research G15).

Qt-free and registry-free: it works on anything shaped like
uninstaller.InstalledProgram (uninstall_string, quiet_uninstall_string,
registry_path and the optional windows_installer / inno_setup / nsis_marker
hints), so tests feed it plain objects on any OS.

Three questions:
- build_plan(): which installer made this entry, and which exact argv
  removes it - silently when the installer type is known, else the
  vendor's QuietUninstallString, else the interactive UninstallString.
- execute_plan(): run that argv (never through a shell) and interpret the
  exit code - msiexec's 1605/1641/3010/1618 are not plain "failed".
- interpret_exit_code(): the exit-code mapping on its own.

Registry text is untrusted (any installer writes it). It is parsed into an
argv list with the Windows CRT rules and handed to CreateProcess directly,
so "&", "|" or ">" in it are just characters of an argument - there is no
cmd.exe in between to give them a meaning.
"""

import ntpath
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

KIND_MSI = "msi"
KIND_INNO = "inno"
KIND_NSIS = "nsis"
KIND_VENDOR_QUIET = "vendor_quiet"
KIND_INTERACTIVE = "interactive"
KIND_NONE = "none"

# Strict: exactly one braced product code, nothing before or after - the
# only registry-derived text that ever reaches the msiexec argv.
_GUID_RE = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")
_INNO_EXE_RE = re.compile(r"unins\d{3}\.exe", re.IGNORECASE)
# The unquoted "C:\Program Files\App\uninst.exe /x" form: the program ends
# at the first executable extension followed by a blank (what CreateProcess
# would try for .exe; cmd.exe used to do the same for .bat/.cmd).
_UNQUOTED_EXE_RE = re.compile(r"(.*?\.(?:exe|com|bat|cmd))(?=[ \t]|$)", re.IGNORECASE)
_ENV_VAR_RE = re.compile(r"%([A-Za-z0-9_()]+)%")
# NSIS's first header ("\xEF\xBE\xAD\xDE" + "NullsoftInst") sits in the
# overlay behind the small PE stub; uninstallers are well under this size.
_NSIS_SIGNATURE = b"NullsoftInst"
_NSIS_SCAN_LIMIT = 8 * 1024 * 1024

_INNO_SILENT_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")

# msiexec / Windows Installer exit codes (documented, locale-free):
# 1605 ERROR_UNKNOWN_PRODUCT - the product is not installed (any more),
# 1641 ERROR_SUCCESS_REBOOT_INITIATED, 3010 ERROR_SUCCESS_REBOOT_REQUIRED,
# 1618 ERROR_INSTALL_ALREADY_RUNNING, 1602 ERROR_INSTALL_USEREXIT.
OUTCOME_OK = "ok"
OUTCOME_ALREADY_GONE = "already_gone"
OUTCOME_REBOOT_INITIATED = "reboot_initiated"
OUTCOME_REBOOT_REQUIRED = "reboot_required"
OUTCOME_BUSY_RETRY = "busy_retry"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_FAILED = "failed"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_LAUNCH_ERROR = "launch_error"
OUTCOME_NO_COMMAND = "no_command"

_MSI_EXIT_CODES = {
    0: (True, OUTCOME_OK),
    1605: (True, OUTCOME_ALREADY_GONE),
    1641: (True, OUTCOME_REBOOT_INITIATED),
    3010: (True, OUTCOME_REBOOT_REQUIRED),
    1618: (False, OUTCOME_BUSY_RETRY),
    1602: (False, OUTCOME_CANCELLED),
}

# English, for the audit log and the report (the panel shows the i18n
# text "uninstaller_outcome_<outcome>" instead).
OUTCOME_MESSAGES = {
    OUTCOME_OK: "Uninstalled.",
    OUTCOME_ALREADY_GONE: "msiexec 1605: the product is not installed - already removed.",
    OUTCOME_REBOOT_INITIATED: "msiexec 1641: uninstalled, the installer started a restart.",
    OUTCOME_REBOOT_REQUIRED: "msiexec 3010: uninstalled, a restart is required to finish.",
    OUTCOME_BUSY_RETRY: "msiexec 1618: another installation is in progress - wait for it to finish and try again.",
    OUTCOME_CANCELLED: "msiexec 1602: the uninstall was cancelled.",
    OUTCOME_FAILED: "The uninstaller reported a failure.",
    OUTCOME_TIMEOUT: "Uninstaller timed out.",
    OUTCOME_LAUNCH_ERROR: "The uninstaller could not be started.",
    OUTCOME_NO_COMMAND: "No uninstall command found for this program.",
}

# Why the NSIS argv carries no "_?=<dir>": an NSIS uninstaller copies itself
# to %TEMP% and relaunches from there so it can delete its own folder - the
# process we wait for exits right away while the copy keeps working. "_?="
# would make it run in place and wait, but then Uninstall.exe and the
# install folder are left behind. We accept the early return instead.
NSIS_BACKGROUND_NOTE = (
    "NSIS uninstallers finish in a background copy - the program may disappear "
    "from the list only a few seconds after this."
)


@dataclass(frozen=True)
class UninstallPlan:
    kind: str
    argv: tuple[str, ...]
    # Silent plans run with the timeout; interactive ones wait for the
    # technician to click through the uninstaller's own window.
    silent: bool
    product_code: str | None = None
    log_path: str | None = None

    @property
    def command(self) -> str:
        # Exactly the command line CreateProcess receives (subprocess builds
        # it with the same function) - what DRY-RUN and the audit show.
        return subprocess.list2cmdline(list(self.argv)) if self.argv else ""


class UninstallResult(NamedTuple):
    ok: bool
    output: str
    exit_code: int | None
    outcome: str


def _split_arguments(text: str) -> list[str]:
    # The Windows CRT (CommandLineToArgvW) rules: blanks separate, quotes
    # group, 2n backslashes + quote -> n backslashes and a quote toggle,
    # 2n+1 -> n backslashes and a literal quote, "" inside quotes -> ".
    args: list[str] = []
    i, n = 0, len(text)
    while True:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            return args
        buf: list[str] = []
        in_quotes = False
        while i < n:
            ch = text[i]
            if ch == "\\":
                j = i
                while j < n and text[j] == "\\":
                    j += 1
                count = j - i
                if j < n and text[j] == '"':
                    buf.append("\\" * (count // 2))
                    if count % 2:
                        buf.append('"')
                        i = j + 1
                    else:
                        i = j
                else:
                    buf.append("\\" * count)
                    i = j
                continue
            if ch == '"':
                if in_quotes and i + 1 < n and text[i + 1] == '"':
                    buf.append('"')
                    i += 2
                    continue
                in_quotes = not in_quotes
                i += 1
                continue
            if ch in " \t" and not in_quotes:
                break
            buf.append(ch)
            i += 1
        args.append("".join(buf))


def _expand_env(text: str, environ) -> str:
    # UninstallString is often REG_EXPAND_SZ ("%ProgramFiles%\..."), which
    # winreg returns unexpanded - cmd.exe used to expand it. Only %NAME%
    # forms; an unknown variable stays as written.
    def repl(match: re.Match) -> str:
        name = match.group(1)
        for key, value in environ.items():
            if key.upper() == name.upper():
                return value
        return match.group(0)

    return _ENV_VAR_RE.sub(repl, text)


def split_command_line(text: str | None, environ=None) -> list[str] | None:
    """Parse a registry uninstall command into argv, or None when empty."""
    if not text or not text.strip():
        return None
    environ = os.environ if environ is None else environ
    text = text.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        program, rest = (text[1:], "") if end == -1 else (text[1:end], text[end + 1:])
    else:
        match = _UNQUOTED_EXE_RE.match(text)
        if match:
            program, rest = match.group(1), text[match.end():]
        else:
            program, _, rest = text.replace("\t", " ").partition(" ")
    program = _expand_env(program.strip(), environ)
    if not program:
        return None
    return [program] + [_expand_env(arg, environ) for arg in _split_arguments(rest)]


def _basename(program: str) -> str:
    return ntpath.basename(program).lower()


def _is_msiexec(argv: list[str] | None) -> bool:
    return bool(argv) and _basename(argv[0]) in ("msiexec", "msiexec.exe")


def _product_code_from_msiexec(argv: list[str] | None) -> str | None:
    # "MsiExec.exe /I{GUID}", "/X{GUID}" or "/X {GUID}" - only the token
    # after the /I or /X switch counts, and only when it is a whole GUID.
    if not _is_msiexec(argv):
        return None
    args = argv[1:]
    for index, arg in enumerate(args):
        if len(arg) >= 2 and arg[0] in "/-" and arg[1] in "IiXx":
            value = arg[2:] or (args[index + 1] if index + 1 < len(args) else "")
            if _GUID_RE.fullmatch(value):
                return value.upper()
            return None
    return None


def _product_code_from_key(registry_path: str | None) -> str | None:
    # For MSI entries Windows names the Uninstall subkey after the product code.
    name = ntpath.basename(registry_path or "")
    return name.upper() if _GUID_RE.fullmatch(name) else None


def msiexec_path(environ=None) -> str:
    # Full path: a bare "msiexec.exe" is searched in the application's own
    # folder first - on a portable tool, a USB stick anyone could write to.
    environ = os.environ if environ is None else environ
    root = environ.get("SystemRoot") or environ.get("SYSTEMROOT") or r"C:\Windows"
    return ntpath.join(root, "System32", "msiexec.exe")


def file_has_nsis_marker(path: str, limit: int = _NSIS_SCAN_LIMIT) -> bool:
    try:
        with open(path, "rb") as handle:
            tail = b""
            read = 0
            while read < limit:
                chunk = handle.read(min(1024 * 1024, limit - read))
                if not chunk:
                    return False
                if _NSIS_SIGNATURE in tail + chunk:
                    return True
                tail = chunk[-len(_NSIS_SIGNATURE):]
                read += len(chunk)
    except OSError:
        return False
    return False


def msi_log_name(product_code: str) -> str:
    return f"msi_uninstall_{product_code.strip('{}')}.log"


def _with_flags(argv: list[str], flags: tuple[str, ...], case_sensitive: bool = False) -> tuple[str, ...]:
    def norm(value: str) -> str:
        return value if case_sensitive else value.upper()

    present = {norm(arg) for arg in argv[1:]}
    return tuple(argv) + tuple(flag for flag in flags if norm(flag) not in present)


def build_plan(
    program,
    log_dir: str | Path | None = None,
    environ=None,
    has_nsis_marker: Callable[[str], bool] = file_has_nsis_marker,
) -> UninstallPlan:
    """Pick the installer type and the argv that removes `program`."""
    environ = os.environ if environ is None else environ
    plain = split_command_line(getattr(program, "uninstall_string", None), environ)
    quiet = split_command_line(getattr(program, "quiet_uninstall_string", None), environ)

    # 1. Windows Installer: always our own "/x {GUID} /qn". The entry's own
    # string is usually "MsiExec.exe /I{GUID}", which opens the maintenance
    # dialog instead of removing anything - it never runs.
    windows_installer = bool(getattr(program, "windows_installer", False))
    if windows_installer or _is_msiexec(plain) or _is_msiexec(quiet):
        code = (
            _product_code_from_msiexec(plain) or _product_code_from_msiexec(quiet)
            or (_product_code_from_key(getattr(program, "registry_path", None)) if windows_installer else None)
        )
        if code:
            argv = [msiexec_path(environ), "/x", code, "/qn", "/norestart"]
            log_path = None
            if log_dir is not None:
                log_path = str(Path(log_dir) / msi_log_name(code))
                argv += ["/l*v", log_path]
            return UninstallPlan(KIND_MSI, tuple(argv), True, product_code=code, log_path=log_path)
        # An msiexec command without a usable product code: nothing safe to
        # run (it may well be an /I), unless the vendor gave a quiet string
        # that is not msiexec itself.
        if quiet and not _is_msiexec(quiet):
            return UninstallPlan(KIND_VENDOR_QUIET, tuple(quiet), True)
        if _is_msiexec(plain) or not plain:
            return UninstallPlan(KIND_NONE, (), False)
    if _is_msiexec(quiet):
        # Only reachable without a product code: never run it (see above).
        quiet = None

    if plain:
        name = _basename(plain[0])
        # 2. Inno Setup: unins000.exe, or the "Inno Setup: ..." values it writes.
        if getattr(program, "inno_setup", False) or _INNO_EXE_RE.fullmatch(name):
            return UninstallPlan(KIND_INNO, _with_flags(plain, _INNO_SILENT_FLAGS), True)
        # 3. NSIS: an uninst*.exe that carries the NSIS header, or an entry
        # that names NSIS. "/S" is case-sensitive for NSIS. No "_?=" - see
        # NSIS_BACKGROUND_NOTE.
        if name.endswith(".exe") and (
            getattr(program, "nsis_marker", False)
            or (name.startswith("uninst") and has_nsis_marker(plain[0]))
        ):
            return UninstallPlan(KIND_NSIS, _with_flags(plain, ("/S",), case_sensitive=True), True)

    # 4. The vendor's own silent command, 5. the interactive one.
    if quiet:
        return UninstallPlan(KIND_VENDOR_QUIET, tuple(quiet), True)
    if plain:
        return UninstallPlan(KIND_INTERACTIVE, tuple(plain), False)
    return UninstallPlan(KIND_NONE, (), False)


def interpret_exit_code(plan: UninstallPlan, returncode: int) -> tuple[bool, str]:
    if plan.kind == KIND_MSI or _is_msiexec(list(plan.argv)):
        if returncode in _MSI_EXIT_CODES:
            return _MSI_EXIT_CODES[returncode]
    if returncode == 0:
        return True, OUTCOME_OK
    return False, OUTCOME_FAILED


def execute_plan(plan: UninstallPlan, timeout_sec: int | None, run=None) -> UninstallResult:
    """Run the plan's argv without a shell; timeout_sec None waits forever."""
    run = run or subprocess.run
    if not plan.argv:
        return UninstallResult(False, OUTCOME_MESSAGES[OUTCOME_NO_COMMAND], None, OUTCOME_NO_COMMAND)
    if plan.log_path:
        try:
            Path(plan.log_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # msiexec then just can't write its log - no reason not to uninstall.
            pass
    try:
        result = run(
            list(plan.argv), shell=False,
            capture_output=True, text=True, errors="replace", timeout=timeout_sec,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return UninstallResult(False, OUTCOME_MESSAGES[OUTCOME_TIMEOUT], None, OUTCOME_TIMEOUT)
    except OSError as exc:
        return UninstallResult(
            False, f"{OUTCOME_MESSAGES[OUTCOME_LAUNCH_ERROR]} {exc}", None, OUTCOME_LAUNCH_ERROR,
        )
    ok, outcome = interpret_exit_code(plan, result.returncode)
    lines = [((result.stdout or "") + (result.stderr or "")).strip()]
    if outcome != OUTCOME_OK or result.returncode != 0:
        lines.append(f"{OUTCOME_MESSAGES[outcome]} (exit {result.returncode})")
    if plan.kind == KIND_NSIS and ok:
        lines.append(NSIS_BACKGROUND_NOTE)
    if plan.log_path:
        lines.append(f"msiexec log: {plan.log_path}")
    return UninstallResult(ok, "\n".join(line for line in lines if line), result.returncode, outcome)


def split_queues(programs: list, plans: dict) -> tuple[list, list]:
    """(interactive, silent) in run order, each keeping the given order.

    The interactive queue runs first: the technician has just confirmed and
    is at the PC to click through those windows; the silent queue then runs
    on its own and needs nobody. An entry with no command at all sits in the
    silent queue - it only reports "no uninstall command".
    """
    interactive = [p for p in programs if plans[p.name].kind == KIND_INTERACTIVE]
    silent = [p for p in programs if plans[p.name].kind != KIND_INTERACTIVE]
    return interactive, silent
