# ftp-use prompt templates

Pass the quoted string to `opencode run`. Paths are the real FTPS paths under `/programmer`. Login starts at `/`. For `upload` / `append` / `update file`, the agent writes a local document from `topic` and transfers it. `download` copies the remote file to the local Desktop.

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

Programmer `path` must stay under `/programmer`. Do not write at `/`. Do not use `/ftp-root`.

## Examples

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /programmer/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /programmer/sprint-notes.txt, topic: backend ticket list for this sprint, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use append to append text, {path: /programmer/sprint-notes.txt, topic: update after manager review, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /programmer/sprint-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use copy to copy a file, {source path: /programmer/sprint-notes.txt, destination path: /programmer/handoff/sprint-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use rename to rename a file, {path: /programmer/sprint-notes.txt, new name: sprint-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use move to move a file, {source path: /programmer/sprint-notes-final.txt, destination path: /programmer/handoff/sprint-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use create folder to create a folder, {path: /programmer/handoff}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete file to delete a file, {path: /programmer/handoff/sprint-notes-final.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete folder to delete a folder, {path: /programmer/handoff}"
```
