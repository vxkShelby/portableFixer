import base64
import os
import subprocess
import sys
from pathlib import Path

from portablefix import signing
from signing_keys import TEST_SEED

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_signed_file_verifies_and_returns_only_the_body():
    body = b"aa  App/PortableFix.exe\nbb  Modules/m/actions.yaml\n"
    signed = signing.sign(body, TEST_SEED)
    assert signed.startswith(body)
    assert signing.verified_body(signed) == body


def test_signature_line_is_skipped_by_parsers_of_old_clients(tmp_path):
    # <= 1.14 parse SHA256SUMS with parse_sha256sums and the zip's .sha256
    # with .split()[0]; the signature line must be invisible to both.
    from portablefix.sha256sums import parse_sha256sums

    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(signing.sign(b"aa  App/PortableFix.exe\n", TEST_SEED))
    assert parse_sha256sums(sums) == {"App/PortableFix.exe": "aa"}
    asset = signing.sign(b"ab" * 32 + b"  PortableFix-Portable.zip\r\n", TEST_SEED)
    assert asset.decode().split()[0] == "ab" * 32


def test_crlf_body_verifies():
    body = b"ab  PortableFix-Portable.zip\r\n"
    assert signing.verified_body(signing.sign(body, TEST_SEED)) == body


def test_resigning_replaces_the_old_signature():
    once = signing.sign(b"aa  x\n", TEST_SEED)
    assert signing.sign(once, TEST_SEED) == once


def test_unsigned_tampered_or_foreign_signatures_fail():
    signed = signing.sign(b"aa  App/PortableFix.exe\n", TEST_SEED)
    assert signing.verified_body(b"aa  App/PortableFix.exe\n") is None
    assert signing.verified_body(signed.replace(b"aa", b"ab", 1)) is None
    # Anything after the signature line is not covered - refuse it.
    assert signing.verified_body(signed + b"cc  App/evil.dll\n") is None
    assert signing.verified_body(b"aa  x\ned25519:not base64!\n") is None
    other = signing.sign(b"aa  App/PortableFix.exe\n", b"\x07" * 32)
    assert signing.verified_body(other) is None


def test_release_public_key_is_the_real_one():
    # A fresh process: in this one the conftest swapped in the test key.
    out = subprocess.run(
        [sys.executable, "-c", "import base64; from portablefix import signing; print(base64.b64encode(signing.PUBLIC_KEY).decode())"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    ).stdout.strip()
    assert out == "WqbyQCgaol4dMDc93bPvnmNSWGSjxZvvoB22t81cJ+k="


def _run_sign(env_key: str | None, *files: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != signing.KEY_ENV_VAR}
    if env_key is not None:
        env[signing.KEY_ENV_VAR] = env_key
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "sign_release.py"), *map(str, files)],
        capture_output=True, text=True, env=env,
    )


def test_sign_script_refuses_without_a_key(tmp_path):
    target = tmp_path / "SHA256SUMS"
    target.write_bytes(b"aa  x\n")
    result = _run_sign(None, target)
    assert result.returncode == 1
    assert target.read_bytes() == b"aa  x\n"


def test_sign_script_refuses_a_key_that_does_not_match_the_app(tmp_path):
    # The subprocess sees the real public key, not the conftest's test key.
    target = tmp_path / "SHA256SUMS"
    target.write_bytes(b"aa  x\n")
    secret = base64.b64encode(TEST_SEED).decode()
    result = _run_sign(secret, target)
    assert result.returncode == 1
    assert target.read_bytes() == b"aa  x\n"
    assert secret not in result.stdout + result.stderr
