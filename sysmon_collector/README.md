# sysmon-collect

Exports **yesterday's** Sysmon and Security logon events to evtx at local midnight. It does not install Sysmon and does not load an XML config.

Run from an **Administrator PowerShell**. `soldier listen` and `attacker` do not start this process.

## 1. Configure Sysmon first (once, and again after any XML change)

```powershell
# Office hosts / DC
Sysmon64.exe -c <HolyFW>\sysmonconfig.xml

# Attacker host only (do not use the repo-root XML)
Sysmon64.exe -c <HolyFW>\attacker\sysmonconfig.xml

# Print the config that is currently loaded
Sysmon64.exe -c
```

The attacker profile logs process create and network connect for python/nmap/kerbrute (EID 1 / 3) and **does not log EID 10** (ProcessAccess), so AV process-access noise cannot fill the default 64MB channel and wipe the day's events.

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

`attacker extract` does not read these paths by itself; pass that day's attacker `sysmon_*.evtx` as `--evtx`.

## 3. Optional environment variables

| Variable | Effect |
|----------|--------|
| `HOLYFW_SYSMON_LOG_DIR` | evtx output directory. Default `soldier/logs/sysmon/` (same relative path on the attacker host) |
| `HOLYFW_SYSMON` / `SYSMON` | Path to `Sysmon64.exe`. Default: search `PATH` |

`HOLYFW_SYSMON_CONFIG` is only used to resolve a file path. **The collector never runs `Sysmon64.exe -c`.** Load filter rules yourself with `Sysmon64.exe -c`.
