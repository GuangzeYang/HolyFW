# sysmon-collect

Exports **yesterday's** Sysmon and Security logon events to evtx at local midnight. It does not install Sysmon and does not load an XML config.

Run from an **Administrator PowerShell**. `soldier listen` and `attacker` do not start this process.

## 1. Configure Sysmon first (once, and again after any XML change)

```powershell
# All lab hosts (office roles, DC, attacker)
Sysmon64.exe -c <HolyFW>\attacker\sysmonconfig.xml

# Print the config that is currently loaded
Sysmon64.exe -c
```

This profile is sysmon-modular medium plus `holyfw_attacker_tool` (python/nmap/kerbrute EID 1 / 3) and `holyfw_exclude_kaspersky` (drop KES `avp.exe` ProcessAccess). The repo-root `sysmonconfig.xml` is the stock profile without those patches.

Enlarge the channel (`Sysmon64.exe -c` does not change this):

```powershell
wevtutil sl "Microsoft-Windows-Sysmon/Operational" /ms:2147483648
wevtutil gl "Microsoft-Windows-Sysmon/Operational"
```

## 2. Start the collector

```powershell
sysmon-collect
# or: python -m sysmon_collector
```

At local 00:00 it exports the previous calendar day (`00:00–24:00`):

- `soldier/logs/sysmon/sysmon_YYYY-MM-DD.evtx`
- `soldier/logs/sysmon/security_logon_YYYY-MM-DD.evtx` (logon/auth IDs such as 4624/4625/4768/4769/4776)

It does not export the still-open current day. Midnight export still runs if Sysmon is not running when observed.

`python -m dataset_processor` / `attacker extract` does not read these paths by itself. On Windows pass `--evtx` as `sysmon_*.evtx`, or first `python -m dataset_processor export-evtx` and on Linux pass the XML. Pass `security_logon_*.evtx` as `--security-evtx` (attacker) and/or `--dc-security-evtx` (DC) only on Windows when you want per-task Security slices. The mixed SPAN pcap passed as `--pcap` is never modified.

## 3. Optional environment variables

| Variable | Effect |
|----------|--------|
| `HOLYFW_SYSMON_LOG_DIR` | evtx output directory. Default `soldier/logs/sysmon/` (same relative path on the attacker host) |
| `HOLYFW_SYSMON` / `SYSMON` | Path to `Sysmon64.exe`. Default: search `PATH` |

`HOLYFW_SYSMON_CONFIG` is only used to resolve a file path. **The collector never runs `Sysmon64.exe -c`.** Load filter rules yourself with `Sysmon64.exe -c`.
