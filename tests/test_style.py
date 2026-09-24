import ctypes
import sys

from portablefix.gui import style


def _fake_spi(flags: int, ok: int = 1, calls: list | None = None):
    # Stands in for user32.SystemParametersInfoW: fills the HIGHCONTRASTW
    # struct through the pointer it is handed, like Windows does.
    def spi(action, size, pointer, winini):
        if calls is not None:
            calls.append((action, size, winini))
        pointer.contents.dwFlags = flags
        return ok

    return spi


def test_high_contrast_on_is_detected():
    calls = []
    assert style.is_high_contrast(_fake_spi(style.HCF_HIGHCONTRASTON, calls=calls)) is True
    # SPI_GETHIGHCONTRAST with cbSize = sizeof(HIGHCONTRASTW), as the API requires.
    assert calls == [(0x0042, ctypes.sizeof(style._HighContrastW), 0)]


def test_high_contrast_off_or_other_flags_only_is_not_detected():
    assert style.is_high_contrast(_fake_spi(0)) is False
    # HCF_AVAILABLE (0x2) etc. without HCF_HIGHCONTRASTON means "off".
    assert style.is_high_contrast(_fake_spi(0x2 | 0x4)) is False


def test_high_contrast_failed_call_is_not_detected():
    # SystemParametersInfoW returned FALSE - the struct is not trustworthy.
    assert style.is_high_contrast(_fake_spi(style.HCF_HIGHCONTRASTON, ok=0)) is False


def test_high_contrast_call_raising_is_not_detected():
    def raising(*args):
        raise OSError("user32 unavailable")

    assert style.is_high_contrast(raising) is False


def test_high_contrast_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert style.is_high_contrast() is False


def test_high_contrast_on_windows_without_windll_is_not_detected(monkeypatch):
    # Defensive: a broken/missing ctypes.windll must not crash startup.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert style.is_high_contrast() is False


def test_highcontrastw_struct_matches_winuser_layout():
    # UINT cbSize; DWORD dwFlags; LPWSTR lpszDefaultScheme.
    assert ctypes.sizeof(style._HighContrastW) == 8 + ctypes.sizeof(ctypes.c_void_p)


def test_stylesheet_is_empty_in_high_contrast_and_full_theme_otherwise(monkeypatch):
    assert style.stylesheet(high_contrast=True) == ""
    assert style.stylesheet(high_contrast=False) == style.STYLE
    monkeypatch.setattr(style, "is_high_contrast", lambda: True)
    assert style.stylesheet() == ""
    monkeypatch.setattr(style, "is_high_contrast", lambda: False)
    assert style.stylesheet() == style.STYLE
