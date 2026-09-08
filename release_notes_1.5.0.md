## Added

- **Dashboard**: new home screen (first sidebar entry, opens by default) with a system score, an "Analyze (full diagnostic)" button, and a clickable tile per category showing its action count and a live badge for how many recommended fixes are waiting there.
- **Dynamic Winget update panel**: the Winget category now lists outdated software as a per-app checkbox list (installed vs available version, search, select all/none, refresh, update selected) instead of a flat report + "update everything" action - inspired by Ashampoo WinOptimizer's software-updates screen, built in PortableFix's own dark/cyan look.
- **Driver updates, Winget, and Uninstaller are now separate categories** (previously drivers/winget were folded into Diagnostics/Repair):
  - Driver updates gets a real update action (`drv_install_updates`) via the Windows Update Agent API - previously the category only had reports/backup, no actual update capability.
  - Uninstaller: a new dialog listing installed programs from the registry, with search, bulk selection, batch uninstall, and an automatic leftover-cleanup pass afterward (orphaned registry entries whose install folder is gone).

Full history since v1.4.3: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.4.3...v1.5.0).
