# Programmer opencode prompt templates

English only. Run on the Programmer soldier host with the matching skill installed. Detailed copies live under `new_skill/programmer-skills/`; `skills/programmer-skills/` holds the deployed Programmer skills.

Grammar details live next to each skill:

- [exchange-use/templates.md](exchange-use/templates.md)
- [odoo-use/templates.md](odoo-use/templates.md)
- [playwright-browser/templates.md](playwright-browser/templates.md)
- [smb-access/templates.md](smb-access/templates.md)
- [ftp-use/templates.md](ftp-use/templates.md)
- [pdf/SKILL.md](pdf/SKILL.md) (copied unchanged; follow that skill for PDF work)

## exchange-use

```text
Use the exchange-use skill, open the Exchange mailbox, <action>, {<field>: <value>, ...}
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager, subject: Weekly staffing update, min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, reply, {min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, search, {query: onboarding}"
```

## odoo-use

```text
Use the odoo-use skill, log in to the Odoo system, use the <module> module, <operation>, {<field>: <value>, ...}
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, add employee, {name: DemoNew1, job position: Sales, work email: demonew1@ndrtest.local}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Recruitment module, create job posting, {job position: Human Resources Manager, email address: jobs@ndrtest.local}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Discuss module, post message, {channel: general, topic: please send updated headcount by Friday, min_words: 300}"
```

## playwright-browser

```text
Use the playwright-browser skill, open the browser, then execute:
1. <op> [, {key: value}]
2. <op> [, {key: value}]
...
Verify: <observable result>
Close the browser after verification.
```

Ops: `goto` `search` `click` `type` `fill` `scroll` `wait` `select` `press` `check` `uncheck` `upload` `download` `extract` `follow` `hover` `back` `forward` `reload` `new tab`.

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: Windows Active Directory backup} 2. follow, {nth: 1} 3. scroll, {direction: down} 4. extract. Verify: article text is non-empty. Close the browser after verification."
```

Replace `REPLACE_*` tokens when generating random browse traffic:

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: REPLACE_QUERY} 2. follow, {nth: 2} 3. click, {name: REPLACE_IN_PAGE_CONTROL} 4. scroll, {direction: down}. Verify: REPLACE_VISIBLE_RESULT. Close the browser after verification."
```

## smb-access

```text
Use the smb-access skill, connect to the SMB shared directory, use <op> to <detail>, {<field>: <value>, ...}
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use view to view a folder, {path: /Company_Data/IT-Dev/}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use create file to create a file, {path: /Company_Data/IT-Dev/sprint-notes.docx, topic: backend ticket list for this sprint, min_words: 400}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /Company_Data/IT-Dev/sprint-notes.docx, destination path: /Company_Data/Exchange/sprint-notes.docx}"
```

```text
opencode run "Use the smb-access skill, connect to the SMB shared directory, use download to download a file, {path: /Company_Data/Exchange/sprint-notes.docx}"
```

## ftp-use

```text
Use the ftp-use skill, connect to the FTPS server, use <op> to <detail>, {<field>: <value>, ...}
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use list to list a folder, {path: /ftp-root/programmer/}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use upload to upload a file, {path: /ftp-root/programmer/sprint-notes.txt, topic: backend ticket list for this sprint, min_words: 500}"
```

```text
opencode run "Use the ftp-use skill, connect to the FTPS server, use download to download a file, {path: /ftp-root/programmer/sprint-notes.txt}"
```
