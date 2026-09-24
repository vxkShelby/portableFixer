# tests/test_safe_delete.py
"""Elevated recursive deletes must never follow a junction, symlink or mount
point out of the folder being cleaned.

Windows PowerShell 5.1's `Remove-Item -Recurse` (and `Get-ChildItem -Recurse |
Remove-Item`) walks *into* directory reparse points, so a standard user who
plants `%WINDIR%\\Temp\\x -> C:\\Windows\\System32` (or pre-creates C:\\NVIDIA as a
junction) turns an administrator's cleanup into deleting System32 or another
user's files - the CVE-2026-55567 class that BleachBit fixed in 2026.

Every catalog command that deletes a folder tree therefore inlines one shared
helper, Remove-PfSafe (commands are standalone one-liners, so it cannot be
imported). It walks the tree itself, reads each entry's *own* attributes
([IO.File]::GetAttributes does not follow links), deletes a reparse point as
the link only (non-recursive [IO.Directory]::Delete / [IO.File]::Delete, which
remove the link and never touch the target) and never descends through one.
It returns how many entries it could not delete, which the commands keep
reporting as "skipped locked/in-use items". A root that is itself a link
(C:\\NVIDIA planted by a user) loses only the link; the takeown/icacls steps
of the Windows.old / upgrade-leftover actions skip such a root entirely.

Those two actions used to run `takeown /R` and `icacls /reset /T` on the
root. Microsoft's command reference only says takeown /R "performs a
recursive operation on all files in the specified directory and
subdirectories" and icacls /T "performs the operation on all specified
files in the current directory and its subdirectories"; neither says the
walk stops at a junction or symlink, and takeown has no switch to act on a
link itself (icacls has /L: "performs the operation on a symbolic link
itself versus its target"), so both are treated as following links. Any
authenticated user can create C:\\$WINDOWS.~BT (or C:\\Windows.old on a
machine without one) with junctions inside, so the
actions now (1) refuse - exit 1, nothing changed - unless the root is owned
by SYSTEM, TrustedInstaller or Administrators, read as a SID from Get-Acl
(never the localized account name), and (2) walk the tree themselves like
Remove-PfSafe and run the non-recursive `takeown /F <folder> /A` and
`icacls <folder> /reset /L /C /Q` once per real folder, never on a link.
Files keep their ACLs: owning and resetting the parent folder grants
Administrators "delete child", which lets Remove-PfSafe delete them.

It is still path-based: an attacker who swaps an already-checked folder for
a junction in the instant before a child is deleted could win a race.
Closing that needs handle-relative deletes (P/Invoke), which a one-line
catalog command cannot carry; the planted-link attack itself is closed.

The static half of this file keeps new raw recursive deletes out of the
catalog; the pwsh half runs the real catalog commands against a temp tree
holding a directory symlink (and, on Windows, a junction) to a "victim"
folder and checks the victim survives while the link itself is gone.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from portablefix.module_engine import load_module

MODULES_DIR = Path(__file__).resolve().parent.parent / "Modules"
STUB_GUARD_EXIT = 97

# The canonical helper, inlined verbatim at the start of every command that
# uses it. One line, Windows PowerShell 5.1 syntax. Notes on the choices:
# - iterative (explicit stack + post-order directory list), not recursive:
#   PowerShell's call depth limit would abort on deep node_modules trees;
# - a vanished entry is not a failure (another process cleaned it first);
# - a directory that is only "not empty" because a child failed is not
#   counted again - the child already was, as Remove-Item's count did;
#   nor is one that cannot be listed ($left starts at 1): the walk already
#   counted its failed enumeration;
# - read-only is cleared only on real entries (Remove-Item -Force did that
#   too), never on a reparse point, whose attribute calls could reach the
#   target.
SAFE_DELETE_HELPER = (
    "function Remove-PfSafe([string]$Path) { $n = 0; "
    "$i = Get-Item -LiteralPath $Path -Force -EA SilentlyContinue; if (-not $i) { return $n }; "
    "$ro = [IO.FileAttributes]::ReadOnly; $rp = [IO.FileAttributes]::ReparsePoint; $dir = [IO.FileAttributes]::Directory; "
    "$dirs = New-Object System.Collections.Generic.List[string]; $todo = New-Object System.Collections.Generic.Stack[string]; "
    "$todo.Push($i.FullName); "
    "while ($todo.Count -gt 0) { $p = $todo.Pop(); "
    "try { $a = [IO.File]::GetAttributes($p) } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] { continue } catch { $n++; continue }; "
    "if (($a -band $dir) -and -not ($a -band $rp)) { $dirs.Add($p); "
    "try { foreach ($c in [IO.Directory]::GetFileSystemEntries($p)) { $todo.Push($c) } } catch { $n++ }; continue }; "
    "try { if ($a -band $rp) { if ($a -band $dir) { [IO.Directory]::Delete($p) } else { [IO.File]::Delete($p) } } "
    "else { if ($a -band $ro) { [IO.File]::SetAttributes($p, ($a -bxor $ro)) }; [IO.File]::Delete($p) } } catch { $n++ } }; "
    "for ($k = $dirs.Count - 1; $k -ge 0; $k--) { $d = $dirs[$k]; "
    "try { $a = [IO.File]::GetAttributes($d); if (($a -band $ro) -and -not ($a -band $rp)) { [IO.File]::SetAttributes($d, ($a -bxor $ro)) }; "
    "[IO.Directory]::Delete($d) } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] { } "
    "catch { $left = 1; try { $left = @([IO.Directory]::GetFileSystemEntries($d)).Count } catch { }; if ($left -eq 0) { $n++ } } }; "
    "return $n }"
)

# Every catalog command that deletes a tree, i.e. must carry the helper.
SAFE_DELETE_ACTIONS = {
    ("m02_cleanup", "user_temp"),
    ("m02_cleanup", "system_temp"),
    ("m02_cleanup", "wer_reports"),
    ("m02_cleanup", "windows_update_cache"),
    ("m02_cleanup", "windows_old_removal"),
    ("m02_cleanup", "browser_cache_sweep"),
    ("m02_cleanup", "windows_upgrade_leftovers"),
    ("m02_cleanup", "gpu_driver_install_leftovers"),
    ("m02_cleanup", "directx_shader_cache"),
    ("m02_cleanup", "crash_dumps"),
    ("m05_windows_update", "wu_reset_cache"),
    ("m14_printing", "print_reset_print_system"),
}

# Raw recursive-delete shapes. A statement is cut at ; | } so a -Recurse
# further along (e.g. in a later Get-ChildItem) is not blamed on a delete.
_DELETE_VERB = r"(?:Remove-Item|ri|rm|del|erase|rd|rmdir)"
RAW_RECURSIVE_DELETE = [
    # Remove-Item ... -Recurse / rm -r / rd /s / del /s
    re.compile(r"(?i)(?:^|[\s;{(|])" + _DELETE_VERB + r"(?=\s)[^;|}]*?\s(?:-r\w*|/s)\b"),
    # Get-ChildItem -Recurse ... | Remove-Item: the enumeration walks the link
    re.compile(r"(?i)-Recurse\b[^;]*\|\s*(?:%|ForEach-Object\s*\{[^}]*)?(?<![\w$-])" + _DELETE_VERB + r"\b"),
    # [IO.Directory]::Delete(path, $true) and DirectoryInfo.Delete($true)
    re.compile(r"(?i)::Delete\([^)]*,\s*\$true\s*\)"),
    re.compile(r"(?i)\.Delete\(\s*\$true\s*\)"),
    # cmd's own recursive delete
    re.compile(r"(?i)\b(?:rd|rmdir|del|erase)\b[^;|]*\s/s\b"),
]
# The registry provider has no junctions to follow; these keys are HKLM/HKCU
# policy/CLSID paths removed on purpose.
REGISTRY_PATH = re.compile(r"(?i)\bHK(?:LM|CU|CR|U|CC):")


def _all_catalog_scripts():
    for yaml_path in sorted(MODULES_DIR.glob("*/actions.yaml")):
        module = load_module(yaml_path)
        for action in module.actions:
            for field in ("command", "preview_command", "undo_command"):
                script = getattr(action, field)
                if script:
                    yield module.module_id, action.id, field, script


def _raw_recursive_deletes(script: str) -> list[str]:
    body = script.replace(SAFE_DELETE_HELPER, "")
    hits = []
    for pattern in RAW_RECURSIVE_DELETE:
        for m in pattern.finditer(body):
            statement_start = max(body.rfind(";", 0, m.start()), 0)
            statement_end = body.find(";", m.end())
            statement = body[statement_start : statement_end if statement_end != -1 else len(body)]
            if not REGISTRY_PATH.search(statement):
                hits.append(m.group(0).strip())
    return hits


@pytest.mark.parametrize(
    "snippet",
    [
        'Remove-Item "$env:TEMP\\*" -Recurse -Force',
        "Get-ChildItem $p -Force | Remove-Item -Recurse -Force -EA SilentlyContinue",
        "Get-ChildItem $p -Recurse -Force | Remove-Item -Force",
        "Get-ChildItem $p -Recurse | ForEach-Object { Remove-Item $_.FullName -Force }",
        "rm -r $p",
        "cmd /c rd /s /q C:\\NVIDIA",
        "cmd /c del /s /q C:\\x\\*",
        "[IO.Directory]::Delete($p, $true)",
        "(Get-Item $p).Delete($true)",
    ],
)
def test_static_check_catches_raw_recursive_delete_shapes(snippet):
    assert _raw_recursive_deletes(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        SAFE_DELETE_HELPER + "; $skipped += Remove-PfSafe $p",
        "Remove-Item 'HKLM:\\SOFTWARE\\Policies\\Google\\Chrome' -Recurse -Force -EA SilentlyContinue",
        "$f = Get-ChildItem $p -Recurse -File -Force -EA SilentlyContinue; $c = $f.Count",
        'Remove-Item "$env:WINDIR\\Prefetch\\*" -Force -EA SilentlyContinue',
        "Get-ChildItem $p -Recurse -File | ForEach-Object { $_.Word }",
    ],
)
def test_static_check_leaves_safe_shapes_alone(snippet):
    assert _raw_recursive_deletes(snippet) == []


def test_no_catalog_command_uses_a_raw_recursive_delete():
    offenders = [
        f"{module_id}/{action_id}.{field}: {hit}"
        for module_id, action_id, field, script in _all_catalog_scripts()
        for hit in _raw_recursive_deletes(script)
    ]
    assert offenders == [], "use the inlined Remove-PfSafe helper instead:\n" + "\n".join(offenders)


def test_every_copy_of_the_helper_is_the_canonical_one_at_the_start():
    # A drifted copy (someone "fixing" one action only) would silently lose
    # the guarantee this file tests, so all copies must be byte-identical.
    users = set()
    for module_id, action_id, field, script in _all_catalog_scripts():
        if "Remove-PfSafe" not in script:
            continue
        users.add((module_id, action_id))
        assert field == "command", (module_id, action_id, field)
        assert script.startswith(SAFE_DELETE_HELPER + "; "), (module_id, action_id)
        assert script.count("function Remove-PfSafe") == 1, (module_id, action_id)
        assert "Remove-PfSafe" in script[len(SAFE_DELETE_HELPER) :], (module_id, action_id)
    assert users == SAFE_DELETE_ACTIONS


def test_takeown_and_icacls_never_run_through_a_planted_root_link():
    # takeown and icacls on a root that is a junction would re-own and
    # re-ACL whatever it points at before any delete happens, so the
    # reparse-point check must come first and guard both (the per-folder
    # walk they now run in is exercised below).
    for module_id, action_id in (("m02_cleanup", "windows_old_removal"), ("m02_cleanup", "windows_upgrade_leftovers")):
        command = next(a for a in load_module(MODULES_DIR / module_id / "actions.yaml").actions if a.id == action_id).command
        body = command[command.index("$trusted = ") :]
        guard = body.index("[IO.FileAttributes]::ReparsePoint")
        assert guard < body.index("Get-PfOwnerSid $i.FullName") < body.index("Grant-PfAdminTree $i.FullName"), action_id
        assert body.count("Grant-PfAdminTree") == 1, action_id


# --- pwsh: parse + behaviour ------------------------------------------------


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _command(module_dir: str, action_id: str) -> str:
    module = load_module(MODULES_DIR / module_dir / "actions.yaml")
    return next(a for a in module.actions if a.id == action_id).command


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Same checker as the m08/m19 catalog tests: every script parses and none uses
# PS7-only syntax that pwsh accepts but Windows PowerShell 5.1 rejects.
PARSE_CHECKER = (
    "$s = Get-Content -Raw -Encoding UTF8 -LiteralPath $env:PFSCRIPTS_FILE | ConvertFrom-Json; "
    "foreach ($p in $s.PSObject.Properties) { $t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseInput($p.Value, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $p.Name + ': ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output ('ERR ' + $p.Name + ': PowerShell 7-only syntax') } }; "
    "Write-Output PARSE_DONE"
)


def test_safe_delete_commands_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {f"{m}.{a}": _command(m, a) for m, a in sorted(SAFE_DELETE_ACTIONS)}
    scripts["helper"] = SAFE_DELETE_HELPER
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=dict(os.environ, PFSCRIPTS_FILE=str(scripts_file)),
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


OLD = time.time() - 30 * 86400  # past the temp actions' 3-day cutoff


def _link(link: Path, target: Path, kind: str) -> None:
    """Directory link `link` -> `target`: a symlink everywhere, a junction
    (mount-point reparse point, no privilege needed) on Windows only."""
    if kind == "junction":
        if sys.platform != "win32":
            pytest.skip("junctions exist only on Windows")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode != 0 or not link.exists():
            pytest.skip("cannot create a junction here")
        return
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlinks here (Windows without Developer Mode / privilege)")


def _backdate_link(link: Path) -> None:
    """Age the link itself, not its target: the temp actions only delete
    entries older than their cutoff, and enumeration reports a reparse
    point's own timestamps. os.utime(follow_symlinks=False) needs
    utimensat/lutimes, which Windows CPython lacks (NotImplementedError), so
    Windows opens the reparse point itself and calls SetFileTime - that
    covers junctions too."""
    if sys.platform != "win32":
        if os.utime not in os.supports_follow_symlinks:
            pytest.skip("cannot set a link's own timestamp here")
        os.utime(link, (OLD, OLD), follow_symlinks=False)
        return
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    FILE_WRITE_ATTRIBUTES, OPEN_EXISTING = 0x100, 3
    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT = 0x02000000, 0x00200000
    share = 1 | 2 | 4  # read | write | delete
    h = k32.CreateFileW(str(link), FILE_WRITE_ATTRIBUTES, share, None, OPEN_EXISTING,
                        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        ft = int(OLD * 10_000_000) + 116444736000000000  # FILETIME: 100ns ticks since 1601
        ftime = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
        if not k32.SetFileTime(h, None, ctypes.byref(ftime), ctypes.byref(ftime)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(h)


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True)
    (victim / "keep.txt").write_text("precious", encoding="utf-8")
    (victim / "sub" / "deep.txt").write_text("precious too", encoding="utf-8")
    os.utime(victim, (OLD, OLD))
    return victim


def _assert_victim_intact(victim: Path) -> None:
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "precious"
    assert (victim / "sub" / "deep.txt").read_text(encoding="utf-8") == "precious too"


def _plant(root: Path, victim: Path, kind: str) -> list[Path]:
    """What a standard user leaves in a folder an admin later cleans: an old
    ordinary folder with a link to the victim buried inside (plus a file
    link), and a link straight at the top level. Returns the planted
    top-level entries; the caller asserts they are gone."""
    root.mkdir(parents=True, exist_ok=True)
    junk = root / "junk"
    (junk / "nested" / "deeper").mkdir(parents=True)
    (junk / "a.tmp").write_text("x", encoding="utf-8")
    (junk / "nested" / "deeper" / "b.tmp").write_text("x", encoding="utf-8")
    readonly = junk / "nested" / "ro.tmp"
    readonly.write_text("x", encoding="utf-8")
    os.chmod(readonly, 0o444)
    _link(junk / "nested" / "to_victim", victim, kind)
    if kind == "symlink":
        _link(junk / "file_link.txt", victim / "keep.txt", kind)
    for d in (junk / "nested" / "deeper", junk / "nested", junk):
        os.utime(d, (OLD, OLD))
    top = root / "top_link"
    _link(top, victim, kind)
    _backdate_link(top)
    return [junk, top]


# pwsh 7 no longer follows links in Remove-Item -Recurse, so on the Linux/pwsh
# test runs the pre-fix commands would pass by luck. This stand-in behaves the
# way Windows PowerShell 5.1 does - it enumerates *through* a directory link
# and deletes the target's contents before unlinking - so every catalog run
# below proves the command no longer relies on Remove-Item for trees at all.
PS51_REMOVE_ITEM = (
    "function __pf51Remove([string]$p, [bool]$rec) { "
    "if ($rec -and (Test-Path -LiteralPath $p -PathType Container)) { "
    "foreach ($c in @(Get-ChildItem -LiteralPath $p -Force -EA SilentlyContinue)) { __pf51Remove $c.FullName $rec } }; "
    "try { if (Test-Path -LiteralPath $p -PathType Container) { [IO.Directory]::Delete($p) } else { [IO.File]::Delete($p) } } catch { } }; "
    "function Remove-Item { [CmdletBinding()] param([Parameter(Position = 0)] [string[]] $Path, [string[]] $LiteralPath, "
    "[Parameter(ValueFromPipeline = $true)] $InputObject, [switch] $Recurse, [switch] $Force) "
    "process { $all = @(); if ($InputObject) { $all += $InputObject.FullName }; "
    "foreach ($x in @($Path)) { if ($x) { $all += @(Get-Item -Path $x -Force -EA SilentlyContinue | ForEach-Object { $_.FullName }) } }; "
    "foreach ($x in @($LiteralPath)) { if ($x) { $all += $x } }; "
    "foreach ($x in $all) { __pf51Remove $x $Recurse.IsPresent } } }; "
    f"if ((Get-Command Remove-Item).CommandType -ne 'Function') {{ exit {STUB_GUARD_EXIT} }}"
)


def test_the_ps51_stand_in_really_deletes_through_links(tmp_path):
    # Control for every catalog test below: the harness must be able to see
    # the bug, or a green run proves nothing.
    victim = _victim(tmp_path)
    tree = tmp_path / "tree"
    _plant(tree, victim, "symlink")
    result, _ = _run(tmp_path, f"Remove-Item {_ps_quote(str(tree))} -Recurse -Force -EA SilentlyContinue", {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (victim / "keep.txt").exists()


def _stub_prelude(log: Path, names, c_drive: Path | None) -> str:
    """Every system-changing command becomes a logging no-op function, and the
    script refuses to run (exit 97) unless each name really resolves to it.
    Remove-Item always becomes the Windows PowerShell 5.1 stand-in.
    c_drive maps the hard-coded C:\\ paths onto a temp folder (non-Windows
    only - there is no real C: to hit there)."""
    lg = _ps_quote(str(log))
    parts = [
        f"function {n} {{ Add-Content -LiteralPath {lg} -Value ('{n} ' + ($args -join ' ')); $global:LASTEXITCODE = 0 }}"
        for n in names
    ]
    if names:
        quoted = ", ".join(_ps_quote(n) for n in names)
        parts.append(
            f"foreach ($n in @({quoted})) {{ if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') {{ exit {STUB_GUARD_EXIT} }} }}"
        )
    if c_drive is not None:
        parts.append(f"$null = New-PSDrive -Name C -PSProvider FileSystem -Root {_ps_quote(str(c_drive))}")
    parts.append(PS51_REMOVE_ITEM)
    return "; ".join(parts) + "; "


def _run(tmp_path: Path, script: str, env_dirs: dict, stubs=(), c_drive: Path | None = None):
    log = tmp_path / "calls.log"
    log.write_text("", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir(exist_ok=True)
    # The redirects are set inside the script, not in the child's environment:
    # Windows PowerShell itself needs the real SystemRoot/WINDIR to start.
    redirects = "".join(f"$env:{k} = {_ps_quote(str(v))}; " for k, v in env_dirs.items())
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + _stub_prelude(log, list(stubs), c_drive) + redirects + script
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
    return result, log.read_text(encoding="utf-8-sig").splitlines()


LINK_KINDS = ["symlink", "junction"]
non_windows_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the command hard-codes C:\\ paths - only safe to run where C: can be faked"
)


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_helper_removes_the_links_but_never_their_target(tmp_path, kind):
    victim = _victim(tmp_path)
    tree = tmp_path / "tree"
    planted = _plant(tree, victim, kind)
    root_link = tmp_path / "root_link"
    _link(root_link, victim, kind)
    script = (
        SAFE_DELETE_HELPER
        + f"; $a = Remove-PfSafe {_ps_quote(str(tree))}; $b = Remove-PfSafe {_ps_quote(str(root_link))}; "
        + f"$c = Remove-PfSafe {_ps_quote(str(tmp_path / 'does_not_exist'))}; Write-Output ('RESULT ' + $a + ' ' + $b + ' ' + $c)"
    )
    result, _ = _run(tmp_path, script, {})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT 0 0 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted + [tree, root_link]:
        assert not os.path.lexists(p), p


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_backdating_ages_the_link_and_leaves_its_target_alone(tmp_path, kind):
    # The cutoff-filtered temp actions only see a planted top-level link if
    # it is old; this proves _plant really ages the link itself (and, on
    # Windows, that the SetFileTime path works for symlinks and junctions).
    target = tmp_path / "target"
    target.mkdir()
    before = os.stat(target).st_mtime
    link = tmp_path / "link"
    _link(link, target, kind)
    _backdate_link(link)
    assert abs(os.lstat(link).st_mtime - OLD) < 5
    assert os.stat(target).st_mtime == before


def test_helper_survives_a_tree_deeper_than_the_powershell_call_depth(tmp_path):
    # A recursive helper would hit PowerShell's call depth limit on deep
    # trees (node_modules, nested archives) - the helper is iterative.
    deep = tmp_path / "deep"
    p = deep
    for i in range(120):
        p = p / "d"
    p.mkdir(parents=True)
    (p / "leaf.txt").write_text("x", encoding="utf-8")
    script = SAFE_DELETE_HELPER + f"; Write-Output ('RESULT ' + (Remove-PfSafe {_ps_quote(str(deep))}))"
    result, _ = _run(tmp_path, script, {})
    assert "RESULT 0" in result.stdout, result.stdout + result.stderr
    assert not deep.exists()


# --- the real catalog commands ------------------------------------------------


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_user_temp_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    temp = tmp_path / "Temp"
    planted = _plant(temp, victim, kind)
    fresh = temp / "fresh.tmp"
    fresh.write_text("recent - kept by the 3-day rule", encoding="utf-8")
    result, _ = _run(tmp_path, _command("m02_cleanup", "user_temp"), {"TEMP": temp})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert fresh.exists()


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_system_temp_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    windir = tmp_path / "Windows"
    planted = _plant(windir / "Temp", victim, kind)
    result, _ = _run(tmp_path, _command("m02_cleanup", "system_temp"), {"WINDIR": windir})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_windows_update_cache_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    windir = tmp_path / "Windows"
    download = windir / "SoftwareDistribution" / "Download"
    planted = _plant(download, victim, kind)
    result, calls = _run(
        tmp_path, _command("m02_cleanup", "windows_update_cache"), {"WINDIR": windir}, stubs=("Stop-Service", "Start-Service")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0; service restart: OK" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert download.is_dir()
    assert [c.split()[0] for c in calls] == ["Stop-Service", "Start-Service"]


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_browser_cache_sweep_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    local = tmp_path / "AppData" / "Local"
    chrome = local / "Google" / "Chrome" / "User Data" / "Default" / "Cache"
    firefox = local / "Mozilla" / "Firefox" / "Profiles" / "abcd.default" / "cache2"
    planted = _plant(chrome, victim, kind) + _plant(firefox, victim, kind)
    result, _ = _run(tmp_path, _command("m02_cleanup", "browser_cache_sweep"), {"LOCALAPPDATA": local})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Swept 2 cache folder(s), skipped/locked: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert chrome.is_dir() and firefox.is_dir()


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_directx_shader_cache_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    local = tmp_path / "AppData" / "Local"
    planted = _plant(local / "D3DSCache", victim, kind)
    result, _ = _run(tmp_path, _command("m02_cleanup", "directx_shader_cache"), {"LOCALAPPDATA": local})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cleared 1 shader cache folder(s), skipped/locked: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_crash_dumps_keeps_the_victim_behind_a_planted_link(tmp_path, kind):
    victim = _victim(tmp_path)
    root = tmp_path / "Windows"
    (root / "Minidump").mkdir(parents=True)
    (root / "Minidump" / "092426-1.dmp").write_text("x", encoding="utf-8")
    (root / "Minidump" / "notes.txt").write_text("not a dump", encoding="utf-8")
    (root / "MEMORY.DMP").write_text("x", encoding="utf-8")
    planted = _plant(root / "LiveKernelReports", victim, kind)
    result, _ = _run(tmp_path, _command("m02_cleanup", "crash_dumps"), {"SystemRoot": root})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert not (root / "Minidump" / "092426-1.dmp").exists()
    assert not (root / "MEMORY.DMP").exists()
    # Only *.dmp in Minidump, and the folders themselves stay.
    assert (root / "Minidump" / "notes.txt").exists()
    assert (root / "LiveKernelReports").is_dir()


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_wu_reset_cache_removes_a_stale_bak_without_following_its_links(tmp_path, kind):
    victim = _victim(tmp_path)
    windir = tmp_path / "Windows"
    for live, bak in (
        (windir / "SoftwareDistribution", windir / "SoftwareDistribution.bak"),
        (windir / "System32" / "catroot2", windir / "System32" / "catroot2.bak"),
    ):
        live.mkdir(parents=True)
        (live / "live.marker").write_text("x", encoding="utf-8")
        _plant(bak, victim, kind)
    result, _ = _run(tmp_path, _command("m05_windows_update", "wu_reset_cache"), {"WINDIR": windir})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SoftwareDistribution and catroot2 reset." in result.stdout
    _assert_victim_intact(victim)
    # The old .bak (with its planted links) is gone; the new .bak is the
    # folder that was just renamed.
    for bak in (windir / "SoftwareDistribution.bak", windir / "System32" / "catroot2.bak"):
        assert sorted(os.listdir(bak)) == ["live.marker"], bak


@pytest.mark.parametrize("kind", LINK_KINDS)
def test_print_reset_clears_the_spool_folder_without_following_links(tmp_path, kind):
    victim = _victim(tmp_path)
    windir = tmp_path / "Windows"
    printers = windir / "System32" / "spool" / "PRINTERS"
    planted = _plant(printers, victim, kind)
    (printers / "00012.SPL").write_text("job", encoding="utf-8")
    result, calls = _run(
        tmp_path,
        _command("m14_printing", "print_reset_print_system"),
        {"WINDIR": windir},
        stubs=("Stop-Service", "Start-Service", "Get-Printer", "Remove-Printer"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All printers removed and spool queue cleared." in result.stdout
    assert calls[:2] == ["Stop-Service -Name Spooler -Force -EA Stop", "Start-Service -Name Spooler -EA Stop"]
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert os.listdir(printers) == []


@non_windows_only
def test_gpu_leftovers_planted_as_a_root_link_only_lose_the_link(tmp_path):
    # C:\ lets any authenticated user create folders, so C:\NVIDIA can be a
    # link planted by a standard user before the admin ever runs this.
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    c_drive.mkdir()
    _link(c_drive / "NVIDIA", victim, "symlink")
    planted = _plant(c_drive / "AMD", victim, "symlink")
    result, _ = _run(tmp_path, _command("m02_cleanup", "gpu_driver_install_leftovers"), {}, c_drive=c_drive)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Removed 2 GPU driver install folder(s), skipped/locked: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    assert not os.path.lexists(c_drive / "NVIDIA")
    assert not os.path.lexists(c_drive / "AMD")
    for p in planted:
        assert not os.path.lexists(p), p


SYSTEM_SID = "S-1-5-18"
TRUSTED_INSTALLER_SID = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
ADMINISTRATORS_SID = "S-1-5-32-544"
STANDARD_USER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


def _owner_stub(log: Path, owners: dict) -> str:
    """Get-Acl stand-in (pwsh has none off Windows): the owner of a folder
    is looked up by its last path segment in `owners` (None or missing =
    Get-Acl fails, as on a folder the admin cannot read). Each call is
    logged with the identity type asked for, so the tests see the owner is
    read as a SID - a localized account name is never compared."""
    table = "; ".join(f"{_ps_quote(k)} = {_ps_quote(v)}" for k, v in owners.items() if v)
    lg = _ps_quote(str(log))
    return (
        f"$global:PfOwners = @{{ {table} }}; "
        "function Get-Acl { [CmdletBinding()] param([string]$LiteralPath) "
        f"Add-Content -LiteralPath {lg} -Value ('Get-Acl ' + $LiteralPath); "
        "$leaf = Split-Path -Leaf $LiteralPath; "
        "if (-not $global:PfOwners.ContainsKey($leaf)) { throw 'access denied' }; "
        "$o = New-Object psobject; $o | Add-Member -MemberType NoteProperty -Name PfSid -Value $global:PfOwners[$leaf]; "
        "$o | Add-Member -MemberType ScriptMethod -Name GetOwner -Value { param($t) "
        f"Add-Content -LiteralPath {lg} -Value ('GetOwner ' + $t.FullName); "
        "[pscustomobject]@{ Value = $this.PfSid } }; $o }; "
    )


def _owner_calls(calls: list[str]) -> list[str]:
    return [c for c in calls if c.split()[0] in ("takeown", "icacls")]


@non_windows_only
def test_windows_old_as_a_root_link_is_unlinked_without_takeown_or_icacls(tmp_path):
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    c_drive.mkdir()
    _link(c_drive / "Windows.old", victim, "symlink")
    log = tmp_path / "calls.log"
    result, calls = _run(
        tmp_path,
        _owner_stub(log, {"Windows.old": TRUSTED_INSTALLER_SID}) + _command("m02_cleanup", "windows_old_removal"),
        {},
        stubs=("takeown", "icacls"),
        c_drive=c_drive,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    assert not os.path.lexists(c_drive / "Windows.old")
    # A link is not asked for its owner either - Get-Acl would follow it.
    assert calls == []


@pytest.mark.parametrize("owner", [SYSTEM_SID, TRUSTED_INSTALLER_SID, ADMINISTRATORS_SID])
@non_windows_only
def test_windows_old_with_old_profile_links_inside_keeps_their_targets(tmp_path, owner):
    # A real Windows.old holds the old profiles' compatibility junctions
    # ("Application Data", "My Documents", ...) - deleting through them is
    # exactly how PS 5.1's Remove-Item -Recurse would reach live data, and
    # takeown /R / icacls /T could re-own and re-ACL their targets.
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    old = c_drive / "Windows.old"
    planted = _plant(old, victim, "symlink")
    log = tmp_path / "calls.log"
    result, calls = _run(
        tmp_path,
        _owner_stub(log, {"Windows.old": owner}) + _command("m02_cleanup", "windows_old_removal"),
        {},
        stubs=("takeown", "icacls"),
        c_drive=c_drive,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted:
        assert not os.path.lexists(p), p
    assert not os.path.lexists(c_drive / "Windows.old")
    assert calls[:2] == [f"Get-Acl {c_drive / 'Windows.old'}", "GetOwner System.Security.Principal.SecurityIdentifier"]
    owned = _owner_calls(calls)
    # One non-recursive takeown + icacls per real folder, parents first, and
    # never on a link (top_link, to_victim) or anything behind one.
    real_dirs = ["Windows.old", "junk", "nested", "deeper"]
    assert len(owned) == 2 * len(real_dirs), owned
    for i, name in enumerate(real_dirs):
        take, acl = owned[2 * i], owned[2 * i + 1]
        assert take.startswith("takeown /F ") and take.endswith(f"{name} /A"), take
        assert acl.startswith("icacls ") and acl.endswith(f"{name} /reset /L /C /Q"), acl
    assert owned[0] == f"takeown /F {old} /A"
    for c in owned:
        assert "victim" not in c and "top_link" not in c, c
        assert " /R" not in c and " /T" not in c and " /D" not in c, c


@pytest.mark.parametrize("owner", [STANDARD_USER_SID, None])
@non_windows_only
def test_windows_old_owned_by_a_standard_user_is_refused_untouched(tmp_path, owner):
    # Any authenticated user can create C:\Windows.old on a machine that
    # has none. Its owner is then that user (or unreadable): refused, with
    # no ownership change and no delete, and the action fails visibly.
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    old = c_drive / "Windows.old"
    planted = _plant(old, victim, "symlink")
    log = tmp_path / "calls.log"
    result, calls = _run(
        tmp_path,
        _owner_stub(log, {"Windows.old": owner}) + _command("m02_cleanup", "windows_old_removal"),
        {},
        stubs=("takeown", "icacls"),
        c_drive=c_drive,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused: C:\\Windows.old is owned by " + (owner or "an unreadable owner") in result.stdout, result.stdout
    assert "not SYSTEM, TrustedInstaller or Administrators" in result.stdout
    assert "Skipped locked/in-use items" not in result.stdout
    assert _owner_calls(calls) == []
    _assert_victim_intact(victim)
    for p in planted:
        assert os.path.lexists(p), p


@non_windows_only
def test_windows_upgrade_leftovers_never_take_ownership_through_a_root_link(tmp_path):
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    c_drive.mkdir()
    _link(c_drive / "$WINDOWS.~BT", victim, "symlink")
    planted = _plant(c_drive / "$WINDOWS.~WS", victim, "symlink")
    windir = tmp_path / "Windows"
    windir.mkdir()
    log = tmp_path / "calls.log"
    result, calls = _run(
        tmp_path,
        _owner_stub(log, {"$WINDOWS.~WS": SYSTEM_SID, "$WINDOWS.~BT": SYSTEM_SID})
        + _command("m02_cleanup", "windows_upgrade_leftovers"),
        {"WINDIR": windir},
        stubs=("takeown", "icacls"),
        c_drive=c_drive,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Processed 2 leftover folder(s), skipped/locked: 0" in result.stdout, result.stdout + result.stderr
    assert "Refused" not in result.stdout
    _assert_victim_intact(victim)
    assert not os.path.lexists(c_drive / "$WINDOWS.~BT")
    assert not os.path.lexists(c_drive / "$WINDOWS.~WS")
    for p in planted:
        assert not os.path.lexists(p), p
    # Only the real folder and its real subfolders got takeown/icacls, never
    # the root link to the victim nor the links inside ~WS.
    owned = _owner_calls(calls)
    assert len(owned) == 2 * 4, owned
    assert all("WINDOWS.~WS" in c for c in owned), owned
    assert all("victim" not in c and "top_link" not in c for c in owned), owned
    assert [c for c in calls if c.startswith("Get-Acl")] == [f"Get-Acl {c_drive / '$WINDOWS.~WS'}"]


@non_windows_only
def test_windows_upgrade_leftovers_refuse_a_planted_folder_and_still_clean_the_rest(tmp_path):
    # C:\$WINDOWS.~BT pre-created by a standard user (junctions inside) is
    # left exactly as it is; the genuine WinREAgent is still cleaned, and
    # the action exits 1 naming the refused folder.
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    c_drive.mkdir()
    bt = c_drive / "$WINDOWS.~BT"
    planted = _plant(bt, victim, "symlink")
    windir = tmp_path / "Windows"
    agent = windir / "WinREAgent"
    (agent / "Scratch").mkdir(parents=True)
    (agent / "Scratch" / "x.wim").write_text("x", encoding="utf-8")
    log = tmp_path / "calls.log"
    result, calls = _run(
        tmp_path,
        _owner_stub(log, {"$WINDOWS.~BT": STANDARD_USER_SID, "WinREAgent": TRUSTED_INSTALLER_SID})
        + _command("m02_cleanup", "windows_upgrade_leftovers"),
        {"WINDIR": windir},
        stubs=("takeown", "icacls"),
        c_drive=c_drive,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Processed 1 leftover folder(s), skipped/locked: 0" in result.stdout, result.stdout + result.stderr
    assert f"C:\\$WINDOWS.~BT (owner {STANDARD_USER_SID})" in result.stdout, result.stdout
    assert "Refused, not owned by SYSTEM, TrustedInstaller or Administrators" in result.stdout
    _assert_victim_intact(victim)
    for p in planted:
        assert os.path.lexists(p), p
    assert not agent.exists()
    owned = _owner_calls(calls)
    assert owned and all("WinREAgent" in c for c in owned), owned


@pytest.mark.parametrize("action_id", ["windows_old_removal", "windows_upgrade_leftovers"])
@non_windows_only
def test_owner_check_really_calls_get_acl_by_sid_type(tmp_path, action_id):
    # Control for the stub: the command itself (not the stub) must hand
    # GetOwner the SecurityIdentifier type. Without a Get-Acl at all
    # (pwsh off Windows) the owner is unreadable and the folder refused.
    c_drive = tmp_path / "C"
    target = c_drive / ("Windows.old" if action_id == "windows_old_removal" else "$WINDOWS.~BT")
    (target / "sub").mkdir(parents=True)
    windir = tmp_path / "Windows"
    windir.mkdir()
    result, calls = _run(
        tmp_path, _command("m02_cleanup", action_id), {"WINDIR": windir}, stubs=("takeown", "icacls"), c_drive=c_drive
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused" in result.stdout, result.stdout
    assert calls == []
    assert (target / "sub").is_dir()


@non_windows_only
def test_wer_reports_keeps_the_victim_behind_a_planted_link(tmp_path):
    victim = _victim(tmp_path)
    c_drive = tmp_path / "C"
    wer = c_drive / "ProgramData" / "Microsoft" / "Windows" / "WER"
    planted = _plant(wer / "ReportQueue", victim, "symlink")
    _link(wer / "ReportArchive", victim, "symlink")
    local = tmp_path / "AppData" / "Local"
    planted += _plant(local / "Microsoft" / "Windows" / "WER" / "ReportQueue", victim, "symlink")
    result, _ = _run(tmp_path, _command("m02_cleanup", "wer_reports"), {"LOCALAPPDATA": local}, c_drive=c_drive)
    assert result.returncode == 0, result.stdout + result.stderr
    # The absent per-user ReportArchive is not a "skipped" item.
    assert "Skipped locked/in-use items: 0" in result.stdout, result.stdout + result.stderr
    _assert_victim_intact(victim)
    for p in planted + [wer / "ReportQueue", wer / "ReportArchive"]:
        assert not os.path.lexists(p), p
