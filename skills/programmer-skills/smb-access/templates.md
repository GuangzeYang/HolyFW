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

Programmer `path` must stay under `/Company_Data/IT-Dev`, `/Company_Data/Public`, or `/Company_Data/Exchange`.

## Examples

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/IT-Dev/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/IT-Dev/sprint-notes.txt, content: Backend ticket list for this sprint.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/IT-Dev/sprint-notes.txt, destination path: /Company_Data/Exchange/sprint-notes.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use move to move a file, {source path: /Company_Data/Public/notice.txt, destination path: /Company_Data/IT-Dev/notice.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use append to append text, {path: /Company_Data/IT-Dev/sprint-notes.txt, content: Updated after manager reply.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use rename to rename a file, {path: /Company_Data/IT-Dev/sprint-notes.txt, new name: sprint-notes-final.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /Company_Data/IT-Dev/sprint-notes-final.txt}"
```
