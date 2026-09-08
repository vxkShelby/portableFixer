## New

- **Antivirus category**: Windows Defender status/update/scan/exclusions moved out of Security into their own dedicated category - room for future scanners without cluttering general security hardening.
- **Winget updates on the dashboard**: the live update panel now lives directly on the Prehlad/Dashboard card, no separate category click needed.
- **Manual update fallback**: when a winget package fails to update (Docker Desktop, Epic Online Services, and similar vendor-side limitations), its row now shows an "Aktualizovat manualne" button that opens the installed app instead of silently giving up.
- **Live speed test**: measures download, upload, and ping, updating each line as its stage finishes instead of one final number.
- **Single running instance**: launching a second copy now shows "already running" and exits instead of opening another window.

## Fixed

- CPU frequency showed N/A whenever Windows' Core Isolation/Memory Integrity (HVCI) blocked LibreHardwareMonitor's driver - added a WMI fallback.

Full history since v1.5.2: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.5.2...v1.6.0).
