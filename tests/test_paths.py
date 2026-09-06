import sys
import tempfile
from pathlib import Path

from portablefix.paths import (
    compute_temp_protected_child,
    compute_windir_temp_protected_child,
    get_base_dir,
    resolve_temp_root,
    resolve_windir_temp_root,
    resolve_writable_base_dir,
)


def test_get_base_dir_dev_mode():
    result = get_base_dir()
    assert result == Path(__file__).resolve().parent.parent


def test_get_base_dir_frozen_mode(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(Path("C:/USB/PortableFix/App/PortableFix.exe")))
    result = get_base_dir()
    assert result == Path("C:/USB/PortableFix")


def test_resolve_writable_base_dir_success(tmp_path):
    result, used_fallback = resolve_writable_base_dir(tmp_path)
    assert result == tmp_path
    assert used_fallback is False


def test_resolve_writable_base_dir_fallback(monkeypatch, tmp_path):
    import tempfile

    def raise_oserror(self, *args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", raise_oserror)
    result, used_fallback = resolve_writable_base_dir(tmp_path)
    assert used_fallback is True
    assert result == Path(tempfile.gettempdir()) / "PortableFix"


def test_resolve_writable_base_dir_raises_actionable_error_when_temp_also_unwritable(monkeypatch, tmp_path):
    import pytest

    def raise_oserror(self, *args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", raise_oserror)
    monkeypatch.setattr(Path, "mkdir", raise_oserror)

    with pytest.raises(RuntimeError, match="TEMP"):
        resolve_writable_base_dir(tmp_path)


def test_compute_temp_protected_child_nested_ancestor(monkeypatch, tmp_path):
    # The app runs several levels deep under a wrapper folder (e.g. a
    # PyInstaller _MEI* extraction dir) that itself sits directly under
    # %TEMP% - the wrapper is the top-level child that must be protected,
    # not the app's own (deeper) folder.
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    wrapper_child = temp_root / "_MEI123456"
    app_dir = wrapper_child / "deep" / "nested" / "PortableFix"
    app_dir.mkdir(parents=True)

    assert compute_temp_protected_child(app_dir) == wrapper_child.resolve()


def test_compute_temp_protected_child_exact_top_level_match(monkeypatch, tmp_path):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app_dir = temp_root / "PortableFix"
    app_dir.mkdir()

    assert compute_temp_protected_child(app_dir) == app_dir.resolve()


def test_compute_temp_protected_child_unrelated_app_dir_returns_none(monkeypatch, tmp_path):
    # The overwhelmingly common case - app installed on a USB drive, Program
    # Files, etc. - nothing under %TEMP% to protect at all.
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app_dir = tmp_path / "elsewhere" / "PortableFix"
    app_dir.mkdir(parents=True)

    assert compute_temp_protected_child(app_dir) is None


def test_compute_temp_protected_child_app_dir_is_temp_root_itself_returns_sentinel(monkeypatch, tmp_path):
    # Bizarre edge case: the app's own root IS %TEMP%. There's no single
    # safe child to protect, so the function signals "refuse" by returning
    # the temp root itself - callers detect this by comparing the result
    # against resolve_temp_root() and must skip running a %TEMP%-wiping
    # action entirely rather than pick an unsafe child.
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))

    result = compute_temp_protected_child(temp_root)

    assert result == resolve_temp_root()
    assert result == temp_root.resolve()


def test_compute_temp_protected_child_returns_none_when_app_dir_resolve_fails(monkeypatch, tmp_path):
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app_dir = temp_root / "gone"
    original_resolve = Path.resolve

    def selective_raise(self, *args, **kwargs):
        if self == app_dir:
            raise OSError("cannot resolve")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", selective_raise)

    assert compute_temp_protected_child(app_dir) is None


def test_compute_temp_protected_child_handles_short_path_alias_via_resolve(monkeypatch, tmp_path):
    # Path.resolve() on Windows calls the OS to canonicalize an existing
    # path, which rewrites a short (8.3) path alias back to the real
    # long-form path - this is what rules out a %TEMP% short-path vs.
    # long-path mismatch as a false "unrelated" result. 8.3 name generation
    # is disabled by default on many modern NTFS volumes, so this test
    # skips itself when it can't fabricate a real alias to exercise.
    import pytest

    if not sys.platform.startswith("win"):
        pytest.skip("8.3 short-path aliasing is a Windows-only concern")
    import ctypes

    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app_dir = temp_root / "SomeLongApplicationFolderName"
    app_dir.mkdir()

    buf = ctypes.create_unicode_buffer(260)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(app_dir), buf, 260)
    if n == 0 or buf.value == str(app_dir):
        pytest.skip("8.3 short names are disabled on this volume - no alias available to test")

    result = compute_temp_protected_child(Path(buf.value))

    assert result == app_dir.resolve()


def test_compute_temp_protected_child_refuses_when_temp_itself_is_a_junction(monkeypatch, tmp_path):
    # A real-world recurrence: %TEMP% redirected to another drive with
    # `mklink /J` (a common "move Temp off the SSD" tip). PowerShell's
    # Get-ChildItem "$env:TEMP" enumerates through the raw junction path, so
    # `.FullName` on children is always reported in unresolved
    # (junction-prefixed) form - it never matches a resolved comparison
    # target, regardless of how correct the comparison logic is. Refuse via
    # the same sentinel as the "app dir IS temp root" case rather than hand
    # back a protected child that's guaranteed to never match.
    import subprocess

    import pytest

    if not sys.platform.startswith("win"):
        pytest.skip("junctions are a Windows-only concern")

    real_temp = tmp_path / "RealTemp"
    real_temp.mkdir()
    junction_temp = tmp_path / "FakeTemp"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction_temp), str(real_temp)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(junction_temp))
    app_dir = junction_temp / "AppFolder" / "bin"
    app_dir.mkdir(parents=True)

    result = compute_temp_protected_child(app_dir)

    assert result == resolve_temp_root()
    assert result == real_temp.resolve()


def test_compute_temp_protected_child_unaffected_when_temp_is_not_redirected(monkeypatch, tmp_path):
    # The common case: %TEMP% is a plain directory, not a reparse point -
    # raw and resolved forms match, so the junction-refusal check must never
    # trigger and normal protected-child computation proceeds as usual.
    temp_root = tmp_path / "faketemp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    app_dir = temp_root / "PortableFix"
    app_dir.mkdir()

    result = compute_temp_protected_child(app_dir)

    assert result == app_dir.resolve()
    assert result != resolve_temp_root()


def test_compute_windir_temp_protected_child_nested_ancestor(monkeypatch, tmp_path):
    windir_root = tmp_path / "fakewindir"
    (windir_root / "Temp").mkdir(parents=True)
    monkeypatch.setenv("WINDIR", str(windir_root))
    app_dir = windir_root / "Temp" / "PortableFix"
    app_dir.mkdir()

    assert compute_windir_temp_protected_child(app_dir) == app_dir.resolve()


def test_compute_windir_temp_protected_child_unrelated_app_dir_returns_none(monkeypatch, tmp_path):
    windir_root = tmp_path / "fakewindir"
    (windir_root / "Temp").mkdir(parents=True)
    monkeypatch.setenv("WINDIR", str(windir_root))
    app_dir = tmp_path / "elsewhere" / "PortableFix"
    app_dir.mkdir(parents=True)

    assert compute_windir_temp_protected_child(app_dir) is None


def test_compute_windir_temp_protected_child_app_dir_is_root_itself_returns_sentinel(monkeypatch, tmp_path):
    windir_root = tmp_path / "fakewindir"
    windir_temp = windir_root / "Temp"
    windir_temp.mkdir(parents=True)
    monkeypatch.setenv("WINDIR", str(windir_root))

    result = compute_windir_temp_protected_child(windir_temp)

    assert result == resolve_windir_temp_root()
    assert result == windir_temp.resolve()


def test_compute_windir_temp_protected_child_missing_windir_env_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.delenv("SystemRoot", raising=False)

    assert compute_windir_temp_protected_child(tmp_path) is None
