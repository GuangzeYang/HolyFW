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

## MCP

- github
- playwright
- excel

## Skills

1. `exchange-use`: Logs in to the Exchange server through a browser and supports operations such as sending and viewing email.
2. `playwright-browser`: Uses Playwright to open a browser, visit URLs, browse web pages, and perform searches.
3. `smb-access`: Supports uploading files, viewing shared-folder contents, copying files locally, and handling insufficient-access situations.
4. FTP skill: Uploads files to and downloads files from a target FTP server.
5. `odoo-use`: Logs in to the web-based Odoo office automation system through a browser and supports personnel management.
6. `penetration-test`: Used only by the Victim role. Runs one authorized adversary-emulation phase (recon, execution, credential access, privilege escalation, lateral movement, persistence, C2 simulation, synthetic exfiltration, or cleanup) against explicitly approved lab assets.

# Working Hours

- Working days: Monday through Friday
- Working hours: 9:00-12:00 and 13:30-18:00

# Task Description Templates

**All generated task descriptions must be written entirely in English.** This requirement applies to actions, parameter names, subjects, message bodies, document contents, and all other generated free text.

When a generated task uses a domain resource, its description must follow the corresponding invocation template below.

## Exchange Email

Template:

`Use the exchange-use skill, open the Exchange mailbox, <action>[, {parameters}]`

Examples:

- `Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: admin@example.com, cc: boss@example.com, subject: Project status report, body: Please review the latest project progress.}`
- `Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}`
- `Use the exchange-use skill, open the Exchange mailbox, reply to email, {cc: team@example.com, body: Received. I will handle it shortly.}`

The Exchange mailbox supports the following three core actions:

1. `send email`
   - Parameters contain the recipient, carbon-copy recipient, subject, and message body.
   - Use a JSON-like format without quotation marks. The supported fields are `recipient`, `cc`, `subject`, and `body`.
   - Example: `{recipient: admin@example.com, cc: boss@example.com, subject: Project status report, body: Please review the progress.}`
   - **Important:** Values for `recipient` and `cc` must be converted into valid, complete email addresses in strict accordance with the Recipient Address Handling Rules before they are inserted.

2. `view email`
   - The parameter identifies the email to view.
   - Use a JSON-like format without quotation marks. The supported field is `target`.
   - Example: `{target: first email}`

3. `reply to email`
   - Parameters contain a carbon-copy recipient when required by the task and the reply body.
   - Use a JSON-like format without quotation marks. The supported fields are `cc` and `body`.
   - Example: `{cc: team@example.com, body: Received, thank you.}`
   - The `cc` value must also be converted into a valid, complete email address in strict accordance with the Recipient Address Handling Rules. A reply should normally preserve the original email content.

## Chrome Browser

Template:

`Use the playwright-browser skill, open the browser, <action>.`

Examples:

- `Use the playwright-browser skill, open the browser, visit the xxx website.`
- `Use the playwright-browser skill, open the browser, search for the xxx keyword, randomly select a relevant page, and browse its content for xx seconds.`

The `<action>` placeholder describes browser interactions, including page scrolling, mouse clicks, keyboard input, and URL navigation.

## SMB Shared Folders

Template:

`Use the smb-access skill, connect to the SMB shared directory, use <operation type> to <specific operation>[, {parameters}]`

Examples:

- `Use the smb-access skill, connect to the SMB shared directory, use create to create a file, {path: /share/test.txt, content: hello world}`
- `Use the smb-access skill, connect to the SMB shared directory, use copy to copy a file, {source path: /share/a.txt, destination path: /backup/a.txt}`
- `Use the smb-access skill, connect to the SMB shared directory, use delete to delete a file, {path: /share/old.txt}`

SMB shared-file operations are divided into the following operation types:

1. `create`: Create a folder, create a file, or write content.
   - Parameters contain path and content information.
   - Use a JSON-like format without quotation marks. The supported fields are `path` and `content`.
   - Example: `{path: /share/test.txt, content: hello world}`

2. `copy`: Copy a folder or file.
   - Parameters contain the source and destination paths.
   - Use a JSON-like format without quotation marks. The supported fields are `source path` and `destination path`.
   - Example: `{source path: /share/a.txt, destination path: /backup/a.txt}`

3. `move`: Move a folder or file.
   - Parameters contain the source and destination paths.
   - Use a JSON-like format without quotation marks. The supported fields are `source path` and `destination path`.
   - Example: `{source path: /share/a.txt, destination path: /backup/a.txt}`

4. `delete`: Delete a folder or file.
   - The parameter contains the target path.
   - Use a JSON-like format without quotation marks. The supported field is `path`.
   - Example: `{path: /share/old.txt}`

5. `view`: View folder contents or file contents.
   - The parameter contains the target path.
   - Use a JSON-like format without quotation marks. The supported field is `path`.
   - Example: `{path: /share/}`

## Odoo System

Template:

`Use the playwright-browser and odoo-use skills, open the browser, log in to the Odoo system, use the <module> module, <operation>[, {parameters}]`

Examples:

- `Use the playwright-browser and odoo-use skills, open the browser, log in to the Odoo system, use the Employees module, add employee, {name: DemoNew1, job position: Sales, work email: 123987@demo.com}`
- `Use the playwright-browser and odoo-use skills, open the browser, log in to the Odoo system, use the Employees module, delete employee, {name: demo-1, job position: Sales, work email: 12fgh87@demo.com}`

Odoo provides the following modules and operations:

1. `Employees` module: Add an employee, delete an employee, or update employee information.
   - Parameters contain employee information.
   - Use a JSON-like format without quotation marks. The supported fields are `name`, `job position`, and `work email`.
   - Example: `{name: DemoNew1, job position: Sales, work email: 123987@demo.com}`

2. `Recruitment` module: Create a job posting, update recruitment information, or view job applications.
   - Parameters contain recruitment information.
   - Use a JSON-like format without quotation marks. The supported fields are `job position`, `department`, `email address`, and `work location`.
   - Example: `{job position: Human Resources Manager, department: Human Resources, email address: 123987@demo.com, work location: Nanjing}`

## Victim Penetration Test

Template:

`Use the penetration-test skill on the victim host, run <mode> for the <phase> phase, {run_id: <id>, approved target: <exact host or IP>, technique: <approved technique>, traffic objective: <expected protocol behavior>, success criteria: <bounded evidence>, cleanup: <rollback and verification>}`

Examples:

- `Use the penetration-test skill on the victim host, run observe for the reconnaissance phase, {run_id: recon-001, approved target: <DC_IP>, technique: domain users and trusts, traffic objective: LDAP queries to the approved DC, success criteria: sanitized user and trust counts saved, cleanup: not applicable}`
- `Use the penetration-test skill on the victim host, run execute for the execution phase, {run_id: exec-wmi-001, approved target: <TARGET_IP>, technique: WMIExec, traffic objective: WMI/DCOM remote command, success criteria: remote marker file created then deleted, cleanup: delete C:\Windows\Temp\holyfw_exec-wmi-001.txt}`
- `Use the penetration-test skill on the victim host, run execute for the credential-access phase, {run_id: cred-kerb-001, approved target: <DC_IP>, technique: Kerberoasting CA-01, traffic objective: Kerberos TGS-REQ to the approved DC, success criteria: ticket count recorded without hash contents, cleanup: delete C:\temp\kerberoast_cred-kerb-001.txt}`

Rules for Victim tasks:

- Generate Victim tasks only when the `victim` role is present in `commander.ini`.
- Every task uses exactly one technique and one approved target.
- Prefer modes `observe` or `simulate` unless prerequisites for `execute` are explicitly available.
- Never invent credentials, hashes, tickets, subnets, or unapproved hosts. Use placeholders and treat missing values as blockers.
- Do not generate defense-evasion tasks that disable EDR/AV/logging, open-ended scanning, destructive impact, or multi-host autonomous propagation.

