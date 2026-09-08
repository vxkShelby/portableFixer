## Fixed

- **"Select all" could queue up a driver-backup restore nobody asked for.** Selecting all in the Driver Updates category ran `drv_export_backup` then `drv_restore_backup` (overwriting current drivers with an old backup) right alongside the actual driver update - a new `exclude_from_select_all` flag keeps recovery-style actions (driver backup restore, user-folder backup restore) out of bulk selection; they still run fine when checked deliberately.
- **Winget's "Obnovit" (refresh) button disappeared** whenever no updates were found, leaving no way to re-check without restarting the app.
- **A console window flashed/lingered during winget and uninstall operations** - both now run fully suppressed like every other subprocess call in the app.

## Changed

- Selection now clears automatically after every run, instead of leaving the same actions checked and inviting an accidental re-run.
- Winget category: the select-all/safe/none buttons now sit directly above the static action list they control, instead of appearing above the dynamic update panel they don't.
- Dashboard tiles show the "needs attention" count inline in orange next to the action count (e.g. "29 akcii (3)") instead of a separate badge.
- Uninstaller: the program list now shows directly in the category (no extra dialog/click), with size, install path, and install date per program.

Full history since v1.5.0: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.5.0...v1.5.1).
