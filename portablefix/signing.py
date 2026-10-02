"""ed25519 signatures on the release manifests (research G32), Qt-free so the
update core and the build scripts can use it.

A signed file is its ordinary text followed by one last line
`ed25519:<base64 signature over every byte before that line>`. The signature
lives inside the file it signs rather than in a separate .sig on purpose:
clients up to 1.14 run the update swap themselves and install only an
allowlist of Data/ files (SHA256SUMS, the .cer, .gitkeep), so a separate
Data/SHA256SUMS.sig would never reach an install they update. The line has
no whitespace, so their parsers skip it: parse_sha256sums() drops lines
that do not split in two, and the updater takes only the first token of
PortableFix-Portable.zip.sha256.

Signed: Data/SHA256SUMS (covers App/, Modules/, Vendor/) and the release
asset PortableFix-Portable.zip.sha256 (covers the whole zip, including
PortableFix.cmd and Data/, which SHA256SUMS does not)."""
import base64
import binascii

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

# The release key's public half. Its private half exists only as the GitHub
# Actions secret PORTABLEFIX_RELEASE_SIGNING_KEY (.github/workflows/release.yml).
# Tests swap this for a test key (tests/conftest.py) - never an env var or a
# file here: anything the app reads from disk could be planted next to it.
PUBLIC_KEY = base64.b64decode("O9lzLtsl2uQhIDcnXDNAZT2KJvRtLeoM1gFfkqozDTU=")

SIG_PREFIX = b"ed25519:"
KEY_ENV_VAR = "PORTABLEFIX_RELEASE_SIGNING_KEY"


def _split(data: bytes) -> tuple[bytes, bytes] | None:
    """(body, base64 signature) when the last non-empty line is a signature."""
    stripped = data.rstrip(b"\r\n")
    cut = stripped.rfind(b"\n") + 1
    line = stripped[cut:].rstrip(b"\r")
    if not line.startswith(SIG_PREFIX):
        return None
    return data[:cut], line[len(SIG_PREFIX):]


def verified_body(data: bytes) -> bytes | None:
    """The signed part of data - only what the signature covers, never the
    signature line or anything after it - or None when data carries no
    signature or one that does not verify with PUBLIC_KEY. Callers must use
    the returned bytes, not data, once this succeeds."""
    parts = _split(data)
    if parts is None:
        return None
    body, sig_b64 = parts
    try:
        VerifyKey(PUBLIC_KEY).verify(body, base64.b64decode(sig_b64, validate=True))
    except (BadSignatureError, ValueError, binascii.Error):
        return None
    return body


def sign(data: bytes, seed: bytes) -> bytes:
    """data with its signature line (re-signing replaces an existing one)."""
    parts = _split(data)
    body = data if parts is None else parts[0]
    if body and not body.endswith(b"\n"):
        body += b"\n"
    signature = SigningKey(seed).sign(body).signature
    return body + SIG_PREFIX + base64.b64encode(signature) + b"\n"
