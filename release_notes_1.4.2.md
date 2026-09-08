## Fixed

- **Category/risk sidebar still showed a horizontal scrollbar** even after v1.4.1's width fix - the stylesheet's own item padding wasn't fully accounted for. Padding allowance corrected and the scrollbar now explicitly disabled as a hard guarantee.
- **Disk health and VPN status in the system info panel wrapped awkwardly** (multi-drive health list, VPN adapter name with tunnel type) - they now get the same full-width single-line treatment as the OS/CPU/GPU rows from v1.4.1.

Full history since v1.4.1: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.4.1...v1.4.2).
