## Added

- **Kernel-level rootkit gap approximations** (Security module): user-mode-only heuristics for detecting boot-sector/MBR tampering, hidden processes, and unsigned running processes - each honestly discloses it is not real kernel-level rootkit detection (that would need a signed kernel driver, which this app does not have).
  - `sec_bootsector_check`, `sec_hidden_process_heuristic`, `sec_process_signature_audit`
- **Common malware-damage repairs** inspired by RogueKiller feature comparison:
  - `online_proxy_reset` - clears per-user proxy server/PAC URL and resets system WinHTTP proxy (fixes malware/adware traffic redirection)
  - `net_dns_reset_automatic` - clears static DNS on active adapters back to DHCP (fixes DNS hijacking)
  - `sec_restore_taskmgr_regedit` - removes the classic DisableTaskMgr/DisableRegistryTools policy lockout malware uses to block its own removal

All new state-changing actions are MODERATE risk with backup-on-every-run and full undo.

## Fixed

- `user_temp` cleanup no longer deletes live AI coding-agent scratch data (`%TEMP%\claude`, `%TEMP%\context-mode-guidance-s-*`, plus Copilot CLI and Codex CLI scratch dirs) - it was silently wiping active session data on dev machines.

Full history since v1.2.1: see the [commit log](https://github.com/vxkShelby/portableFixer/compare/v1.2.1...v1.3.0).
