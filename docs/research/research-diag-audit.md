# PortableFix Diagnostics Catalog Audit — M01, M07, M10, M12

Scope: `Modules/m01_diagnostics/actions.yaml`, `Modules/m07_autoruns/actions.yaml`,
`Modules/m10_drivers/actions.yaml`, `Modules/m12_online/actions.yaml`, read against
`portablefix/executor.py` (Windows PowerShell 5.1 via `powershell -NoProfile
-NonInteractive -ExecutionPolicy Bypass -Command`, stdout+stderr merged, read line by
line, NUL bytes stripped, **no timeout anywhere in the process lifecycle**).

All 21 existing actions across the 4 files are correctly labeled `risk: SAFE` — every
command is a pure read/enumerate operation (no `Set-`/`Remove-`/`Stop-`/`New-`/`Disable-`/
`Enable-` cmdlet appears anywhere in these 4 files). **No risk-mislabeling found.** All
YAML string-escaping (`\"`, `\\`) was hand-decoded and every command is syntactically
valid PowerShell — no quoting bugs found. No deprecated CIM class or removed cmdlet is
used anywhere in the current catalog (see "Verified clean" at the end).

## Severity counts

| Severity | Count |
|---|---|
| CRITICAL | 3 |
| IMPORTANT | 13 |
| MINOR | 9 |
| **Total actionable findings** | **25** |

(Plus 4 "verified clean" confirmations and 1 explanatory note, not counted above.)

---

## Cross-cutting finding (applies to all 4 files)

### X-1. [CRITICAL] `portablefix/executor.py` — no timeout/kill mechanism for any action

`ActionRunner.run()` (lines 37-64) calls `subprocess.Popen(...)` (lines 45-52), then
blocks on `for raw_line in process.stdout` and `process.wait()` (line 59). There is no
`timeout=` argument, no `QTimer`/watchdog, and no `process.terminate()` call except
inside the generic `except Exception` handler, which only fires on a raised exception —
never on a command that is simply slow-to-never-return. `-NonInteractive` only suppresses
interactive *prompts*; it does not bound execution time.

Practical consequence: because `ActionRunner` is a `QThread`, a hung action won't freeze
the whole GUI — but that one action's output pane will sit frozen forever with no
progress indicator, no automatic recovery, and no way to tell "still working" from
"stuck" apart from the process list. This is most dangerous precisely where this tool is
most needed: `Get-ScheduledTask` (M07) depends on the Task Scheduler service,
`Get-PnpDevice`/`pnputil` (M10) depend on PnP/DevQuery, `Get-CimInstance` (M01) depends on
the WMI service — all three are common failure points on genuinely broken Windows
installs, i.e. exactly the machines this tool is run against. `Resolve-DnsName`/
`Test-Connection` (M12) against a black-holed host/DNS server are the classic
network-hang case.

This can't be fixed inside the YAML catalogs alone (it's an `executor.py` gap), but two
YAML-level mitigations are given below for the worst offenders (M12 `-QuickTimeout`,
finding M12-3).

---

## M01 — `Modules/m01_diagnostics/actions.yaml`

### M01-1. [MINOR] `computer_info` (command at line 16) — memory shown in raw bytes
`TotalPhysicalMemory` prints as a raw byte integer (e.g. `17179869184`), not GB.

```powershell
Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,@{N='TotalPhysicalMemoryGB';E={[math]::Round($_.TotalPhysicalMemory/1GB,2)}},Domain | Format-List
```

### M01-2. [MINOR] `bios_info` (line 24) — unfiltered `Format-List` is noisy
No `Select-Object` means every Win32_BIOS property is dumped. Curate it and add
`SerialNumber`/`ReleaseDate` (useful for warranty/firmware-age checks, currently buried):

```powershell
Get-CimInstance Win32_BIOS | Select-Object Manufacturer,Name,SMBIOSBIOSVersion,ReleaseDate,SerialNumber | Format-List
```

### M01-3. [MINOR] `cpu_info` (line 32) — missing throttling/thread signals
`MaxClockSpeed` is the nominal spec, not actual running speed — no way to see thermal/
power throttling. Also missing logical processor count.

```powershell
Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed,CurrentClockSpeed,LoadPercentage | Format-List
```

### M01-4. [IMPORTANT] `memory_info` (line 40) — raw-byte `Capacity` + missing `-AutoSize`
`Capacity` is a per-DIMM byte count (e.g. `8589934592`) shown in a `Format-Table` with no
`-AutoSize`, so it's both unreadable and prone to column misalignment.

```powershell
Get-CimInstance Win32_PhysicalMemory | Select-Object BankLabel,DeviceLocator,@{N='CapacityGB';E={[math]::Round($_.Capacity/1GB,2)}},Speed,ConfiguredClockSpeed,Manufacturer | Format-Table -AutoSize
```

### M01-5. [IMPORTANT] `volumes` (line 48) — `Select-Object` silently defeats `Get-Volume`'s built-in friendly-size formatting
`Get-Volume` alone renders `Size`/`SizeRemaining` as human-readable strings (e.g. "465.63
GB") via a format view keyed to the CIM type name
`...CimInstance#ROOT/Microsoft/Windows/Storage/MSFT_Volume`. Piping through
`Select-Object` changes the object's type name to `Selected.Microsoft.Management...`,
which has no matching format view, so `Format-Table` falls back to printing the raw
`UInt64` byte counts instead. As written, this action's numbers will be much less
readable than plain `Get-Volume` would produce. Also missing `-AutoSize`.

```powershell
Get-Volume | Select-Object DriveLetter,FileSystem,HealthStatus,@{N='SizeRemainingGB';E={[math]::Round($_.SizeRemaining/1GB,2)}},@{N='SizeGB';E={[math]::Round($_.Size/1GB,2)}} | Format-Table -AutoSize
```

### M01-6. [MINOR] `physical_disks` (line 56) — raw-byte `Size`, missing `-AutoSize`, no real health data
`HealthStatus` is almost always just "Healthy" until something is badly wrong; there's no
SMART-style signal (wear, temperature, error counters) for early warning. In-box fix via
Storage Spaces reliability counters (caveat: often returns blank/zero on USB-bridged or
hardware-RAID-abstracted disks — call this out in the description text so it isn't read
as "silently broken"):

```powershell
Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus,@{N='SizeGB';E={[math]::Round($_.Size/1GB,2)}} | Format-Table -AutoSize
# optional companion line:
Get-PhysicalDisk | Get-StorageReliabilityCounter | Select-Object DeviceId,Wear,Temperature,ReadErrorsTotal,WriteErrorsTotal,PowerOnHours | Format-Table -AutoSize
```

### M01-7. [CRITICAL] `recent_hotfixes` (line 64) — `Get-HotFix` materially under-reports patch status on Windows 10/11
`Get-HotFix` wraps `Win32_QuickFixEngineering`, which was never updated to reflect
component-based-servicing (CBS) updates — i.e. it does **not** show most monthly
cumulative updates or feature updates on Windows 10/11, only a narrow slice of
standalone-MSU-style patches. A machine that's fully current can show this action
returning almost nothing, or a handful of old entries, which reads as "this system is
barely patched" — actively misleading for a diagnostic tool whose job is accurate
assessment. This is a data-completeness gap, not a deprecation (the cmdlet still runs
fine), but the output is diagnostically wrong. Replace with the Windows Update Agent COM
history, which reflects real update history including cumulative/feature updates and
needs no external module:

```powershell
$s = New-Object -ComObject Microsoft.Update.Session; $r = $s.CreateUpdateSearcher(); $h = $r.QueryHistory(0,$r.GetTotalHistoryCount()); $h | Sort-Object Date -Descending | Select-Object -First 20 Title,Date,@{N='Result';E={$_.ResultCode}} | Format-Table -AutoSize -Wrap
```

### M01-8. [MINOR] `defender_status` (line 72) — no error handling for the common "3rd-party AV present" case
No `-ErrorAction`/try-catch: on any machine where Defender is uninstalled or its
module/namespace isn't available (common once a 3rd-party AV takes over), this throws a
raw, multi-line PowerShell error block instead of a clean message.

```powershell
try { Get-MpComputerStatus -ErrorAction Stop | Format-List } catch { Write-Output ('Windows Defender status unavailable: ' + $_.Exception.Message) }
```
Blind spot: this only ever reports on *Defender's own* state. It never tells you whether
a different AV is registered/active. Windows' own Security Center exposes that via the
(undocumented but stable since Vista) `root\SecurityCenter2` namespace:
```powershell
Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | Select-Object displayName,productState | Format-Table -AutoSize
```

### M01-9. [IMPORTANT] `top_cpu_processes` (line 80) — label promises "current load", command measures cumulative CPU-time
`Sort-Object CPU` sorts by total CPU-**seconds** consumed since each process started, not
current utilization — a long-running idle service can rank above something actually
busy right now. The English label "Top CPU-consuming processes" implies a live-load
snapshot, which this is not. Also: `Format-Table` has neither `-AutoSize` nor `-Wrap`, so
the `Path` column (the one field that disambiguates which of several same-named
processes is which) is the most likely to get truncated; `WS` is raw bytes.

```powershell
Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Name,Id,CPU,@{N='WorkingSetMB';E={[math]::Round($_.WS/1MB,1)}},Path | Format-Table -AutoSize -Wrap
```
(Getting a true "right now" CPU% snapshot needs two time-spaced samples, e.g. via
`Get-Counter '\Process(*)\% Processor Time'` twice with a short `Start-Sleep` between —
worth a separate action rather than silently changing this one's semantics.)

### M01-10. [IMPORTANT] Inconsistent `-AutoSize` usage
5 of M01's 10 actions use `Format-Table` with **no** `-AutoSize`
(`memory_info` L40, `volumes` L48, `physical_disks` L56, `recent_hotfixes` L64,
`top_cpu_processes` L80), while every `Format-Table` call in M07/M10/M12 consistently
includes it. Under a non-interactive redirected console (exactly how `executor.py` runs
these), `Format-Table`'s column-width guess is unreliable and can truncate/misalign
columns. Add `-AutoSize` to all five for consistency with the rest of the catalog.

### M01-11. [IMPORTANT] Blind spot — no pending-reboot check anywhere
Nothing in M01 (or the whole catalog) answers "is this machine waiting on a reboot to
finish servicing?" — a basic, high-value, zero-risk diagnostic:

```powershell
$pending = @()
if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $pending += 'CBS RebootPending' }
if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $pending += 'Windows Update RebootRequired' }
if ((Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations) { $pending += 'PendingFileRenameOperations' }
if ($pending.Count -eq 0) { Write-Output 'No reboot pending.' } else { Write-Output ('Reboot pending: ' + ($pending -join ', ')) }
```

### M01-12. [IMPORTANT] Blind spot — no event-log / crash history check anywhere
Nothing in the whole catalog surfaces recent System-log Critical/Error entries or BSOD
history, arguably the single highest-value thing missing from a "diagnostics" category:

```powershell
$events = Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 25 -ErrorAction SilentlyContinue
if ($events) { $events | Select-Object TimeCreated,ProviderName,Id,Message | Format-List } else { Write-Output 'No System log Critical/Error events in the last 7 days.' }
```
A BSOD-specific variant (Source "BugCheck", Event ID 1001) could be a second, cheap
addition.

### M01-13. [MINOR] Blind spot — no computed uptime
`os_info` shows `LastBootUpTime` as a raw timestamp; nothing turns it into "up for N
days" (CIM auto-converts this to a real `[datetime]`, so no DMTF-parsing workaround is
even needed):
```powershell
$os = Get-CimInstance Win32_OperatingSystem; Write-Output ('Uptime: ' + ((Get-Date) - $os.LastBootUpTime))
```

### M01-14. [MINOR] Blind spot — no battery status for laptops
`powercfg /batteryreport` writes an HTML file, not console text, so it's a poor fit for
this "plain text console" architecture (best case output is just a file path). A direct
CIM query is a better fit and stays in-console:
```powershell
Get-CimInstance Win32_Battery | Select-Object Name,BatteryStatus,EstimatedChargeRemaining,DesignVoltage | Format-List
```

*Note on temperature sensors (not a numbered finding):* deliberately **not**
recommending this. `Get-CimInstance -Namespace root/wmi -ClassName
MSAcpi_ThermalZoneTemperature` is the only in-box option and it frequently returns
nothing at all on modern OEM hardware (vendors commonly lock out ACPI thermal zone
exposure) — exactly the "command silently produces nothing" failure mode the audit
brief warns about. Current omission is the right call; only add with an explicit
"may be unsupported on this hardware" caveat in the description text.

---

## M07 — `Modules/m07_autoruns/actions.yaml`

### M07-1. [IMPORTANT] `autoruns_registry_run` (line 8) — misses all 32-bit autostart entries
The `foreach` list checks only the native `HKLM`/`HKCU` `Run`/`RunOnce` keys. On every
64-bit Windows install (the overwhelming majority), a 32-bit process's view of
`HKLM:\Software\...\Run` is redirected to `HKLM:\Software\WOW6432Node\...\Run` — but a
64-bit PowerShell process reading the plain path does **not** see WOW6432Node content at
all. Any 32-bit application's Run-key autostart entry (still extremely common: older
Adobe/Java/print-driver/OEM utilities) is completely invisible to this action.

```powershell
foreach ($path in 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\Microsoft\Windows\CurrentVersion\RunOnce','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce','HKCU:\Software\Microsoft\Windows\CurrentVersion\Run','HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce') { Write-Output ('=== ' + $path + ' ==='); Get-ItemProperty -Path $path -ErrorAction SilentlyContinue | Format-List }
```
(Safe to always include — on a hypothetical 32-bit Windows the path simply doesn't
exist and `-ErrorAction SilentlyContinue` already handles that.)

### M07-2. [MINOR] `autoruns_registry_run` (line 8) — `Get-ItemProperty` output is cluttered with PowerShell plumbing
`Get-ItemProperty` always attaches `PSPath`, `PSParentPath`, `PSChildName`, `PSDrive`,
`PSProvider` note properties, so every `Format-List` block prints 5 irrelevant
provider-internals lines mixed in with the actual autostart values.
```powershell
Get-ItemProperty -Path $path -ErrorAction SilentlyContinue | Select-Object * -ExcludeProperty PS* | Format-List
```

### M07-3. [IMPORTANT] `autoruns_scheduled_tasks` (line 22) — omits the one field that matters: what the task runs
Only `TaskName,TaskPath,State` are shown. For a "what's misbehaving / what's persisting"
diagnostic, the actual command line (`Actions.Execute` + `Actions.Arguments`) is the
whole point — without it, a suspicious task name is unverifiable and a boring name could
be hiding a malicious payload path.
```powershell
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' } | Select-Object TaskName,TaskPath,State,@{N='Action';E={($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join '; '}} | Format-Table -AutoSize -Wrap
```

### M07-4. [IMPORTANT] `autoruns_autostart_services` (line 29) — wrong cmdlet family for the data needed
`Get-Service` (ServiceController-backed) has no `PathName`/run-as-account property at
all — it structurally cannot show the service's binary path, which is exactly what you
need to spot a hijacked or fake service. `Get-CimInstance Win32_Service` has both
`PathName` and `StartName`:
```powershell
Get-CimInstance Win32_Service | Where-Object { $_.StartMode -eq 'Auto' } | Select-Object Name,DisplayName,State,StartName,PathName | Format-Table -AutoSize -Wrap
```
Note `Win32_Service.StartMode` values are `'Auto'/'Manual'/'Disabled'`, not
`Get-Service`'s `'Automatic'/'Manual'/'Disabled'` — the filter value must change too, not
just the cmdlet.

---

## M10 — `Modules/m10_drivers/actions.yaml`

### M10-1. [IMPORTANT] `drv_problem_devices` (line 8) — shows *that* a device has a problem, never *why*
`Status,Class,FriendlyName,InstanceId` is a good start, but omits the actual Device
Manager problem/error code (Code 28 = drivers not installed, Code 43 = device reported
failure, Code 10 = can't start, etc.), which is the detail that actually determines the
fix path. Also doesn't pass `-PresentOnly`, so long-disconnected "ghost" devices (an old
USB printer, a phone plugged in once) add noise to what should be an actionable list.
```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.Status -ne 'OK' } | ForEach-Object { $_ | Add-Member -NotePropertyName ProblemCode -NotePropertyValue (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName 'DEVPKEY_Device_ProblemCode' -ErrorAction SilentlyContinue).Data -PassThru } | Select-Object Status,Class,FriendlyName,ProblemCode,InstanceId | Format-Table -AutoSize -Wrap
```

### M10-2. [MINOR] Blind spot — no per-device driver version/date audit
`drv_third_party_list` (`pnputil /enum-drivers`, line 15) only lists staged third-party
driver *packages* in the Driver Store — it doesn't cover inbox/Microsoft drivers
currently bound to hardware, and isn't sorted by age. `Win32_PnPSignedDriver` (still
fully functional on Win10/11, just not receiving new properties) covers every currently
active driver with a date, letting a stale one (e.g. a 2013 chipset/network driver on a
2026 machine) jump out immediately:
```powershell
Get-CimInstance Win32_PnPSignedDriver | Select-Object DeviceName,DriverVersion,DriverDate,Manufacturer,IsSigned | Sort-Object DriverDate | Format-Table -AutoSize -Wrap
```

*(`drv_third_party_list` itself — `pnputil /enum-drivers` at line 15 — is correct,
current, and one of the cleanest actions in the whole catalog: plain native console text
with no `Format-Table` truncation risk. No change needed.)*

---

## M12 — `Modules/m12_online/actions.yaml`

### M12-1. [IMPORTANT] `online_connectivity_ladder` (line 8) — ICMP-only "internet" rung produces false negatives on common networks
The "internet" rung is `Test-Connection` to `8.8.8.8` (ICMP only). Many real corporate/
ISP/hotel networks block raw ICMP outright while HTTP/HTTPS works perfectly — this
ladder will report "internet: fail" on plenty of genuinely healthy connections. Add a
TCP/HTTPS-layer rung, which is far less likely to be filtered:
```powershell
Write-Output '--- HTTPS to www.microsoft.com:443 ---'; Test-NetConnection -ComputerName www.microsoft.com -Port 443 | Select-Object ComputerName,RemotePort,TcpTestSucceeded | Format-Table -AutoSize
```
(The existing choice to ping the fixed IP `8.8.8.8` instead of a hostname, to separate
network-layer from DNS-layer failures, is good design — not flagged.)

### M12-2. [IMPORTANT] `online_dns_benchmark` (line 15) — hardcoded Slovak string leaks into English-locale output
`Write-Output 'Aktualny DNS:'` is Slovak ("Current DNS:") hardcoded directly into the
`command` string. Every other embedded output string in this catalog
(`online_connectivity_ladder`'s `'Gateway: '`, `'--- 8.8.8.8 ---'`, etc.,
`autoruns_registry_run`'s `'=== ' + $path + ' ==='`) is English/locale-neutral. This is
the one exception, and since the label/description bilingual split (`label_sk`/
`label_en`) implies an English UI mode exists, English-locale users will see a stray
Slovak line in an otherwise-English diagnostic. One-word fix:
```powershell
Write-Output 'Current DNS:'; (Measure-Command { Resolve-DnsName microsoft.com -ErrorAction SilentlyContinue }).TotalMilliseconds; Write-Output 'Google DNS (8.8.8.8):'; (Measure-Command { Resolve-DnsName microsoft.com -Server 8.8.8.8 -ErrorAction SilentlyContinue }).TotalMilliseconds
```

### M12-3. [CRITICAL] `online_dns_benchmark` (line 15) — the benchmark is structurally biased against the alternate DNS server
`Resolve-DnsName microsoft.com` (no `-Server`) is served through the OS-level DNS Client
cache (Dnscache service) — if `microsoft.com` was resolved by *anything* on the machine
recently, this call can return near-instantly regardless of true resolver speed.
`Resolve-DnsName microsoft.com -Server 8.8.8.8` always issues a direct query to that
server, bypassing the local cache service entirely. The two measurements are therefore
not comparable: the "current DNS" side gets a free chance at a cache hit while the
"Google DNS" side never does, so 8.8.8.8 will systematically look slower than it really
is. This defeats the entire purpose of the action. Minimal fix — clear the cache before
the first measurement so both sides start cold (note: clearing the DNS client cache may
require elevated PowerShell in some configurations — verify against this tool's
elevation model before shipping):
```powershell
Write-Output 'Current DNS:'; Clear-DnsClientCache; (Measure-Command { Resolve-DnsName microsoft.com -ErrorAction SilentlyContinue }).TotalMilliseconds; Write-Output 'Google DNS (8.8.8.8):'; (Measure-Command { Resolve-DnsName microsoft.com -Server 8.8.8.8 -ErrorAction SilentlyContinue }).TotalMilliseconds
```

### M12-4. [IMPORTANT] `online_connectivity_ladder` + `online_dns_benchmark` — unbounded `Resolve-DnsName` calls, no timeout anywhere in the executor
Combined with the executor-wide finding (X-1), the four `Resolve-DnsName` calls across
these two actions (lines 8 and 15) are the single most likely thing in this whole catalog
to hang for an uncomfortably long time: a DNS server that silently drops packets (rather
than refusing the connection) makes the default resolver retry/timeout cycle run long,
with zero bound from `executor.py`. `Resolve-DnsName` has a built-in switch for exactly
this scenario:
```powershell
Resolve-DnsName microsoft.com -QuickTimeout -ErrorAction SilentlyContinue
Resolve-DnsName microsoft.com -Server 8.8.8.8 -QuickTimeout -ErrorAction SilentlyContinue
```
Add `-QuickTimeout` to all four `Resolve-DnsName` invocations (2 in
`online_connectivity_ladder`, 2 in `online_dns_benchmark`).

*(`online_proxy_check`, line 22, is well designed — it checks both the system-level
WinHTTP proxy (`netsh winhttp show proxy`, used by Windows Update/BITS/services) and the
per-user WinINet proxy (registry), which are genuinely different settings that fail
independently. No change needed. Validating the content of `AutoConfigURL` would require
an online fetch with its own timeout risk — reasonable to leave out under the current
one-liner/no-extra-hang-risk constraints.)*

---

## Verified clean (checked, no issue found)

1. **No deprecated CIM classes or removed cmdlets** anywhere in the 4 files. All of
   `Win32_OperatingSystem`, `Win32_ComputerSystem`, `Win32_BIOS`, `Win32_Processor`,
   `Win32_PhysicalMemory`, `Get-Volume`, `Get-PhysicalDisk`, `Get-HotFix`,
   `Get-MpComputerStatus`, `Get-Process`, `Get-ItemProperty`, `Get-ChildItem`,
   `Get-ScheduledTask`, `Get-Service`, `Get-PnpDevice`, `pnputil`, `Get-NetRoute`,
   `Test-Connection`, `Resolve-DnsName`, `netsh` are current and fully functional on
   Windows 10/11 as of 2026.
2. **No YAML/PowerShell quoting bugs.** Every `\"`/`\\` escape in the YAML decodes to
   syntactically valid PowerShell (verified by hand for every multi-quote command:
   `autoruns_registry_run`, `autoruns_startup_folder`, `online_proxy_check`).
3. **Risk levels are 100% accurate.** All 21 actions across these 4 files are correctly
   `SAFE` — none perform a write/mutating operation.
4. `drv_third_party_list` (M10) and `online_proxy_check` (M12) need no changes — see
   notes inline above.

## Note on `powershell` vs `pwsh`
`executor.py` invokes Windows PowerShell 5.1 (`powershell`, not PowerShell 7's `pwsh`),
which is the correct choice for `Test-Connection -ComputerName` as used in
`online_connectivity_ladder` — that parameter set behaves differently in PS7's rewritten
`Test-Connection`. Not a current bug, just a coupling to be aware of if the executor is
ever changed to invoke `pwsh`.
