## Fixed

- **Winget upgrade failed for any package with an undetermined installed version** (e.g. Battle.net, FiveM) with "This package's version number cannot be determined" - the actual upgrade command was missing `--include-unknown`, even though the scan already used it.
- **The update panel showed nothing while a winget upgrade was running**, easy to mistake for the app being frozen on a slow install - it now logs "Aktualizujem: &lt;name&gt;..." as each package starts, not only once it finishes.
- Update button stayed disabled after a batch finished in some cases.

Docker Desktop's "no applicable upgrade" and Epic Online Services' "install technology is different" are winget's own limitations (architecture/requirements mismatch, MSI-vs-EXE reinstall needed) - not something PortableFix can fix, already reported honestly in the console.

Full history since v1.5.1: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.5.1...v1.5.2).
