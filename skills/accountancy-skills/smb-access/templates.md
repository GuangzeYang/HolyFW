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

Accountancy `path` must stay under `/Company_Data/accountancy`, `/Company_Data/Public`, or `/Company_Data/Exchange`.

## Examples

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/accountancy/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/accountancy/ledger-notes.txt, content: Q3 invoice draft.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/accountancy/ledger-notes.txt, destination path: /Company_Data/Exchange/ledger-notes.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use move to move a file, {source path: /Company_Data/Public/notice.txt, destination path: /Company_Data/accountancy/notice.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use append to append text, {path: /Company_Data/accountancy/ledger-notes.txt, content: Updated after manager reply.}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use rename to rename a file, {path: /Company_Data/accountancy/ledger-notes.txt, new name: ledger-notes-final.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /Company_Data/accountancy/ledger-notes-final.txt}"
```
