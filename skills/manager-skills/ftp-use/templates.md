# ftp-use prompt templates

Pass the quoted string to `opencode run`. Paths are the real FTPS paths under `/manager`. Login starts at `/`. For `upload` / `append` / `update file`, the agent writes a local document from `topic` and transfers it. `download` copies the remote file to the local Desktop.

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

Manager `path` must stay under `/manager`. Do not write at `/`. Do not use `/ftp-root`.

## Examples

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /manager/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /manager/spec-notes.txt, topic: draft review notes for this week, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use append to append text, {path: /manager/spec-notes.txt, topic: update after programmer reply, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /manager/spec-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use copy to copy a file, {source path: /manager/spec-notes.txt, destination path: /manager/archive/spec-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use rename to rename a file, {path: /manager/spec-notes.txt, new name: spec-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use move to move a file, {source path: /manager/spec-notes-final.txt, destination path: /manager/archive/spec-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use create folder to create a folder, {path: /manager/reviews}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete file to delete a file, {path: /manager/archive/spec-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete folder to delete a folder, {path: /manager/reviews}"
```
