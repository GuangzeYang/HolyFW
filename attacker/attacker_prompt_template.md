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

The technique id determines which field is consumed (each technique's "Inputs" in SKILL.md says which); the `using` clause names the source object and the `against` clause names the target. The grammar is uniform across all techniques.

One-shot intents (given in the task text, not stored in state): the spray candidate password, a reset password, a file path to download, and a user to impersonate.

## 3. Technique ids

Each id maps to exactly one command (see SKILL.md).

| Phase | Technique | id |
|-------|-----------|----|
| discovery | orientation | `discovery.orientation` |
| discovery | host discovery | `discovery.host-scan` |
| discovery | single-host port scan | `discovery.port-scan` |
| discovery | host identification (reverse DNS) | `discovery.host-identify` |
| discovery | username enumeration (kerbrute) | `discovery.user-enum-kerbrute` |
| discovery | username enumeration (LDAP) | `discovery.user-enum-ldap` |
| discovery | SID enumeration | `discovery.user-enum-sid` |
| discovery | network share enumeration | `discovery.share-enum` |
| discovery | domain group enumeration | `discovery.group-enum` |
| discovery | password policy discovery | `discovery.password-policy` |
| discovery | domain trust discovery | `discovery.trust-enum` |
| credential | password spraying | `credential.password-spray` |
| credential | single-account brute force | `credential.brute-user` |
| credential | brute force | `credential.brute-force` |
| credential | AS-REP roasting | `credential.asrep-roast` |
| credential | Kerberoasting | `credential.kerberoast` |
| credential | credential dumping (SAM/LSA) | `credential.dump-secrets` |
| credential | DCSync | `credential.dcsync` |
| credential | GPP password (cPassword) | `credential.gpp-password` |
| lateral | pass-the-hash (PsExec) | `lateral.pth-psexec` |
| lateral | pass-the-hash (WMI) | `lateral.pth-wmiexec` |
| lateral | pass-the-hash (SMBExec) | `lateral.pth-smbexec` |
| lateral | over-pass-the-hash | `lateral.overpass-the-hash` |
| lateral | remote shell (WMI) | `lateral.exec-wmiexec` |
| lateral | remote shell (SMBExec) | `lateral.exec-smbexec` |
| lateral | remote shell (PsExec) | `lateral.exec-psexec` |
| lateral | remote shell (DCOM) | `lateral.exec-dcomexec` |
| lateral | remote shell (AtExec) | `lateral.exec-atexec` |
| lateral | delegation enumeration | `lateral.delegation-enum` |
| lateral | delegation abuse (S4U) | `lateral.delegation-s4u` |
| lateral | pass-the-ticket | `lateral.pass-the-ticket` |
| lateral | lateral tool transfer | `lateral.tool-transfer` |
| collection | data from network shared drive | `collection.share-download` |
| persistence | golden ticket | `persistence.golden-ticket` |
| persistence | silver ticket | `persistence.silver-ticket` |
| persistence | create machine account | `persistence.add-computer` |
| persistence | RBCD delegation | `persistence.rbcd` |
| persistence | reset account password | `persistence.reset-password` |

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

On a fresh deployment, schedule the first tasks in dependency order so the state file is seeded before any lateral/persistence task is requested:

1. `discovery.orientation` — fills `domain.name`, `domain.dc_ip`, `domain.dc_fqdn`, `domain.domain_sid`.
2. `discovery.host-scan` — fills `hosts`.
3. `discovery.host-identify` — fills `hosts[].fqdn`, `hosts[].machine_account`.
4. `discovery.port-scan` / `discovery.share-enum` — fills ports, services, shares.
5. `discovery.user-enum-kerbrute` (or `discovery.user-enum-ldap`) — fills `domain.usernames`.
6. `discovery.group-enum` / `discovery.password-policy` / `discovery.trust-enum` — fills groups, policy, trusts.
7. `credential.password-spray` / `credential.kerberoast` / `credential.gpp-password` — fills `users`, `domain.spns`.
8. Only then: lateral movement, collection, and persistence.

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
```

### Collection

```
opencode run "Use the ad-attack skill: using the password of user alice, execute collection.share-download against host 192.168.14.71."
```

### Persistence

```
opencode run "Use the ad-attack skill: using the ntlm_hash of user krbtgt, execute persistence.golden-ticket against domain."
opencode run "Use the ad-attack skill: using the ntlm_hash of user svc_sql, execute persistence.silver-ticket against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the machine_account of campaign, execute persistence.add-computer against domain."
opencode run "Use the ad-attack skill: using the machine_account of campaign, execute persistence.rbcd against host 192.168.14.71."
opencode run "Use the ad-attack skill: using the password of user admin, execute persistence.reset-password against host 192.168.14.71."
```

## 7. Guardrails (do / don't)

Do:

- Give the generator the full `state.json` before asking it to write a task.
- Reference objects by the exact names that exist in the state file.
- Keep secrets out of the task text; only object names appear.
- Use the stable technique ids (they double as the capture `--label`).
- Schedule discovery before credential/lateral/collection/persistence on a cold start.
- Pre-fill `campaign.machine_account` and `campaign.tools` before running the techniques that consume them.

Don't:

- Do not ask the agent to run a technique whose object/field is not yet in state (it will fail and roll back).
- Do not put raw hashes, passwords, or the domain SID in the task text.
- Do not request more than one technique per task; the skill brackets each atomic action individually.
