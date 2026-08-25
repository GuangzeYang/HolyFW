---
name: smb-access
description: Use when creating, viewing, updating, copying, moving, renaming, or deleting files on the company SMB share through PowerShell. Manager may use Management, Public, and Exchange. Do not use a GUI explorer. Stop immediately on Access Denied.
---

Use PowerShell only. Do not open File Explorer. Do not write browser or SMB GUI automation.

# Endpoints (frozen)

- Share IP: `\\172.16.24.11\Company_Data`
- Documented FQDN `\\i2-dc0-c08.edrtest.local\Company_Data` **does not resolve**. Do not fall back to it.
- Do not invent another server IP.

# Path mapping

Task paths look like `/Company_Data/Management/spec.txt`. Convert to UNC:

1. If the string already starts with `\\`, use it unchanged. Do **not** strip those slashes (that turns `\\172.16.24.11\...` into `172.16.24.11\...` and breaks the share).
2. Strip a leading `/` or `\` only when the string does **not** already start with `\\`.
3. If the path starts with `Company_Data\`, prefix `\\172.16.24.11\`.
4. Always use `-LiteralPath` on Get/Set/Add-Content, Copy/Move/Remove/Rename-Item, and Test-Path (Chinese names and `file[1].txt` brackets). **Exception:** `New-Item` on this workstation is Windows PowerShell **5.1** and has **no** `-LiteralPath`. Use `-Path` for `New-Item` only.

Examples:

- `/Company_Data/Management/a.txt` → `\\172.16.24.11\Company_Data\Management\a.txt`
- `/Company_Data/Public/` → `\\172.16.24.11\Company_Data\Public\`
- `/Company_Data/Exchange/inbox.zip` → `\\172.16.24.11\Company_Data\Exchange\inbox.zip`

# Allowed trees (Manager policy)

Write and delete only under:

- `\\172.16.24.11\Company_Data\Management`
- `\\172.16.24.11\Company_Data\Public`
- `\\172.16.24.11\Company_Data\Exchange`

Do **not** write, move-into, or delete under `HR-Private`, `accountancy`, or `IT-Dev` (Access Denied).

Other department folders may appear in a listing. **Do not use them.** Stick to Management / Public / Exchange.

Do not delete share-root files such as `frontend_req_v1.txt` or `new_frontend_project_notice.txt` unless the prompt names that exact path.

# Connect

1. Open PowerShell.
2. Run:

```powershell
Get-ChildItem -LiteralPath '\\172.16.24.11\Company_Data'
```

(`dir \\172.16.24.11\Company_Data` is equivalent.)

3. Success: the listing includes `Management`, `Public`, and `Exchange`.
4. If the command fails with disconnected / network-path not found, run `net use \\172.16.24.11\Company_Data` once, then retry step 2. If it still fails, stop. Do not switch server.

# Shared rules

- Stop on Access Denied, path not found (unless the op is delete and the prompt allows idempotent miss), or authentication errors.
- After create/update/copy/move/download, prove the result with `Test-Path` or `Get-ChildItem`. Use `Get-Content` only on `.txt`, `.md`, or `.csv`. For `.docx` print `Name`, `Length`, and `LastWriteTime` from `Get-Item`.
- Create is not idempotent if the prompt wants a new unique name. Prefer the path the prompt gives. `New-Item -Force` on an existing folder is safe.
- Delete: if `Test-Path` is false, the delete already succeeded; do not error.
- Text files: `Set-Content` / `Add-Content` `-Encoding UTF8`.

# Prose expansion

Commander sets `min_words` plus `topic` or a one-sentence `content` outline. Expand the document on this host. Do not expect the full document in the prompt.

1. If `min_words` is present, write original English of at least that many whitespace-separated words about `topic` or the outline. No lorem ipsum. No invented credentials, hosts, or secrets.
2. If `min_words` is absent and `content` is present, use `content` unchanged (legacy prompts).
3. Do not expand paths or file names.

# Word documents

When `create file` / `update file` uses a `.docx` path, write a real Word document from the expanded prose, then **upload** it to the share. Do **not** `Set-Content` a `.docx` (that is not a Word file). Do not rename a `.txt` to `.docx`.

1. Apply **Prose expansion**.
2. Save locally with Word COM (Desktop or `$env:TEMP`), then `Copy-Item` to the UNC path.
3. Success: UNC `Test-Path` is `$true` and `(Get-Item -LiteralPath '<unc>').Length -gt 4000`.

```powershell
$local = Join-Path $env:USERPROFILE 'Desktop\<file.docx>'
$parent = Split-Path -Parent '<unc>'
if (-not (Test-Path -LiteralPath $parent)) {
  New-Item -ItemType Directory -Force -Path $parent | Out-Null
}
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$doc = $word.Documents.Add()
$word.Selection.TypeText('<topic>')
$word.Selection.TypeParagraph()
$word.Selection.TypeText('<expanded prose>')
$doc.SaveAs2($local, 16)
$doc.Close()
$word.Quit()
Copy-Item -LiteralPath $local -Destination '<unc>' -Force
```

If Word COM is unavailable, stop and report the error. Text files (`.txt`, `.md`, `.csv`) still use `Set-Content` / `Add-Content` `-Encoding UTF8` as below.

# Operations

Skip unused fields. Resolve `path` / `source path` / `destination path` with Path mapping first.

## view

`{path: ...}`

- Directory: `Get-ChildItem -LiteralPath '<unc>'`
- Text file (`.txt`, `.md`, `.csv`): `Get-Content -LiteralPath '<unc>'`
- Word file (`.docx`): `Get-Item -LiteralPath '<unc>'` and print `Name`, `Length`, `LastWriteTime`. Do not `Get-Content` a `.docx`.
- If the path ends with `\` or has no file name, treat as directory.
- Print names (and file text). Do not invent files that were not listed.

## create folder

`{path: ...}`

```powershell
New-Item -ItemType Directory -Force -Path '<unc>'
Test-Path -LiteralPath '<unc>'
```

Success: `Test-Path` is `$true`.

## create file

`{path: ..., min_words: ..., topic: ...}` Optional short `content` outline.

If the path ends in `.docx`, follow **Word documents** (compose locally, then upload). Otherwise apply **Prose expansion**, then:

```powershell
Set-Content -LiteralPath '<unc>' -Value '<expanded text>' -Encoding UTF8
Get-Content -LiteralPath '<unc>' -Raw
```

Success: file exists and the written text has at least `min_words` whitespace-separated words (or matches legacy `content` when `min_words` is absent). Parent folders: create with `New-Item -ItemType Directory -Force -Path '<parent>'` on the parent if missing, then write the file. `Set-Content -Encoding UTF8` on Windows PowerShell 5.1 writes a UTF-8 **BOM**; that is success, not corruption.

## append

`{path: ..., min_words: ..., topic: ...}` Optional short `content` outline.

```powershell
if (-not (Test-Path -LiteralPath '<unc>')) { throw 'append target missing' }
Add-Content -LiteralPath '<unc>' -Value '<expanded text>' -Encoding UTF8
```

Apply **Prose expansion** first. Do not append to `.docx`; use `create file` or `update file` for Word. On this host **Add-Content creates a missing file**. For an append-only prompt, `Test-Path` first; if false, stop. Do not treat a newly created file as a successful append.

## update file

`{path: ..., min_words: ..., topic: ...}` Optional short `content` outline.

If the path ends in `.docx`, follow **Word documents** (replace the local document, then upload again). Otherwise apply **Prose expansion**, then overwrite with `Set-Content` (same as create file). Success: content matches the expanded text.

## copy

`{source path: ..., destination path: ...}`

```powershell
Copy-Item -LiteralPath '<src>' -Destination '<dst>' -Recurse -Force
```

Success: destination `Test-Path` is `$true`. Destination parent must be an allowed tree.

## download

`{path: ...}` Optional `local path` (default: `$env:USERPROFILE\Desktop\<filename>`).

Copy the share file onto this host (download). Resolve `path` with Path mapping first.

```powershell
$local = '<local path or Desktop\filename>'
Copy-Item -LiteralPath '<unc>' -Destination $local -Force
```

Success: local `Test-Path` is `$true` and `(Get-Item $local).Length` equals the source length. Do not `Get-Content` a `.docx`.

## move

`{source path: ..., destination path: ...}`

```powershell
Move-Item -LiteralPath '<src>' -Destination '<dst>' -Force
```

Success: destination exists and source is gone. Both sides must stay inside allowed trees.

## rename

`{path: ..., new name: ...}`

```powershell
Rename-Item -LiteralPath '<unc>' -NewName '<new name>'
```

Success: the new name exists in the same folder. Do not use `..` in `new name`.

## delete

`{path: ...}`

```powershell
if (Test-Path -LiteralPath '<unc>') {
  Remove-Item -LiteralPath '<unc>' -Recurse -Force
}
```

Success: `Test-Path` is `$false`. Never `Remove-Item` `\\172.16.24.11\Company_Data` itself.

# Anti-patterns

- Do not use File Explorer or `Start-Process` on the UNC path.
- Do not call `net use` unless Connect reported the share disconnected.
- Do not continue after Access Denied.
- Do not target `\\i2-dc0-c08.edrtest.local\...`.
- Do not write to `HR-Private`, `accountancy`, or `IT-Dev` on a normal Manager task.
- Do not call `New-Item -LiteralPath` (parameter does not exist on PS 5.1 here). Use `New-Item -Path`.
- Do not put `..` in `Rename-Item -NewName` (OS rejects it as a path).
- Do not `Set-Content` or `Get-Content` a `.docx`. Use Word COM, then `Copy-Item` to upload or download.
