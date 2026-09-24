"""scripts/verify_release.py: the gate between a build and a published
release. Every broken package here must give a non-zero exit."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from update_fixtures import release_files, sums_for, write_release_zip

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import verify_release  # noqa: E402


def _good_files() -> dict[str, bytes]:
    files = release_files()
    del files["Data/settings.json"]
    files["portablefix.ico"] = b"icon"
    return files


def _release(tmp_path: Path, files=None, *, sums=None, extra_names=None, sidecar=True) -> Path:
    zip_path = write_release_zip(
        tmp_path / "Output" / "PortableFix-Portable.zip",
        _good_files() if files is None else files,
        sums=sums,
        extra_names=extra_names,
    )
    if sidecar:
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        Path(str(zip_path) + ".sha256").write_text(f"{digest}  PortableFix-Portable.zip\n", encoding="ascii")
    return zip_path


def _tree(tmp_path: Path, files=None, *, sums=None) -> Path:
    root = tmp_path / "repo"
    files = _good_files() if files is None else dict(files)
    files["Data/SHA256SUMS"] = sums_for(files) if sums is None else sums
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def _without(name: str) -> dict[str, bytes]:
    return {rel: data for rel, data in _good_files().items() if not rel.startswith(name)}


def test_a_good_release_passes_and_the_zip_is_kept(tmp_path, capsys):
    zip_path = _release(tmp_path)
    tree = _tree(tmp_path)

    assert verify_release.main(["--tree", str(tree), "--zip", str(zip_path)]) == 0
    # Staging deletes the zip it is given - verification must work on a copy.
    assert zip_path.is_file()
    assert "OK" in capsys.readouterr().out


def test_the_script_runs_standalone(tmp_path):
    zip_path = _release(tmp_path)
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_release.py"), "--zip", str(zip_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr


def test_nothing_to_check_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        verify_release.main([])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "files, sums, expected",
    [
        # A zip holding the build machine's settings would overwrite nothing
        # (the swap skips it), but the installer used to - and it leaks them.
        ({**_good_files(), "Data/settings.json": b"{}"}, None, "Data/settings.json must not ship"),
        ({**_good_files(), "Data/update_status.txt": b"ok"}, None, "Data/update_status.txt must not ship"),
        (_without("Vendor/"), None, "Vendor"),
        (_without("portablefix.ico"), None, "portablefix.ico is missing"),
        # Signed (or rebuilt) after the manifest was generated.
        (_good_files(), sums_for({**_good_files(), "App/PortableFix.exe": b"unsigned"}), "refuse"),
        (_good_files(), sums_for(_good_files(), skip=("App/PortableFix.exe",)), "App/PortableFix.exe"),
        (_good_files(), sums_for(_good_files(), skip=("Modules/m01_diagnostics/actions.yaml",)),
         "Modules/m01_diagnostics/actions.yaml is not in Data/SHA256SUMS"),
    ],
    ids=["settings", "update-status", "no-vendor", "no-icon", "stale-sums", "no-exe-in-sums", "unlisted-file"],
)
def test_a_broken_zip_fails(tmp_path, capsys, files, sums, expected):
    zip_path = _release(tmp_path, files, sums=sums)

    assert verify_release.main(["--zip", str(zip_path)]) == 1
    assert expected in capsys.readouterr().err


def test_a_zip_with_two_top_level_folders_fails(tmp_path, capsys):
    zip_path = _release(tmp_path, extra_names={"Other\\readme.txt": b"x"})

    assert verify_release.main(["--zip", str(zip_path)]) == 1
    assert "more than one top-level folder" in capsys.readouterr().err


@pytest.mark.parametrize("sidecar_text", [None, "0" * 64 + "  PortableFix-Portable.zip\n", ""])
def test_a_missing_or_wrong_sha256_file_fails(tmp_path, capsys, sidecar_text):
    zip_path = _release(tmp_path, sidecar=False)
    if sidecar_text is not None:
        Path(str(zip_path) + ".sha256").write_text(sidecar_text, encoding="ascii")

    assert verify_release.main(["--zip", str(zip_path)]) == 1
    assert "PortableFix-Portable.zip.sha256" in capsys.readouterr().err


@pytest.mark.parametrize(
    "files, sums, expected",
    [
        (_good_files(), sums_for({**_good_files(), "App/PortableFix.exe": b"unsigned"}),
         "App/PortableFix.exe changed after Data/SHA256SUMS was generated"),
        (_good_files(), sums_for(_good_files(), skip=("App/PortableFix.exe",)),
         "Data/SHA256SUMS does not list App/PortableFix.exe"),
        (_without("Vendor/"), None, "Vendor/ is missing or empty"),
        (_good_files(), sums_for({**_good_files(), "App/gone.dll": b"x"}),
         "App/gone.dll is listed in Data/SHA256SUMS but missing"),
    ],
    ids=["stale-sums", "no-exe-in-sums", "no-vendor", "listed-but-missing"],
)
def test_a_broken_tree_fails(tmp_path, capsys, files, sums, expected):
    tree = _tree(tmp_path, files, sums=sums)

    assert verify_release.main(["--tree", str(tree)]) == 1
    assert expected in capsys.readouterr().err


def test_a_tree_without_a_manifest_fails(tmp_path, capsys):
    tree = _tree(tmp_path)
    (tree / "Data" / "SHA256SUMS").unlink()

    assert verify_release.main(["--tree", str(tree)]) == 1
    assert "generate_sha256sums.py" in capsys.readouterr().err


def test_the_build_machines_own_data_files_do_not_fail_the_tree(tmp_path):
    # They are real on the build machine (the app runs from the repo too);
    # the zip and the installer take Data\ by allowlist, and --zip checks it.
    tree = _tree(tmp_path)
    (tree / "Data" / "settings.json").write_text("{}", encoding="utf-8")

    assert verify_release.main(["--tree", str(tree)]) == 0


# --- scripts/build.ps1 and the packaging scripts ---------------------------


def _powershell_or_skip() -> str:
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    return exe


# The target is Windows PowerShell 5.1: pwsh parses ??, ?., ternaries and
# &&/|| that 5.1 rejects, so those are flagged explicitly.
_PARSE_CHECK = (
    "$t = $null; $e = $null; "
    "$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:PF_PARSE_FILE, [ref]$t, [ref]$e); "
    "foreach ($x in @($e)) { if ($x) { Write-Output ('ERR ' + $x.Message) } }; "
    "$ps7 = @($t | Where-Object { [string]$_.Kind -in @('QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') }); "
    "$ps7 += @($ast.FindAll({ param($n) $n.GetType().Name -in @('TernaryExpressionAst','PipelineChainAst') }, $true)); "
    "if ($ps7.Count) { Write-Output 'ERR PowerShell 7-only syntax' }; "
    "Write-Output PARSE_DONE"
)


@pytest.mark.parametrize("script", ["build.ps1", "build_release_zip.ps1"])
def test_packaging_scripts_parse_on_windows_powershell(script):
    env = dict(os.environ, PF_PARSE_FILE=str(ROOT / "scripts" / script))
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-Command", _PARSE_CHECK],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert "PARSE_DONE" in result.stdout, result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("ERR")] == []


def test_build_refuses_a_tag_that_does_not_match_the_version_before_building():
    result = subprocess.run(
        [_powershell_or_skip(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "scripts" / "build.ps1"), "-Tag", "v0.0.0-not-this-version"],
        capture_output=True, text=True, timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Version mismatch" in output
    assert "==> PyInstaller" not in output


def test_version_py_and_the_installer_agree():
    # build.ps1 enforces it too; this catches a half-done bump on every run.
    from portablefix.version import APP_VERSION

    iss = (ROOT / "installer" / "PortableFix.iss").read_text(encoding="utf-8")
    assert re.search(r'#define\s+MyAppVersion\s+"([^"]+)"', iss).group(1) == APP_VERSION


def test_build_runs_its_steps_in_the_release_order():
    text = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    steps = [
        "Version mismatch",
        "requirements-build.txt pins",
        '"PyInstaller" {',
        'Invoke-Signing "$distStage\\PortableFix.exe"',
        "generate_sha256sums.py",
        "verify_release.py\" --tree",
        "build_release_zip.ps1",
        "verify_release.py\" --zip",
        "/DMyAppVersion=$appVersion",
        'Invoke-Signing "$root\\Output\\PortableFix-Setup.exe"',
    ]
    positions = [code.index(step) for step in steps]
    assert positions == sorted(positions)


def test_the_build_pins_the_pyinstaller_it_checks():
    lines = (ROOT / "requirements-build.txt").read_text(encoding="utf-8").splitlines()
    assert "-r requirements.txt" in lines
    assert "pyinstaller==6.22.3" in lines
