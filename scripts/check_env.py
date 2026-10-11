"""Compares the installed packages with the exact pins in requirements*.txt.

Local runs drifted from CI before (a newer PySide6, no pytest-timeout) and
"passes here, fails on CI" was partly "different Qt". tests/conftest.py
shows the result in the pytest header; run it by hand with
`python scripts/check_env.py` (exit 1 on any difference). Informational only
inside pytest - it never fails a run."""
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*([^\s#;]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_pins(root: Path = ROOT) -> dict[str, str]:
    """name -> version for every `name==version` line of requirements*.txt."""
    pins: dict[str, str] = {}
    for path in sorted(root.glob("requirements*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = PIN.match(line)
            if match:
                pins[_norm(match.group(1))] = match.group(2)
    return pins


def find_drift(root: Path = ROOT) -> list[str]:
    """One line per pinned package that is missing or at another version."""
    drift = []
    for name, wanted in read_pins(root).items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            drift.append(f"{name}: not installed (pinned {wanted})")
            continue
        if installed != wanted:
            drift.append(f"{name}: installed {installed}, pinned {wanted}")
    return drift


if __name__ == "__main__":
    problems = find_drift()
    for problem in problems:
        print(problem)
    print("environment matches the pins" if not problems else f"{len(problems)} package(s) differ from the pins")
    sys.exit(1 if problems else 0)
