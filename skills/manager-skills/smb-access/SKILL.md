---
name: smb-access
description: Use when creating, copying, moving, deleting, or viewing files on the company SMB share through PowerShell. Do not use a GUI file explorer. Stop immediately on Access Denied.
---

# Connect

1. Open PowerShell.
2. Run `dir \\172.16.24.11\Company_Data`
3. If the command fails, stop. Do not retry with a different server.

Allowed paths for Manager: `\\172.16.24.11\Company_Data\Management`, `\\172.16.24.11\Company_Data\Public`, `\\172.16.24.11\Company_Data\Exchange`. Do not write to HR-Private, accountancy, or IT-Dev.

Map task paths such as `/Company_Data/Management/spec.md` to `\\172.16.24.11\Company_Data\Management\spec.md`.

# Create

- Folder: `New-Item -ItemType Directory -Force -Path '<path>'`
- File: `Set-Content -Path '<path>' -Value '<content>'`
- If either command throws, stop.

# Copy

`Copy-Item -Path '<source>' -Destination '<destination>' -Recurse -Force`

# Move

`Move-Item -Path '<source>' -Destination '<destination>' -Force`

# Delete

`Remove-Item -Path '<path>' -Recurse -Force`

# View

- Folder: `Get-ChildItem -Path '<path>'`
- File: `Get-Content -Path '<path>'`

# Anti-patterns

- Do not use `net use` unless the dir command reports the share is disconnected.
- Do not continue after Access Denied, path not found, or authentication errors.
- Do not invent share names or IP addresses.
