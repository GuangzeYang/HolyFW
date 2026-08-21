# smb-access prompt templates

Pass the quoted string to `opencode run`. Paths may use `/Company_Data/...`; the skill maps them to `\\172.16.24.11\Company_Data\...`.

## Grammar

```text
Use the smb-access skill, connect to the SMB shared directory, use <op> to <detail>, {<field>: <value>, ...}
```

| `<op>` | Fields |
|---|---|
| `view` | `path` |
| `create folder` | `path` |
| `create file` | `path`, `content` |
| `append` | `path`, `content` |
| `update file` | `path`, `content` |
| `copy` | `source path`, `destination path` |
| `move` | `source path`, `destination path` |
| `rename` | `path`, `new name` |
| `delete` | `path` |

Manager `path` must stay under `/Company_Data/Management`, `/Company_Data/Public`, or `/Company_Data/Exchange`.

## Examples

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/Management/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/Management/spec-notes.md, content: Draft review notes for this week.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/Management/spec-notes.md, destination path: /Company_Data/Exchange/spec-notes.md}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use move to move a file, {source path: /Company_Data/Public/notice.txt, destination path: /Company_Data/Management/notice.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use append to append text, {path: /Company_Data/Management/spec-notes.md, content: Updated after HR reply.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use rename to rename a file, {path: /Company_Data/Management/spec-notes.md, new name: spec-notes-final.md}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /Company_Data/Management/spec-notes-final.md}"
```
