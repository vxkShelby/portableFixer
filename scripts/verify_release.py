"""Checks a release before it is published; exit code 0 only when it is
sound. scripts/build.ps1 runs it twice:

  verify_release.py --tree <repo>   after generate_sha256sums.py
  verify_release.py --zip <zip>     after build_release_zip.ps1

--tree: Data/SHA256SUMS covers exactly the files under App/ and Modules/,
with their current hashes. A manifest generated before the exe was signed
(or rebuilt) is a tamper warning on every launch of every copy.

--zip: the zip is staged with update_swap.stage_update, the very code the
clients run, so a package they would refuse never gets published. On top of
that it applies the release rules clients cannot enforce: Data/ holds only
the allowlist (never a build machine's settings.json), the manifest covers
the whole App/ and Modules/, the icon is there, and the .sha256 file next
to the zip matches it.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portablefix.integrity import TARGET_DIRS
from portablefix.sha256sums import compute_sha256, parse_sha256sums
from portablefix.update_swap import (
    DATA_ALLOWLIST,
    MANIFEST_EXE,
    ROOT_ALLOWLIST,
    UpdateStageError,
    stage_update,
)


def _manifest_problems(root: Path) -> list[str]:
    sums_path = root / "Data" / "SHA256SUMS"
    if not sums_path.is_file():
        return ["Data/SHA256SUMS is missing - run scripts/generate_sha256sums.py"]
    try:
        manifest = parse_sha256sums(sums_path)
    except (OSError, UnicodeDecodeError) as exc:
        return [f"Data/SHA256SUMS is unreadable: {exc}"]
    problems = []
    if MANIFEST_EXE not in manifest:
        problems.append(f"Data/SHA256SUMS does not list {MANIFEST_EXE}")
    # Same file set as scripts/generate_sha256sums.py collects.
    present = {
        path.relative_to(root).as_posix(): path
        for target in TARGET_DIRS
        if (root / target).is_dir()
        for path in (root / target).rglob("*")
        if path.is_file()
    }
    for rel in sorted(set(manifest) - set(present)):
        problems.append(f"{rel} is listed in Data/SHA256SUMS but missing")
    for rel in sorted(set(present) - set(manifest)):
        problems.append(f"{rel} is not in Data/SHA256SUMS - regenerate it after the last build or signing step")
    for rel in sorted(set(manifest) & set(present)):
        if compute_sha256(present[rel]) != manifest[rel]:
            problems.append(f"{rel} changed after Data/SHA256SUMS was generated (stale manifest)")
    return problems


def _layout_problems(root: Path) -> list[str]:
    problems = []
    if not (root / "App" / "PortableFix.exe").is_file():
        problems.append("App/PortableFix.exe is missing")
    for name in ("Modules", "Vendor"):
        folder = root / name
        if not folder.is_dir() or not any(folder.iterdir()):
            problems.append(f"{name}/ is missing or empty")
    for name in ROOT_ALLOWLIST:
        if not (root / name).is_file():
            problems.append(f"{name} is missing")
    return problems


def check_tree(root: Path) -> list[str]:
    """The built tree in the repo. Its Data/ is not checked against the
    allowlist: on the build machine it also holds that machine's own runtime
    files, which build_release_zip.ps1 and the installer leave out."""
    return _layout_problems(root) + _manifest_problems(root)


def _sidecar_problems(zip_path: Path) -> list[str]:
    sidecar = zip_path.with_name(zip_path.name + ".sha256")
    if not sidecar.is_file():
        return [f"{sidecar.name} is missing - clients refuse a release without it"]
    tokens = sidecar.read_text(encoding="utf-8", errors="replace").split()
    # The updater compares the first token only (updater.download_update).
    if not tokens or tokens[0].lower() != compute_sha256(zip_path):
        return [f"{sidecar.name} does not match {zip_path.name}"]
    return []


def check_zip(zip_path: Path) -> list[str]:
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return [f"{zip_path} does not exist"]
    problems = _sidecar_problems(zip_path)
    with tempfile.TemporaryDirectory(prefix="pf_verify_") as tmp:
        install_dir = Path(tmp) / "install"
        install_dir.mkdir()
        # stage_update deletes the zip it staged; the release zip must stay.
        copy = Path(tmp) / zip_path.name
        shutil.copyfile(zip_path, copy)
        try:
            staged = stage_update(copy, install_dir)
        except UpdateStageError as exc:
            return problems + [f"clients would refuse this package: {exc}"]
        root = staged.stage_root
        data = root / "Data"
        for extra in sorted(p.name for p in data.iterdir() if p.name not in DATA_ALLOWLIST):
            problems.append(f"Data/{extra} must not ship - only {', '.join(DATA_ALLOWLIST)} may")
        problems += _layout_problems(root) + _manifest_problems(root)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a PortableFix release before publishing it.")
    parser.add_argument("--tree", type=Path, help="the built repo tree (App/, Modules/, Data/SHA256SUMS)")
    parser.add_argument("--zip", type=Path, help="the finished PortableFix-Portable.zip")
    args = parser.parse_args(argv)
    if args.tree is None and args.zip is None:
        parser.error("give --tree, --zip or both")
    problems = []
    if args.tree is not None:
        problems += [f"tree: {p}" for p in check_tree(args.tree)]
    if args.zip is not None:
        problems += [f"zip: {p}" for p in check_zip(args.zip)]
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"verify_release: {len(problems)} problem(s) - do not publish this build", file=sys.stderr)
        return 1
    print("verify_release: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
