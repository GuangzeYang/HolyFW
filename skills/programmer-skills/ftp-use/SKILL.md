---
name: ftp-use
description: Use when logging in to the company FTPS server and listing, uploading, downloading, or deleting files, or creating or deleting directories. Programmer works under /ftp-root/programmer. Use for explicit FTPS, FTP over TLS, or remote file transfer. Do not use for SMB shares or Exchange mail.
---

Do not ask the user. If a required value is missing, stop and report why.

Use Python's standard library only. Do not open a GUI FTP client.

# Endpoints (frozen)

- Host: `172.16.24.15`
- Port: `21` (explicit FTPS). Use this value; do not fall back to another common port if it looks empty.
- Username: `ndrtest\programmer`
- Password: `Njupt@241`
- Remote root: `/ftp-root`
- Role home (default when the prompt does not name another folder): `/ftp-root/programmer`
- Do not invent another host, port, mailbox, or password.

# Connect

1. Check that host, port, username, and password are all present. If any is missing, stop.
2. Open an explicit FTPS session with `ftplib.FTP_TLS`. Do not use plain `FTP()` or `ftp://`.
3. Call, in order: `connect(host, port, timeout=...)`, `auth()`, `login(username, password)`, `prot_p()`.
4. Use passive mode: `set_pasv(True)`.
5. Set a reasonable timeout on every connection.
6. Verify the server certificate by default. On a self-signed certificate or a name mismatch, disable verification, note that in the result, and continue. Do not invent another host.
7. After login, `cwd` to `/ftp-root`, then to `/ftp-root/programmer`. If `programmer` is missing, create it, report that, and enter it.
8. Call `pwd()` to prove the session. Report only success or failure, the current remote directory, and the protocol mode. Never print the password.
9. When finished, `quit()`. If that fails, `close()`.
10. On login failure, stop and report the error. Do not retry with a guessed account.

# Shared rules

- Remote paths use `/`. Local paths use the current OS format.
- Relative remote paths are resolved from the directory after login (`pwd()` first).
- Treat spaces, Chinese, and other special characters as a single string argument. Do not build a shell command by concatenating paths.
- Do not follow `..` out of the user-specified tree unless the prompt already named that parent.
- Default work stays under `/ftp-root/programmer`. Create that folder if it is missing and report that.
- Do not overwrite an existing remote or local file unless the prompt explicitly says overwrite. On a name clash, stop and report.
- On batch work, record each item. One failure must not hide the others.
- Never print the password, TLS session keys, or the full authentication command.

# Prose expansion

Commander may set `min_words` plus `topic` (or a one-sentence `content` outline) on `upload`. Expand the document on this host. Do not expect the full document in the prompt.

1. If `min_words` is present, write original English of at least that many whitespace-separated words about `topic` or the outline. No lorem ipsum. No invented credentials, hosts, or secrets.
2. If `min_words` is absent and `content` is present, use `content` unchanged (legacy prompts).
3. Do not expand paths or file names.
4. Save the expanded text as a local UTF-8 file (default: Desktop or `%TEMP%` using the remote file name), then upload that file.

# Operations

Skip unused fields. Resolve `path` under `/ftp-root` first. If `path` is omitted, use `/ftp-root/programmer`.

## list

`{path: ...}`

1. Sign in.
2. If `path` is set, `cwd(path)` first.
3. Prefer `mlsd()` for name, type, size, and modified time.
4. If the server rejects `MLSD`, fall back to `nlst()`. Do not guess whether an unrecognized name is a file or a directory.
5. Return the current remote directory and the listing. Say explicitly when the directory is empty.

## upload

`{path: ..., min_words: ..., topic: ...}` Optional `local path`, short `content` outline.

`path` is the remote file path (directory plus file name).

1. Sign in.
2. If `min_words` or `content` is set, apply **Prose expansion** and write the local file first. If `local path` is set, use it; otherwise write next to Desktop/`%TEMP%` using the remote file name.
3. If the local file is still missing and there is no `min_words` or `content`, stop and report.
4. `cwd` to the remote parent. If that folder is missing, create it and report that.
5. If a remote file with the same name exists, stop unless the prompt explicitly says overwrite.
6. Upload in binary. Do **not** prefer `storbinary()`: on IIS FTPS it often times out during TLS shutdown; a delayed `226` desynchronizes or hangs the control channel.
7. Use this compatible path: `transfercmd("STOR <remote name>")`, write the local file in 8192-byte chunks, close the data connection, then `voidresp()` for the `226`.

```python
cmd = f"STOR {remote_name}"
with open(local_path, "rb") as f:
    conn = ftp.transfercmd(cmd)
    while True:
        chunk = f.read(8192)
        if not chunk:
            break
        conn.sendall(chunk)
    conn.close()
ftp.voidresp()
```

8. If `storbinary()` was used by mistake and you see a TLS shutdown timeout, a desynchronized control channel, or a long hang, retry immediately with the compatible path. Do not keep waiting.
9. After the upload, list the target directory again and check the remote name and size.
10. Report success only after that check passes.

## download

`{path: ...}` Optional `local path` (default: Desktop/`<filename>`).

1. Sign in and confirm the remote file exists. If it does not, stop and report.
2. If the local parent directory is missing, create it and mention that at the end. If the local file already exists, stop unless the prompt explicitly says overwrite.
3. Download into a sibling temp file `<filename>.part`.
4. Use `retrbinary("RETR <remote file>", file.write)`.
5. After the transfer, check the size, then rename the temp file to the final name.
6. On failure, delete the incomplete `.part` file and report the error.

## delete file

`{path: ...}`

1. Sign in and resolve the full remote file path.
2. Show the target path in the result. Delete only when the prompt already named that path.
3. Use `delete(remote_file)`.
4. List the parent directory again and confirm the file is gone.
5. Do not recurse. Do not treat "file not found" as a successful delete; if it is already missing, stop and report.

## create folder

`{path: ...}`

1. Sign in and resolve the parent plus the new folder name.
2. If the folder already exists, say so and do not create it again.
3. Otherwise `mkd(remote_dir)`.
4. List the parent again and confirm the new folder exists.

## delete folder

`{path: ...}`

1. Sign in and resolve the full remote directory path.
2. Show the target path in the result.
3. Prefer deleting an empty directory.
4. If it is not empty, delete the contents and then the folder, and report that.
5. List the parent again and confirm the folder is gone.

# Path mapping

Task paths look like `/ftp-root/programmer/sprint-notes.txt`. Use them as remote FTPS paths.

1. If the string already starts with `/ftp-root`, use it unchanged.
2. A bare file name is created or resolved under `/ftp-root/programmer`.
3. Do **not** write under `/ftp-root/hr`, `/ftp-root/accountancy`, or `/ftp-root/manager` unless the prompt names that exact path. Stick to `/ftp-root/programmer`.

# Verify then close

Every operation must check the result and reply in this shape:

```text
Operation:
Protocol: explicit FTPS
Remote path:
Local path: (when used)
Result: success / failure
Verification:
Error: (on failure)
```

Then `quit()` or `close()`.

# Anti-patterns

- Do not ask the user questions.
- Do not use plain FTP or `ftp://`.
- Do not invent another host, port, or password.
- Do not print the password or TLS keys.
- Do not prefer `storbinary()` on this IIS endpoint.
- Do not overwrite without an explicit overwrite request.
- Do not treat a missing delete target as success.
- Do not leave a `.part` file after a failed download.
- Do not work outside `/ftp-root` or, by default, outside `/ftp-root/programmer`.
