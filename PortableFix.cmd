@echo off
setlocal enabledelayedexpansion
net session >nul 2>&1
if !errorlevel! neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~dp0App\PortableFix.exe' -Verb RunAs" 2>nul
    if !errorlevel! neq 0 (
        "%~dp0App\PortableFix.exe"
    )
) else (
    "%~dp0App\PortableFix.exe"
)
endlocal
