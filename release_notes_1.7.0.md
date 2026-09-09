## New

- **Winget ignore list**: each package now has an "Ignorovat" button - hides it from future scans (persisted). "Sprava ignorovanych" shows the list with an un-ignore option per package.
- **Scheduled auto-check**: optional periodic re-scan (15/30/60/120 min) while the app is open.
- **Export/import package list**: save the current winget package list to a JSON file, or import one to pre-check matching packages.

## Fixed

- **Cleanup could delete a freshly installed program**: `user_temp`/`system_temp` (Cistenie category) wildcard-deleted everything under `%TEMP%`/`%WINDIR%\Temp` except a narrow hardcoded name whitelist - any other software living directly in TEMP (common for portable/self-extracting apps) was deleted along with real junk. Now skips anything modified in the last 3 days and any folder currently owned by a running process, instead of relying on a fixed list of known names.

Full history since v1.6.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.6.0...v1.7.0).
