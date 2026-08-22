# ftp-use prompt templates

Pass the quoted string to `opencode run`. Paths use `/ftp-root/hr/...`. For `upload`, the agent writes a local document from `topic` and uploads it over explicit FTPS. `download` copies the remote file to the local Desktop.

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

HR `path` must stay under `/ftp-root/hr`.

## Examples

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /ftp-root/hr/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /ftp-root/hr/staffing-notes.txt, topic: weekly headcount draft, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /ftp-root/hr/staffing-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use create folder to create a folder, {path: /ftp-root/hr/onboarding}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete file to delete a file, {path: /ftp-root/hr/staffing-notes.txt}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use delete folder to delete a folder, {path: /ftp-root/hr/onboarding}"
```
