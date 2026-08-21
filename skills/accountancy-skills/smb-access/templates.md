# smb-access prompt templates

Pass the quoted string to `opencode run`. Paths may use `/Company_Data/...`; the skill maps them to `\\172.16.24.11\Company_Data\...`. For `.docx`, the agent writes a Word document from `topic` and uploads it; `download` copies the share file to the local Desktop.

## Grammar

```text
Use the smb-access skill, connect to the SMB shared directory, use <op> to <detail>, {<field>: <value>, ...}
```

| `<op>` | Fields |
|---|---|
| `view` | `path` |
| `create folder` | `path` |
| `create file` | `path` (prefer `.docx`), `min_words` (300-800), `topic` (required), `content` (optional short outline) |
| `append` | `path` (`.txt` / `.md` / `.csv`), `min_words` (300-800), `topic` (required), `content` (optional short outline) |
| `update file` | `path`, `min_words` (300-800), `topic` (required), `content` (optional short outline) |
| `copy` | `source path`, `destination path` |
| `download` | `path`, `local path` (optional; default Desktop) |
| `move` | `source path`, `destination path` |
| `rename` | `path`, `new name` |
| `delete` | `path` |

Accountancy `path` must stay under `/Company_Data/accountancy`, `/Company_Data/Public`, or `/Company_Data/Exchange`.

## Examples

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/accountancy/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/accountancy/ledger-notes.docx, topic: Q3 invoice draft, min_words: 400}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/accountancy/ledger-notes.docx, destination path: /Company_Data/Exchange/ledger-notes.docx}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use download to download a file, {path: /Company_Data/Exchange/ledger-notes.docx}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use move to move a file, {source path: /Company_Data/Public/notice.txt, destination path: /Company_Data/accountancy/notice.txt}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use append to append text, {path: /Company_Data/accountancy/ledger-notes.txt, topic: update after manager reply, min_words: 300}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use rename to rename a file, {path: /Company_Data/accountancy/ledger-notes.docx, new name: ledger-notes-final.docx}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /Company_Data/accountancy/ledger-notes-final.docx}"
```
