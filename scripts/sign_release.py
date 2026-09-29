"""Signs release manifests in place with the ed25519 release key (research
G32; the format is in portablefix/signing.py):

    python scripts/sign_release.py Data/SHA256SUMS Output/PortableFix-Portable.zip.sha256

The key - the base64 32-byte seed - comes only from the environment variable
PORTABLEFIX_RELEASE_SIGNING_KEY (the GitHub Actions secret of the same name,
see .github/workflows/release.yml). It is never printed. Each file is checked
against the public key built into the app right after signing, so a wrong or
rotated secret fails the build instead of shipping a release no client
accepts."""
import base64
import binascii
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portablefix.signing import KEY_ENV_VAR, sign, verified_body


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: sign_release.py FILE [FILE ...]", file=sys.stderr)
        return 2
    try:
        seed = base64.b64decode(os.environ.get(KEY_ENV_VAR, ""), validate=True)
    except (ValueError, binascii.Error):
        seed = b""
    if len(seed) != 32:
        print(f"{KEY_ENV_VAR} is not set or is not a base64 32-byte ed25519 seed", file=sys.stderr)
        return 1
    for name in argv:
        path = Path(name)
        signed = sign(path.read_bytes(), seed)
        if verified_body(signed) is None:
            print(f"{path}: the signing key does not match the public key in portablefix/signing.py", file=sys.stderr)
            return 1
        path.write_bytes(signed)
        print(f"Signed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
