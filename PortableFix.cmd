@echo off
rem An auto-update interrupted mid-swap (USB stick pulled, power lost) can
rem leave the app only as App.old - put it back so the stick still starts.
if not exist "%~dp0App\PortableFix.exe" if exist "%~dp0App.old\PortableFix.exe" rd /s /q "%~dp0App" 2>nul
rem Only when App\ is really gone: moving into a half-deleted App\ would
rem nest the good copy as App\App.old and lose the recovery source.
if not exist "%~dp0App\" if exist "%~dp0App.old\PortableFix.exe" move "%~dp0App.old" "%~dp0App" >nul
rem One line, so cmd.exe has parsed all of it before the app starts: cmd
rem re-reads a batch file by byte offset once a command returns, and an
rem update replaces this file while cmd is still waiting on the old app.
"%~dp0App\PortableFix.exe" %* & exit /b
