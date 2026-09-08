## Fixed

- System info panel (OS, CPU, GPU, disk health, VPN) had their value visually split onto a second line below the caption instead of staying on the same row. Reverted to one row per field, on a wider panel (460-640px) so the longest value (full CPU model name) still fits without wrapping.
- Default window size bumped 1000x700 -> 1200x760 so the sidebar, action list, and wider info panel all fit without squeezing the center list.

Full history since v1.4.2: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.4.2...v1.4.3).
