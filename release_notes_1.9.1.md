## Fixed

- **In-app update-and-restart could silently fail** ("asks for restart, doesn't restart, still old version after manually restarting"): the winget panel's background scan/update threads were tracked on the wrong widget, so the app's shutdown logic didn't know to wait for them. If either was still running when you confirmed "restart to install update", the old process never actually exited - the update swap script gives up waiting after ~15s and relaunches the still-old version. Now properly tracked and waited on, so the swap can complete before relaunch.

Full history since v1.9.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.9.0...v1.9.1).
