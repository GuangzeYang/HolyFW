---
name: ftp-use
description: Use when listing, uploading, downloading, appending, copying, moving, renaming, or deleting files and folders on the company FTPS server. Programmer works under /programmer. Use for explicit FTPS. Do not use for SMB shares or Exchange mail.
---

Do not ask the user. If a required value is missing, stop and report why.

Use Python's standard library only. Do not open a GUI FTP client.

# Endpoints (frozen)

- Host: `172.16.24.15`
- Port: `21` (explicit FTPS). Do not fall back to 990.
- Username: `ndrtest/programmer` (forward slash). `programmer` and `programmer@ndrtest.local` also work. **`ndrtest\programmer` returns 530.**
- Password: `Njupt@241`
- Remote root / initial directory: `/`
- Role home: `/programmer`
- SYST: `Windows_NT` (Microsoft FTP Service).
- No FQDN. Do not invent a host, port, or password.

Task paths are the **real remote paths**: `/programmer/sprint-notes.txt`, `/programmer/handoff`. Do not write `/ftp-root/...`.

# Connect

```python
import ssl
from ftplib import FTP_TLS

ctx = ssl._create_unverified_context()
ftp = FTP_TLS(context=ctx, timeout=30)
ftp.connect('172.16.24.15', 21, timeout=30)
ftp.auth()
ftp.login('ndrtest/programmer', '<password>')
ftp.prot_p()
ftp.set_pasv(True)
ftp.sendcmd('OPTS UTF8 ON')
ftp.voidcmd('TYPE I')
ftp.cwd('/')
```

1. Require host, port, username, and password. If any is missing, stop.
2. Use `ftplib.FTP_TLS` only. No plain `FTP()` and no `ftp://`.
3. Order: `connect(..., timeout=30)` → `auth()` → `login('ndrtest/programmer', password)` → `prot_p()` → `set_pasv(True)` → `OPTS UTF8 ON` → **`TYPE I`** → `cwd('/')`.
4. Default cert verify fails: `IP address mismatch, certificate is not valid for '172.16.24.15'`. Always use `ssl._create_unverified_context()` and note that in the result.
5. After login `pwd()` is `/`. Still `cwd('/')`. Do not `cwd('/ftp-root')` (550).
6. Then `cwd('/programmer')`. If missing: `cwd('/')`, `mkd('programmer')`, report that, `cwd('/programmer')`. Do not create files at `/`.
7. `STAT` after login shows `TYPE: ASCII` unless you send `TYPE I`. Binary transfers need `TYPE I`.
8. Report success or failure, `pwd()`, and `Protocol: explicit FTPS`. Never print the password.
9. Finish with `quit()`, or `close()` if that fails.
10. On 530, stop. Do not retry `ndrtest\programmer`.

# Shared rules

- Remote paths use `/`. Local paths use the current OS format.
- Resolve every path with **Path mapping** before `cwd` / `STOR` / `RETR` / `APPE` / `RNFR`.
- Spaces, Chinese, and `file[1].txt` are one string argument. After `OPTS UTF8 ON` they work.
- Do not follow `..` out of the named tree.
- Default work stays under `/programmer`. Do not write at `/` (root currently has `test.txt`; leave it).
- `STOR` **silently overwrites** on this IIS (`226` and the size changes). Always `nlst()`/`LIST` first. Stop on a name clash unless the prompt says overwrite or the op is `update file`.
- `RNTO` onto an existing name returns `550 Cannot create a file when that file already exists.`
- `mkd('a/b')` fails if `a` is missing (`550`). Create each parent, then the child.
- One batch failure must not hide the others.
- If `storbinary()` times out, the control channel is dead (`PASV` then returns `200 Type set to A`). `quit()`/`close()`, reconnect, use `transfercmd`. Do not reuse that session.

# Prose expansion

On `upload`, `append`, and `update file`, commander may set `min_words` plus `topic` (or a short `content` outline). Expand on this host.

1. If `min_words` is present, write original English of at least that many whitespace-separated words about `topic` or the outline. No lorem ipsum. No invented credentials.
2. If `min_words` is absent and `content` is present, use `content` unchanged.
3. Do not expand paths or file names.
4. Save as a local UTF-8 file (Desktop or `%TEMP%` using the remote name), then transfer that file.

# Transfer helpers

No `MLSD` (`500`). Prefer `LIST`. Fallback `nlst()`. `SIZE <name>` → `213 <bytes>`. `MDTM <name>` → `213 YYYYMMDDHHMMSS`.

```python
def stor_compat(ftp, local_path, remote_name):
    cmd = f'STOR {remote_name}'
    with open(local_path, 'rb') as f:
        conn = ftp.transfercmd(cmd)
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            conn.sendall(chunk)
        conn.close()
    return ftp.voidresp()  # 226 Transfer complete.

def appe_compat(ftp, local_path, remote_name):
    cmd = f'APPE {remote_name}'
    with open(local_path, 'rb') as f:
        conn = ftp.transfercmd(cmd)
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            conn.sendall(chunk)
        conn.close()
    return ftp.voidresp()
```

Do **not** use `storbinary()` or `ftp.storbinary(..., APPE)` — same TLS shutdown hang.

`retrbinary()` works. Use it for download.

```python
with open(part, 'wb') as f:
    ftp.retrbinary(f'RETR {remote_name}', f.write)
```

# Operations

Skip unused fields. If `path` is omitted, use `/programmer`.

## list

`{path: ...}`

```python
ftp.cwd(remote_path)
lines = []
try:
    ftp.retrlines('LIST', lines.append)
except Exception:
    lines = ftp.nlst()
```

`LIST` looks like `08-22-26  01:32PM       <DIR>          hr` or `08-10-26  11:30AM                   27 test.txt`. Empty dir: `nlst()` is `[]` — say empty. Return `pwd()` and the listing.

## upload

`{path: ..., min_words: ..., topic: ...}` Optional `local path`, short `content`.

`path` is directory plus file name.

1. Apply **Prose expansion** when `min_words` or `content` is set. Write the local file. Default local path: Desktop/`%TEMP%` / remote name.
2. If the local file is still missing, stop.
3. `cwd` to the parent. Create missing parents one level at a time.
4. If the remote name exists, stop unless overwrite was requested.
5. `stor_compat`. Success: `226` and `SIZE` matches local bytes.

## append

`{path: ..., min_words: ..., topic: ...}` Optional short `content`.

`APPE` on a **missing** file creates it (`226`, new `SIZE`). For append-only, `nlst()` first; if missing, stop. Do not treat a newly created file as a successful append.

```python
if remote_name not in ftp.nlst():
    raise RuntimeError('append target missing')
appe_compat(ftp, local_path, remote_name)
```

Success: `SIZE` grew. Apply **Prose expansion** first.

## update file

`{path: ..., min_words: ..., topic: ...}` Optional short `content`.

Overwrite an existing remote file. If it is missing, stop. Apply **Prose expansion**, then `stor_compat` (IIS `STOR` replaces in place). Success: `SIZE` matches the new local file.

## download

`{path: ...}` Optional `local path` (default Desktop/`<filename>`).

1. Confirm remote exists (`SIZE` or `LIST`). If not, stop.
2. Create a missing local parent and mention it. If the local file exists, stop unless overwrite was requested.
3. Write `<filename>.part`, `retrbinary`, compare sizes, `os.replace` to the final name.
4. On failure, delete the `.part` file.

## copy

`{source path: ..., destination path: ...}`

No server-side COPY. `RETR` to a local temp, then `stor_compat` to the destination (both under `/programmer` unless the prompt named another allowed path).

Success: destination `SIZE` equals source `SIZE`. Destination parent: create stepwise if missing. Stop if the destination name exists unless overwrite was requested.

## move

`{source path: ..., destination path: ...}`

```python
ftp.sendcmd(f'RNFR {source_name_or_path}')  # 350
ftp.sendcmd(f'RNTO {dest_name_or_path}')    # 250
```

Same-volume move works (`RNTO dest/renamed.txt`). Both sides stay under `/programmer`. Success: destination exists, source is gone. Clash: `550 Cannot create a file when that file already exists` — stop.

## rename

`{path: ..., new name: ...}`

`RNFR` then `RNTO` in the same folder. Do not put `..` in `new name`. Success: the new name is in `nlst()` and the old name is not.

## delete file

`{path: ...}`

Show the target path. If `nlst()` lacks the name, the delete already succeeded (`DELE` on a missing file is `550 The system cannot find the file specified.`). Otherwise `delete(name)` → `250 DELE command successful.`, then list again. Do not recurse.

## create folder

`{path: ...}`

If `cwd(path)` works, it exists — say so. Else `mkd` each missing segment, then `cwd` to confirm.

## delete folder

`{path: ...}`

If already missing, success. Empty: `rmd` → `250`. Non-empty: `550 The directory is not empty` — delete contents, then `rmd`. Confirm the parent listing no longer has the name.

# Path mapping

Use the path the server actually has.

| Prompt path | Remote path |
|---|---|
| `/programmer` or `/programmer/` | `/programmer` |
| `/programmer/sprint-notes.txt` | `/programmer/sprint-notes.txt` |
| `sprint-notes.txt` | `/programmer/sprint-notes.txt` |
| `/` | `/` (list only; do not write) |

1. If the string starts with `/`, use it as the remote path.
2. A bare name is under `/programmer`.
3. Stay under `/programmer` unless the prompt already named another exact path.
4. `/ftp-root` is **not** on this server. If a leftover prompt still contains `/ftp-root/`, strip that prefix (`/ftp-root/programmer/a.txt` → `/programmer/a.txt`). Do not `cwd('/ftp-root')`.
5. `/hr`, `/accountancy`, `/manager` are not present from a typical listing (`550`). Do not create them on a normal Programmer task.

# Verify then close

```text
Operation:
Protocol: explicit FTPS
Remote path:
Local path: (when used)
Certificate: unverified (IP name mismatch)
Result: success / failure
Verification:
Error: (on failure)
```

Then `quit()` or `close()`.

# Anti-patterns

- Do not ask the user questions.
- Do not use plain FTP or `ftp://`.
- Do not login as `ndrtest\programmer`.
- Do not treat `/ftp-root` as a real directory or put it in new paths.
- Do not invent a host, port, FQDN, or password.
- Do not print the password or TLS keys.
- Do not call `mlsd()`.
- Do not call `storbinary()`. Do not reuse a session after a TLS timeout.
- Do not skip the existence check before `STOR` (it overwrites).
- Do not `APPE` a missing file and call that append.
- Do not `mkd('a/b')` in one shot when `a` is missing.
- Do not leave a `.part` file after a failed download.
- Do not write at `/` or, by default, outside `/programmer`.
