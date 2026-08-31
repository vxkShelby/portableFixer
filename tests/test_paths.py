import sys
from pathlib import Path

from portablefix.paths import get_base_dir, resolve_writable_base_dir


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
