@echo off
rem An auto-update interrupted mid-swap (USB stick pulled, power lost) can
rem leave the app only as App.old - put it back so the stick still starts.
if not exist "%~dp0App\PortableFix.exe" if exist "%~dp0App.old\PortableFix.exe" rd /s /q "%~dp0App" 2>nul
rem Only when App\ is really gone: moving into a half-deleted App\ would
rem nest the good copy as App\App.old and lose the recovery source.
if not exist "%~dp0App\" if exist "%~dp0App.old\PortableFix.exe" move "%~dp0App.old" "%~dp0App" >nul
rem start, so this console closes at once instead of staying open behind
rem the app (and never re-reads this file after an update replaced it).
rem /D: the install root as working directory, never App\, which must stay
rem renameable for the next update.
start "" /D "%~dp0" "%~dp0App\PortableFix.exe" %* & exit /b
