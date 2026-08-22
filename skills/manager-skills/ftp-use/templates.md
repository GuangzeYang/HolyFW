# ftp-use prompt templates

Pass the quoted string to `opencode run`. Paths use `/ftp-root/manager/...`. For `upload`, the agent writes a local document from `topic` and uploads it over explicit FTPS. `download` copies the remote file to the local Desktop.

## Grammar

```text
Use the ftp-use skill, connect to the FTPS server, use <op> to <detail>, {<field>: <value>, ...}
```

| `<op>` | Fields |
|---|---|
| `list` | `path` |
| `upload` | `path`, `min_words` (500-800), `topic` (required), `local path` (optional), `content` (optional short outline) |
| `download` | `path`, `local path` (optional; default Desktop) |
| `create folder` | `path` |
| `delete file` | `path` |
| `delete folder` | `path` |

Manager `path` must stay under `/ftp-root/manager`.

## Examples

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /ftp-root/manager/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /ftp-root/manager/spec-notes.txt, topic: draft review notes for this week, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /ftp-root/manager/spec-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use create folder to create a folder, {path: /ftp-root/manager/reviews}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete file to delete a file, {path: /ftp-root/manager/spec-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete folder to delete a folder, {path: /ftp-root/manager/reviews}"
```
