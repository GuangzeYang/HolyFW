---
name: ad-attack
description: Shared Active Directory attack runtime for an attacker agent on a domain-joined Windows host. Holds state.json, changes.json, capture/state scripts, and the mandatory execution protocol (pre-flight, capture brackets, write-back, rollback, and the per-task display-filter file). Do not invoke this skill for a technique. Use the phase skill named in the task (ad-discovery, ad-credential, ad-lateral, ad-collection, ad-persistence).
---

# AD Attack Runtime

## Overview

This is the **shared runtime** for attacker phase skills. It does not contain a technique catalog. Load the phase skill named in the dispatched task:

| Technique prefix | Skill |
|------------------|-------|
| `discovery.*` | `ad-discovery` |
| `credential.*` | `ad-credential` |
| `lateral.*` | `ad-lateral` |
| `collection.*` | `ad-collection` |
| `persistence.*` | `ad-persistence` |

Always `cd` to this skill root (`~/.config/opencode/skills/ad-attack`) before `python scripts/...`. Attacks stay object-driven and stateful:

- `state.json` is the single source of truth for every command parameter. It holds three knowledge partitions — `domain`, `hosts`, and `users` — plus runtime `tickets`, `files`, `techniques`, and `notes`.
- `changes.json` is the operator rollback ledger: every technique that mutates the target AD (new user, machine account, password reset, RBCD, DC config) must append one record. HolyFW does not revert the domain; the operator uses this file by hand.
- Each technique id maps to exactly one command in the matching phase skill.
- Every atomic action is bracketed by log capture. Live tshark is disabled. After the task, write a display-filter-only `{task_id}.txt`. Offline extract first slices the domain SPAN by the task time window, then applies that expression.

## Directory Layout

```
attacker/skills/
├── ad-attack/                  # this runtime (scripts + state)
│   ├── SKILL.md
│   ├── config.json
│   ├── state.json
│   ├── changes.json
│   ├── scripts/
│   │   ├── check_environment.py
│   │   ├── state.py
│   │   ├── changes.py
│   │   ├── capture_traffic.py
│   │   ├── capture_logs.py
│   │   ├── write_filter.py     # writes {task_id}.txt display filter
│   │   └── elevate.py
│   └── wordlists/
├── ad-discovery/SKILL.md
├── ad-credential/SKILL.md
├── ad-lateral/SKILL.md
├── ad-collection/SKILL.md
└── ad-persistence/SKILL.md
```

Log captures are written to `attacker/logs/YYYY-MM-DD/` when the scheduler sets `HOLYFW_ATTACKER_OUTPUT_DIR`. File names are `{task_id}_{technique-id}_{channel}.evtx`. The same directory receives `{task_id}.md` (transcript) and `{task_id}.txt` (display filter). Per-task pcaps are **not** written during the skill run — `attacker extract --date YYYY-MM-DD` slices the domain SPAN by the transcript time window, then applies `{task_id}.txt`. `config.json` `output_dir` is only the fallback for a manual skill run.

All script invocations below use `python`; run them from this skill root so relative paths resolve correctly.

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

Additional tools used by the extended techniques:

| Tool | Used by | Invocation (PATH-independent) |
|------|---------|-------------------------------|
| `bloodhound` (bloodhound-python) | `discovery.bloodhound` | `python -m bloodhound -c All -u <user> -p <pw> -d <domain> -ns <dc-ip> --dns-tcp` (install: `pip install bloodhound`) |
| `comsvcs.dll` (built-in) | `credential.lsass-dump` | `rundll32 C:\windows\system32\comsvcs.dll, MiniDump <pid> <out> full` |
| `winrs` (built-in) | `lateral.exec-winrm` | `winrs -r:<fqdn> -u:<domain>\<user> -p:<pw> "cmd /c <cmd>"` |
| `schtasks` (built-in) | `lateral.exec-schtasks` | `schtasks /create /s <ip> /u <user> /p <pw> /tn <name> /tr <cmd> /sc once ...` |
| `sc` (built-in) | `persistence.service` | `sc create <name> binPath= "<cmd>" start= auto` (via remote shell) |
| `tar` / `Compress-Archive` (built-in) | `collection.archive` | `tar -cf ...` or `powershell -c "Compress-Archive ..."` |
| `pypykatz` / `mimikatz` (offline) | `credential.lsass-dump` parse | operator step, not an attack action |

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

> **PowerShell 5.1 native-argument quoting.** When you call `python scripts/state.py <cmd> <path> '<json>'` from PowerShell, the shell strips the inner double quotes of a JSON argument before it reaches Python (`state.py` then fails with "Expecting property name enclosed in double quotes"). Escape the quotes first — build the JSON in a variable and pass `($json -replace '"','\"')`:
>
> ```
> $merge = '{"fqdn": "i1-dc1-c01.ndrtest.local", "machine_account": "I1-DC1-C01$"}'
> python scripts/state.py merge hosts[1] ($merge -replace '"','\"')
> ```
>
> The same trick applies to `add`/`set`/`merge`/`changes.py add`. Alternatively use a Python one-liner that loads `state.py` directly to avoid shell quoting entirely:
>
> ```
> python -c "import sys; sys.path.insert(0,'scripts'); import state; d=state._load(); ...; state._save(d)"
> ```

## Mandatory Execution Protocol

Every attack task MUST follow this exact sequence. Do not skip steps.

### Step 0 — Pre-flight check (once per day of attack activity)

Run:

```
python scripts/check_environment.py
```

Decide what to do with the output using this gate (do **not** simply report and stop):

- `"ok": true` (warnings are acceptable) → continue to Step 1.
- `"ok": false` **and the current user already has the needed permission** — i.e. `admin.is_admin` is `true` **and** every `channels_readable.*.readable` is `true` (e.g. the agent process is already elevated / running as SYSTEM) → continue to Step 1. No elevation is performed.
- `"ok": false` **because of a permission shortfall** — `admin.is_admin` is `false`, or any `channels_readable.*.readable` is `false` (access denied, returncode 5), or `auditpol` reports 0x00000522 — and the **current user's live probes genuinely fail** → run the **Local Elevation Protocol** below, re-run `check_environment.py` elevated to confirm `ok: true`, then continue to Step 1.
- `"ok": false` **for any other reason** (missing executable, impacket not runnable, etc.) → do **not** stop the task: record the reason in `notes`, skip the techniques that depend on the missing capability (e.g. `.evtx` export) and keep running the rest. Never attempt to substitute a missing tool or hallucinate a command.

The gate is judged by **live capability probes** (`wevtutil qe`, `auditpol`), never by group membership alone: a user can be a member of the local `Administrators` group while its token is UAC-filtered (Medium integrity) and cannot read the logs.

The check also guarantees impacket is actually runnable: it reports `python_executable` (the interpreter running the check — the same `python` the attack commands below will use) and `impacket.impacket_file` (where impacket lives), and it executes `python -m impacket.examples.secretsdump --help` as a live proof. If those do not line up, `ok` is `false`.

> **Elevation requirement.** Capturing `.evtx` requires read access to the Sysmon **and Security** logs: `capture_logs.py stop` runs `wevtutil epl` on each configured channel, which fails with "access denied" for unprivileged users. The pre-flight report probes this directly — `channels_readable` runs `wevtutil qe <log> /c:1` per channel (Sysmon + Security) as a live capability test (and is the authoritative gate: `ok` is `false` if any channel is unreadable). `admin.is_admin` is a reference field only, since elevation does not guarantee log access and, conversely, SYSTEM can read the logs without being in the Administrators group. Run the attacker agent elevated (e.g. a local administrator or SYSTEM) so every atomic action produces its `.evtx` captures.

> **Local elevation (when permission is denied on the attack host).** If the agent's shell lacks the rights to install/update Sysmon (`sysmon64 -c/-i`), run `auditpol`, or export the Sysmon/Security logs, elevate the specific command with `scripts/elevate.py` — it launches the command through a one-shot **scheduled task** (`schtasks /ru <account> /rp <password>`), which runs with the account's *full* (unfiltered) token because the Task Scheduler service runs as SYSTEM, bypassing UAC token filtering. `runas` cannot be used from an unattended shell (it reads the password from the console). Example:
>
> ```
> # as a local administrator (full token: check the High Integrity Level)
> python scripts/elevate.py --user ATYdemo --password '<pw>' -- whoami /groups
>
> # as a domain admin already in the local Administrators group
> python scripts/elevate.py --user NDRTEST\<da> --password '<pw>' -- sysmon64.exe -c C:\path\attacker\sysmonconfig.xml
>
> Apply `attacker/sysmonconfig.xml` on the **attack host only**, by hand (`Sysmon64.exe -c <repo>\attacker\sysmonconfig.xml`). `attacker build` does not load Sysmon. Do not use this file on office role hosts.
>
> # the elevated process does NOT inherit this user's environment — pass PYTHONPATH etc. via --env
> python scripts/elevate.py --user ATYdemo --password '<pw>' \
>     --env 'PYTHONPATH=C:\...\impacket;C:\Users\...\AppData\Roaming\Python\Python314\site-packages' \
>     -- C:\Python314\python.exe -c "import impacket; print(impacket.__file__)"
> ```
>
> The target account must be a member of the local `Administrators` group (the `Domain Admins` group usually is, by default). Commands are wrapped in a temp batch, output is captured to a temp file, and everything is cleaned up automatically.

> **Local Elevation Protocol (autonomous).** When the pre-flight gate (above) decides that a local elevation is required, follow this exact sequence:
>
> 1. **Check the current user first.** Read `whoami` and `net localgroup administrators`. If the current account is a member of the local `Administrators` group, prefer to elevate **as that same account** (it is usually only UAC-filtered): resolve its password from `users` via `state.py`, or acquire it with `credential.brute-user` / `credential.password-spray` against the DC and store it in `users` with `state.py add users '{...}'`. Record `is_local_admin_on_attack_host: true` on the object.
> 2. **Operator-preseeded account.** If `campaign.local_admin_account` is set (`{"name": "...", "password": "..."}`), use it directly.
> 3. **Resolved from state.** Enumerate `net localgroup administrators`, match an account to a `users` object that carries a non-empty `password`.
> 4. **Recovered from the domain.** For a domain account that is a local administrator (e.g. `NDRTEST\attdemo`), recover its password with `credential.brute-user` / `credential.password-spray` and store it in `users`.
> 5. **No account available** → do not stop the task: record a `notes` entry, keep running (live tshark is disabled; only the `.evtx` export needs elevation) and mark the `.evtx` export as unavailable, prompting the operator to preseed `campaign.local_admin_account` or run the agent elevated.
>
> Re-run the failed command elevated with:
>
> ```
> python scripts/elevate.py --user <account> --password '<pw>' --cwd <skill-root> [--env 'PYTHONPATH=<venv>;<site-packages>'] -- <absolute-path-command>
> ```
>
> The elevated process runs in the target account's environment with a **full token** (Task Scheduler runs as SYSTEM, bypassing UAC filtering). It does **not** inherit the current shell's environment — pass `PYTHONPATH` via `--env` for python-based commands, and always use absolute paths (the scheduled task starts in `System32`, so `--cwd <skill-root>` makes skill-relative paths resolve).
>
> To prove the gate passed, re-run `python scripts/check_environment.py` elevated and confirm `ok: true`. Then record the used account and confirm the result:
>
> ```
> python scripts/state.py set campaign.local_admin_account '{"name": "<account>", "password": "<password>"}'
> python scripts/state.py add notes '{"text": "elevated via <account> for <reason>"}'
> ```
>
> For log export specifically, `capture_logs.py stop` always retries `wevtutil epl` elevated on access denied, using the resolved account (see the Capture scripts section). Do not omit the stop call. `--elevate` is accepted but unnecessary.

> **Logon/Kerberos audit events.** `capture_logs.py` also exports the **Security** log (`config.json` `logs.security_log`), which records logon/authentication events: `4624/4625` (logon success/failure), `4634` (logoff), `4672` (special logon), `4648` (explicit credentials), `4776` (NTLM credential validation), and — on a **domain controller** — `4768` (TGT) / `4769` (service ticket). The pre-flight report's `auditing` field checks these audit subcategories (via `auditpol /get /subcategory:<GUID>`); if any are disabled, `ok` stays `true` but a warning explains which `auditpol` subcategory to enable. Note: `4768/4769` are emitted by the KDC, so they appear in a DC's Security log, not on the attack host; the attack host's Security log captures its own network logons (`4624` LogonType 3) from lateral-movement tooling.

### Step 1 — Read the state file and resolve the object references

Read the long-term state:

```
python scripts/state.py read
```

The task references objects by name (e.g. `user svc_backup`, `host 172.16.24.11`). Resolve each reference to its fields in `state.json`. Every command parameter (domain name, DC IP, username, hash, ccache file, SPN, etc.) MUST come from the resolved object. Never invent credentials, hashes, hostnames, or targets. If a referenced object or its required field is missing from the state, first run the Discovery/Credential Access technique that produces it, then proceed.

### Step 2 — Wrap each atomic action with capture

For every single atomic attack action (one command = one action):

1. Start traffic capture (protocol stub — does not write a pcap):

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

   Always run `stop` after `start`, even when the attack command fails. If a channel's `wevtutil epl` fails with access denied, `stop` automatically retries that channel elevated via the account resolved by the Local Elevation Protocol. Do not skip capture brackets because pre-flight reported unreadable log channels.

5. Stop traffic capture (protocol stub — no pcap is finalized):

   ```
   python scripts/capture_traffic.py stop
   ```

`<technique-id>` is the stable identifier of the technique (see each technique below). The capture start/stop calls must bracket the action even when the action fails, so the failed attempt is still recorded. `capture_logs.py stop` writes one evtx per configured channel named `{task_id}_{label}_{channel}.evtx` (e.g. `a1b2c3d4e5f67890_pass-the-ticket_Security.evtx`); a channel that fails to export does not block the others. Malicious pcaps are produced later by `attacker extract`, named `{task_id}_{technique-id}.pcapng`.

### Step 3 — Update the state file

Write the outcome back into the state file using `state.py` (see the "Outputs" block of each technique). On success, mark the technique `done`; on failure, mark it `failed` and record the reason.

### Step 4 — Rollback on stale information

If a command fails because a state object's field is wrong (a hash that does not authenticate, a DC IP that is unreachable, a credential that is rejected), the object is stale. Mark it stale and re-collect it:

```
python scripts/state.py mark-stale <path-to-the-object>
```

Then re-run the technique that originally produced that object (see "Rollback" in each technique and the dedicated Rollback section at the end).

### Step 5 — Write the task display filter (mandatory)

Time-window slicing of the domain SPAN happens later. This step writes **only** a Wireshark display filter for the packets this task generated, assuming the pcap has already been cut to the task window.

After every network action in this task (the dispatched technique **and** any Local Elevation Protocol spray/brute against the DC), compose **one** display filter. Union multiple actions with `||`. Fill IPs from `state.json` and the local host. Then:

```
python scripts/write_filter.py --expression "<display filter>"
```

The script writes `{HOLYFW_ATTACKER_OUTPUT_DIR}/{HOLYFW_ATTACKER_TASK_ID}.txt` next to the task markdown. The file must contain the expression and nothing else.

Allowed:

```
ip.addr == 172.16.24.10 && ip.addr == 172.16.24.11 && (kerberos || ldap || tcp.port == 88 || tcp.port == 389)
```

Forbidden in the expression and in the txt file:

- a full tshark command (`tshark`, `-r`, `-Y`, `-w`)
- time conditions (`frame.time_epoch`, `frame.time`)
- comments, wrapping quotes, markdown fences

Host-local techniques that generate no packets (orientation, local-groups, archive, and any technique you marked failed before it sent traffic) still write a file. Use the sentinel `frame.number == 0`.

Each phase skill's **Traffic Filter** block is the template. Substitute placeholders; do not leave `<attacker-ip>`, `<dc-ip>`, or `<target-ip>` in the file. Run Step 5 even when the attack command fails.

---

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
| `security_products[]` | Antivirus/EDR product names discovered by `discovery.security-software` |
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
| `tools_dir` | Local directory that holds attacker tools (relative to the skill root or absolute) |
| `tools[]` | Local tool filenames used by `lateral.tool-transfer` (e.g. `mimikatz.exe`) |
| `local_admin_account.name` / `password` | Account the agent uses for local elevation on the attack host (Local Elevation Protocol): shape `{"name": "<account>", "password": "<password>"}`. Operator-preseeded as a fallback; the agent also records the account it actually elevated with so later elevations reuse it. |
| `local_admins[]` | Members of the local Administrators group discovered by `discovery.local-groups` (feeds the Local Elevation Protocol and host privilege mapping) |
| `staging_dir` | Local directory where collected data is staged and archived by `collection.archive` |

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

Task text names a **phase skill**, not `ad-attack`:

```
Use the <phase-skill> skill: using the <field> of <object>, execute <technique-id> against <target-ref>.
```

`<phase-skill>` is `ad-discovery`, `ad-credential`, `ad-lateral`, `ad-collection`, or `ad-persistence` according to the technique id prefix.

Reference grammar:

- `<object>`: `user <username|upn|sid>`, `host <ip|fqdn|machine_account>`, `domain`, `wordlists`, `campaign`, or `ticket <tgt|service|golden|silver>[<index>]`.
- `<field>`: the object attribute to use (e.g. `password`, `ntlm_hash`, `kerberos_aes256`, `usernames`, `passwords`, `spns`, `open_ports`, `os`, `dc_ip`, `domain_sid`).
- `<target-ref>`: `host <ip|fqdn|machine_account>`, `domain`, or `subnet <cidr>`.
- The `using the <field> of <object>` clause is omitted for techniques with no source object (orientation, host-scan, port-scan).

Examples:

```
Use the ad-discovery skill: execute discovery.orientation against domain.
Use the ad-discovery skill: execute discovery.port-scan against host 172.16.24.11.
Use the ad-credential skill: using the password of user alice, execute credential.kerberoast against domain.
Use the ad-lateral skill: using the ntlm_hash of user svc_backup, execute lateral.pth-psexec against host 172.16.24.11.
Use the ad-collection skill: using the password of user alice, execute collection.share-download against host 172.16.24.11.
Use the ad-persistence skill: using the ntlm_hash of user krbtgt, execute persistence.golden-ticket against domain.
```

The only parameter that may originate outside `state.json` is a single candidate password for spraying (the user/password wordlist paths themselves come from `wordlists`).

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

> **Scalar vs object fields.** `mark-stale`/`unset-stale`/`touch` operate on *object* fields (dicts, e.g. `users[n]`, `hosts[n]`, `domain.spns[n]`) because a `stale` flag can only live on a dict. Scalar fields (e.g. `domain.dc_ip`, `domain.name`, `domain.domain_sid`) cannot carry a flag — when they go stale, simply re-run the producing technique and overwrite them with `set` (Step 2 + `set`, skipping `mark-stale`/`unset-stale`).

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
