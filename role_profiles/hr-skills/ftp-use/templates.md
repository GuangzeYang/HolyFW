# ftp-use prompt templates

Pass the quoted string to `opencode run`. Paths are the real FTPS paths under `/hr`. Login starts at `/`. For `upload` / `append` / `update file`, the agent writes a local document from `topic` and transfers it. `download` copies the remote file to the local Desktop.

## Grammar

```text
Use the ftp-use skill, connect to the FTPS server, use <op> to <detail>, {<field>: <value>, ...}
```

| `<op>` | Fields |
|---|---|
| `list` | `path` |
| `upload` | `path`, `min_words` (500-800), `topic` (required), `local path` (optional), `content` (optional short outline) |
| `append` | `path`, `min_words` (500-800), `topic` (required), `content` (optional short outline) |
| `update file` | `path`, `min_words` (500-800), `topic` (required), `content` (optional short outline) |
| `download` | `path`, `local path` (optional; default Desktop) |
| `copy` | `source path`, `destination path` |
| `move` | `source path`, `destination path` |
| `rename` | `path`, `new name` |
| `create folder` | `path` |
| `delete file` | `path` |
| `delete folder` | `path` |

HR `path` must stay under `/hr`. Do not write at `/`. Do not use `/ftp-root`.

## Examples

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /hr/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /hr/staffing-notes.txt, topic: weekly headcount draft, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use append to append text, {path: /hr/staffing-notes.txt, topic: update after manager reply, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /hr/staffing-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use copy to copy a file, {source path: /hr/staffing-notes.txt, destination path: /hr/archive/staffing-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use rename to rename a file, {path: /hr/staffing-notes.txt, new name: staffing-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use move to move a file, {source path: /hr/staffing-notes-final.txt, destination path: /hr/archive/staffing-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use create folder to create a folder, {path: /hr/onboarding}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete file to delete a file, {path: /hr/archive/staffing-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete folder to delete a folder, {path: /hr/onboarding}"
```
