---
name: odoo-use
description: Use when the accountancy role must work in Odoo 17 (Employees, Recruitment, Discuss, Calendar, Contacts, Surveys, and other Home Menu apps). Depends on playwright-browser. Do not use for Exchange mail or SMB files.
---

Load and follow `playwright-browser` before any browser step. Use Playwright MCP only.

If Discuss or another Odoo app chrome is already visible (Home Menu button present), skip Sign in.

# Endpoints (frozen)

- URL: `http://172.16.24.14:8069/` (login form is `/web/login`)
- Email field: `accountancy`
- Password: `Njupt@241`
- Do not invent another URL or password. Do not open the **Apps** installer.

# Sign in

1. Navigate to `http://172.16.24.14:8069/web/login`.
2. Fill `#login` (label **Email**, placeholder Email) with `accountancy`.
3. Fill `#password` (label **Password**) with the password above.
4. Click the button **Log in**.
5. Wait until the URL contains `/web` and the purple navbar shows **Discuss** (or another app name) with a waffle/grid icon on the far left. Default landing is **Discuss**. If `#login` is still shown, stop. Do not wait for an accessible name `Home Menu`; Playwright `getByRole('button', { name: 'Home Menu' })` is often **false** even when the waffle is on screen.

# Home Menu

1. Click the **waffle / 3×3 grid** at the far left of the purple navbar (left of the current app name, e.g. Discuss). Prefer `button[title='Home Menu']` or `.o_navbar_apps_menu`. Do not require `getByRole('button', { name: 'Home Menu' })`.
2. A left app list appears. Click the app named in the prompt. Known apps: **Discuss**, **To-do**, **Calendar**, **Contacts**, **Project**, **Email Marketing**, **Surveys**, **Employees**, **Recruitment**.
3. Wait for that app’s action header (often a purple **New** button or Inbox).
4. If already in the requested app (navbar shows that name), skip this section.

# Shared form rules

- Many2one (Department, Job Position record, Manager, Coach): click the input, type the value, wait for the dropdown. Click the matching row. If the only row is **Create ...**, click that **Create** row. Do not press Enter blindly.
- After a create/update, click **Save manually** (cloud icon, accessible name contains `Save manually`) in the action header next to **New**. Success: that button **disappears**. If it stays visible, a required field is missing (empty **Employee's Name**, incomplete applicant, etc.). Stop. Click **Discard changes**. Do **not** click `.o_cp_action_menus` — that gear stays **disabled** until the record is saved, and the click times out.
- Unwanted new form: click **Discard changes**, never **Save manually**.
- Saving a blank employee shows a notification/modal. Dismiss it and **Discard**. Do not retry Save in a loop.
- Search: click the header **Search...** input, type the query, press Enter. Wait for kanban/list to refresh.
- Open a kanban record: click the card `div` whose title matches the name. Scroll until it is in view.
- `getByRole('button', { name: 'Home Menu' })` is **false** after login. Always use `button[title='Home Menu']` / `.o_navbar_apps_menu`.

# Employees

Navbar: **Employees** | **Departments** | Reporting | Configuration.

Kanban of employee cards; left sidebar **DEPARTMENT**.

## search employee

`{name: ...}` or `{query: ...}`

1. Open **Employees**.
2. Type the name into **Search...**. Press Enter.
3. Confirm a card showing that name, or an empty kanban. Stop if missing when the prompt required a hit.

## open employee

`{name: ...}`

1. Search if needed, then click the card whose **name** matches.
2. Wait for the form (large **Employee's Name** input). Confirm the name field value.

## add employee

`{name: ...}` required. Optional: `job position`, `work email`, `work phone`, `work mobile`, `department`, `manager`, `tags`.

1. Open **Employees**. Click **New**. Wait for breadcrumb `Employees / New`.
2. **Employee's Name**: `#name_0`, placeholder `Employee's Name`. Fill `name`.
3. **Do not** type the job into `#job_title_0` (placeholder **Job Position** directly under the name). That is a free-text title, not the job record.
4. **Job Position (record)**: on the right column, input beside the **lower** Job Position label (header row with Department / Job Position / Manager / Coach). Type `job position`, then pick or **Create ...** as in Shared form rules.
5. **Work Email**: input to the right of **Work Email** (`#work_email_0`). Fill `work email`.
6. Fill any other provided fields by their labels: **Work Mobile**, **Work Phone**, **Department**, **Manager**, **Tags**.
7. Click **Save manually**. Confirm it hides. Confirm the breadcrumb no longer says `New`.

## update employee

`{name: ...}` required, plus any fields to change from add employee.

1. Open the employee.
2. Change only the provided fields (same locators as add). Still skip `#job_title_0` unless the prompt explicitly says job title text.
3. Click **Save manually**. Confirm it hides.

## delete employee

`{name: ...}` required.

1. Open the employee.
2. Click `.o_cp_action_menus button` (gear / ⋮ in the action header beside **New**).
3. Click **Delete**. In the dialog click **Delete** again.
4. Success: search for the name returns no kanban card. If the card is already gone, treat delete as done (idempotent).

## departments

1. Open **Employees**. Click navbar **Departments**.
2. To inspect: click a department row/card.
3. To add only if the prompt has `{name: ...}`: click **New**, fill the department name, **Save manually**. Otherwise stay read-only.

# Recruitment

Navbar: **Recruitment** | **Applications** | Reporting | Configuration.

Kanban title **Job Positions**. Each card: job title, star, **⋮**, button **N New Applications**, link **N To Recruit**.

## create job posting

`{job position: ...}` required. Optional: `email address` (application alias).

1. Open **Recruitment**. Click **New**. Dialog **Create a Job Position**.
2. **Job Position**: `#name_0`, placeholder `e.g. Sales Manager`. Fill `job position`.
3. If `email address` is set: split at `@`. Prefix → `#alias_name_0` (`e.g. sales-manager`). Domain → `#alias_domain_id_0` (`e.g. domain.com`). If there is no `@`, type the whole value in the prefix box.
4. Click **Create** (not Discard, not the page-level New).
5. Confirm a kanban card titled with `job position`. If the dialog is still open, Create failed; stop.

## update job posting

`{job position: ...}` plus fields to change: `department`, `email address`, new title.

1. Open **Recruitment**. Click the **N To Recruit** link (`a[name=edit_job]`) on the card whose title matches `job position` (or click the job title).
2. Edit **Job Position** (clear first if replacing). **Department**: many2one to the right of Department. **Email Alias**: two inputs split at `@`.
3. **Save manually**. Confirm it hides.

## delete job posting

`{job position: ...}` required.

1. Open **Recruitment**. Click **N To Recruit** (`a[name=edit_job]`) on the matching card.
2. Click `.o_cp_action_menus button`, then **Delete**, then dialog **Delete**.
3. Search for the job title. Success: no kanban card. If already gone, stop successfully.

## view applications

Optional `{job position: ...}`.

1. Open **Recruitment**.
2. On the matching card (or the first card if omitted), click **N New Applications** (`button[name=324]`).
3. Wait for the application kanban/list. Do not create an applicant unless the prompt has applicant fields.

## create applicant

`{name: ...}` required. Optional: `job position`, `email`, `phone`.

1. Navbar **Applications** or **view applications** first.
2. Click **New**. Fill the applicant **name** and any provided email/phone/job. Name-only is often **not** enough: **Save manually** stays visible and the Actions gear stays **disabled**. Fill every required field shown with a red/invalid marker, then Save until the cloud icon **hides**.
3. **Save manually**. Confirm it hides. If it does not hide, **Discard** — do not click Delete.

# Discuss

1. Open **Discuss** (often already open after login).
2. **read inbox**: click **Inbox**. Read the empty state or the first thread. Optional **Mark all read**.
3. **post message**: click channel **general** (or `{channel: ...}`). Click the composer at the bottom, type `{body: ...}`, press Enter or click Send.
4. **search messages**: click **Search Messages**, type `{query: ...}`.

# Calendar

1. Open **Calendar**.
2. **view**: confirm Week/Today chrome. Stop if the prompt is view-only.
3. **create event** when `{title: ...}` is set: click **New**. Fill the title / attendees (`+ Add Attendees`) from the prompt. Save if a Save/Close control appears. Do not save an empty event.

# Contacts

1. Open **Contacts**.
2. Search or click **New** only when `{name: ...}` is set. Fill **Name** and optional **Email** / **Phone**. **Save manually**.

# Surveys / To-do / Project / Email Marketing

Open the app from Home Menu. Use **Search...** or click a card. Click **New** only when the prompt supplies a title/name. For Surveys, **Try It** is allowed for read-only traffic. Do not uninstall modules. Do not open **Apps**.

# Anti-patterns

- Do not type the job record into the Job Position box **under** Employee's Name (`#job_title_0`).
- Do not skip **Save manually** after a real create/update.
- Do not click **Create** on a discarded recruitment dialog leftover.
- Do not invent Odoo URLs, databases, or passwords.
- Do not block on `getByRole('button', { name: 'Home Menu' })`; use the waffle `title='Home Menu'`.

# Idempotency

- Search + open the same employee twice is safe.
- Add/create is **not** idempotent: use a unique `{name}` / `{job position}` from the prompt. Do not create a second record because the first save looked slow.
- Update: writing the same field values again is safe.
- Delete: if search returns no card, the delete already succeeded.
