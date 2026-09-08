## Fixed

- **Windows 11 was reported as "Windows 10"** in the system info panel and reports. The registry value Windows itself exposes for this (`ProductName`) still literally says "Windows 10 ..." on every real Windows 11 install - Microsoft never updated it after the Windows 11 rebrand. Now corrected using the build number (22000+), the actual signal for the OS generation.
- **CPU/GPU model names and the OS line wrapped or got clipped** in the system info panel. They now get their own full-width row instead of squeezing next to their label, so they stay on one line.
- **Category/risk sidebar showed a horizontal scrollbar** on longer entries (e.g. "Risk: REQUIRES_REBOOT"). It now sizes itself to the widest actual label instead of a fixed width.

Full history since v1.4.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.4.0...v1.4.1).
