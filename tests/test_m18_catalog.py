import csv
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from portablefix.models import ModuleCategory, RiskLevel
from portablefix.module_engine import load_module

CATALOG_PATH = Path(__file__).resolve().parent.parent / "Modules" / "m18_user_backup" / "actions.yaml"


def test_m18_catalog_loads_5_actions_in_repair_category():
    module = load_module(CATALOG_PATH)
    assert module.module_id == "m18_user_backup"
    assert module.category == ModuleCategory.REPAIR
    assert len(module.actions) == 5


def test_m18_catalog_risk_distribution():
    module = load_module(CATALOG_PATH)
    by_risk = {}
    for action in module.actions:
        by_risk.setdefault(action.risk, []).append(action.id)
    assert set(by_risk[RiskLevel.SAFE]) == {"backup_list_existing", "backup_verify_external"}
    assert set(by_risk[RiskLevel.MODERATE]) == {
        "backup_user_folders",
        "backup_restore_latest",
        "backup_user_data_external",
    }
    assert RiskLevel.DESTRUCTIVE not in by_risk
    assert RiskLevel.REQUIRES_REBOOT not in by_risk


def test_m18_catalog_only_backup_creation_has_undo_and_preview():
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert by_id["backup_user_folders"].undo_command is not None
    assert by_id["backup_user_folders"].preview_command is not None
    assert by_id["backup_list_existing"].undo_command is None
    assert by_id["backup_restore_latest"].undo_command is None


def test_m18_catalog_robocopy_exit_code_normalized_to_strict_zero():
    # robocopy's own exit codes 0-7 are success (bitflags), only 8+ is a real
    # failure - the command must translate that before the app's strict
    # exit_code == 0 check treats a normal robocopy run as a failure.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "backup_user_folders")
    assert "$LASTEXITCODE -ge 8" in action.command


def test_m18_catalog_covers_expected_ids():
    module = load_module(CATALOG_PATH)
    ids = {a.id for a in module.actions}
    assert ids == {
        "backup_user_folders",
        "backup_list_existing",
        "backup_restore_latest",
        "backup_user_data_external",
        "backup_verify_external",
    }


def test_m18_catalog_every_action_has_both_language_labels_and_descriptions():
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        assert action.label_sk
        assert action.label_en
        assert action.description_sk
        assert action.description_en


def test_m18_catalog_backup_folder_lockdown_grants_the_current_user_too():
    # An Administrators+SYSTEM-only ACL breaks non-elevated writes/deletes on
    # this same folder, since a nominally-admin but non-elevated user gets a
    # filtered token where that group membership is deny-only for ACL checks.
    module = load_module(CATALOG_PATH)
    action = next(a for a in module.actions if a.id == "backup_user_folders")
    assert "icacls" in action.command
    assert "S-1-5-32-544" in action.command
    assert "S-1-5-18" in action.command
    assert "$env:USERDOMAIN" in action.command
    # Must be unconditional (icacls is idempotent) so an already-existing
    # unhardened root from an older install gets fixed too, not just a
    # freshly created one.
    assert 'icacls $root /inheritance:r' in action.command
    assert "Test-Path $root" not in action.command


def test_m18_catalog_restore_paths_check_robocopy_exit_code_too():
    # The forward backup command already translates robocopy's bitflag exit
    # codes (0-7 = success, 8+ = real failure) - both restore paths
    # (backup_user_folders' undo, and the standalone backup_restore_latest)
    # run the identical robocopy call and must apply the same check, not
    # just claim "Restored..." unconditionally.
    module = load_module(CATALOG_PATH)
    by_id = {a.id: a for a in module.actions}
    assert "$LASTEXITCODE -ge 8" in by_id["backup_user_folders"].undo_command
    assert "$LASTEXITCODE -ge 8" in by_id["backup_restore_latest"].command


def test_m18_catalog_restore_and_external_backup_exclude_from_select_all():
    # backup_restore_latest is a standalone "restore an old backup" recovery
    # action, and backup_user_data_external needs PortableFix on an external
    # drive and can run for hours - neither may fire as a side effect of
    # "select all" for this category, only when checked deliberately.
    module = load_module(CATALOG_PATH)
    for action in module.actions:
        expected = action.id in {"backup_restore_latest", "backup_user_data_external"}
        assert action.exclude_from_select_all is expected, action.id


# --- G14: backup to another drive with a SHA-256 manifest, and its verify ---
#
# The commands run below in PowerShell against stubs: robocopy, the volume
# lookups (Get-Partition, Get-CimInstance), the Shell Folders registry read
# and Get-FileHash are functions (functions win command lookup), and the
# script exits 97 unless each name really resolves to the stub. The
# PortableFix drive is a PSDrive E: rooted in tmp_path and the script starts
# there, the same as the frozen app starts every PowerShell in its install
# root. The robocopy stub prints Slovak lines on purpose - nothing may key
# off them.

BACKUP_ID = "backup_user_data_external"
VERIFY_ID = "backup_verify_external"
USF_KEY = "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders"
STUB_GUARD_EXIT = 97


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


def _action(action_id):
    return next(a for a in load_module(CATALOG_PATH).actions if a.id == action_id)


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


class Box:
    """A fake PC: profile folders, browser profiles and the PortableFix drive."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.drive = tmp_path / "E"
        self.profile = tmp_path / "profile"
        self.local = tmp_path / "local"
        self.roaming = tmp_path / "roaming"
        for p in (self.drive, self.profile, self.local, self.roaming):
            p.mkdir()
        self.log = tmp_path / "calls.log"
        self.log.write_text("", encoding="utf-8")

    def backups(self) -> list[Path]:
        base = self.drive / "PortableFix_Backups"
        return sorted(base.iterdir()) if base.exists() else []

    def calls(self, prefix: str) -> list[str]:
        return [c for c in self.log.read_text(encoding="utf-8-sig").splitlines() if c.startswith(prefix)]

    def run(self, command: str, *, system_drive="C:", partitions=None, free_space=500 * 1024**3,
            usf=None, robocopy_exit=None, location="E:\\", computer="PC1"):
        """partitions: drive letter -> disk number (a missing letter makes
        Get-Partition throw, as for a network drive). usf: registry value ->
        path (None = key absent). robocopy_exit: target folder name -> exit
        code (default 1, "files copied", which must count as success)."""
        lg = _ps_quote(self.log)
        partitions = {"C": 0, "E": 1} if partitions is None else partitions
        part_map = "@{ " + "; ".join(f"{_ps_quote(k)} = {v}" for k, v in partitions.items()) + " }"
        usf_obj = "$null" if usf is None else (
            "[pscustomobject]@{ " + "; ".join(f"{_ps_quote(k)} = {_ps_quote(v)}" for k, v in usf.items()) + " }"
        )
        rc_map = "@{ " + "; ".join(f"{_ps_quote(k)} = {v}" for k, v in (robocopy_exit or {}).items()) + " }"
        free = "$null" if free_space is None else f"[pscustomobject]@{{ DeviceID = 'E:'; FreeSpace = [uint64]{free_space} }}"
        stubs = [
            "function Get-ItemProperty { [CmdletBinding()] param([string] $Path) "
            f"if ($Path -eq {_ps_quote(USF_KEY)}) {{ {usf_obj} }} else {{ throw 'Neočakávaný kľúč' }} }}",
            "function Get-Partition { [CmdletBinding()] param([char] $DriveLetter) "
            f"$m = {part_map}; $k = [string]$DriveLetter; "
            "if ($m.ContainsKey($k)) { [pscustomobject]@{ DiskNumber = $m[$k] } } else { throw 'Nenájdené' } }",
            "function Get-CimInstance { [CmdletBinding()] param([string] $ClassName, [string] $Filter) "
            f"Add-Content -LiteralPath {lg} -Value ('cim ' + $ClassName + ' ' + $Filter); {free} }}",
            "function robocopy { $a = @($args); "
            f"Add-Content -LiteralPath {lg} -Value ('robocopy ' + ($a -join '|')); "
            "New-Item -ItemType Directory -Force -Path $a[1] | Out-Null; "
            "if ($a[2] -notlike '/*') { Copy-Item -LiteralPath (Join-Path $a[0] $a[2]) -Destination $a[1] } "
            "else { Get-ChildItem -LiteralPath $a[0] -Force | Copy-Item -Destination $a[1] -Recurse -Force }; "
            # A file robocopy leaves out without raising its exit code (as /XJ
            # does with a reparse-point folder).
            "Get-ChildItem -LiteralPath $a[1] -Recurse -File -Force -Filter '*robocopy-skips*' | Remove-Item -Force; "
            "'  Nový súbor    dokument.txt'; '100%'; "
            f"$m = {rc_map}; $n = Split-Path -Leaf $a[1]; "
            "if ($m.ContainsKey($n)) { $global:LASTEXITCODE = $m[$n] } else { $global:LASTEXITCODE = 1 } }",
            # Real hashing, except for files named *locked* (held open elsewhere).
            # Hashed with .NET rather than by delegating to the real command:
            # in Windows PowerShell 5.1 Get-FileHash lives in the Utility
            # module's nested script module, so the module-qualified name
            # Microsoft.PowerShell.Utility\\Get-FileHash does not resolve.
            "function Get-FileHash { [CmdletBinding()] param([string] $LiteralPath, [string] $Algorithm) "
            "if ((Split-Path -Leaf $LiteralPath) -like '*locked*') { throw 'Proces nemá prístup k súboru.' }; "
            # .NET resolves paths without PowerShell drives (E: is a PSDrive here).
            "$pfStubSt = [IO.File]::OpenRead((Resolve-Path -LiteralPath $LiteralPath -EA Stop).ProviderPath); "
            "try { $pfStubH = [Security.Cryptography.SHA256]::Create().ComputeHash([IO.Stream]$pfStubSt) } "
            "finally { $pfStubSt.Dispose() }; "
            "[pscustomobject]@{ Algorithm = 'SHA256'; Hash = ([BitConverter]::ToString($pfStubH) -replace '-', ''); "
            "Path = $LiteralPath } }",
        ]
        names = ["Get-ItemProperty", "Get-Partition", "Get-CimInstance", "robocopy", "Get-FileHash"]
        # In Windows PowerShell 5.1 Get-FileHash is a function exported by the
        # autoloading Utility module: the first Utility cmdlet the script calls
        # would import it and overwrite a stub defined earlier. Loading the
        # module first makes the stub the later definition. The guard also
        # demands an empty Source, so a module-exported function (the real
        # 5.1 Get-FileHash, the CDXML Get-Partition) never passes as a stub.
        preload = ["Import-Module Microsoft.PowerShell.Utility"]
        guard = (
            "foreach ($n in " + ", ".join(_ps_quote(n) for n in names) + ") { "
            "$c = Get-Command $n -EA SilentlyContinue | Select-Object -First 1; "
            "if (-not $c -or $c.CommandType -ne 'Function' -or $c.Source -or $c.Module) "
            f"{{ exit {STUB_GUARD_EXIT} }} }}"
        )
        # Redirected inside the script, not in the child environment: Windows
        # PowerShell cannot even start with a fake SystemRoot, so no test
        # hands it a doctored environment.
        env_vars = {
            "SystemDrive": system_drive, "USERPROFILE": self.profile, "LOCALAPPDATA": self.local,
            "APPDATA": self.roaming, "COMPUTERNAME": computer, "USERNAME": "klient",
        }
        env_lines = [f"$env:{k} = {_ps_quote(v)}" for k, v in env_vars.items()]
        setup = [
            f"New-PSDrive -Name E -PSProvider FileSystem -Root {_ps_quote(self.drive)} -Scope Global | Out-Null",
            f"Set-Location {_ps_quote(location)}",
        ]
        script = "; ".join(
            ["[Console]::OutputEncoding=[Text.Encoding]::UTF8"] + env_lines + preload + stubs + [guard] + setup + [command]
        )
        result = subprocess.run(
            [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode != STUB_GUARD_EXIT, "a system tool was not stubbed - refusing to run the real one"
        return result


def _populate(box: Box) -> dict[str, bytes]:
    """A typical profile; returns the expected manifest (relative path -> bytes)."""
    docs = box.tmp / "OneDrive" / "Dokumenty"
    chrome = box.local / "Google" / "Chrome" / "User Data"
    firefox = box.roaming / "Mozilla" / "Firefox" / "Profiles" / "ab12.default-release"
    expected = {
        "Desktop/poznámky.txt": (box.profile / "Desktop" / "poznámky.txt", b"desktop note"),
        "Documents/faktúry/2026.pdf": (docs / "faktúry" / "2026.pdf", b"%PDF-1.7 invoice" * 50),
        "Pictures/dovolenka.jpg": (box.profile / "Pictures" / "dovolenka.jpg", b"\xff\xd8\xff" + b"x" * 300),
        "Downloads/setup.exe": (box.profile / "Downloads" / "setup.exe", b"MZ" + b"\0" * 64),
        "Favorites/Banka.url": (box.profile / "Favorites" / "Banka.url", b"[InternetShortcut]\r\nURL=https://example.org\r\n"),
        "Bookmarks/Chrome/Default/Bookmarks": (chrome / "Default" / "Bookmarks", b'{"roots": {}}'),
        "Bookmarks/Edge/Profile 1/Bookmarks": (
            box.local / "Microsoft" / "Edge" / "User Data" / "Profile 1" / "Bookmarks", b'{"edge": 1}'),
        "Bookmarks/Firefox/ab12.default-release/bookmarks-2026-09-24.jsonlz4": (
            firefox / "bookmarkbackups" / "bookmarks-2026-09-24.jsonlz4", b"mozLz40\0data"),
    }
    for path, data in expected.values():
        _write(path, data)
    # Only the Bookmarks file of a browser profile is copied, never its history.
    _write(chrome / "Default" / "History", b"not copied")
    _write(firefox / "places.sqlite", b"locked while Firefox runs")
    # Pre-redirect Documents: the registry value (OneDrive) wins over it.
    _write(box.profile / "Documents" / "stale.txt", b"old")
    return {rel: data for rel, (_, data) in expected.items()}


def _usf(box: Box) -> dict:
    return {"Personal": str(box.tmp / "OneDrive" / "Dokumenty")}


def _manifest(backup: Path) -> dict[str, tuple[str, str]]:
    with (backup / "manifest-sha256.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return {r["Path"].replace("\\", "/"): (r["Size"], r["SHA256"]) for r in rows}


def _status(backup: Path) -> list[str]:
    return (backup / "backup-status.txt").read_text(encoding="utf-8-sig").splitlines()


def _backup(box: Box) -> Path:
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box))
    assert result.returncode == 0, result.stdout + result.stderr
    return box.backups()[-1]


def test_g14_backup_copies_every_source_and_writes_a_matching_manifest(tmp_path):
    box = Box(tmp_path)
    expected = _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box))
    assert result.returncode == 0, result.stdout + result.stderr
    [backup] = box.backups()
    assert backup.name.startswith("PC1_")
    assert "Backup complete: " in result.stdout
    assert _manifest(backup) == {rel: (str(len(data)), _sha(data)) for rel, data in expected.items()}
    for rel, data in expected.items():
        assert (backup / "Files" / rel).read_bytes() == data
    assert not (backup / "Files" / "Documents" / "stale.txt").exists()
    # The source is never touched.
    assert (tmp_path / "OneDrive" / "Dokumenty" / "faktúry" / "2026.pdf").read_bytes() == expected[
        "Documents/faktúry/2026.pdf"]
    assert (box.profile / "Desktop" / "poznámky.txt").read_bytes() == b"desktop note"
    # The SHA-256 of the manifest itself lands in the report output.
    manifest_hash = _sha((backup / "manifest-sha256.csv").read_bytes())
    assert f"manifest SHA-256 {manifest_hash}" in result.stdout
    assert _status(backup) == ["RESULT=COMPLETE"]
    assert f"Estimated {len(expected)} file(s), manifest {len(expected)} file(s)" in result.stdout
    assert "WARN:" not in result.stdout


def test_g14_backup_warns_when_robocopy_silently_copied_fewer_files_than_estimated(tmp_path):
    box = Box(tmp_path)
    expected = _populate(box)
    _write(box.profile / "Desktop" / "robocopy-skips.txt", b"left out")
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box))
    # robocopy reported success, so the run stays COMPLETE, but the gap is
    # visible in the report instead of hiding behind a green status.
    assert result.returncode == 0, result.stdout + result.stderr
    total = len(expected) + 1
    assert f"Estimated {total} file(s), manifest {len(expected)} file(s)" in result.stdout
    assert "WARN: 1 file(s) fewer in the backup than estimated" in result.stdout


def test_g14_backup_robocopy_never_follows_junctions_and_copies_only_the_bookmarks_file(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [c[len("robocopy "):].split("|") for c in box.calls("robocopy ")]
    assert len(calls) == 8
    for args in calls:
        for flag in ("/COPY:DAT", "/R:1", "/W:1", "/XJ", "/XA:O"):
            assert flag in args, args
        # Nothing robocopy offers for moving or pruning may ever appear.
        assert not {"/MOV", "/MOVE", "/MIR", "/PURGE"} & {a.upper() for a in args}, args
    folders = [a for a in calls if "/E" in a]
    singles = [a for a in calls if a[2] == "Bookmarks"]
    assert len(folders) == 6  # 5 known folders + Firefox bookmarkbackups
    assert len(singles) == 2  # Chrome + Edge Bookmarks files, not /E of the whole browser profile
    assert all("/E" not in a for a in singles)


def test_g14_backup_without_the_shell_folders_key_falls_back_to_the_profile(tmp_path):
    box = Box(tmp_path)
    _write(box.profile / "Documents" / "a.txt", b"a")
    result = box.run(_action(BACKUP_ID).command, usf=None)
    assert result.returncode == 0, result.stdout + result.stderr
    [backup] = box.backups()
    assert set(_manifest(backup)) == {"Documents/a.txt"}


@pytest.mark.parametrize("code", [0, 1, 3, 7])
def test_g14_backup_robocopy_exit_codes_below_8_are_success(tmp_path, code):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box), robocopy_exit={"Documents": code, "Desktop": code})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backup complete" in result.stdout


@pytest.mark.parametrize("code", [8, 16])
def test_g14_backup_robocopy_exit_8_or_more_fails_but_still_writes_the_manifest(tmp_path, code):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box), robocopy_exit={"Documents": code})
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"Copy failed: Documents (robocopy exit {code})" in result.stdout
    assert "Backup INCOMPLETE" in result.stdout
    [backup] = box.backups()
    assert "Desktop/poznámky.txt" in _manifest(backup)
    assert _status(backup) == ["RESULT=INCOMPLETE", f"COPY_FAILED=Documents (robocopy exit {code})"]
    # Every file that did get copied still matches, yet verify must not call
    # an incomplete backup OK.
    verify = box.run(_action(VERIFY_ID).command)
    assert verify.returncode == 1, verify.stdout + verify.stderr
    assert "VERDICT: FAIL - all " in verify.stdout
    assert "the backup run itself did not complete" in verify.stdout


def test_g14_backup_hash_failure_fails_and_leaves_an_empty_hash_in_the_manifest(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    _write(box.profile / "Desktop" / "locked.pst", b"outlook")
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Hash failed: Desktop" in result.stdout
    assert "Backup INCOMPLETE" in result.stdout
    [backup] = box.backups()
    assert _manifest(backup)["Desktop/locked.pst"] == ("7", "")
    assert _status(backup)[0] == "RESULT=INCOMPLETE"
    assert _status(backup)[1].startswith("HASH_FAILED=Desktop")


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"system_drive": "E:"}, "is the system drive"),
        ({"system_drive": "e:\\"}, "is the system drive"),
        ({"partitions": {"C": 0, "E": 0}}, "another partition of physical disk 0"),
        ({"free_space": 1024}, "Not enough free space on E:"),
        ({"free_space": None}, "Cannot read the free space of E:"),
        ({"location": "Env:\\"}, "not running from a drive letter"),
    ],
)
def test_g14_backup_refuses_without_copying_anything(tmp_path, kwargs, reason):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box), **kwargs)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED: " in result.stdout and reason in result.stdout, result.stdout
    assert box.calls("robocopy ") == []
    assert box.backups() == []
    # DRY-RUN shows the same refusal without failing the batch.
    preview = box.run(_action(BACKUP_ID).preview_command, usf=_usf(box), **kwargs)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert "Would refuse: " in preview.stdout and reason in preview.stdout, preview.stdout


def test_g14_backup_network_drive_without_a_partition_is_allowed(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf=_usf(box), partitions={"C": 0})
    assert result.returncode == 0, result.stdout + result.stderr


def test_g14_backup_refuses_a_destination_inside_a_source_folder(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    result = box.run(_action(BACKUP_ID).command, usf={"Personal": "E:\\"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED: The destination lies inside the source folder E:" in result.stdout
    assert box.calls("robocopy ") == []


def test_g14_backup_with_nothing_to_back_up_refuses(tmp_path):
    box = Box(tmp_path)
    result = box.run(_action(BACKUP_ID).command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REFUSED: None of Desktop" in result.stdout


def test_g14_preview_estimates_without_copying_and_skips_linked_folders(tmp_path):
    box = Box(tmp_path)
    expected = _populate(box)
    # A link inside Documents (like the legacy "My Music" junction) is not
    # walked - robocopy /XJ does not copy it either.
    elsewhere = box.tmp / "elsewhere"
    for i in range(5):
        _write(elsewhere / f"big{i}.bin", b"z" * 1000)
    (box.tmp / "OneDrive" / "Dokumenty" / "link").symlink_to(elsewhere, target_is_directory=True)
    result = box.run(_action(BACKUP_ID).preview_command, usf=_usf(box))
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Estimate: {len(expected)} file(s)" in result.stdout
    assert f"Would copy {len(expected)} file(s) to " in result.stdout
    assert "Destination: E:" in result.stdout and "PC1_" in result.stdout
    assert box.calls("robocopy ") == []
    assert box.backups() == []
    assert box.calls("cim Win32_LogicalDisk DeviceID='E:'")


def test_g14_verify_untouched_backup_is_ok(tmp_path):
    box = Box(tmp_path)
    expected = _populate(box)
    backup = _backup(box)
    copies = len(box.calls("robocopy "))
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backup: " in result.stdout and backup.name in result.stdout
    assert f"Manifest SHA-256: {_sha((backup / 'manifest-sha256.csv').read_bytes())}" in result.stdout
    assert f"VERDICT: OK - all {len(expected)} file(s) match the manifest" in result.stdout
    assert len(box.calls("robocopy ")) == copies  # verify copies nothing


def test_g14_verify_reports_missing_changed_and_unreadable_files(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    backup = _backup(box)
    files = backup / "Files"
    (files / "Pictures" / "dovolenka.jpg").unlink()
    # Same size, different content: only the hash catches it.
    note = files / "Desktop" / "poznámky.txt"
    note.write_bytes(note.read_bytes()[::-1])
    (files / "Downloads" / "setup.exe").write_bytes(b"MZ")
    _write(files / "Favorites" / "locked.url", b"x")
    _write(files / "Favorites" / "hand" / "edited.url", b"y")
    # Rows written by hand or on another OS use either separator; both
    # must match the file on disk, or it counts as "not in manifest".
    with (backup / "manifest-sha256.csv").open("a", encoding="utf-8", newline="") as fh:
        fh.write('"Favorites/locked.url","1","' + _sha(b"x") + '"\r\n')
        fh.write('"Favorites\\hand/edited.url","1","' + _sha(b"y") + '"\r\n')
    _write(files / "Desktop" / "extra.txt", b"new")
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 1, result.stdout + result.stderr
    out = result.stdout
    assert "Missing: Pictures" in out
    assert "Changed: Desktop" in out
    assert "Changed: Downloads" in out
    assert "Unreadable: Favorites" in out
    assert "Not in manifest (ignored): 1 file(s)" in out
    assert "VERDICT: FAIL - 1 missing, 2 changed, 1 unreadable of 10 file(s)" in out


def test_g14_verify_a_row_whose_hash_failed_at_backup_time_is_unreadable(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    _write(box.profile / "Desktop" / "locked.pst", b"outlook")
    box.run(_action(BACKUP_ID).command, usf=_usf(box))
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Unreadable: Desktop" in result.stdout
    assert "VERDICT: FAIL - 0 missing, 0 changed, 1 unreadable" in result.stdout


def test_g14_verify_without_a_backup_says_so_and_fails(tmp_path):
    # Nothing was verified: a zero exit would show green in the report.
    box = Box(tmp_path)
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "VERDICT: NO BACKUP - no PC1_* backup in E:" in result.stdout


def test_g14_verify_off_a_drive_letter_says_no_backup(tmp_path):
    box = Box(tmp_path)
    result = box.run(_action(VERIFY_ID).command, location="Env:\\")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "VERDICT: NO BACKUP - PortableFix is not running from a drive letter" in result.stdout


def test_g14_verify_unfinished_backup_without_manifest_fails(tmp_path):
    box = Box(tmp_path)
    (box.drive / "PortableFix_Backups" / "PC1_20260925_101010" / "Files").mkdir(parents=True)
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "VERDICT: FAIL - manifest-sha256.csv is missing" in result.stdout


def test_g14_verify_backup_interrupted_before_its_status_was_written_fails(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    backup = _backup(box)
    (backup / "backup-status.txt").unlink()
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "the backup run itself did not complete" in result.stdout


def test_g14_verify_picks_this_computers_newest_backup(tmp_path):
    box = Box(tmp_path)
    base = box.drive / "PortableFix_Backups"
    header = '"Path","Size","SHA256"\r\n'
    for name, rows in (
        ("PC1_20260101_080000", '"a.txt","1","BAD"\r\n'),
        ("PC1_20260925_090000", ""),
        ("PC2_20991231_000000", '"a.txt","1","BAD"\r\n'),
    ):
        _write(base / name / "manifest-sha256.csv", (header + rows).encode())
        _write(base / name / "backup-status.txt", b"RESULT=COMPLETE\r\n")
        (base / name / "Files").mkdir()
    result = box.run(_action(VERIFY_ID).command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PC1_20260925_090000" in result.stdout
    assert "VERDICT: OK - all 0 file(s)" in result.stdout


def test_g14_list_existing_shows_backups_on_the_portablefix_drive(tmp_path):
    box = Box(tmp_path)
    _populate(box)
    backup = _backup(box)
    (box.drive / "PortableFix_Backups" / "PC1_20200101_000000").mkdir()
    result = box.run(_action("backup_list_existing").command)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backups on the PortableFix drive (E:" in result.stdout
    rows = {line.split()[0]: line.split()[-1] for line in result.stdout.splitlines() if line.startswith("PC1_")}
    assert rows == {backup.name: "True", "PC1_20200101_000000": "False"}


def test_g14_actions_tiers_and_flags():
    by_id = {a.id: a for a in load_module(CATALOG_PATH).actions}
    backup, verify = by_id[BACKUP_ID], by_id[VERIFY_ID]
    assert backup.risk == RiskLevel.MODERATE and verify.risk == RiskLevel.SAFE
    assert backup.preview_command is not None
    # Nothing to undo: the backup only adds files on another drive, and an
    # undo that deleted the client's backup is the last thing anyone wants.
    assert backup.undo_command is None and verify.undo_command is None
    assert backup.changes_system is False
    # Whole profiles over USB take longer than the 2 h default cap.
    assert backup.hard_cap_sec > 7200 and verify.hard_cap_sec > 7200
    # Hashing one multi-GB file prints nothing for minutes.
    assert backup.inactivity_timeout_sec >= 1800 and verify.inactivity_timeout_sec >= 1800


# Windows PowerShell 5.1 is the target: every script must parse, and none may
# use PS7-only syntax that pwsh accepts but 5.1 rejects at run time.
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


def test_m18_catalog_scripts_parse_without_powershell_7_only_syntax(tmp_path):
    scripts = {
        f"{a.id}.{field}": getattr(a, field)
        for a in load_module(CATALOG_PATH).actions
        for field in ("command", "undo_command", "preview_command")
        if getattr(a, field)
    }
    scripts_file = tmp_path / "scripts.json"
    scripts_file.write_text(json.dumps(scripts), encoding="utf-8")
    env = dict(os.environ, PFSCRIPTS_FILE=str(scripts_file))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", PARSE_CHECKER],
        env=env, capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "PARSE_DONE" in result.stdout, result.stdout + result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []
