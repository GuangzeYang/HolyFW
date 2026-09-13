---
name: ad-discovery
description: Discovery-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers orientation and discovery techniques (host/port scan, user/group/share enum, BloodHound) using nmap, kerbrute, and impacket. Load this skill when the task names ad-discovery. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Discovery Skill

Load this skill **only** when the dispatched task names `ad-discovery`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

## Phase 0: Orientation

Before any attack, discover the domain context from the local foothold and store it.

- Technique id: `discovery.orientation`
- ATT&CK: T1082 (System Information Discovery), T1016 (System Network Configuration Discovery), T1482 (Domain Trust Discovery)

Commands:

```
whoami /upn
whoami /user
ipconfig /all
systeminfo
nltest /dclist:<domain>
net time /domain
```

Outputs:

```
python scripts/state.py set domain.name "<domain-fqdn>"
python scripts/state.py set domain.netbios "<netbios-name>"
python scripts/state.py set domain.domain_sid "<domain-sid>"
python scripts/state.py set domain.dc_ip "<primary-dc-ip>"
python scripts/state.py set domain.dc_fqdn "<primary-dc-fqdn>"
python scripts/state.py add domain.dcs '{"fqdn": "<dc-fqdn>", "ip": "<dc-ip>", "is_pdc": true}'
```

- `nltest /dclist:<domain>` lists **every** DC. Record each one with its own `add domain.dcs` entry (mark the PDC `is_pdc: true`), and set the singular `dc_ip`/`dc_fqdn` to the primary (PDC) so `-dc-ip` targets resolve.
- Derive `domain_sid` from `whoami /user`: take the user SID and drop the trailing RID (the part after the last `-`).
- This is information gathering, not an attack action, so no capture brackets are required.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


## 0.2 Security Software Discovery
- Technique id: `discovery.security-software`
- ATT&CK: T1518.001 (Security Software Discovery)

Purpose: identify antivirus/EDR products on the attack host so later actions can avoid detection.

Inputs: none (local foothold context).

Procedure (one atomic action):

```
powershell -c "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object displayName,productState | Format-List"
powershell -c "Get-MpComputerStatus | Select-Object AMRunningMode,RealTimeProtectionEnabled | Format-List"
```

Outputs: record the discovered products:

```
python scripts/state.py set domain.security_products '["<product1>", "<product2>"]'
```

Rollback: not applicable (re-run if the defensive stack changes).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


## 0.3 Local Group Enumeration
- Technique id: `discovery.local-groups`
- ATT&CK: T1069.001 (Permission Groups Discovery: Local Groups)

Purpose: enumerate local groups and the local Administrators membership (feeds the Local Elevation Protocol and host privilege mapping).

Inputs: none (local context).

Procedure (one atomic action):

```
net localgroup
net localgroup administrators
```

Outputs: record the discovered administrators:

```
python scripts/state.py set campaign.local_admins '["<account1>", "<account2>"]'
```

Rollback: re-run if local membership changes.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
frame.number == 0
```


# Phase 1: Discovery

## 1.1 Host Discovery (IP Scan)
- Technique id: `discovery.host-scan`
- ATT&CK: T1018 (Remote System Discovery)

Purpose: enumerate live hosts in the domain subnet.

Inputs: the target subnet from the `against subnet <cidr>` clause or `domain.dc_ip`.

Procedure (one atomic action):

```
nmap -sn <subnet>
```

For firewalled environments:

```
nmap -Pn -sn <subnet>
```

Outputs: append each live host to `hosts`:

```
python scripts/state.py add hosts '{"ip": "<ip>", "role": "unknown", "open_ports": [], "services": [], "compromised": false, "source": "discovery.host-scan"}'
```

Rollback: not applicable (a failed scan is simply re-run).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.src == <attacker-ip> && (icmp || arp || (tcp.flags.syn == 1 && tcp.flags.ack == 0))
```


## 1.2 Single-Host Port Scan
- Technique id: `discovery.port-scan`
- ATT&CK: T1046 (Network Service Discovery)

Purpose: enumerate open ports and services on one host.

Inputs: the target host object from `hosts` (or `domain.dc_ip`).

Procedure (one atomic action):

```
nmap -Pn -sT -p- <target-ip>
```

Focused AD-port scan:

```
nmap -Pn -p 53,88,135,139,389,445,464,593,636,3268,3269,5985,5986,9389 <target-ip>
```

Outputs: update the host object's `open_ports`, `services`, `os`, `role`:

```
python scripts/state.py merge hosts[<index>] '{"open_ports": [445, 135], "services": ["SMB", "MS-RPC"], "os": "<os>", "role": "<role>"}'
```

Rollback: not applicable.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.src == <attacker-ip> && ip.dst == <target-ip> && (icmp || arp || (tcp.flags.syn == 1 && tcp.flags.ack == 0) || tcp)
```


## 1.3 Username Enumeration (kerbrute)
- Technique id: `discovery.user-enum-kerbrute`
- ATT&CK: T1087.002 (Account Discovery: Domain Account)

Purpose: enumerate valid domain usernames without credentials (Kerberos pre-authentication behavior).

Inputs: `domain.name`, `domain.dc_ip`, and `wordlists.usernames` (the username wordlist path).

Procedure (one atomic action):

```
kerbrute userenum -d <domain.name> --dc <domain.dc_ip> <userlist>
```

Outputs: append each discovered username to `domain.usernames`:

```
python scripts/state.py add domain.usernames '"<username>"'
```

Rollback: if a username list is later found to contain dead accounts, re-run this technique to refresh it.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


## 1.4 Username Enumeration (LDAP)
- Technique id: `discovery.user-enum-ldap`
- ATT&CK: T1087.002 (Account Discovery: Domain Account)

Purpose: list all domain users via LDAP with valid credentials.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.GetADUsers -all -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Outputs: append enumerated usernames to `domain.usernames` and set the count:

```
python scripts/state.py add domain.usernames '"<username>"'
python scripts/state.py set domain.user_count <n>
```

Rollback: if the credential is rejected, `mark-stale` the user object and refresh it.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || tcp.port == 389 || tcp.port == 636)
```


## 1.5 SID Enumeration
- Technique id: `discovery.user-enum-sid`
- ATT&CK: T1087.002 (Account Discovery: Domain Account)

Purpose: enumerate accounts and SIDs with valid credentials.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.lookupsid <domain.name>/<user>:<password>@<domain.dc_ip>
```

Outputs: append enumerated accounts to `domain.usernames` and record SIDs on user objects:

```
python scripts/state.py add domain.usernames '"<username>"'
```

Rollback: if the credential is rejected, `mark-stale` the user object and refresh it.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (msrpc || smb || tcp.port == 445 || tcp.port == 135)
```


## 1.6 Network Share Enumeration
- Technique id: `discovery.share-enum`
- ATT&CK: T1135 (Network Share Discovery)

Purpose: enumerate SMB shares on a host.

Inputs: a host object from `hosts` (ip/fqdn), and a user object from `users` if the listing requires authentication.

Procedure (one atomic action):

```
net view \\<target-ip>
```

Alternative with impacket:

```
python -m impacket.examples.smbclient -no-pass -k <domain.name>/<user>@<target-fqdn>
```

Then run `shares` inside the client.

Outputs: record the share names on the host object:

```
python scripts/state.py merge hosts[<index>] '{"shares": ["Company_Data", "Public"]}'
```

Rollback: if a share list is stale, `mark-stale` the host object and re-run this technique.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || nbns || tcp.port == 445 || tcp.port == 139)
```


## 1.7 Domain Group Enumeration
- Technique id: `discovery.group-enum`
- ATT&CK: T1069.002 (Permission Groups Discovery: Domain Groups)

Purpose: enumerate domain security groups and their members.

Inputs: `domain.name`, optionally an account from `users`.

Procedure (one atomic action):

```
net group /domain
net group "Domain Admins" /domain
```

Outputs: record group names and map members onto user objects:

```
python scripts/state.py add domain.groups '"Domain Admins"'
python scripts/state.py merge users[<index>] '{"groups": ["Domain Admins"]}'
```

Rollback: if group membership is stale, `mark-stale` the user/group entry and re-run.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || nbns || tcp.port == 445 || tcp.port == 139)
```


## 1.8 Password Policy Discovery
- Technique id: `discovery.password-policy`
- ATT&CK: T1201 (Password Policy Discovery)

Purpose: read the domain password/lockout policy before spraying.

Inputs: none (domain context).

Procedure (one atomic action):

```
net accounts /domain
```

Outputs:

```
python scripts/state.py set domain.password_policy '{"lockout_threshold": 5, "min_password_length": 7, "max_password_age_days": 42}'
```

Rollback: not applicable (re-run if the policy changes).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || nbns || tcp.port == 445 || tcp.port == 139)
```


## 1.9 Domain Trust Discovery
- Technique id: `discovery.trust-enum`
- ATT&CK: T1482 (Domain Trust Discovery)

Purpose: enumerate trust relationships.

Inputs: none.

Procedure (one atomic action):

```
nltest /domain_trusts
nltest /trusted_domains
```

Outputs:

```
python scripts/state.py add domain.trusts '{"target": "child.corp.local", "direction": "bidirectional", "type": "parentchild"}'
```

Rollback: not applicable (re-run if the trust topology changes).

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || tcp.port == 389 || tcp.port == 636)
```


## 1.10 Host Identification (reverse DNS)
- Technique id: `discovery.host-identify`
- ATT&CK: T1018 (Remote System Discovery), T1040 (Network Sniffing - hostname resolution)

Purpose: resolve a host's FQDN and machine account name from its IP (fills fields required by Kerberos/ticket commands and RBCD).

Inputs: a host object with `ip` from `hosts`.

Procedure (one atomic action):

```
nslookup <target-ip>
ping -a <target-ip>
```

Outputs: derive the FQDN and the machine account name (hostname without domain + `$`) and write them back:

```
python scripts/state.py merge hosts[<index>] '{"fqdn": "<host>.<domain.name>", "machine_account": "<hostname>$"}'
```

Rollback: if the FQDN/machine account no longer resolves, `mark-stale` the host object and re-run this technique.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && (dns || icmp || udp.port == 53 || tcp.port == 53)
```


## 1.11 BloodHound Domain Mapping
- Technique id: `discovery.bloodhound`
- ATT&CK: T1087.002 (Account Discovery: Domain Account), T1482 (Domain Trust Discovery)

Purpose: collect domain objects, GPOs, ACLs, and attack paths over LDAP (BloodHound collection method).

Inputs: `domain.name`, `domain.dc_ip`, and a user object with `password` from `users`. Requires the `bloodhound` tool (installed via `pip install bloodhound`; runs as `python -m bloodhound`). If the tool is missing, record the missing capability in `notes` and skip this technique.

Procedure (one atomic action):

```
python -m bloodhound -c All -u <user> -p <password> -d <domain.name> -ns <domain.dc_ip> --dns-tcp
```

Outputs: record the produced JSON bundle:

```
python scripts/state.py add files '{"path": "<timestamp>_bloodhound.zip", "description": "BloodHound collection data"}'
```

Rollback: re-run if the collection is incomplete or stale.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || dns || kerberos || tcp.port == 389 || tcp.port == 636 || tcp.port == 88 || udp.port == 53)
```

