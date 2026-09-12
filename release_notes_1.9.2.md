## Fixed

- **The actual root cause of the broken in-app update-and-restart**: the update swap script relaunched the app unconditionally, even when the old process hadn't exited yet. That spawned a second instance which immediately lost to the single-instance lock added in v1.6.0 and exited silently - looking exactly like "clicked restart, nothing happened, still the old version." The relaunch is now skipped in that case (the old process is already the running instance, nothing needs to be spawned), and the wait for the old process to exit was widened from 15s to 30s for slower machines/antivirus scanning.

Full history since v1.9.1: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.9.1...v1.9.2).
