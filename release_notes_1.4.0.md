## Fixed

- **Auto-update could quit the app and never restart it, leaving the old version behind** even when the update itself downloaded fine. Root cause: if the old app's process took longer than ~15 seconds to fully exit (antivirus scanning it, a slow machine), the update script aborted with a hard exit - which also skipped the step that relaunches the app. The user was left with no running app at all until they found and ran the exe manually. This is a bug in every version up to and including v1.3.0 - if you're on any older version, in-app auto-update may silently fail exactly this way. Download this version manually (`PortableFix-Setup.exe` below) to get the fix once.
- The relaunch is now unconditional (happens whether the swap succeeded, was skipped, or was rolled back) and verified by checking the actual running process, with a fallback relaunch attempt if the first one doesn't take.

## Added

- **Recommended fixes after a full diagnostic run**: when a diagnostic check finds a known problem (e.g. UAC disabled, Safe Mode boot flag left on, PawnIO sensor driver missing), the results dialog now shows a "Recommended fixes" section with the matching repair actions pre-checked - one click selects them and jumps to the right category.
- System info panel (right side) now grows into the window's spare width instead of staying pinned at a fixed size.

Full history since v1.3.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.3.0...v1.4.0).
