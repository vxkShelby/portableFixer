# M08 Security Module — Catalog + Tool-Posture Audit

Scope: `Modules/m08_security/actions.yaml` (5 actions), `portablefix/executor.py`,
`portablefix/gui/main_window.py`, plus `portablefix/restore_point.py`,
`portablefix/models.py`, `portablefix/report.py`, `portablefix/audit_log.py`,
`portablefix/elevation.py`, `portablefix/i18n.py` as needed to trace behavior.
Read-only research; no files modified.

Current catalog (verbatim from `Modules/m08_security/actions.yaml`):

| id | risk | command |
|---|---|---|
| `sec_defender_status` | SAFE | `Get-MpComputerStatus \| Select-Object AMServiceEnabled,RealTimeProtectionEnabled,AntivirusEnabled,AntivirusSignatureLastUpdated,QuickScanAge,FullScanAge \| Format-List` |
| `sec_firewall_status` | SAFE | `Get-NetFirewallProfile \| Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction \| Format-Table -AutoSize` |
| `sec_defender_update` | SAFE | `Update-MpSignature` |
| `sec_defender_quickscan` | MODERATE | `Start-MpScan -ScanType QuickScan` |
| `sec_uac_status` | SAFE | `Get-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System' \| Select-Object EnableLUA,ConsentPromptBehaviorAdmin,PromptOnSecureDesktop \| Format-List` |

---

## Severity summary

- **CRITICAL:** none found on the 5 *current* actions themselves (none leak secrets today).
- **HIGH:**
  1. No action is gated on elevation, yet 3 of 5 commands (`Get-MpComputerStatus`, `Update-MpSignature`, `Start-MpScan`) realistically require admin rights — non-admin is the default state (elevation is a manual opt-in button), so the *common* case is Access-Denied failures, not the 3rd-party-AV case the audit asked about.
  2. `sec_defender_quickscan` runs through a genuinely no-timeout pipeline (`executor.py` has no timeout anywhere) with a cmdlet that emits **zero stdout** while it runs — the GUI will show a completely blank, unmoving output pane for the full scan duration with no way to distinguish "working" from "hung."
  3. `report.py` / `audit_log.py` persist 100% of an action's raw stdout, unredacted, into two plaintext files on the portable medium (HTML `<pre>` + JSONL with a SHA-256 "integrity" hash over the *unredacted* text). This doesn't leak anything for the current 5 actions, but it is a hard constraint on every future M08 status action: the executor has no allowlist/redaction layer, so any future command that can print a secret (autologon password, tokens, etc.) will get written to disk verbatim.
  4. `sec_defender_update` is tagged `risk: SAFE`, which (a) auto-selects it by default (`checked = action.risk == RiskLevel.SAFE` in `main_window.py`) and (b) skips the "are you sure" confirmation dialog (only non-SAFE risk shows a confirm box) — yet it mutates system AV signature state and needs network access. It is not read-only despite living in a module whose other 4 actions all advertise "changes nothing."
- **MEDIUM:**
  5. UAC status omits `ConsentPromptBehaviorUser` and shows raw enum integers with no legend — a user sees `ConsentPromptBehaviorAdmin : 5` with no explanation, and can see genuinely **blank** fields on an unmodified machine (see finding 3.2).
  6. SECURITY category triggers a System Restore Point before running *any* of its actions, including 3 pure-read status queries whose own descriptions promise "changes nothing" — this is worse here than in other categories because 60% of M08's current actions are read-only, so the mismatch between "just checking a status" and "creating a restore point + possibly showing a failure dialog" is most visible in this module.
  7. `Get-MpComputerStatus`/`Update-MpSignature`/`Start-MpScan` error surfaces are raw, unfiltered PowerShell/CIM exception text merged stdout+stderr with no interpretation — technically visible, not usefully explained to a lay user.
- **LOW:**
  8. `Get-NetFirewallProfile`/`Get-BitLockerVolume`-style cmdlets are edition/SKU-dependent; no existence probing anywhere in the executor.
  9. Generic `confirm_risky_action` dialog text ("Are you sure you want to run this action?") gives the MODERATE quick-scan no scan-specific context (duration, no-cancel-once-started, silent-for-minutes) beyond the static YAML description.

---

## 1. Command correctness

### 1.1 `sec_defender_status` — `Get-MpComputerStatus`
- **Issue:** `Get-MpComputerStatus` reads from the Defender WMI/CIM provider (`root\Microsoft\Windows\Defender`, class `MSFT_MpComputerStatus`) via the `WinDefend` service. Two distinct failure modes matter, and they are *not* the same thing:
  - **Non-admin (the app's default state):** this cmdlet is commonly reported to fail for non-elevated callers with an Access-Denied style CIM error (HRESULT `0x80070005`). Since `portablefix` does not gate any action on `is_admin` (confirmed: no `requires_admin` field exists on `ActionDef` in `portablefix/models.py`, and `main_window.py` never checks `self.is_admin` before dispatch — the admin pill/"restart as admin" button is purely informational), this is the failure a normal user hits *first*, before ever encountering a 3rd-party-AV scenario.
  - **3rd-party AV present:** if Defender is merely in **passive mode** (common, automatic when another AV registers with Security Center), `Get-MpComputerStatus` still succeeds and returns a valid object — just with `AntivirusEnabled=False`/`RealTimeProtectionEnabled=False` while `AMServiceEnabled` may still read `True`. If Defender is fully disabled via policy (`DisableAntiSpyware`) or the `WinDefend` service is stopped/disabled outright, the cmdlet throws a CIM error such as "No MSFT_MpComputerStatus objects found..." or a service-unavailable HRESULT (commonly `0x80070422`). Either way the failure text is a multi-line raw PowerShell exception, not a friendly message, and it is piped straight to the user via `output_line` with no parsing.
- **Value:** none of this is wrong per se, but the description text ("Shows Defender protection status... changes nothing") sets an expectation of a clean read that doesn't hold once a 3rd-party AV or non-admin context is involved.
- **Exact command:** `Get-MpComputerStatus | Select-Object AMServiceEnabled,RealTimeProtectionEnabled,AntivirusEnabled,AntivirusSignatureLastUpdated,QuickScanAge,FullScanAge | Format-List`

### 1.2 `sec_defender_update` — `Update-MpSignature`
- **Issue:** Same service/elevation dependency as 1.1 (requires `WinDefend` running + realistically admin), plus a hard runtime dependency on internet access that the description does flag ("Requires internet access") — but the YAML still tags it `risk: SAFE`, which auto-checks it by default and skips the confirmation dialog (see HIGH finding 4). If Defender is in passive mode because a 3rd-party AV owns real-time protection, signature updates can still legitimately fail or no-op; the raw error/no-op output is shown unfiltered.
- **Exact command:** `Update-MpSignature`

### 1.3 `sec_defender_quickscan` — `Start-MpScan -ScanType QuickScan`
- **Issue:** Same service/elevation dependency again. Additionally this is the one M08 action most exposed to the no-timeout, no-progress pipeline — see section 2.
- **Exact command:** `Start-MpScan -ScanType QuickScan`

### 1.4 `sec_firewall_status` — `Get-NetFirewallProfile`
- **Issue:** Backed by the `NetSecurity` module / `MpsSvc` (Windows Firewall service) CIM provider — a **separate** component from Windows Defender AV, so it is largely unaffected by a 3rd-party antivirus disabling Defender AV specifically. It *can* fail the same way if a 3rd-party **firewall/security suite** stops `MpsSvc` or if run on a stripped SKU (Server Core / some IoT builds) where the `NetSecurity` module isn't present, in which case the failure is `CommandNotFoundException` rather than a CIM error — a different failure shape than 1.1–1.3 that the current pipeline doesn't distinguish either. On mainstream Windows 10/11 Home/Pro/Enterprise/Education this command is reliable.
- **Exact command:** `Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction | Format-Table -AutoSize`

### 1.5 `sec_uac_status` — registry read
- Covered in detail in section 3 (correctness is fine; completeness/interpretation is not).

---

## 2. `Start-MpScan -ScanType QuickScan` through the no-timeout pipeline

Traced in `portablefix/executor.py`:

```python
process = subprocess.Popen(
    self._plan.argv,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    encoding="utf-8",
    errors="replace",
    creationflags=subprocess.CREATE_NO_WINDOW,
)
...
for raw_line in process.stdout:
    ...
process.wait()
```

- **No timeout anywhere.** `subprocess.Popen(...)` carries no `timeout`; the only place in the whole codebase that passes `timeout=` to a subprocess call is `restore_point.py`'s `create_restore_point` (`timeout=120`). `ActionRunner.run()` will block on `for raw_line in process.stdout` for as long as the child process runs — indefinitely, if `Start-MpScan` hangs (e.g., waiting on a Defender service that's in a bad state after a 3rd-party AV conflict).
- **Output behavior: silent for minutes, confirmed.** `Start-MpScan` is a blocking cmdlet that, on success, returns **no pipeline output at all** — it simply returns control to the shell when the scan finishes. It has no built-in `Write-Output`/`Write-Host` progress. Any progress it might report internally goes through PowerShell's *Progress* stream (`Write-Progress`), which is a distinct stream from stdout/stderr; even in an interactive console this renders as an ephemeral progress bar, not text, and here the process is launched with `-NonInteractive` and stdout is the *only* stream being read (`stderr=subprocess.STDOUT` merges only stdout+stderr, not the progress stream). Net effect: for a "quick scan" that can easily run 2–10+ minutes depending on the machine, the GUI's output pane will show **nothing** — not a single line — from start to finish, then the whole thing appears at once on exit. A user has no way to tell "still scanning" from "frozen app."
- **Compounding factor:** because SECURITY-category dispatch already showed a restore-point step and a MODERATE confirmation dialog before this action even starts, the perceived "is it stuck?" risk stacks on top of an already multi-step, already-opaque flow.
- **Exact command:** `Start-MpScan -ScanType QuickScan`
- **Not proposing a fix** (would require executor-level timeout/heartbeat/progress-stream plumbing — that's an executor change, out of scope for a YAML-only M08 catalog audit), but flagging it as the single most likely "is this tool broken?" support complaint this module can generate.

---

## 3. UAC registry read — completeness and interpretation

Command: `Get-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System' | Select-Object EnableLUA,ConsentPromptBehaviorAdmin,PromptOnSecureDesktop | Format-List`

### 3.1 Missing value: `ConsentPromptBehaviorUser`
- **Issue:** The action reads the *admin* prompt-behavior value (`ConsentPromptBehaviorAdmin`) but not the *standard-user* one. `ConsentPromptBehaviorUser` (0 = auto-deny elevation requests for standard users, 1 = prompt for credentials on the secure desktop, 3 = prompt for credentials — default) is arguably just as relevant to a "is UAC actually protecting this machine" status check, especially value `0`, which silently blocks standard users from ever elevating (a support-relevant, security-relevant state this action currently cannot surface).
- **Also worth including for completeness (still read-only, still a status check, not a hardening change):** `FilterAdministratorToken` (0/1 — whether Admin Approval Mode applies to the built-in Administrator account; default 0).
- **Proposed value (audit-scope, SAFE, read-only) — extend the same action rather than add a new one:**
  `Get-ItemProperty -Path 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Policies\System' | Select-Object EnableLUA,ConsentPromptBehaviorAdmin,ConsentPromptBehaviorUser,PromptOnSecureDesktop,FilterAdministratorToken | Format-List`

### 3.2 Blank fields on an unmodified machine (correctness gotcha, not a bug in the command)
- **Issue:** `Get-ItemProperty` only returns values that literally exist under that registry key. `EnableLUA` is reliably present (Windows always writes it), but `ConsentPromptBehaviorAdmin` and `PromptOnSecureDesktop` are **not always present** on a stock, never-touched-by-GPO consumer install — Windows applies its built-in defaults (`ConsentPromptBehaviorAdmin` defaults to `5`, `PromptOnSecureDesktop` defaults to `1`) in code, without necessarily writing them to the registry. On such a machine this command's `Format-List` output shows those two fields **blank**, not "5" and "1". A user (or support person) reading the output has no way to tell "blank = using the secure Windows default" apart from "blank = something is wrong."
- **Value shown today:** raw values or blanks, no default-fallback, no legend.

### 3.3 Interpretation guidance missing (raw numbers only)
- **Issue:** Even when populated, `ConsentPromptBehaviorAdmin` is an opaque 0–5 enum (0 = elevate without prompting, 1 = prompt for creds on secure desktop, 2 = prompt for consent on secure desktop, 3 = prompt for creds, 4 = prompt for consent, 5 = prompt for consent for non-Windows binaries — the out-of-box default). The action's description (`"Shows whether UAC is enabled and how elevation prompts behave, changes nothing."`) promises behavioral insight the raw number alone doesn't deliver — a user sees `ConsentPromptBehaviorAdmin : 5` with zero context about what "5" means or whether it's good/bad/default. `EnableLUA` (0/1) and `PromptOnSecureDesktop` (0/1) are more self-evident but still unlabeled.
- **This is a report-rendering concern, not a command-correctness one** — the existing `Format-List` approach is consistent with the rest of the catalog's style (raw cmdlet output, no post-processing anywhere in `executor.py`), so fixing it would be a catalog-wide pattern question (e.g., a YAML-declared value legend), not specific to M08 — flagging for awareness, not proposing a mechanism here.

---

## 4. SECURITY category → restore point ordering/risk interaction

Traced in `portablefix/gui/main_window.py`:

```python
needs_restore_point = action.risk == RiskLevel.DESTRUCTIVE or module.category in (
    ModuleCategory.REPAIR,
    ModuleCategory.SECURITY,
)
if needs_restore_point and not self._restore_point_attempted and not self.settings.dry_run:
    self._restore_point_attempted = True
    ...
    rp_runner.start()
    return
```

Per instructions, not re-litigating the mechanism itself (category-level gating regardless of per-action risk is a known, existing, coarse design). Two things specific to M08's composition make the coarseness land harder here than it might elsewhere:

- **4.1 — 3 of 5 M08 actions are pure reads, and all 3 still trigger it.** `sec_defender_status`, `sec_firewall_status`, and `sec_uac_status` are all `risk: SAFE` and their own descriptions explicitly claim "changes nothing" (`description_en`). Yet because the gate is `module.category in (..., SECURITY)` rather than keyed off the action's own risk, selecting *only* one of these three read-only checks still attempts to create a System Restore Point first — including the possible `restore_point_failed_confirm` warning dialog ("Could not create a System Restore Point... Continue anyway?") interrupting what the user believed was a single read-only status glance. No other current module has this high a proportion (60%) of read-only actions inside a category that forces the restore-point path, so M08 is the module where the mismatch between promised behavior ("changes nothing") and actual behavior (attempts a system-state-changing operation first) is most visible to users.
- **4.2 — Batch dedup means ordering matters once, not per-action.** `_restore_point_attempted` is set `True` immediately when the *first* queued action needing it is reached (before success/failure is even known), so if a user selects multiple M08 actions in one run, only one restore-point attempt happens for the whole batch, not one per action — this part is *not* worse for M08, it's the same dedup behavior as any other category and is working as intended. Flagging only to confirm it does **not** compound with finding 4.1 (i.e., checking all 5 M08 actions doesn't create 5 restore points, just 1 — the finding is about the *first* one being spent on what may be a pure status read, not about repetition).
- **4.3 — Risk-level miscategorization compounds this.** `sec_defender_update` is `risk: SAFE` despite mutating signature state (see HIGH finding 4 in the summary and section 1.2). Combined with 4.1, a user who runs "everything auto-checked by default" in M08 (the four SAFE actions, since `checked = action.risk == RiskLevel.SAFE` pre-selects them) gets: a restore point creation attempt, then three genuinely read-only checks, then one real mutating network operation — all under a single silent "SAFE, no confirmation needed" umbrella. Nothing here is catastrophic (signature updates are benign/reversible in practice), but it means "SAFE" in this module's YAML doesn't reliably mean "read-only," and the restore-point trigger and the mutating action end up bundled into the same no-confirmation default path.

**Not proposing a mechanism change** (moving the gate to per-action risk, or splitting SAFE-in-SECURITY from the restore-point trigger) — that's the coarse mechanism the task says not to re-litigate. Documenting only that M08's specific action mix (mostly-reads + one mistagged mutator) is where this existing behavior is most visible.

---

## 5. Blind spots within a status-audit scope (candidate future M08 actions)

All proposals below are **read-only status checks** consistent with M08's current identity (audit + report, changes nothing) — same spirit as the 3 existing SAFE reads. None of them change a setting. Anything that *would* change a setting is explicitly called out as **[HARDENING — NOT M08]** and should not be added to this module.

| # | Area | Proposed command | Issue / value |
|---|---|---|---|
| 5.1 | BitLocker status | `Get-BitLockerVolume \| Select-Object MountPoint,VolumeStatus,ProtectionStatus,EncryptionPercentage \| Format-Table -AutoSize` | **Edition gap, same class of issue raised for `Get-NetFirewallProfile`:** the `BitLocker` module (and this cmdlet) is not present on Windows Home SKUs — it errors as an unrecognized cmdlet there, not a clean "not encrypted" result. Needs to fail gracefully/be understood as "N/A on this edition," not "error." |
| 5.2 | Secure Boot state | `Confirm-SecureBootUEFI` | Throws (`Cmdlet not supported on this platform`) on legacy BIOS/CSM machines — this is a guaranteed, common, non-error condition (most older or BIOS-mode machines) that would surface as a raw red exception under this module's current "just print what the cmdlet says" style, same silent-error-quality concern as section 1. |
| 5.3 | TPM status | `Get-Tpm \| Select-Object TpmPresent,TpmReady,TpmEnabled,TpmActivated \| Format-List` | Generally reliable on Win10/11 client SKUs; low risk addition. |
| 5.4 | Local admin accounts | `Get-LocalGroupMember -SID "S-1-5-32-544" \| Select-Object Name,PrincipalSource \| Format-Table -AutoSize` | Use the well-known SID rather than the literal name `"Administrators"` — the group can be renamed; resolving by SID avoids a needless "group not found" failure mode. |
| 5.5 | RDP enabled | `Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections \| Select-Object fDenyTSConnections` | Classic **inverted-logic footgun**: `0` = RDP **enabled**, `1` = disabled. Exactly the "raw number shown without meaning" problem already flagged for UAC (section 3.3) — an inverted boolean is worse than a plain enum because a naive glance reads it backwards. |
| 5.6 | SMBv1 enabled | `Get-SmbServerConfiguration \| Select-Object EnableSMB1Protocol` | Prefer this over `Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol`, which goes through DISM, is noticeably slower (seconds, sometimes tens of seconds), and itself needs elevation to query — same "silent/slow pipeline" concern as section 2, avoidable here by picking the faster cmdlet. |
| 5.7 | Password-never-expires accounts | `Get-LocalUser \| Where-Object { $_.Enabled -and -not $_.PasswordExpires } \| Select-Object Name,Enabled,PasswordExpires \| Format-Table -AutoSize` | Straightforward; low risk. |
| 5.8 | Unquoted service paths | `Get-CimInstance Win32_Service \| Where-Object { $_.PathName -and $_.PathName -notmatch '^"' -and $_.PathName -match '\s' -and $_.PathName -match '\.exe' } \| Select-Object Name,PathName,StartMode \| Format-Table -AutoSize` | Heavier query (enumerates every service, ~150-300 on a typical machine) — noticeably slower than the module's other one-liners, though nowhere near `Start-MpScan` territory. **Also a correctness trap of its own:** naive unquoted-path regexes are known to both false-positive (paths with trailing arguments after a properly quoted exe) and false-negative (unusual quoting) — this one should be treated as a best-effort heuristic and labeled as such in its description, not presented as a definitive vulnerability list. |
| 5.9 | Autologon credentials in registry | `Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name AutoAdminLogon,DefaultUserName -ErrorAction SilentlyContinue \| Select-Object AutoAdminLogon,DefaultUserName` | **Must never select `DefaultPassword`,** even though that value can legitimately exist in cleartext under this exact key when autologon was configured by directly editing the registry (as opposed to Sysinternals Autologon, which encrypts it via an LSA secret). Given `report.py`/`audit_log.py` write 100% of an action's stdout verbatim into an on-disk HTML report and a JSONL audit log (see tool-posture note below), a future action that carelessly dumped the whole `Winlogon` key would write a plaintext admin/user password into two files sitting on the same portable USB drive. This is the one blind spot in this list with real severity if implemented carelessly — worth flagging as CRITICAL-if-mishandled even though the *fixed* version (name/flag only, never the password field) is safe. |
| 5.10 | WDigest | `Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' -Name UseLogonCredential -ErrorAction SilentlyContinue \| Select-Object UseLogonCredential` | `1` = plaintext credentials cached in LSASS memory (classic credential-dumping vector); default/absent is safe on current Windows. Same "raw 0/1/absent with no legend" concern as 3.3/5.5. |
| 5.11 | LSA protection (RunAsPPL) | `Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name RunAsPPL -ErrorAction SilentlyContinue \| Select-Object RunAsPPL` | `1`/`2` = LSASS runs as a protected process (harder to dump credentials from); absent/`0` = not protected. Same legend concern. |
| 5.12 | Exploit protection baseline | `Get-ProcessMitigation -System` | Returns a deeply nested object (each mitigation category is its own sub-object); the module's current "raw cmdlet output via Format-List" style doesn't flatten this well — would need either `Format-List *` (still nested) or explicit per-property `Select-Object` unwrapping to be readable. Flagging as an implementation-effort/output-quality issue, not a blocker. |

**[HARDENING — NOT M08, listed only for completeness of scope boundary]:** enabling BitLocker, turning on Secure Boot, disabling SMBv1, removing an admin account, forcing password expiry, quoting a service path, disabling WDigest, enabling RunAsPPL, applying exploit-protection settings — all of these are *setting changes*, not status reads. They belong in a future hardening module, not M08, and are explicitly out of scope here per the module's read-only-audit identity (mirroring the fact that only `sec_defender_update` and `sec_defender_quickscan` mutate anything today, and both already mutate *state*, not *configuration/settings*).

---

## Tool-posture note relevant to M08 specifically

- `portablefix/executor.py`'s `ActionRunner` applies **zero interpretation, filtering, or redaction** to any command's stdout — every action in every module is piped through identically, then handed verbatim to: (a) the live GUI output pane, (b) `audit_log.append_entry` → a plaintext JSONL file (`Logs/{run_id}.jsonl`, fields `command` + `output` + a SHA-256 hash *of that same unredacted output*), and (c) `report.generate_report` → an HTML `<pre>` block (HTML-escaped, but otherwise verbatim) plus a parallel JSON file. All of this lives on the portable medium itself.
- None of the 5 *current* M08 actions leak anything sensitive through this pipeline (Defender/firewall/UAC status fields are not secrets). This is purely a forward-looking constraint: **any future M08 status action must be designed with the assumption that whatever it prints to stdout will be permanently and verbatim written to two on-disk files** — there is no allowlist/redaction layer anywhere to catch a mistake (see 5.9 for the concrete case this matters for).
- Elevation is informational only (`elevation.is_admin()` drives a GUI pill/button in `main_window.py`); no `ActionDef` field gates dispatch on admin state, and none of the 5 M08 actions check it before running. This is the most likely real-world failure mode for `sec_defender_status`/`sec_defender_update`/`sec_defender_quickscan` (see section 1), more so than the 3rd-party-AV scenario the audit specifically asked about, because it doesn't require any special machine configuration to hit — just running the app without clicking "restart as admin" first.

---

## Files read

- `Modules/m08_security/actions.yaml`
- `portablefix/executor.py`
- `portablefix/gui/main_window.py`
- `portablefix/restore_point.py`
- `portablefix/models.py`
- `portablefix/elevation.py`
- `portablefix/report.py`
- `portablefix/audit_log.py`
- `portablefix/i18n.py` (confirm/warning dialog strings only)
