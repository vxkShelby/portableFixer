"""The technician's own branding in the report header (research G20):
company name, company ID (IČO), contact and a logo. All optional.

The logo is copied into settings.json as base64 - the report is a single
self-contained HTML file, and a logo that only pointed at a path on the
technician's PC would be missing on the client's. It is checked by its
magic bytes, not its file extension: whatever ends up in the report's
<img src="data:..."> must really be a PNG or a JPEG. Qt-free on purpose -
settings.py, report.py and the tests use the same checks.
"""

import base64
import binascii
from pathlib import Path

MAX_LOGO_BYTES = 256 * 1024
MAX_COMPANY_LENGTH = 120
MAX_COMPANY_ID_LENGTH = 40
MAX_CONTACT_LENGTH = 300

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
# Base64 of 256 KB, rounded up to whole 4-character groups.
_MAX_LOGO_BASE64_LENGTH = (MAX_LOGO_BYTES + 2) // 3 * 4

# LogoError codes; the GUI shows branding_logo_<code>.
LOGO_TOO_LARGE = "too_large"
LOGO_NOT_IMAGE = "not_image"
LOGO_UNREADABLE = "unreadable"


class LogoError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def logo_mime(data: bytes) -> str | None:
    """"image/png" / "image/jpeg" by the file's first bytes, else None."""
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return None


def encode_logo(data: bytes) -> str:
    """The logo bytes as the base64 text stored in settings. Raises
    LogoError when they are too large or not a PNG/JPEG."""
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError(LOGO_TOO_LARGE)
    if logo_mime(data) is None:
        raise LogoError(LOGO_NOT_IMAGE)
    return base64.b64encode(data).decode("ascii")


def load_logo_file(path: Path) -> str:
    """Reads and checks a logo file picked by the technician. The size is
    checked before the whole file is read - a 2 GB file picked by mistake
    must not be loaded into memory first."""
    try:
        with Path(path).open("rb") as f:
            data = f.read(MAX_LOGO_BYTES + 1)
    except OSError:
        raise LogoError(LOGO_UNREADABLE) from None
    return encode_logo(data)


def decode_logo(value) -> tuple[str, str] | None:
    """(mime type, base64) of a stored logo, or None when it is missing or
    not a valid PNG/JPEG within the limit - a hand-edited settings.json or
    report.json must never put arbitrary text into the report's <img>."""
    if not isinstance(value, str) or not value or len(value) > _MAX_LOGO_BASE64_LENGTH:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    mime = logo_mime(data)
    if mime is None or len(data) > MAX_LOGO_BYTES:
        return None
    # Re-encoded, so the text going into the page is exactly base64.
    return mime, base64.b64encode(data).decode("ascii")


def clean_text(value, limit: int) -> str:
    """A branding text field: a string, trimmed and cut to `limit`."""
    return value.strip()[:limit] if isinstance(value, str) else ""


def clean_branding(raw) -> dict:
    """The branding for a report: company, company_id, contact, logo_mime
    and logo, only those that are set. {} when nothing is."""
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, limit in (
        ("company", MAX_COMPANY_LENGTH),
        ("company_id", MAX_COMPANY_ID_LENGTH),
        ("contact", MAX_CONTACT_LENGTH),
    ):
        text = clean_text(raw.get(key), limit)
        if text:
            cleaned[key] = text
    logo = decode_logo(raw.get("logo"))
    if logo is not None:
        cleaned["logo_mime"], cleaned["logo"] = logo
    return cleaned
