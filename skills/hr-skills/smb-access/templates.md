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

HR `path` must stay under `/Company_Data/HR-Private`, `/Company_Data/Public`, or `/Company_Data/Exchange`.

## Examples

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/HR-Private/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/HR-Private/staffing-notes.txt, content: Headcount draft for this week.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/HR-Private/staffing-notes.txt, destination path: /Company_Data/Exchange/staffing-notes.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use move to move a file, {source path: /Company_Data/Public/notice.txt, destination path: /Company_Data/HR-Private/notice.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use append to append text, {path: /Company_Data/HR-Private/staffing-notes.txt, content: Updated after manager reply.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use rename to rename a file, {path: /Company_Data/HR-Private/staffing-notes.txt, new name: staffing-notes-final.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /Company_Data/HR-Private/staffing-notes-final.txt}"
```
