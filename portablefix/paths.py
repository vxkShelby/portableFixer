import os
import sys
import tempfile
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def _raw_temp_dir() -> str:
    # os.environ["TEMP"] directly, not tempfile.gettempdir() - the latter
    # checks TMPDIR before TEMP, but the PowerShell side reads $env:TEMP
    # literally. If TMPDIR were ever set to a different directory than TEMP,
    # this function and the PowerShell command would silently reason about
    # two different roots - exactly the class of bug this module exists to
    # close, just via a different env var.
    return os.environ.get("TEMP") or tempfile.gettempdir()


def resolve_temp_root() -> Path | None:
    """Canonical %TEMP% root, or None if it can't be resolved."""
    try:
        return Path(_raw_temp_dir()).resolve()
    except OSError:
        return None


def resolve_windir_temp_root() -> Path | None:
    """Canonical %WINDIR%\\Temp root, or None if it can't be resolved."""
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if not windir:
        return None
    try:
        return (Path(windir) / "Temp").resolve()
    except OSError:
        return None


def _compute_protected_child(app_dir: Path, raw_root: Path, resolved_root: Path | None) -> Path | None:
    """Shared logic behind compute_temp_protected_child and its %WINDIR%\\Temp
    sibling.

    Both `app_dir` and `resolved_root` are already canonicalized with
    Path.resolve(), which asks the OS to resolve symlinks/junctions and
    rewrite any short (8.3) path alias to the real long-form path. That's
    what makes the result safe to compare against plain PowerShell path
    strings later - PowerShell doesn't have to reason about ancestry,
    trailing slashes, or short/long-path aliasing, because Python already
    normalized both sides before this was computed.

    Returns None when the app isn't under the root at all - the
    overwhelmingly common case (USB drive, Program Files, etc.): nothing to
    protect, the caller's wipe-everything-under-root action runs unmodified.

    Two situations have no single safe child to protect, and both are
    signalled the same way: returning `resolved_root` itself (never a valid
    "child to protect" otherwise, since a directory is never its own
    top-level child). Callers detect this by comparing the result against
    the matching resolve_*_root() and must refuse to run the wipe entirely
    rather than pick an unsafe child:

    1. `app_dir` resolves to the root itself - the whole root would have to
       be excluded.
    2. `raw_root` sits behind a reparse point (junction/symlink) - e.g. a
       user redirected %TEMP% to another drive with `mklink /J`, a common
       "move Temp off the SSD" tip. PowerShell's Get-ChildItem enumerates
       through the *raw* root path, so it reports children in unresolved
       (junction-prefixed) form; this function's answer is always resolved.
       Comparing a resolved protected-child against PowerShell's unresolved
       child path would silently never match, so if the app is actually
       found under the resolved root in this situation, refuse rather than
       hand back a comparison that's guaranteed to fail.
    """
    if resolved_root is None:
        return None
    try:
        app_resolved = app_dir.resolve()
    except OSError:
        return None
    if app_resolved == resolved_root:
        return resolved_root
    try:
        relative = app_resolved.relative_to(resolved_root)
    except ValueError:
        return None
    if str(raw_root).lower() != str(resolved_root).lower():
        return resolved_root
    return resolved_root / relative.parts[0]


def compute_temp_protected_child(app_dir: Path) -> Path | None:
    """The single top-level %TEMP% child that a %TEMP%-wiping action must
    never delete, because the running app lives inside it (or %TEMP% itself
    is redirected in a way that makes it unsafe to tell). See
    `_compute_protected_child` for the full explanation, including the two
    "refuse" sentinels.
    """
    return _compute_protected_child(app_dir, Path(_raw_temp_dir()), resolve_temp_root())


def compute_windir_temp_protected_child(app_dir: Path) -> Path | None:
    """Same as compute_temp_protected_child but rooted at %WINDIR%\\Temp -
    used by the system_temp cleanup action. %WINDIR%\\Temp needs admin
    rights to write to, so an app living there in practice is rare, but the
    protection shouldn't have a gap just because the case is unlikely.
    """
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if not windir:
        return None
    return _compute_protected_child(app_dir, Path(windir) / "Temp", resolve_windir_temp_root())


def _fallback_state_dirs() -> list[Path]:
    roots: list[Path] = []
    try:
        roots.append(Path(tempfile.gettempdir()))
    except OSError:
        # gettempdir() raises FileNotFoundError when none of its candidate
        # directories is usable - it must not end the search for a fallback.
        pass
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        roots.append(Path(local_appdata))
    try:
        roots.append(Path.home())
    except (OSError, RuntimeError):
        pass
    return [root / "PortableFix" for root in roots]


def _is_usable_dir(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # mkdir(exist_ok=True) succeeds on an existing read-only folder, so
        # actually write something before trusting it with logs/reports.
        with tempfile.TemporaryFile(dir=directory):
            pass
        return True
    except OSError:
        return False


def resolve_writable_base_dir(base_dir: Path) -> tuple[Path, bool]:
    probe = base_dir / ".write_test"
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return base_dir, False
    except OSError:
        pass
    # %TEMP% first (what the fallback banner promises), then per-user
    # locations: a redirected/dead TEMP on a broken machine used to be a
    # hard "Startup failed" even though the user profile was fine.
    candidates = _fallback_state_dirs()
    for fallback in candidates:
        if _is_usable_dir(fallback):
            return fallback, True
    tried = ", ".join(str(c) for c in candidates) or "%TEMP%"
    raise RuntimeError(
        f"Neither {base_dir} nor any fallback folder ({tried}) is writable. "
        "Check the TEMP environment variable."
    )
