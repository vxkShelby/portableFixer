@echo off
rem An auto-update interrupted mid-swap (USB stick pulled, power lost) can
rem leave the app only as App.old - put it back so the stick still starts.
if not exist "%~dp0App\PortableFix.exe" if exist "%~dp0App.old\PortableFix.exe" rd /s /q "%~dp0App" 2>nul
if not exist "%~dp0App\PortableFix.exe" if exist "%~dp0App.old\PortableFix.exe" move "%~dp0App.old" "%~dp0App" >nul
"%~dp0App\PortableFix.exe"
