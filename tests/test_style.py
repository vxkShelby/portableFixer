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


# --- light / dark themes ------------------------------------------------------

import pytest


@pytest.fixture(autouse=True)
def _back_to_dark():
    yield
    style.set_theme("dark")


def _luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# Surfaces text sits on (hover fills and disabled controls are exempt).
SURFACES = ("bg", "bg_end", "surface", "control", "inset", "row_bg")
TEXT_TOKENS = (
    "text", "text_soft", "muted", "subtle", "accent", "accent2", "accent_text", "magenta",
    "ok", "warn", "bad", "purple", "custom_text", "custom_hover", "custom_checked", "console_text",
)
FILL_TOKENS = ("ok", "warn", "bad", "purple", "run_a", "run_b", "run_a_hover", "run_b_hover")


@pytest.mark.parametrize("theme", sorted(style.PALETTES))
def test_palette_text_and_accent_colours_reach_wcag_aa_on_every_surface(theme):
    palette = style.PALETTES[theme]
    for token in TEXT_TOKENS:
        for surface in SURFACES:
            assert _contrast(palette[token], palette[surface]) >= 4.5, (theme, token, surface)


@pytest.mark.parametrize("theme", sorted(style.PALETTES))
def test_palette_text_on_filled_badges_buttons_and_avatars_reaches_wcag_aa(theme):
    palette = style.PALETTES[theme]
    for token in FILL_TOKENS:
        assert _contrast(palette["on_fill"], palette[token]) >= 4.5, (theme, token)
    for background in palette["avatar_bgs"]:
        assert _contrast(palette["avatar_fg"], background) >= 4.5, (theme, background)


@pytest.mark.parametrize("theme", sorted(style.PALETTES))
def test_each_theme_builds_a_complete_stylesheet_with_its_own_colours(theme):
    sheet = style.build_stylesheet(theme)
    assert "$" not in sheet
    assert style.PALETTES[theme]["surface"] in sheet
    other = "light" if theme == "dark" else "dark"
    assert style.PALETTES[other]["accent"] not in sheet


def test_both_palettes_define_the_same_tokens_and_dark_is_the_default_sheet():
    assert style.PALETTES["dark"].keys() == style.PALETTES["light"].keys()
    assert style.STYLE == style.build_stylesheet("dark")


def test_set_theme_switches_stylesheet_risk_and_console_colours_in_place():
    console_colors = style.CONSOLE_COLORS
    assert style.set_theme("light") == "light"
    assert style.active_theme() == "light"
    assert style.stylesheet(high_contrast=False) == style.build_stylesheet("light")
    assert style.RISK_COLORS["SAFE"] == style.PALETTES["light"]["ok"]
    assert style.CONSOLE_COLORS is console_colors
    assert console_colors["error"] == style.PALETTES["light"]["bad"]
    assert style.avatar_colors("Fake App")[1] == style.PALETTES["light"]["avatar_fg"]
    # Windows High Contrast stays stylesheet-free in either theme.
    assert style.stylesheet(high_contrast=True) == ""
    assert style.set_theme("dark") == "dark"
    assert style.stylesheet(high_contrast=False) == style.STYLE
    assert console_colors["error"] == style.PALETTES["dark"]["bad"]


def test_risk_and_console_colours_are_readable_on_the_console_background_in_both_themes():
    for theme, palette in style.PALETTES.items():
        style.set_theme(theme)
        for color in (*style.RISK_COLORS.values(), *style.CONSOLE_COLORS.values()):
            assert _contrast(color, palette["inset"]) >= 4.5, (theme, color)


def test_auto_follows_the_os_colour_scheme_and_falls_back_to_dark(monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    class Hints:
        def __init__(self, scheme):
            self._scheme = scheme

        def colorScheme(self):
            return self._scheme

    class NoColorScheme:  # Qt older than 6.5
        pass

    for hints, expected in (
        (Hints(Qt.ColorScheme.Light), "light"),
        (Hints(Qt.ColorScheme.Dark), "dark"),
        (Hints(Qt.ColorScheme.Unknown), "dark"),
        (NoColorScheme(), "dark"),
    ):
        monkeypatch.setattr(QGuiApplication, "styleHints", staticmethod(lambda h=hints: h))
        assert style.resolve_theme("auto") == expected
    assert style.resolve_theme("light") == "light" and style.resolve_theme("dark") == "dark"
