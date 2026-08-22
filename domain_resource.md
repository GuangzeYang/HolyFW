# Purpose

We use agents to define a set of roles, with each role assigned to a separate host. Through this role-playing setup, we build a simulated intranet that closely resembles a real corporate environment. Over a period of weeks or months, this intranet should continuously generate realistic, benign business traffic that can be observed and collected.

- Role: A "person" who holds a position in the company. Each position performs different computer-based duties and therefore generates different types of traffic. Emotional and behavioral variation should also be considered so that the roles behave more like real people.

  Example: The company's HR employee may sometimes email all employees to announce a new appointment. At the beginning of a month, HR may email department managers to collect performance information for their staff. When in a good mood, HR may send the request on the first day of the month; when in a bad mood, HR may delay it by several days.

- Role-playing: Each human-like role performs its own duties as a real employee would, allowing the virtual company to operate realistically.

- Company: Because the number of available hosts is limited, the simulated network primarily represents a small or micro-sized company.

# Roles

- HR: The company's human resources employee.
- Accountancy: The company's accounting and finance employee.
- Manager: The company's general manager. The Manager publishes front-end project specifications in the shared file directory and follows up on project progress.
- Programmer: The company's front-end programmer, proficient in Python, HTML, CSS, and JavaScript. The Programmer uses a traditional technology stack, never uses frameworks, and prefers to build front-end pages by hand. The Programmer must report project progress to the Manager from time to time, usually by email.
- Victim: A domain-joined workstation that has already been compromised for authorized NDR/EDR adversary-emulation exercises. The Victim does not perform ordinary office work. It executes one bounded penetration-test technique per task from the `penetration-test` skill, using only approved lab targets, and always records evidence plus cleanup.

# Available Resources

## Servers

- Exchange email server
- Web-based office automation system (Odoo)
- SMB shared folders
  - The FQDN of the shared server is `i2-dc0-c08.edrtest.local`.
  - Available shared paths:
    - `Company_Data\Exchange`: Used to exchange large files temporarily between departments so that unnecessary files are not left in the Public folder.
    - `Company_Data\accountancy`: Reserved for Accountancy. Other employees are not allowed to access it. Accountancy uses this path to save or view Excel workbooks, TXT documents, CSV files, and other company-related files.
    - `Company_Data\HR-Private`: Reserved for HR. Other employees are not allowed to access it. HR uses this path to save or view Excel workbooks, TXT documents, CSV files, and similar files.
    - `Company_Data\IT-Dev`: Reserved for the Programmer. Other employees are not allowed to access it. The Programmer uses this path to save or view Markdown documents, TXT documents, CSV files, source code, and similar files. It generally contains project documentation and development code.
    - `Company_Data\Management`: Reserved for the Manager. Other employees are not allowed to access it. The Manager uses this path to save or view Markdown documents, TXT documents, CSV files, source code, and similar files.
    - `Company_Data\Public`: Shared by everyone. It is generally used for notices, Excel workbooks that all employees need to complete, and other company-wide materials.
- FTP server
  - Production host: `172.16.24.15`, port `21`, explicit FTPS.
  - Remote root: `/ftp-root`. Each office role works in its own folder: `hr`, `accountancy`, `manager`, `programmer`.

## MCP

- github
- playwright
- excel

## Skills

1. `exchange-use`: Logs in to the Exchange server through a browser and supports operations such as sending and viewing email.
2. `playwright-browser`: Uses Playwright to open a browser, visit URLs, browse web pages, and perform searches.
3. `smb-access`: Supports uploading files, viewing shared-folder contents, copying files locally, and handling insufficient-access situations.
4. `ftp-use`: Logs in to the company FTPS server and supports listing, uploading, downloading, and deleting files, plus creating or deleting directories.
5. `odoo-use`: Logs in to the web-based Odoo office automation system through a browser and supports personnel management.
6. `penetration-test`: Used only by the Victim role. Runs one authorized adversary-emulation phase (recon, execution, credential access, privilege escalation, lateral movement, persistence, C2 simulation, synthetic exfiltration, or cleanup) against explicitly approved lab assets.

# Working Hours

- Working days: Monday through Friday
- Working hours: 9:00-12:00 and 13:00-18:00
- Lunch: 12:00-13:00

# Task Description Templates

**All generated task descriptions must be written entirely in English.** This requirement applies to actions, parameter names, subjects, message bodies, document contents, and all other generated free text.

When a generated task uses a domain resource, its description must follow the corresponding invocation grammar below. Parameter blocks are JSON-like: no quotation marks around keys or values. Omit unused keys. Omit the `{...}` block when the action or operation has no fields. The task string is the invocation only (never wrap it with `opencode run`).

For actions that produce prose (`send email`, `reply`, `reply all`, `forward`, `save draft`, `create file`, `append`, `update file`, FTPS `upload`, Odoo `post message`): set `min_words` to an integer from 500 to 800. Do not write a long `body` or `content`; at most one short outline sentence. The soldier expands the prose. Do not put `min_words` on paths, recipients, subjects, or view-only actions. SMB `create file` should prefer a `.docx` path plus `topic`; the soldier writes a Word document about that topic and uploads it. `append` / `update file` stay on `.txt`, `.md`, or `.csv`.

Prefer traffic-producing work as the bulk of a generated day: Exchange `send email` / `reply` / `forward` (use `attachment` when a share file already exists in the day's story); SMB `create file` / `copy` / `download` / `append`; FTPS `upload` / `download`; Odoo create / update / `post message`; Playwright search then follow. Treat view-only actions (`view email`, view folder, `list` on FTPS, `view calendar`, `view surveys`, `open people`, `open tasks`, `flag`, `mark unread`) as filler. Keep one skill invocation per task. Related traffic may be consecutive when causal.

## Exchange Email

Template:

`Use the exchange-use skill, open the Exchange mailbox, <action>, {<field>: <value>, ...}`

`target` defaults to `first email` when omitted on view, reply, reply all, forward, delete, or flag. `recipient`, `cc`, and `bcc` must be a lab role short name (`manager`, `hr`, `accountancy`, `programmer`) or the matching `*@ndrtest.local` address, and must not be the current role.

| `<action>` | Required fields | Optional fields |
|---|---|---|
| `send email` | `recipient`, `subject`, `min_words` | `body` (short outline), `cc`, `bcc`, `attachment` |
| `view email` | | `target`, `folder` |
| `reply` | `min_words` | `body` (short outline), `target`, `cc` |
| `reply all` | `min_words` | `body` (short outline), `target`, `cc` |
| `forward` | `recipient`, `min_words` | `target`, `body` (short outline), `cc` |
| `search` | `query` | `target` |
| `delete` | | `target`, `folder` |
| `move` | `folder` | `target` |
| `flag` | | `target` |
| `mark unread` | | `target` |
| `save draft` | `min_words` | `body` (short outline), `recipient`, `subject` |
| `open calendar` | | |
| `open people` | | |
| `open tasks` | | |

## Chrome Browser

Template (one line; numbered ops; must include `Verify:` and `Close the browser after verification.`):

`Use the playwright-browser skill, open the browser, then execute: 1. <op>[, {key: value}] 2. <op>[, {key: value}] ... Verify: <observable result> Close the browser after verification.`

Ops: `goto` `search` `click` `type` `fill` `scroll` `wait` `select` `press` `check` `uncheck` `upload` `download` `extract` `follow` `hover` `back` `forward` `reload` `new tab`.

Do not use Google. Do not open OWA or Odoo URLs with this skill. Each Playwright task must include at least four numbered ops (for example `search` → `follow` → `scroll` → `extract`), plus `Verify:` and `Close the browser after verification.` After `search`, prefer `follow` with `nth`, then `scroll`, `extract`. Do not invent in-page control names for unknown public sites. Do not emit placeholder tokens.

## SMB Shared Folders

Template:

`Use the smb-access skill, connect to the SMB shared directory, use <op> to <detail>, {<field>: <value>, ...}`

`<detail>` restates the op (`view a folder`, `create a file`, `download a file`) and must not add extra semantics. Paths are POSIX `/Company_Data/...` and must stay under the role's allowed trees (`HR-Private`, `accountancy`, `IT-Dev`, `Management`, plus `Public` and `Exchange` as listed for that role). Prefer `.docx` for `create file`. Use `copy` for share-to-share (upload to Exchange/Public). Use `download` to copy a share file to the local Desktop.

| `<op>` | Required fields | Optional fields |
|---|---|---|
| `view` | `path` | |
| `create folder` | `path` | |
| `create file` | `path`, `min_words`, `topic` | `content` (short outline) |
| `append` | `path`, `min_words`, `topic` | `content` (short outline) |
| `update file` | `path`, `min_words`, `topic` | `content` (short outline) |
| `copy` | `source path`, `destination path` | |
| `download` | `path` | `local path` |
| `move` | `source path`, `destination path` | |
| `rename` | `path`, `new name` | |
| `delete` | `path` | |

## FTPS Server

Template:

`Use the ftp-use skill, connect to the FTPS server, use <op> to <detail>, {<field>: <value>, ...}`

`<detail>` restates the op (`list a folder`, `upload a file`, `download a file`) and must not add extra semantics. Paths are POSIX `/ftp-root/<role>/...` and must stay under the role's FTP task path. Prefer `upload` and `download` over `list`. For `upload`, set `min_words` plus a short `topic`; the soldier expands the prose and uploads it.

| `<op>` | Required fields | Optional fields |
|---|---|---|
| `list` | `path` | |
| `upload` | `path`, `min_words`, `topic` | `local path`, `content` (short outline) |
| `download` | `path` | `local path` |
| `create folder` | `path` | |
| `delete file` | `path` | |
| `delete folder` | `path` | |

## Odoo System

Template:

`Use the odoo-use skill, log in to the Odoo system, use the <module> module, <operation>, {<field>: <value>, ...}`

Do not mention `playwright-browser` in the task string. Create and add operations need a unique `name` or `job position`.

| `<module>` | `<operation>` | Required fields | Optional fields |
|---|---|---|---|
| `Employees` | `search employee` | | `name` or `query` |
| `Employees` | `open employee` | `name` | |
| `Employees` | `add employee` | `name` | `job position`, `work email`, `work phone`, `work mobile`, `department`, `manager`, `tags` |
| `Employees` | `update employee` | `name` | same optional fields as add |
| `Employees` | `delete employee` | `name` | |
| `Employees` | `view departments` | | |
| `Recruitment` | `create job posting` | `job position` | `email address` |
| `Recruitment` | `update job posting` | `job position` | `department`, `email address` |
| `Recruitment` | `delete job posting` | `job position` | |
| `Recruitment` | `view applications` | | `job position` |
| `Recruitment` | `create applicant` | `name` | `job position`, `email`, `phone` |
| `Discuss` | `read inbox` | | |
| `Discuss` | `post message` | `min_words` | `body` (short outline), `channel`, `topic` |
| `Calendar` | `view calendar` | | |
| `Calendar` | `create event` | | `title`, `attendee` |
| `Contacts` | `add contact` | | `name`, `email`, `phone` |
| `Surveys` | `view surveys` | | |
| `To-do` | `view tasks` | | |

## Victim Penetration Test

Template:

`Use the penetration-test skill on the victim host, run <mode> for the <phase> phase, {run_id: <id>, approved target: <exact host or IP>, technique: <approved technique>, traffic objective: <expected protocol behavior>, success criteria: <bounded evidence>, cleanup: <rollback and verification>}`

Examples:

- `Use the penetration-test skill on the victim host, run observe for the reconnaissance phase, {run_id: recon-001, approved target: <DC_IP>, technique: domain users and trusts, traffic objective: LDAP queries to the approved DC, success criteria: sanitized user and trust counts saved, cleanup: not applicable}`
- `Use the penetration-test skill on the victim host, run execute for the execution phase, {run_id: exec-wmi-001, approved target: <TARGET_IP>, technique: WMIExec, traffic objective: WMI/DCOM remote command, success criteria: remote marker file created then deleted, cleanup: delete C:\Windows\Temp\holyfw_exec-wmi-001.txt}`
- `Use the penetration-test skill on the victim host, run execute for the credential-access phase, {run_id: cred-kerb-001, approved target: <DC_IP>, technique: Kerberoasting CA-01, traffic objective: Kerberos TGS-REQ to the approved DC, success criteria: ticket count recorded without hash contents, cleanup: delete C:\temp\kerberoast_cred-kerb-001.txt}`

Rules for Victim tasks:

- Do not generate a daily Victim schedule. `victim` is an on-demand role: one technique per dispatched task, driven by campaign state (`last_result` / `next_task`).
- Every task uses exactly one technique and one approved target.
- Prefer modes `observe` or `simulate` unless prerequisites for `execute` are explicitly available.
- Never invent credentials, hashes, tickets, subnets, or unapproved hosts. Use placeholders and treat missing values as blockers.
- Do not generate defense-evasion tasks that disable EDR/AV/logging, open-ended scanning, destructive impact, or multi-host autonomous propagation.

