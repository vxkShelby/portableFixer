import json
import os
import zipfile

import pytest

from portablefix import handoff

HOST = "PC-KLIENT"
RUN = "20260924T100000-abcd1234"
OTHER_RUN = "20260901T080000-ffff0000"


def _write_run(state_dir, run_id=RUN, host=HOST, undo=True):
    reports = state_dir / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{host}_{run_id}.html").write_text(f"<html>{run_id}</html>", encoding="utf-8")
    (reports / f"{host}_{run_id}.json").write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")
    logs = state_dir / "Logs"
    logs.mkdir(parents=True, exist_ok=True)
    # write_bytes, not write_text: text mode turns \n into \r\n on Windows
    # and the tests compare the zipped bytes exactly.
    (logs / f"{run_id}.jsonl").write_bytes(f'{{"run_id": "{run_id}"}}\n'.encode("utf-8"))
    if undo:
        backups = state_dir / "Backups" / run_id
        backups.mkdir(parents=True, exist_ok=True)
        (backups / "undo.ps1").write_bytes("# undo\n".encode("utf-8-sig"))


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


def test_default_package_name():
    assert handoff.default_package_name(HOST, RUN) == f"PortableFix_{HOST}_{RUN}.zip"


def test_zip_contains_run_files_and_readme(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    dest = state / "Reports" / handoff.default_package_name(HOST, RUN)

    result = handoff.build_handoff_zip(state, HOST, RUN, dest)

    assert result == dest and dest.is_file()
    assert _names(dest) == sorted(
        ["README.txt", "report.html", "report.json", "audit_log.jsonl", "undo.ps1"]
    )
    with zipfile.ZipFile(dest) as zf:
        assert zf.read("report.html").decode("utf-8") == f"<html>{RUN}</html>"
        assert zf.read("audit_log.jsonl").decode("utf-8") == f'{{"run_id": "{RUN}"}}\n'
        readme = zf.read("README.txt").decode("utf-8-sig")
    assert HOST in readme and RUN in readme
    # Bilingual, with proper Slovak diacritics, and the undo safety advice.
    assert "SLOVENSKY" in readme and "ENGLISH" in readme
    assert "Spúšťajte" in readme and "správca" in readme
    assert "Administrator" in readme and "undo.ps1" in readme


def test_missing_optional_files_are_skipped(tmp_path):
    state = tmp_path / "state"
    _write_run(state, undo=False)
    (state / "Reports" / f"{HOST}_{RUN}.json").unlink()

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    assert _names(dest) == ["README.txt", "audit_log.jsonl", "report.html"]


def test_no_files_for_run_raises_value_error(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")
    assert not (tmp_path / "out.zip").exists()


def test_never_includes_settings_or_other_runs(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    _write_run(state, run_id=OTHER_RUN)
    (state / "Data").mkdir()
    (state / "Data" / "settings.json").write_text('{"technician_name": "x"}', encoding="utf-8")
    (state / "Logs" / "crash.log").write_text("boom", encoding="utf-8")

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    with zipfile.ZipFile(dest) as zf:
        payload = b"".join(zf.read(name) for name in zf.namelist())
        names = zf.namelist()
    assert not any("settings" in n for n in names)
    assert OTHER_RUN.encode() not in payload
    assert b"technician_name" not in payload and b"boom" not in payload


@pytest.mark.parametrize(
    "bad_run_id",
    ["../x", "..", "x/../../y", "..\\x", "a/b", "C:evil", "", " padded", "x\x00y"],
)
def test_path_traversal_run_id_rejected(tmp_path, bad_run_id):
    state = tmp_path / "state"
    _write_run(state)
    # A file a traversal would reach if run_id were used unchecked.
    (tmp_path / "x.jsonl").write_text("outside", encoding="utf-8")
    dest = tmp_path / "out.zip"
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, HOST, bad_run_id, dest)
    assert not dest.exists()


@pytest.mark.parametrize("bad_host", ["..", "../PC", "a\\b", ""])
def test_path_traversal_hostname_rejected(tmp_path, bad_host):
    state = tmp_path / "state"
    _write_run(state)
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, bad_host, RUN, tmp_path / "out.zip")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_symlink_pointing_outside_state_dir_is_not_followed(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    html = state / "Reports" / f"{HOST}_{RUN}.html"
    html.unlink()
    try:
        html.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks not permitted here")

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")

    assert "report.html" not in _names(dest)
    with zipfile.ZipFile(dest) as zf:
        assert all(b"TOP-SECRET" not in zf.read(n) for n in zf.namelist())


def test_package_sources_stay_inside_state_dir(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    root = state.resolve()
    sources = handoff.package_sources(state, HOST, RUN)
    assert [arc for arc, _ in sources] == ["report.html", "report.json", "audit_log.jsonl", "undo.ps1"]
    assert all(path.is_relative_to(root) for _, path in sources)


def test_write_is_atomic_on_failure(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "out" / "pkg.zip"
    dest.parent.mkdir()
    dest.write_bytes(b"previous package")

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(handoff.zipfile.ZipFile, "write", _boom)
    with pytest.raises(OSError):
        handoff.build_handoff_zip(state, HOST, RUN, dest)

    # The old file is untouched and no temp file is left behind.
    assert dest.read_bytes() == b"previous package"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["pkg.zip"]


def test_write_goes_through_temp_file_and_os_replace(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "pkg.zip"
    calls = []
    real_replace = os.replace

    def _spy(src, dst):
        calls.append((str(src), str(dst)))
        assert zipfile.is_zipfile(src)
        assert not os.path.exists(dst)
        real_replace(src, dst)

    monkeypatch.setattr(handoff.os, "replace", _spy)
    handoff.build_handoff_zip(state, HOST, RUN, dest)
    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(dest) and src != dst
    assert os.path.dirname(src) == str(tmp_path)
    assert not os.path.exists(src)


def test_overwrites_existing_package(tmp_path):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "pkg.zip"
    dest.write_bytes(b"old")
    handoff.build_handoff_zip(state, HOST, RUN, dest)
    assert zipfile.is_zipfile(dest)


def test_unicode_hostname_is_stored_with_utf8_names(tmp_path):
    host = "PC-Žilina"
    state = tmp_path / "state"
    _write_run(state, host=host)
    dest = tmp_path / handoff.default_package_name(host, RUN)
    handoff.build_handoff_zip(state, host, RUN, dest)
    with zipfile.ZipFile(dest) as zf:
        assert "report.html" in zf.namelist()
        assert host in zf.read("README.txt").decode("utf-8-sig")


# --- Windows diagnostics (G19) ---------------------------------------------

import subprocess  # noqa: E402

REPORT_NAMES = [spec.arcname for spec in handoff.DIAGNOSTIC_REPORTS]


class _FakePopen:
    """Stands in for subprocess.Popen: `behaviour` maps the command name to
    (exit code, bytes written, mode) with mode "ok", "hang" or "oserror"."""

    calls: list = []
    stderrs: list = []
    procs: list = []
    behaviour: dict = {}

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, creationflags=0):
        name = os.path.basename(argv[0]).lower().removesuffix(".exe")
        code, payload, mode = self.behaviour.get(name, (0, b"data-" + name.encode(), "ok"))
        type(self).calls.append(list(argv))
        type(self).stderrs.append(stderr)
        if mode == "oserror":
            raise FileNotFoundError(argv[0])
        type(self).procs.append(self)
        self.killed = False
        self._mode = mode
        self._code = code
        if payload:
            if hasattr(stdout, "write"):
                stdout.write(payload)
            else:
                # A report that writes its own file: the only argument with
                # a directory in it (not a "/t"-style switch, which looks
                # absolute on POSIX).
                out_path = next(a for a in argv[1:] if a.count(os.sep) > 1)
                with open(out_path, "wb") as fh:
                    fh.write(payload)

    def wait(self, timeout=None):
        if self._mode == "hang" and not self.killed:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self._code

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_reports(monkeypatch):
    _FakePopen.calls = []
    _FakePopen.stderrs = []
    _FakePopen.procs = []
    _FakePopen.behaviour = {}
    monkeypatch.setattr(handoff.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(handoff, "_has_battery", lambda: True)
    monkeypatch.setattr(handoff, "_find_winget", lambda: os.path.abspath("WindowsApps/winget.exe"))
    return _FakePopen


def _fast_clock(monkeypatch):
    # Every clock read jumps a minute, so a hung report runs out of its
    # (minutes long) timeout at once.
    clock = iter(range(0, 10**7, 60))
    monkeypatch.setattr(handoff.time, "monotonic", lambda: next(clock))


def _zip_texts(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def test_diagnostics_off_runs_nothing(tmp_path, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)

    def _forbidden(*args, **kwargs):
        raise AssertionError("no report may run when diagnostics are off")

    monkeypatch.setattr(handoff.subprocess, "Popen", _forbidden)
    monkeypatch.setattr(handoff, "_has_battery", _forbidden)
    monkeypatch.setattr(handoff, "_find_winget", _forbidden)
    monkeypatch.setattr(handoff.tempfile, "mkdtemp", _forbidden)
    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip")
    assert not any(n.startswith("diagnostics/") for n in _names(dest))


def test_diagnostics_all_reports_in_zip(tmp_path, fake_reports):
    state = tmp_path / "state"
    _write_run(state)
    progress = []

    dest = handoff.build_handoff_zip(
        state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True,
        progress=lambda i, total, name: progress.append((i, total, name)),
    )

    files = _zip_texts(dest)
    expected = {f"diagnostics/{n}" for n in REPORT_NAMES} | {"diagnostics/README.txt"}
    assert expected <= set(files)
    # The run's own files are still there.
    assert {"README.txt", "report.html", "audit_log.jsonl", "undo.ps1"} <= set(files)
    assert files["diagnostics/systeminfo.csv"] == b"data-systeminfo"
    assert files["diagnostics/msinfo32.nfo"] == b"data-msinfo32"
    assert progress == [(i, len(REPORT_NAMES), n) for i, n in enumerate(REPORT_NAMES, start=1)]
    # msinfo32 last (slowest); powercfg /energy never (too slow).
    assert fake_reports.calls[-1][:2] == ["msinfo32", "/nfo"]
    assert not any("/energy" in call for call in fake_reports.calls)


def test_diagnostics_commands_are_locale_free(tmp_path, fake_reports):
    handoff.collect_diagnostics(tmp_path / "work")
    by_name = {os.path.basename(c[0]).lower(): c for c in fake_reports.calls}
    assert by_name["systeminfo"] == ["systeminfo", "/fo", "csv"]
    assert by_name["driverquery"] == ["driverquery", "/v", "/fo", "csv"]
    assert by_name["ipconfig"] == ["ipconfig", "/all"]
    assert by_name["powercfg"][:3] == ["powercfg", "/batteryreport", "/output"]
    assert by_name["dxdiag"][:2] == ["dxdiag", "/t"]
    assert by_name["winget.exe"][1] == "export"
    # Everything except Loaded Modules - the category that makes msinfo32
    # take minutes.
    assert by_name["msinfo32"][1] == "/nfo"
    assert by_name["msinfo32"][-2:] == ["/categories", "+all-loadedmodules"]
    events = [c for c in fake_reports.calls if c[0] == "wevtutil"]
    assert [c[2] for c in events] == ["System", "Application"]
    for call in events:
        assert call[1] == "epl"
        query = next(a for a in call if a.startswith("/q:"))
        # Numeric levels and a 7-day timediff, no localized level names.
        assert "Level=1" in query and "Level=2" in query and "604800000" in query
        assert "Error" not in query and "Chyba" not in query


def test_one_failed_and_one_timed_out_report_do_not_fail_handoff(tmp_path, fake_reports, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    fake_reports.behaviour = {
        "msinfo32": (0, b"partial", "hang"),
        "dxdiag": (0, b"", "oserror"),
        "driverquery": (1, b"error text", "ok"),
    }
    _fast_clock(monkeypatch)

    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True)

    names = set(_names(dest))
    assert "diagnostics/msinfo32.nfo" not in names  # killed - the partial file is dropped
    assert "diagnostics/dxdiag.txt" not in names
    assert "diagnostics/driverquery.csv" not in names  # non-zero exit
    assert {"diagnostics/systeminfo.csv", "diagnostics/ipconfig.txt", "diagnostics/README.txt"} <= names
    readme = _zip_texts(dest)["diagnostics/README.txt"].decode("utf-8-sig")
    assert "[vypršal čas (300 s)]" in readme and "[timed out (300 s)]" in readme
    assert "[zlyhalo (FileNotFoundError)]" in readme and "[failed (exit 1)]" in readme
    assert "[included]" in readme and "[zahrnuté]" in readme


def test_timeout_kills_the_process(tmp_path, fake_reports, monkeypatch):
    fake_reports.behaviour = {"systeminfo": (0, b"x", "hang")}
    _fast_clock(monkeypatch)
    results = handoff.collect_diagnostics(tmp_path / "work")
    assert results[0].arcname == "systeminfo.csv"
    assert results[0].status == handoff.STATUS_TIMEOUT
    assert fake_reports.procs[0].killed
    assert not (tmp_path / "work" / "systeminfo.csv").exists()
    # The next report still ran.
    assert results[1].status == handoff.STATUS_OK


def test_desktop_without_battery_and_missing_winget_are_skipped(tmp_path, fake_reports, monkeypatch):
    monkeypatch.setattr(handoff, "_has_battery", lambda: False)
    monkeypatch.setattr(handoff, "_find_winget", lambda: None)
    results = {r.arcname: r for r in handoff.collect_diagnostics(tmp_path / "work")}
    assert results["battery-report.html"].status == handoff.STATUS_SKIPPED
    assert results["battery-report.html"].detail == "no battery"
    assert results["winget-export.json"].status == handoff.STATUS_SKIPPED
    assert not any(c[0] == "powercfg" or "winget" in c[0] for c in fake_reports.calls)


def test_unknown_battery_state_still_tries_the_report(tmp_path, fake_reports, monkeypatch):
    monkeypatch.setattr(handoff, "_has_battery", lambda: None)
    results = {r.arcname: r for r in handoff.collect_diagnostics(tmp_path / "work")}
    assert results["battery-report.html"].status == handoff.STATUS_OK


def test_self_written_report_with_nonzero_exit_is_kept_and_noted(tmp_path, fake_reports):
    # winget export exits non-zero when a package has no source, but the
    # list it wrote is still useful.
    fake_reports.behaviour = {"winget": (-1978335216, b"{}", "ok")}
    results = {r.arcname: r for r in handoff.collect_diagnostics(tmp_path / "work")}
    assert results["winget-export.json"].status == handoff.STATUS_OK
    assert results["winget-export.json"].detail == "exit -1978335216"


def test_empty_output_counts_as_failure(tmp_path, fake_reports):
    fake_reports.behaviour = {"dxdiag": (0, b"", "ok")}
    results = {r.arcname: r for r in handoff.collect_diagnostics(tmp_path / "work")}
    assert results["dxdiag.txt"].status == handoff.STATUS_FAILED


def test_oversized_report_is_left_out(tmp_path, fake_reports, monkeypatch):
    monkeypatch.setattr(handoff, "MAX_DIAG_FILE_BYTES", 3)
    fake_reports.behaviour = {"ipconfig": (0, b"0123456789", "ok")}
    results = {r.arcname: r for r in handoff.collect_diagnostics(tmp_path / "work")}
    assert results["ipconfig.txt"].status == handoff.STATUS_SKIPPED
    assert not (tmp_path / "work" / "ipconfig.txt").exists()


def test_diagnostics_readme_explains_files_and_personal_data(tmp_path, fake_reports):
    state = tmp_path / "state"
    _write_run(state)
    dest = handoff.build_handoff_zip(
        state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True, dry_run=True
    )
    raw = _zip_texts(dest)["diagnostics/README.txt"]
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    readme = raw.decode("utf-8-sig")
    assert HOST in readme and RUN in readme
    assert "SLOVENSKY" in readme and "ENGLISH" in readme
    assert "osobné" in readme and "anonymizované" in readme
    assert "personal" in readme and "user names" in readme and "network configuration" in readme
    # Every report is described in both languages.
    for spec in handoff.DIAGNOSTIC_REPORTS:
        assert spec.arcname in readme and spec.sk in readme and spec.en in readme
    # DRY-RUN is said, not hidden.
    assert "DRY-RUN" in readme and "iba čítajú" in readme and "only read" in readme
    # The client-facing README points at the folder.
    assert "diagnostics/" in _zip_texts(dest)["README.txt"].decode("utf-8-sig")


def test_readme_without_dry_run_has_no_dry_run_note():
    text = handoff.diagnostics_readme(
        [handoff.DiagnosticResult("ipconfig.txt", handoff.STATUS_OK)], HOST, RUN, dry_run=False
    )
    assert "DRY-RUN" not in text


def test_temp_folder_is_removed_after_zipping(tmp_path, fake_reports, monkeypatch):
    state = tmp_path / "state"
    _write_run(state)
    made = []
    real_mkdtemp = handoff.tempfile.mkdtemp

    def _spy(*a, **k):
        path = real_mkdtemp(*a, **k)
        made.append(path)
        return path

    monkeypatch.setattr(handoff.tempfile, "mkdtemp", _spy)
    handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True)
    assert len(made) == 1 and not os.path.exists(made[0])


def test_stop_request_cancels_without_writing_zip(tmp_path, fake_reports):
    state = tmp_path / "state"
    _write_run(state)

    with pytest.raises(handoff.HandoffCancelled):
        handoff.build_handoff_zip(
            state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True,
            should_stop=lambda: len(fake_reports.calls) >= 2,
        )
    assert len(fake_reports.calls) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state"]


def test_stop_request_kills_running_report(tmp_path, fake_reports):
    fake_reports.behaviour = {"systeminfo": (0, b"x", "hang")}
    answers = iter([False, True])
    with pytest.raises(handoff.HandoffCancelled):
        handoff.collect_diagnostics(tmp_path / "work", should_stop=lambda: next(answers, True))
    assert fake_reports.procs[0].killed


def test_no_run_files_raises_before_any_report_runs(tmp_path, fake_reports):
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(ValueError):
        handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True)
    assert fake_reports.calls == []


def test_handoff_runner_reports_result_and_progress(tmp_path, fake_reports):
    state = tmp_path / "state"
    _write_run(state)
    dest = tmp_path / "out.zip"
    runner = handoff.HandoffRunner(state, HOST, RUN, dest, dry_run=True)
    results, progress = [], []
    runner.result_ready.connect(lambda *a: results.append(a))
    runner.progress.connect(lambda *a: progress.append(a))
    runner.run()  # synchronously - the thread wrapper itself adds nothing
    assert results == [(dest, "", "")]
    assert len(progress) == len(REPORT_NAMES)
    assert "DRY-RUN" in _zip_texts(dest)["diagnostics/README.txt"].decode("utf-8-sig")


def test_handoff_runner_reports_missing_files_and_write_errors(tmp_path, fake_reports, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    results = []
    runner = handoff.HandoffRunner(empty, HOST, RUN, tmp_path / "a.zip")
    runner.result_ready.connect(lambda *a: results.append(a))
    runner.run()
    assert results[0][0] is None and results[0][1] == "no_files"

    state = tmp_path / "state"
    _write_run(state)

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(handoff.zipfile.ZipFile, "write", _boom)
    runner = handoff.HandoffRunner(state, HOST, RUN, tmp_path / "b.zip")
    runner.result_ready.connect(lambda *a: results.append(a))
    runner.run()
    assert results[1] == (None, "failed", "disk full")
    assert not (tmp_path / "b.zip").exists()


def test_i18n_keys_exist_in_both_languages():
    from portablefix import i18n

    for key in (
        "handoff_include_diagnostics", "handoff_include_diagnostics_tip", "handoff_diag_started",
        "handoff_diag_progress", "handoff_diag_dry_run_note", "handoff_diag_busy",
    ):
        sk, en = i18n.translate(key, "sk"), i18n.translate(key, "en")
        assert sk and en and sk != key and en != key and sk != en
    assert "{index}" in i18n.translate("handoff_diag_progress", "sk")


def test_real_process_output_and_timeout(tmp_path):
    # The wait loop against real child processes, not the fake: output is
    # stored byte for byte, and a hung one is killed at its timeout.
    import sys

    reports = (
        handoff.DiagnosticSpec(
            "out.txt", (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xe1 ok')"),
            30, True, "sk", "en",
        ),
        handoff.DiagnosticSpec(
            "hang.txt", (sys.executable, "-c", "import time; time.sleep(60)"), 1, True, "sk", "en",
        ),
    )
    results = handoff.collect_diagnostics(tmp_path, reports=reports)
    assert [r.status for r in results] == [handoff.STATUS_OK, handoff.STATUS_TIMEOUT]
    assert (tmp_path / "out.txt").read_bytes() == b"\xe1 ok"
    assert not (tmp_path / "hang.txt").exists()


def test_diagnostics_stderr_never_lands_in_report_files(tmp_path, fake_reports):
    # stderr is warnings in the display language: mixed into systeminfo.csv
    # it would make the CSV unreadable by any tool.
    handoff.collect_diagnostics(tmp_path / "work")
    assert fake_reports.stderrs
    assert all(err == subprocess.DEVNULL for err in fake_reports.stderrs)


# --- Redact for the client (research G20) ----------------------------------

_SECRET_OUTPUT = (
    "SerialNumber : 5CD1234XYZ\n"
    "Cleaned C:\\Users\\jnovak\\AppData\\Local\\Temp\n"
    "IPv4 192.168.1.23 MAC 00-1A-2B-3C-4D-5E\n"
    "SSID : Novakovci"
)
_SECRETS = ("5CD1234XYZ", "jnovak", "192.168.1.23", "00-1A-2B-3C-4D-5E", "Novakovci")


def _write_real_run(state_dir, run_id=RUN):
    """A run with a real audit log and report (generate_report), the way
    main_window leaves it on the stick - not redacted."""
    import socket

    from portablefix.audit_log import append_entry, make_entry
    from portablefix.report import generate_report

    append_entry(state_dir, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, _SECRET_OUTPUT, False, run_id))
    # A later entry repeating the network name without the "SSID :" key.
    append_entry(state_dir, run_id, make_entry("m02_cleanup", "user_temp", "cmd", 0, "Joined Novakovci", False, run_id))
    job = {"technician": "Ján", "client": "Klient s.r.o.", "note": ""}
    generate_report(state_dir, run_id, [], "sk", {}, {}, job=job)
    backups = state_dir / "Backups" / run_id
    backups.mkdir(parents=True, exist_ok=True)
    undo = "Copy-Item 'C:\\Users\\jnovak\\x.bak' 'C:\\Users\\jnovak\\x'\n".encode("utf-8-sig")
    (backups / "undo.ps1").write_bytes(undo)
    host = socket.gethostname()
    return host, undo


def test_redacted_package_masks_report_and_audit_log_but_not_the_originals(tmp_path):
    state = tmp_path / "state"
    host, undo = _write_real_run(state)
    originals = {p: p.read_bytes() for p in state.rglob("*") if p.is_file()}

    dest = handoff.build_handoff_zip(state, host, RUN, tmp_path / "out.zip", redact=True)

    files = _zip_texts(dest)
    for arcname in ("report.html", "report.json", "audit_log.jsonl"):
        text = files[arcname].decode("utf-8")
        for secret in _SECRETS:
            assert secret not in text, (arcname, secret)
    report_data = json.loads(files["report.json"])
    assert report_data["redacted"] is True
    assert report_data["hostname"] == host
    assert report_data["job"]["client"] == "Klient s.r.o."
    # Re-rendered from the redacted JSON: the page says so, masked values
    # are escaped text.
    html_text = files["report.html"].decode("utf-8")
    assert "Redigované pre klienta" in html_text
    assert "C:\\Users\\&lt;user&gt;\\AppData" in html_text and "Klient s.r.o." in html_text
    # Still one JSON object per line, the SSID masked in the entry that
    # does not name it by key too.
    entries = [json.loads(line) for line in files["audit_log.jsonl"].decode("utf-8").splitlines()]
    assert [e["output"] for e in entries] == [
        "SerialNumber : <serial>\nCleaned C:\\Users\\<user>\\AppData\\Local\\Temp\n"
        "IPv4 <ip> MAC <mac>\nSSID : <ssid>",
        "Joined <ssid>",
    ]
    assert entries[0]["run_id"] == RUN and entries[0]["hostname"] == host
    # undo.ps1 must keep working: it is copied as it is.
    assert files["undo.ps1"] == undo
    readme = files["README.txt"].decode("utf-8-sig")
    assert "REDIGOVANÉ PRE KLIENTA" in readme and "REDACTED FOR THE CLIENT" in readme
    assert "undo.ps1 je nezmenený" in readme and "undo.ps1 is unchanged" in readme
    # The files on the stick are exactly as they were.
    assert {p: p.read_bytes() for p in originals} == originals


def test_package_without_redaction_is_byte_for_byte_the_run(tmp_path):
    state = tmp_path / "state"
    host, _ = _write_real_run(state)
    dest = handoff.build_handoff_zip(state, host, RUN, tmp_path / "out.zip")
    files = _zip_texts(dest)
    html_path, json_path = handoff.history.run_report_paths(state / "Reports", host, RUN)
    assert files["report.html"] == html_path.read_bytes()
    assert files["report.json"] == json_path.read_bytes()
    assert files["audit_log.jsonl"] == handoff.audit_log_path(state, RUN).read_bytes()
    assert b"5CD1234XYZ" in files["audit_log.jsonl"]
    assert "REDACTED" not in files["README.txt"].decode("utf-8-sig")


def test_redacted_package_falls_back_to_text_for_foreign_report_files(tmp_path):
    # A report JSON that is not what generate_report writes (older, hand
    # edited, broken) and a torn audit line are still redacted as text.
    state = tmp_path / "state"
    _write_run(state)
    reports = state / "Reports"
    (reports / f"{HOST}_{RUN}.html").write_text(
        "<html><pre>Path C:\\Users\\jnovak\\Desktop &amp; 192.168.1.23</pre></html>", encoding="utf-8")
    (reports / f"{HOST}_{RUN}.json").write_text("not json 192.168.1.23", encoding="utf-8")
    log = state / "Logs" / f"{RUN}.jsonl"
    log.write_bytes(b'{"run_id": "r", "output": "at 192.168.1.23"}\n{"torn": "C:\\\\Users\\\\jnovak\\\\x\n\xff\n')

    files = _zip_texts(handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip", redact=True))

    html_text = files["report.html"].decode("utf-8")
    assert html_text == "<html><pre>Path C:\\Users\\&lt;user&gt;\\Desktop &amp; &lt;ip&gt;</pre></html>"
    assert files["report.json"].decode("utf-8") == "not json <ip>"
    lines = files["audit_log.jsonl"].decode("utf-8").splitlines()
    assert lines[0] == '{"run_id": "r", "output": "at <ip>"}'
    assert lines[1] == '{"torn": "C:\\\\Users\\\\<user>\\\\x'
    assert len(lines) == 3


def test_hostname_that_looks_like_a_key_is_kept_in_the_package(tmp_path):
    state = tmp_path / "state"
    host = "ABCD1-EFGH2"
    _write_run(state, host=host)
    (state / "Logs" / f"{RUN}.jsonl").write_bytes(
        b'{"run_id": "r", "hostname": "ABCD1-EFGH2", "output": "on ABCD1-EFGH2, key VK7JG-NPHTM-C97JM"}\n')
    files = _zip_texts(handoff.build_handoff_zip(state, host, RUN, tmp_path / "out.zip", redact=True))
    assert b'"output": "on ABCD1-EFGH2, key <key>"' in files["audit_log.jsonl"]
    assert host in files["README.txt"].decode("utf-8-sig")


def test_diagnostics_readme_says_windows_reports_are_not_redacted(tmp_path, fake_reports):
    state = tmp_path / "state"
    _write_run(state)
    dest = handoff.build_handoff_zip(state, HOST, RUN, tmp_path / "out.zip", include_diagnostics=True, redact=True)
    files = _zip_texts(dest)
    readme = files["diagnostics/README.txt"].decode("utf-8-sig")
    assert "Redigovať pre klienta" in readme and "NEMENÍ" in readme
    assert "Redact for the client" in readme and "does NOT change" in readme
    # The Windows reports themselves go in untouched.
    assert files["diagnostics/systeminfo.csv"] == b"data-systeminfo"
    assert "Redact for the client" not in handoff.diagnostics_readme([], HOST, RUN)


def test_handoff_runner_passes_redact_through(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(handoff, "build_handoff_zip", lambda *a, **kw: captured.update(kw) or tmp_path / "x.zip")
    handoff.HandoffRunner(tmp_path, HOST, RUN, tmp_path / "x.zip", redact=True).run()
    assert captured["redact"] is True and captured["include_diagnostics"] is True
