# ad-attack Prompt Invocation Template

This is a **command reference for the human operator / task scheduler**. It tells you the exact `opencode run "..."` text to issue in order to instruct the attacker agent.

The agent's role definition and its mandatory execution protocol (pre-flight check, read state, bracket each action with capture, write back, rollback) live in `attacker/skills/ad-attack/SKILL.md` and are applied automatically by opencode. You do **not** repeat them in the task text — you only select the technique and the objects.

## 1. How the command reaches the agent

1. **Task generation.** Give the generator (commander/LLM) the full `state.json` plus the technique list; it produces a task text that references concrete objects from the state file.
2. **Execution.** The commander dispatches the task to the attacker soldier as:

   ```
   opencode run "<task text>"
   ```

   opencode loads the `ad-attack` skill, reads `state.json`, resolves the object references, and runs the technique.

## 2. Canonical object-based form

```
Use the ad-attack skill: using the <field> of <object>, execute <technique-id> against <target-ref>.
```

Reference grammar:

- `<object>`: `user <username|upn|sid>`, `host <ip|fqdn|machine_account>`, `domain`, `wordlists`, `campaign`, or `ticket <tgt|service|golden|silver>[<index>]`.
- `<field>`: the object attribute to use, e.g. `password`, `ntlm_hash`, `kerberos_aes256`, `usernames`, `passwords` (wordlists), `machine_account`, `tools` (campaign), `spns`, `shares`, `open_ports`, `os`, `dc_ip`, `domain_sid`.
- `<target-ref>`: `host <ip|fqdn|machine_account>`, `domain`, or `subnet <cidr>`.
- Omit the `using the <field> of <object>` clause for techniques with no source object (`discovery.orientation`, `discovery.host-scan`, `discovery.port-scan`, `discovery.host-identify`, `discovery.group-enum`, `discovery.password-policy`, `discovery.trust-enum`).

The catalog `requires` column says which state field to consume; the `using` clause names the source object and the `against` clause names the target. The grammar is uniform across all techniques.

One-shot intents (given in the task text, not stored in state): the spray candidate password, a reset password, a file path to download, and a user to impersonate.

## 3. Technique ids

Each id maps to exactly one command. Pick a row only when its `requires` fields already exist in `state` (or `requires` is `none`). Prefer rows whose `writes` fill missing knowledge; once the seed is filled, interleave refresh and collection.

Legend:

- `none`: no state object (local foothold is enough)
- `users.password` / `users.ntlm_hash`: at least one `users[]` entry carries that field
- `hosts` / `hosts.fqdn`: a `hosts[]` entry already has that field
- `FORBIDDEN`: never generate

| id | requires | does | writes |
|----|----------|------|--------|
| `discovery.orientation` | none | read local domain context | domain.name, dc_ip, dc_fqdn, domain_sid, dcs |
| `discovery.host-scan` | domain.dc_ip | ping-sweep live hosts | hosts[] |
| `discovery.port-scan` | hosts | scan ports on one host | hosts[].open_ports, services, os, role |
| `discovery.host-identify` | hosts.ip | reverse-DNS FQDN and $ account | hosts[].fqdn, machine_account |
| `discovery.user-enum-kerbrute` | domain.name, domain.dc_ip, wordlists.usernames | enum usernames without creds | domain.usernames |
| `discovery.user-enum-ldap` | users.password, domain.dc_ip | list users via LDAP | domain.usernames, user_count |
| `discovery.user-enum-sid` | users.password, domain.dc_ip | enum accounts and SIDs | domain.usernames, users[].sid |
| `discovery.share-enum` | hosts | list SMB shares | hosts[].shares |
| `discovery.group-enum` | domain.name | enum domain groups | domain.groups, users[].groups |
| `discovery.password-policy` | none | read lockout/password policy | domain.password_policy |
| `discovery.trust-enum` | none | enum domain trusts | domain.trusts |
| `discovery.security-software` | none | list AV/EDR on attack host | domain.security_products |
| `discovery.local-groups` | none | list local Administrators | campaign.local_admins |
| `discovery.bloodhound` | domain.name, domain.dc_ip, users.password | collect BloodHound graph | files[] |
| `credential.password-spray` | domain.name, domain.dc_ip, wordlists.usernames | spray one password | users[] |
| `credential.brute-user` | domain.usernames, wordlists.passwords, domain.name, domain.dc_ip | brute one account | users[] |
| `credential.brute-force` | wordlists.combos, domain.name, domain.dc_ip | brute combo list | users[] |
| `credential.asrep-roast` | wordlists.usernames, domain.name, domain.dc_ip | roast no-preauth TGTs | users[].no_preauth, files[] |
| `credential.kerberoast` | users.password, domain.dc_ip | roast SPN tickets | domain.spns, users[] |
| `credential.dump-secrets` | users.password or users.ntlm_hash, hosts | dump SAM/LSA hashes | users[].ntlm_hash |
| `credential.dcsync` | users.password or users.ntlm_hash | replicate krbtgt and hashes | users[krbtgt], domain.domain_sid |
| `credential.gpp-password` | domain.name, domain.dc_ip, users.password | decrypt GPP cPassword | users[] |
| `credential.lsass-dump` | users.password, hosts | dump LSASS memory | files[] |
| `lateral.pth-psexec` | users.ntlm_hash, hosts | PTH exec via SMB | hosts[].compromised |
| `lateral.pth-wmiexec` | users.ntlm_hash, hosts | PTH exec via WMI | hosts[].compromised |
| `lateral.pth-smbexec` | users.ntlm_hash, hosts | PTH exec via SMB pipes | hosts[].compromised |
| `lateral.overpass-the-hash` | users.ntlm_hash | hash to TGT | tickets.tgt[] |
| `lateral.exec-wmiexec` | users.password, hosts | remote shell via WMI | hosts[].compromised |
| `lateral.exec-smbexec` | users.password, hosts | remote shell via SMB | hosts[].compromised |
| `lateral.exec-psexec` | users.password, hosts | remote shell via PsExec | hosts[].compromised |
| `lateral.exec-dcomexec` | users.password, hosts | remote shell via DCOM | hosts[].compromised |
| `lateral.exec-atexec` | users.password, hosts | run scheduled cmd | hosts[].compromised |
| `lateral.delegation-enum` | users.password, domain.dc_ip | find delegation edges | domain.delegation |
| `lateral.delegation-s4u` | users.password or users.ntlm_hash, domain.spns | S4U impersonation ticket | tickets.service[] |
| `lateral.pass-the-ticket` | tickets, hosts.fqdn | reuse Kerberos ticket | hosts[].compromised |
| `lateral.tool-transfer` | users.password, hosts, campaign.tools | upload tool to share | files[] |
| `lateral.exec-winrm` | users.password, hosts | remote cmd via WinRM | hosts[].compromised |
| `lateral.exec-schtasks` | users.password, hosts | remote scheduled task | hosts[].compromised |
| `collection.share-download` | hosts.shares, users.password | download file from share | files[] |
| `collection.local-file` | users.password, hosts | collect local files | files[] |
| `collection.archive` | files | compress staged files | files[] |
| `persistence.golden-ticket` | users[krbtgt].ntlm_hash, domain.domain_sid | forge TGT | tickets.golden[] |
| `persistence.silver-ticket` | users.ntlm_hash, domain.domain_sid, domain.spns | forge service ticket | tickets.silver[] |
| `persistence.add-computer` | domain.name, domain.dc_ip, users.password | create machine account | users[], campaign.machine_account |
| `persistence.rbcd` | FORBIDDEN | — | — |
| `persistence.reset-password` | FORBIDDEN | — | — |
| `persistence.service` | users.password, hosts | install persist service | files[] |

> `persistence.rbcd` and `persistence.reset-password` are **forbidden** (they modify an existing AD account in place). Never generate them. Every other technique id above is available.

## 4. Object resolution rules

- Give the generator the full `state.json` so it references objects that actually exist.
- A `user <name>` reference resolves to the matching object in `users[]` (by `username`, `upn`, or `sid`).
- A `host <ref>` reference resolves to the matching object in `hosts[]` (by `ip`, `fqdn`, or `machine_account`).
- `domain` resolves to the `domain` object.
- `wordlists` resolves to the `wordlists` object (the `usernames`/`passwords` `.txt` paths).
- `campaign` resolves to the `campaign` object (`machine_account`, `tools_dir`, `tools`).
- Secrets (hashes, passwords, SID) are never written into the task text — only object names appear.

One-shot intents that appear in the task text rather than state: a single spray candidate password, a reset password, a file path to download, and a user to impersonate.

## 5. Cold-start ordering

On a fresh deployment, seed in this order (writes are in the catalog):

1. `discovery.orientation`
2. `discovery.host-scan`
3. `discovery.host-identify`
4. `discovery.port-scan` / `discovery.share-enum`
5. `discovery.user-enum-kerbrute` (or `discovery.user-enum-ldap`)
6. `discovery.group-enum` / `discovery.password-policy` / `discovery.trust-enum`
7. `credential.password-spray` / `credential.kerberoast` / `credential.gpp-password`
8. Only then: lateral movement, collection, and persistence.

## 5b. Ongoing campaign

After the seed, keep rotating toward the campaign goal. Map intents to catalog ids (no new commands):

- Expand privilege: `credential.*` → `lateral.*` (mark `hosts[].compromised`) → allowed `persistence.*`.
- Refresh facts: `discovery.orientation`, `host-scan`, `host-identify`, `share-enum`, `group-enum`, `password-policy`, `trust-enum`, `user-enum-*`.
- Domain shares: `discovery.share-enum` → `collection.share-download` → `collection.archive`.
- DC configuration (`hosts[].role` is `dc`): `password-policy`, `trust-enum`, `bloodhound`, `gpp-password`, plus `share-enum` / `share-download` on that DC.
- Employee host files (`role` is member or unknown, credentials already in state): `lateral.exec-*` or PTH, then `collection.local-file`.

## 6. Worked examples (copy-paste commands)

### Discovery

```
opencode run "Use the ad-attack skill: execute discovery.orientation against domain."
opencode run "Use the ad-attack skill: execute discovery.host-scan against subnet 192.168.14.0/24."
opencode run "Use the ad-attack skill: execute discovery.port-scan against host 192.168.14.71."
opencode run "Use the ad-attack skill: execute discovery.host-identify against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the usernames of wordlists, execute discovery.user-enum-kerbrute against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute discovery.user-enum-ldap against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute discovery.user-enum-sid against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute discovery.share-enum against host 192.168.14.71."
opencode run "Use the ad-attack skill: execute discovery.group-enum against domain."
opencode run "Use the ad-attack skill: execute discovery.password-policy against domain."
opencode run "Use the ad-attack skill: execute discovery.trust-enum against domain."
opencode run "Use the ad-attack skill: execute discovery.security-software against domain."
opencode run "Use the ad-attack skill: execute discovery.local-groups against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute discovery.bloodhound against domain."
```

### Credential Access

```
opencode run "Use the ad-attack skill: using the usernames of wordlists, execute credential.password-spray against domain."
opencode run "Use the ad-attack skill: using the passwords of wordlists, execute credential.brute-user against domain."
opencode run "Use the ad-attack skill: using the passwords of wordlists, execute credential.brute-force against domain."
opencode run "Use the ad-attack skill: using the usernames of wordlists, execute credential.asrep-roast against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute credential.kerberoast against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute credential.dump-secrets against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the ntlm_hash of user admin, execute credential.dcsync against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute credential.gpp-password against domain."
opencode run "Use the ad-attack skill: using the password of user admin, execute credential.lsass-dump against host 192.168.14.71."
```

### Lateral Movement

```
opencode run "Use the ad-attack skill: using the ntlm_hash of user svc_backup, execute lateral.pth-psexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the ntlm_hash of user svc_backup, execute lateral.pth-wmiexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the ntlm_hash of user svc_backup, execute lateral.pth-smbexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the ntlm_hash of user administrator, execute lateral.overpass-the-hash against domain."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-wmiexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-smbexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-psexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-dcomexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-atexec against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.delegation-enum against domain."
opencode run "Use the ad-attack skill: using the password of user svc_sql, execute lateral.delegation-s4u against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the ccache_file of ticket tgt[0], execute lateral.pass-the-ticket against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the tools of campaign, execute lateral.tool-transfer against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-winrm against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute lateral.exec-schtasks against host 192.168.14.71."
```

### Collection

```
opencode run "Use the ad-attack skill: using the password of user alice, execute collection.share-download against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user alice, execute collection.local-file against host 192.168.14.71."
opencode run "Use the ad-attack skill: execute collection.archive against domain."
```

### Persistence

```
opencode run "Use the ad-attack skill: using the ntlm_hash of user krbtgt, execute persistence.golden-ticket against domain."
opencode run "Use the ad-attack skill: using the ntlm_hash of user svc_sql, execute persistence.silver-ticket against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the machine_account of campaign, execute persistence.add-computer against domain."
opencode run "Use the ad-attack skill: using the password of user admin, execute persistence.service against host 192.168.14.71."
```

> `persistence.rbcd` and `persistence.reset-password` are forbidden (in-place modification of an existing AD account); never emit them.

## 7. Guardrails (do / don't)

Do:

- Give the generator the full `state.json` before asking it to write a task.
- Reference objects by the exact names that exist in the state file.
- Keep secrets out of the task text; only object names appear.
- Use the stable technique ids (they double as the capture `--label`).
- Schedule discovery before credential/lateral/collection/persistence on a cold start.
- After the seed, keep scheduling toward the campaign goal: expand privilege, refresh known facts, and collect shares / DC config / employee files.
- Pre-fill `campaign.machine_account` and `campaign.tools` before running the techniques that consume them.

Don't:

- Do not ask the agent to run a technique whose object/field is not yet in state (it will fail and roll back).
- Do not put raw hashes, passwords, or the domain SID in the task text.
- Do not request more than one technique per task; the skill brackets each atomic action individually.

Hard mutation constraints (never violated):

- Never generate `persistence.reset-password` (resets an existing account's password) or `persistence.rbcd` (writes a delegation grant onto an existing account). Both modify existing AD account info in place and are forbidden.
- Never generate any task that edits an existing account's password or attributes.
- `persistence.add-computer` (adds a new machine account) is allowed; deleting accounts the attacker itself created is allowed.
- The executing agent records every domain addition in `changes.json`.
