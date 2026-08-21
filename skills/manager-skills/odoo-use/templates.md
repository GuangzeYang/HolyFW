# odoo-use prompt templates

Pass the quoted string to `opencode run`. The skill loads playwright-browser internally; do not require that name in the prompt.

## Grammar

```text
Use the odoo-use skill, log in to the Odoo system, use the <module> module, <operation>, {<field>: <value>, ...}
```

Omit the `{...}` block when the operation has no fields. Omit unused keys.

| `<module>` | `<operation>` | Fields |
|---|---|---|
| `Employees` | `search employee` | `name` or `query` |
| `Employees` | `open employee` | `name` |
| `Employees` | `add employee` | `name` (required), `job position`, `work email`, `work phone`, `work mobile`, `department`, `manager`, `tags` |
| `Employees` | `update employee` | `name` (required), same optional fields as add |
| `Employees` | `delete employee` | `name` |
| `Employees` | `view departments` | none |
| `Recruitment` | `create job posting` | `job position` (required), `email address` |
| `Recruitment` | `update job posting` | `job position`, `department`, `email address` |
| `Recruitment` | `delete job posting` | `job position` |
| `Recruitment` | `create applicant` | `name`, `job position`, `email`, `phone` |
| `Discuss` | `read inbox` | none |
| `Discuss` | `post message` | `body`, `channel` |
| `Calendar` | `view calendar` | none |
| `Calendar` | `create event` | `title`, `attendee` |
| `Contacts` | `add contact` | `name`, `email`, `phone` |
| `Surveys` | `view surveys` | none |
| `To-do` | `view tasks` | none |

## Examples

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, add employee, {name: DemoNew1, job position: Sales, work email: demonew1@ndrtest.local}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, search employee, {name: Alice HR}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, update employee, {name: DemoNew1, job position: Support}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, delete employee, {name: DemoNew1}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Recruitment module, create job posting, {job position: Human Resources Manager, email address: jobs@ndrtest.local}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Recruitment module, view applications, {job position: HR Assistant}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Discuss module, post message, {channel: general, body: Please send updated headcount by Friday.}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Calendar module, create event, {title: Staffing review, attendee: manager}"
```

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Contacts module, add contact, {name: Campus Recruiter, email: campus@ndrtest.local}"
```
