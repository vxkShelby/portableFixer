"""Run the read-only report commands of the catalog in PowerShell against
stubbed system sources (registry, DNS cache, printers).

Every source the command reads is a stub function (functions win over
cmdlets in command lookup) and the script refuses to run (exit 97) unless
each stubbed name resolves to the stub, so a test never reads or prints on
the host. The session culture is Slovak, so a command that parses a date or
a number with the current culture fails here.

Used by the report-action tests in test_m03/m05/m06/m08/m14_catalog.py.
"""
import os
import shutil
import subprocess

import pytest

STUB_GUARD_EXIT = 97


def ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def ps_value(value) -> str:
    if value is None:
        return "$null"
    if isinstance(value, bool):
        return "$true" if value else "$false"
    if isinstance(value, int):
        return str(value)
    return ps_quote(value)


def registry_stub(table: dict) -> str:
    """Get-ItemProperty answering from {key: {value name: data}}."""
    keys = "; ".join(
        f"{ps_quote(key)} = @{{ " + "; ".join(f"{ps_quote(n)} = {ps_value(v)}" for n, v in values.items()) + " }"
        for key, values in table.items()
    )
    return (
        f"$global:PfReg = @{{ {keys} }}; "
        "function Get-ItemProperty { [CmdletBinding()] param([string] $Path, [string] $LiteralPath, [string[]] $Name) "
        "$k = if ($LiteralPath) { $LiteralPath } else { $Path }; if (-not $global:PfReg.ContainsKey($k)) { return }; "
        "$h = $global:PfReg[$k]; if (-not $Name) { return [pscustomobject]$h }; $o = @{}; "
        "foreach ($n in $Name) { if ($h.ContainsKey($n)) { $o[$n] = $h[$n] } }; if ($o.Count) { [pscustomobject]$o } }"
    )


def run_ps(command: str, stubs=(), names=("Get-ItemProperty",)):
    exe = os.environ.get("PORTABLEFIX_TEST_PWSH") or shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("no PowerShell available")
    guard = (
        "foreach ($n in " + ", ".join(ps_quote(n) for n in names) + ") { "
        "if ((Get-Command $n -EA SilentlyContinue | Select-Object -First 1).CommandType -ne 'Function') "
        f"{{ exit {STUB_GUARD_EXIT} }} }}"
    )
    script = "; ".join([
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8",
        "[Threading.Thread]::CurrentThread.CurrentCulture = [Globalization.CultureInfo]::GetCultureInfo('sk-SK')",
        *stubs, guard, command,
    ])
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != STUB_GUARD_EXIT, "a source was not stubbed - refusing to read the real one"
    return result
