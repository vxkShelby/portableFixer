"""Over-the-shoulder elevation (research G25): whose hive per-user settings
go to, the PowerShell prelude that carries it, the audit fields and the
report line. Qt-free - the Windows calls are replaced by a fake."""

import ctypes
import json

import pytest

from portablefix import report, target_user
from portablefix.audit_log import append_entry, audit_log_path, make_entry
from portablefix.executor import POWERSHELL_PREFIX, build_execution_plan
from portablefix.target_user import ProcessOwner, TargetUser

TECH = "S-1-5-21-1111-2222-3333-1001"
CLIENT = "S-1-5-21-1111-2222-3333-1002"
OTHER = "S-1-5-21-1111-2222-3333-1003"
NAMES = {TECH: "PC\\technik", CLIENT: "PC\\klient", OTHER: "PC\\iny"}


class FakeApi:
    def __init__(self, process_sid=TECH, own_session=1, console_session=1, processes=(), session_users=None):
        self._process_sid = process_sid
        self._own_session = own_session
        self._console_session = console_session
        self._processes = list(processes)
        self._session_users = session_users or {}
        self.sessions_asked = []

    def process_sid(self):
        return self._process_sid

    def process_session_id(self):
        return self._own_session

    def console_session_id(self):
        return self._console_session

    def processes(self):
        return list(self._processes)

    def session_user(self, session_id):
        self.sessions_asked.append(session_id)
        return self._session_users.get(session_id, "")

    def account_name(self, sid):
        return NAMES.get(sid, "")

    def account_sid(self, account):
        return {name: sid for sid, name in NAMES.items()}.get(account)


def _explorer(sid, session=1, pid=100):
    return ProcessOwner(pid=pid, session_id=session, name="explorer.exe", sid=sid)


# --- the comparison -------------------------------------------------------


def test_same_user_targets_own_hive_without_difference():
    result = target_user.detect(FakeApi(processes=[_explorer(TECH)]))
    assert result.status == target_user.SAME
    assert not result.differs
    assert result.target_sid == TECH
    assert result.hive == f"Registry::HKEY_USERS\\{TECH}"


def test_over_the_shoulder_elevation_targets_the_signed_in_client():
    api = FakeApi(processes=[
        _explorer(CLIENT),
        ProcessOwner(pid=5, session_id=1, name="PortableFix.exe", sid=TECH),
    ])
    result = target_user.detect(api)
    assert result.status == target_user.DIFFERENT
    assert result.differs
    assert (result.target_sid, result.target_user) == (CLIENT, "PC\\klient")
    assert (result.process_sid, result.process_user) == (TECH, "PC\\technik")


def test_explorer_name_is_compared_case_insensitively():
    api = FakeApi(processes=[ProcessOwner(pid=1, session_id=1, name="EXPLORER.EXE", sid=CLIENT)])
    assert target_user.detect(api).target_sid == CLIENT


def test_users_in_other_sessions_are_ignored():
    # Fast user switching: another user's desktop in session 2 is not ours.
    api = FakeApi(processes=[_explorer(OTHER, session=2), _explorer(CLIENT, session=1)])
    assert target_user.detect(api).target_sid == CLIENT


def test_rdp_uses_own_session_not_the_console():
    # Technician's RDP session 3; the console (session 1) shows someone else.
    api = FakeApi(own_session=3, console_session=1, processes=[_explorer(OTHER, session=1), _explorer(CLIENT, session=3)])
    result = target_user.detect(api)
    assert (result.session_id, result.target_sid) == (3, CLIENT)


def test_session_zero_falls_back_to_the_console_session():
    api = FakeApi(own_session=0, console_session=1, processes=[_explorer(CLIENT, session=1)])
    result = target_user.detect(api)
    assert (result.session_id, result.target_sid) == (1, CLIENT)


def test_pick_session_without_console_is_none():
    assert target_user.pick_session(0, 0xFFFFFFFF) is None
    assert target_user.pick_session(None, None) is None
    assert target_user.pick_session(None, 2) == 2
    assert target_user.pick_session(4, 1) == 4


def test_no_session_at_all_targets_own_hive_as_no_user():
    api = FakeApi(own_session=0, console_session=0xFFFFFFFF, processes=[_explorer(CLIENT, session=1)])
    result = target_user.detect(api)
    assert result.status == target_user.NO_USER
    assert result.target_sid == TECH


def test_no_explorer_falls_back_to_the_session_user():
    # Explorer crashed or a replacement shell - WTS still knows who signed in.
    api = FakeApi(processes=[], session_users={1: "PC\\klient"})
    result = target_user.detect(api)
    assert (result.status, result.target_sid) == (target_user.DIFFERENT, CLIENT)
    assert api.sessions_asked == [1]


def test_no_explorer_and_no_session_user_is_no_user():
    result = target_user.detect(FakeApi(processes=[]))
    assert result.status == target_user.NO_USER
    assert result.target_sid == TECH
    assert not result.differs


def test_several_explorer_owners_are_decided_by_the_session_user():
    # "Run as different user" started a second explorer under the technician.
    api = FakeApi(processes=[_explorer(TECH, pid=1), _explorer(CLIENT, pid=2)], session_users={1: "PC\\klient"})
    result = target_user.detect(api)
    assert (result.status, result.target_sid) == (target_user.DIFFERENT, CLIENT)


def test_several_explorer_owners_without_a_tie_breaker_are_ambiguous():
    api = FakeApi(processes=[_explorer(CLIENT, pid=1), _explorer(OTHER, pid=2)])
    result = target_user.detect(api)
    assert result.status == target_user.AMBIGUOUS
    # Never a guess at somebody else's profile: the own hive, flagged.
    assert result.target_sid == TECH
    assert not result.differs


def test_duplicate_explorers_of_one_user_are_not_ambiguous():
    api = FakeApi(processes=[_explorer(CLIENT, pid=1), _explorer(CLIENT, pid=2)])
    assert target_user.detect(api).status == target_user.DIFFERENT


def test_explorer_with_unreadable_owner_is_skipped():
    api = FakeApi(processes=[_explorer(None, pid=1), _explorer(CLIENT, pid=2)])
    assert target_user.detect(api).target_sid == CLIENT


def test_unknown_account_name_shows_the_sid():
    api = FakeApi(processes=[_explorer("S-1-5-21-9-9-9-1500")])
    result = target_user.detect(api)
    assert result.target_user == "S-1-5-21-9-9-9-1500"


def test_missing_process_sid_is_unknown():
    result = target_user.detect(FakeApi(process_sid=None, processes=[_explorer(CLIENT)]))
    assert result.status == target_user.UNKNOWN
    assert result.hive is None
    assert result.prelude() == ""


def test_failing_api_is_unknown_not_a_crash():
    class Broken(FakeApi):
        def processes(self):
            raise OSError("WTSEnumerateProcessesW failed")

    assert target_user.detect(Broken()).status == target_user.UNKNOWN


def test_not_windows_is_unknown(monkeypatch):
    monkeypatch.setattr(target_user.sys, "platform", "linux")
    assert target_user.detect() == TargetUser()


def test_windows_without_the_dlls_is_unknown(monkeypatch):
    monkeypatch.setattr(target_user.sys, "platform", "win32")

    def no_dll(*args, **kwargs):
        raise OSError("module not found")

    monkeypatch.setattr(ctypes, "WinDLL", no_dll, raising=False)
    assert target_user.detect().status == target_user.UNKNOWN


# --- the ctypes prototypes --------------------------------------------------


class _FakeFunc:
    def __init__(self):
        self.argtypes = None
        self.restype = "unset"


class _FakeDll:
    def __init__(self, name, **kwargs):
        self.name = name
        self.kwargs = kwargs
        self.funcs = {}

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        return self.funcs.setdefault(attr, _FakeFunc())


def test_every_foreign_function_has_a_prototype(monkeypatch):
    # The ctypes default restype is a C int: a 64-bit HANDLE or pointer
    # returned through it is truncated. Every function must declare both.
    dlls = []

    def fake_windll(name, **kwargs):
        dll = _FakeDll(name, **kwargs)
        dlls.append(dll)
        return dll

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)
    target_user.Win32Api()
    assert {d.name for d in dlls} == {"kernel32", "advapi32", "wtsapi32"}
    assert all(d.kwargs.get("use_last_error") for d in dlls)
    declared = {f"{d.name}.{n}": f for d in dlls for n, f in d.funcs.items()}
    for name, func in declared.items():
        assert isinstance(func.argtypes, list), name
        assert func.restype != "unset", name
    from ctypes import wintypes

    k32 = next(d for d in dlls if d.name == "kernel32")
    # GetCurrentProcess returns the pseudo-handle (HANDLE)-1 - pointer-sized.
    assert k32.funcs["GetCurrentProcess"].restype is wintypes.HANDLE
    assert k32.funcs["LocalFree"].restype is ctypes.c_void_p
    wts = next(d for d in dlls if d.name == "wtsapi32")
    assert wts.funcs["WTSFreeMemory"].restype is None
    for needed in ("WTSEnumerateProcessesW", "WTSQuerySessionInformationW", "WTSFreeMemory"):
        assert needed in wts.funcs


def test_wts_process_info_holds_pointers_after_two_dwords(monkeypatch):
    monkeypatch.setattr(ctypes, "WinDLL", _FakeDll, raising=False)
    api = target_user.Win32Api()
    fields = dict(api._WTS_PROCESS_INFOW._fields_)
    assert list(fields) == ["SessionId", "ProcessId", "pProcessName", "pUserSid"]
    assert fields["pUserSid"] is ctypes.c_void_p


# --- the prelude -------------------------------------------------------------


def _different():
    return TargetUser(
        status=target_user.DIFFERENT, process_sid=TECH, process_user="PC\\technik",
        target_sid=CLIENT, target_user="PC\\klient", session_id=1,
    )


def test_prelude_sets_hive_and_sid():
    assert _different().prelude() == (
        f"$__pfUserHive = 'Registry::HKEY_USERS\\{CLIENT}'; $__pfUserSid = '{CLIENT}'; "
    )


def test_prelude_is_set_when_nothing_differs():
    same = TargetUser(status=target_user.SAME, process_sid=TECH, target_sid=TECH)
    assert same.prelude() == f"$__pfUserHive = 'Registry::HKEY_USERS\\{TECH}'; $__pfUserSid = '{TECH}'; "


@pytest.mark.parametrize("bad", ["S-1-5-21-1'; Remove-Item C:\\ -Recurse; '", "", "S-1", "HKCU", None, "S-1-5-21-1-x"])
def test_prelude_refuses_anything_but_a_sid(bad):
    assert TargetUser(status=target_user.DIFFERENT, process_sid=TECH, target_sid=bad).prelude() == ""


def test_execution_plan_puts_the_prelude_first():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=False, target_user=_different())
    assert plan.argv == POWERSHELL_PREFIX + [
        f"$__pfUserHive = 'Registry::HKEY_USERS\\{CLIENT}'; $__pfUserSid = '{CLIENT}'; "
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Write-Output 'hi'"
    ]
    assert plan.display_command == "Write-Output 'hi'"


def test_execution_plan_with_unknown_target_is_unchanged():
    plain = build_execution_plan("Write-Output 'hi'", dry_run=False)
    assert build_execution_plan("Write-Output 'hi'", dry_run=False, target_user=TargetUser()).argv == plain.argv


def test_dry_run_plan_has_no_prelude():
    plan = build_execution_plan("Write-Output 'hi'", dry_run=True, target_user=_different())
    assert plan.argv is None


# --- audit and report ---------------------------------------------------------


def test_audit_entry_records_the_target_user(tmp_path):
    entry = make_entry("m13_debloat", "debloat_disable_copilot", "cmd", 0, "", False, "run1", **_different().audit_fields())
    append_entry(tmp_path, "run1", entry)
    parsed = json.loads(audit_log_path(tmp_path, "run1").read_text(encoding="utf-8"))
    assert parsed["target_user"] == "PC\\klient"
    assert parsed["target_user_sid"] == CLIENT
    assert parsed["target_user_status"] == "different"


def test_audit_entry_target_defaults_to_not_recorded():
    entry = make_entry("m01", "a", "cmd", 0, "", False, "run1")
    assert (entry.target_user, entry.target_user_sid, entry.target_user_status) == ("", "", "")


def _write_log(tmp_path, *entries):
    for entry in entries:
        append_entry(tmp_path, "run1", entry)


def test_report_shows_the_target_user_in_the_job_section(tmp_path):
    _write_log(tmp_path, make_entry("m13_debloat", "x", "cmd", 0, "", False, "run1", **_different().audit_fields()))
    data = report.build_report_data(tmp_path, "run1", [], "sk", {}, {}, job={"technician": "Jano"})
    assert data["target_user"] == {"status": "different", "user": "PC\\klient", "sid": CLIENT}
    page = report.render_report_html(data)
    job = page.split('<div class="job">', 1)[1].split("</div>", 1)[0]
    assert "Jano" in job
    assert "Profil používateľa: <strong>PC\\klient</strong>" in job
    assert CLIENT in job
    assert "prihlásený klient, PortableFix bežal pod iným účtom" in job


def test_report_shows_the_target_user_without_job_details(tmp_path):
    same = TargetUser(status=target_user.SAME, process_sid=TECH, process_user="PC\\technik", target_sid=TECH, target_user="PC\\technik")
    _write_log(tmp_path, make_entry("m13_debloat", "x", "cmd", 0, "", False, "run1", **same.audit_fields()))
    data = report.build_report_data(tmp_path, "run1", [], "en", {}, {})
    page = report.render_report_html(data)
    assert '<div class="job">User profile: <strong>PC\\technik</strong>' in page
    assert "different account" not in page


def test_report_flags_an_uncertain_target(tmp_path):
    unsure = TargetUser(status=target_user.AMBIGUOUS, process_sid=TECH, process_user="PC\\technik", target_sid=TECH, target_user="PC\\technik")
    _write_log(tmp_path, make_entry("m13_debloat", "x", "cmd", 0, "", False, "run1", **unsure.audit_fields()))
    page = report.render_report_html(report.build_report_data(tmp_path, "run1", [], "en", {}, {}))
    assert "could not be determined for sure" in page


def test_report_without_recorded_target_adds_nothing(tmp_path):
    _write_log(tmp_path, make_entry("m01", "a", "cmd", 0, "", False, "run1"))
    _write_log(tmp_path, make_entry("m01", "b", "cmd", 0, "", False, "run1", **TargetUser().audit_fields()))
    data = report.build_report_data(tmp_path, "run1", [], "en", {}, {})
    assert data["target_user"] == {}
    assert 'class="job"' not in report.render_report_html(data)


def test_report_uses_the_newest_recorded_target(tmp_path):
    same = TargetUser(status=target_user.SAME, process_sid=TECH, target_sid=TECH, target_user="PC\\technik")
    _write_log(
        tmp_path,
        make_entry("m01", "a", "cmd", 0, "", False, "run1", **same.audit_fields()),
        make_entry("m01", "b", "cmd", 0, "", False, "run1", **_different().audit_fields()),
    )
    assert report.build_report_data(tmp_path, "run1", [], "en", {}, {})["target_user"]["sid"] == CLIENT
