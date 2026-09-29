"""The test stand-in for the release key: tests/conftest.py makes the app
trust it in place of the real one (portablefix/signing.PUBLIC_KEY)."""
from nacl.signing import SigningKey

from portablefix import signing

TEST_SEED = bytes(range(32))
TEST_PUBLIC_KEY = bytes(SigningKey(TEST_SEED).verify_key)


def sign_for_tests(data: bytes) -> bytes:
    return signing.sign(data, TEST_SEED)
