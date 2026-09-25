"""Whose registry hive per-user settings belong to (research G25).

A technician usually elevates "over the shoulder": the client stays signed
in, UAC asks for the technician's admin account, and PortableFix then runs
under THAT account - so every HKCU:\\ write lands in the technician's
profile and the client sees no change at all. This module finds the user
who owns the desktop (explorer.exe) in PortableFix's own session, compares
their SID with the SID of PortableFix's process token and hands the target
hive to the PowerShell prelude ($__pfUserHive / $__pfUserSid).

Qt-free and without a Windows import at module level: the Windows calls sit
behind a small wrapper (Win32Api) that tests replace with a fake, so the
decision logic runs on Linux.
"""

import re
import sys
from dataclasses import dataclass

# What the comparison concluded - also the audit/report vocabulary.
SAME = "same"  # the desktop user is the account PortableFix runs as
DIFFERENT = "different"  # over-the-shoulder elevation: the client's hive is the target
AMBIGUOUS = "ambiguous"  # several desktop owners, none of them the session's user
NO_USER = "no_user"  # nobody owns a desktop in this session (no explorer, no session user)
UNKNOWN = "unknown"  # detection could not run (not Windows, API failure)

# S-1-<revision>-<authority>-<subauthorities...>: the only thing ever put
# between single quotes in the prelude, so it can never break out of them.
_SID_RE = re.compile(r"^S-1-\d+(-\d+)+$")

# WTSGetActiveConsoleSessionId's "no session attached to the console".
_NO_CONSOLE_SESSION = 0xFFFFFFFF


def is_valid_sid(sid) -> bool:
    return isinstance(sid, str) and bool(_SID_RE.match(sid))


def hive_path(sid: str) -> str:
    return f"Registry::HKEY_USERS\\{sid}"


@dataclass(frozen=True)
class ProcessOwner:
    pid: int
    session_id: int
    name: str
    sid: str | None


@dataclass(frozen=True)
class TargetUser:
    status: str = UNKNOWN
    process_sid: str | None = None
    process_user: str = ""
    target_sid: str | None = None
    target_user: str = ""
    session_id: int | None = None

    @property
    def differs(self) -> bool:
        return self.status == DIFFERENT

    @property
    def hive(self) -> str | None:
        return hive_path(self.target_sid) if is_valid_sid(self.target_sid) else None

    def prelude(self) -> str:
        """PowerShell assignments put in front of every action. Always set
        when a SID is known - it is the process's own hive when nobody else
        owns the desktop - so catalog commands never need to know why."""
        if not is_valid_sid(self.target_sid):
            # Unknown: the commands fall back to HKCU: themselves.
            return ""
        return f"$__pfUserHive = '{hive_path(self.target_sid)}'; $__pfUserSid = '{self.target_sid}'; "

    def audit_fields(self) -> dict:
        """make_entry keyword arguments recording whose hive an action used."""
        return {
            "target_user": self.target_user,
            "target_user_sid": self.target_sid or "",
            "target_user_status": self.status,
        }


def resolve(
    process_sid: str | None,
    process_user: str,
    session_id: int | None,
    explorer_sids: list[str],
    session_user_sid: str | None,
    account_name,
) -> TargetUser:
    """The comparison itself, free of any Windows call.

    explorer_sids: owners of explorer.exe in `session_id` (duplicates fine).
    session_user_sid: the user WTS says is signed in to that session - it
    breaks a tie when explorer runs under more than one account, and stands
    in when explorer is not running (crashed, replaced shell).
    account_name(sid) -> "DOMAIN\\user" or "" for display.
    """
    if not is_valid_sid(process_sid):
        return TargetUser(status=UNKNOWN, session_id=session_id)
    owners = sorted({sid for sid in explorer_sids if is_valid_sid(sid)})
    if not is_valid_sid(session_user_sid):
        session_user_sid = None
    if len(owners) == 1:
        desktop_sid = owners[0]
    elif len(owners) > 1:
        # Several accounts own an explorer here (someone started one with
        # "Run as different user"): the session's own user owns the shell.
        desktop_sid = session_user_sid if session_user_sid in owners else None
        if desktop_sid is None:
            return TargetUser(
                status=AMBIGUOUS, process_sid=process_sid, process_user=process_user,
                target_sid=process_sid, target_user=process_user, session_id=session_id,
            )
    else:
        desktop_sid = session_user_sid
    if desktop_sid is None:
        return TargetUser(
            status=NO_USER, process_sid=process_sid, process_user=process_user,
            target_sid=process_sid, target_user=process_user, session_id=session_id,
        )
    if desktop_sid.upper() == process_sid.upper():
        return TargetUser(
            status=SAME, process_sid=process_sid, process_user=process_user,
            target_sid=process_sid, target_user=process_user, session_id=session_id,
        )
    return TargetUser(
        status=DIFFERENT, process_sid=process_sid, process_user=process_user,
        target_sid=desktop_sid, target_user=account_name(desktop_sid) or desktop_sid,
        session_id=session_id,
    )


def pick_session(own_session: int | None, console_session: int | None) -> int | None:
    """PortableFix's own session: UAC elevation keeps it, and over RDP the
    technician's desktop is that RDP session, not the (possibly empty or
    someone else's) console. Session 0 has no desktop (PortableFix started
    as a service or task), so the console session is the next best guess."""
    if own_session is not None and own_session != 0:
        return own_session
    if console_session is None or console_session == _NO_CONSOLE_SESSION:
        return None
    return console_session


def detect(api=None) -> TargetUser:
    """Never raises: a failed detection is UNKNOWN (no prelude, HKCU: as
    before) rather than a crash at startup."""
    if api is None:
        if sys.platform != "win32":
            return TargetUser()
        try:
            api = Win32Api()
        except (OSError, AttributeError):
            return TargetUser()
    try:
        process_sid = api.process_sid()
        if not is_valid_sid(process_sid):
            return TargetUser()
        session_id = pick_session(api.process_session_id(), api.console_session_id())
        explorer_sids: list[str] = []
        session_user_sid = None
        if session_id is not None:
            explorer_sids = [
                p.sid for p in api.processes()
                if p.session_id == session_id and p.name.lower() == "explorer.exe" and p.sid
            ]
            session_user = api.session_user(session_id)
            session_user_sid = api.account_sid(session_user) if session_user else None
        return resolve(
            process_sid, api.account_name(process_sid) or process_sid, session_id,
            explorer_sids, session_user_sid, api.account_name,
        )
    except Exception:
        return TargetUser()


class Win32Api:
    """The real calls. Every foreign function gets argtypes/restype: the
    ctypes default (int) truncates 64-bit handles and pointers."""

    TOKEN_QUERY = 0x0008
    TOKEN_USER_CLASS = 1  # TOKEN_INFORMATION_CLASS.TokenUser
    WTS_USER_NAME = 5  # WTS_INFO_CLASS.WTSUserName
    WTS_DOMAIN_NAME = 7  # WTS_INFO_CLASS.WTSDomainName
    ERROR_INSUFFICIENT_BUFFER = 122

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wt = wintypes
        # Own WinDLL instances: argtypes set here must not change the
        # prototypes other modules rely on through ctypes.windll.
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        wts = ctypes.WinDLL("wtsapi32", use_last_error=True)

        class WTS_PROCESS_INFOW(ctypes.Structure):
            _fields_ = [
                ("SessionId", wintypes.DWORD),
                ("ProcessId", wintypes.DWORD),
                ("pProcessName", wintypes.LPWSTR),
                ("pUserSid", ctypes.c_void_p),
            ]

        class SID_AND_ATTRIBUTES(ctypes.Structure):
            _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

        self._WTS_PROCESS_INFOW = WTS_PROCESS_INFOW
        self._SID_AND_ATTRIBUTES = SID_AND_ATTRIBUTES
        BOOL, DWORD, HANDLE = wintypes.BOOL, wintypes.DWORD, wintypes.HANDLE
        LPDWORD = ctypes.POINTER(DWORD)

        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = HANDLE
        k32.GetCurrentProcessId.argtypes = []
        k32.GetCurrentProcessId.restype = DWORD
        k32.ProcessIdToSessionId.argtypes = [DWORD, LPDWORD]
        k32.ProcessIdToSessionId.restype = BOOL
        k32.WTSGetActiveConsoleSessionId.argtypes = []
        k32.WTSGetActiveConsoleSessionId.restype = DWORD
        k32.CloseHandle.argtypes = [HANDLE]
        k32.CloseHandle.restype = BOOL
        k32.LocalFree.argtypes = [ctypes.c_void_p]
        k32.LocalFree.restype = ctypes.c_void_p

        adv.OpenProcessToken.argtypes = [HANDLE, DWORD, ctypes.POINTER(HANDLE)]
        adv.OpenProcessToken.restype = BOOL
        adv.GetTokenInformation.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD, LPDWORD]
        adv.GetTokenInformation.restype = BOOL
        adv.IsValidSid.argtypes = [ctypes.c_void_p]
        adv.IsValidSid.restype = BOOL
        adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        adv.ConvertSidToStringSidW.restype = BOOL
        adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        adv.ConvertStringSidToSidW.restype = BOOL
        adv.LookupAccountSidW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_void_p, wintypes.LPWSTR, LPDWORD, wintypes.LPWSTR, LPDWORD, LPDWORD,
        ]
        adv.LookupAccountSidW.restype = BOOL
        adv.LookupAccountNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, LPDWORD, wintypes.LPWSTR, LPDWORD, LPDWORD,
        ]
        adv.LookupAccountNameW.restype = BOOL

        wts.WTSEnumerateProcessesW.argtypes = [
            HANDLE, DWORD, DWORD, ctypes.POINTER(ctypes.POINTER(WTS_PROCESS_INFOW)), LPDWORD,
        ]
        wts.WTSEnumerateProcessesW.restype = BOOL
        wts.WTSQuerySessionInformationW.argtypes = [
            HANDLE, DWORD, ctypes.c_int, ctypes.POINTER(wintypes.LPWSTR), LPDWORD,
        ]
        wts.WTSQuerySessionInformationW.restype = BOOL
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        wts.WTSFreeMemory.restype = None

        self._k32, self._adv, self._wts = k32, adv, wts

    def _sid_to_string(self, psid) -> str | None:
        ctypes = self._ctypes
        if not psid or not self._adv.IsValidSid(psid):
            return None
        out = self._wt.LPWSTR()
        if not self._adv.ConvertSidToStringSidW(psid, ctypes.byref(out)):
            return None
        try:
            return out.value
        finally:
            self._k32.LocalFree(ctypes.cast(out, ctypes.c_void_p))

    def process_sid(self) -> str | None:
        ctypes, wt = self._ctypes, self._wt
        token = wt.HANDLE()
        if not self._adv.OpenProcessToken(self._k32.GetCurrentProcess(), self.TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            needed = wt.DWORD(0)
            # First call only reports the size TOKEN_USER (SID included) needs.
            self._adv.GetTokenInformation(token, self.TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
            if not needed.value:
                return None
            buf = ctypes.create_string_buffer(needed.value)
            if not self._adv.GetTokenInformation(token, self.TOKEN_USER_CLASS, buf, needed, ctypes.byref(needed)):
                return None
            user = ctypes.cast(buf, ctypes.POINTER(self._SID_AND_ATTRIBUTES)).contents
            return self._sid_to_string(user.Sid)
        finally:
            self._k32.CloseHandle(token)

    def process_session_id(self) -> int | None:
        session = self._wt.DWORD(0)
        if not self._k32.ProcessIdToSessionId(self._k32.GetCurrentProcessId(), self._ctypes.byref(session)):
            return None
        return session.value

    def console_session_id(self) -> int | None:
        return int(self._k32.WTSGetActiveConsoleSessionId())

    def processes(self) -> list[ProcessOwner]:
        """WTSEnumerateProcesses hands every process's owner SID over
        without opening another user's process or token - which an
        elevated admin without SeDebugPrivilege may not be allowed to."""
        ctypes, wt = self._ctypes, self._wt
        info = ctypes.POINTER(self._WTS_PROCESS_INFOW)()
        count = wt.DWORD(0)
        # WTS_CURRENT_SERVER_HANDLE is NULL; Reserved 0, Version 1.
        if not self._wts.WTSEnumerateProcessesW(None, 0, 1, ctypes.byref(info), ctypes.byref(count)):
            return []
        try:
            result = []
            for i in range(count.value):
                entry = info[i]
                result.append(ProcessOwner(
                    pid=int(entry.ProcessId), session_id=int(entry.SessionId),
                    name=entry.pProcessName or "", sid=self._sid_to_string(entry.pUserSid),
                ))
            return result
        finally:
            self._wts.WTSFreeMemory(ctypes.cast(info, ctypes.c_void_p))

    def _session_string(self, session_id: int, info_class: int) -> str:
        ctypes, wt = self._ctypes, self._wt
        buf = wt.LPWSTR()
        size = wt.DWORD(0)
        if not self._wts.WTSQuerySessionInformationW(None, session_id, info_class, ctypes.byref(buf), ctypes.byref(size)):
            return ""
        try:
            return buf.value or ""
        finally:
            self._wts.WTSFreeMemory(ctypes.cast(buf, ctypes.c_void_p))

    def session_user(self, session_id: int) -> str:
        user = self._session_string(session_id, self.WTS_USER_NAME)
        if not user:
            return ""
        domain = self._session_string(session_id, self.WTS_DOMAIN_NAME)
        return f"{domain}\\{user}" if domain else user

    def account_name(self, sid: str) -> str:
        ctypes, wt = self._ctypes, self._wt
        psid = ctypes.c_void_p()
        if not self._adv.ConvertStringSidToSidW(sid, ctypes.byref(psid)):
            return ""
        try:
            name_len, domain_len, use = wt.DWORD(0), wt.DWORD(0), wt.DWORD(0)
            self._adv.LookupAccountSidW(None, psid, None, ctypes.byref(name_len), None, ctypes.byref(domain_len), ctypes.byref(use))
            if not name_len.value:
                return ""
            name = ctypes.create_unicode_buffer(name_len.value)
            domain = ctypes.create_unicode_buffer(max(domain_len.value, 1))
            if not self._adv.LookupAccountSidW(None, psid, name, ctypes.byref(name_len), domain, ctypes.byref(domain_len), ctypes.byref(use)):
                return ""
            return f"{domain.value}\\{name.value}" if domain.value else name.value
        finally:
            self._k32.LocalFree(psid)

    def account_sid(self, account: str) -> str | None:
        ctypes, wt = self._ctypes, self._wt
        sid_len, domain_len, use = wt.DWORD(0), wt.DWORD(0), wt.DWORD(0)
        self._adv.LookupAccountNameW(None, account, None, ctypes.byref(sid_len), None, ctypes.byref(domain_len), ctypes.byref(use))
        if not sid_len.value:
            return None
        sid_buf = ctypes.create_string_buffer(sid_len.value)
        domain = ctypes.create_unicode_buffer(max(domain_len.value, 1))
        if not self._adv.LookupAccountNameW(None, account, sid_buf, ctypes.byref(sid_len), domain, ctypes.byref(domain_len), ctypes.byref(use)):
            return None
        return self._sid_to_string(ctypes.cast(sid_buf, ctypes.c_void_p))
