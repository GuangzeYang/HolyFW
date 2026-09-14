---
name: odoo-use
description: Use when the programmer role must work in Odoo 17 (Employees, Recruitment, Discuss, Calendar, Contacts, Surveys, and other Home Menu apps). Depends on playwright-browser. Do not use for Exchange mail or SMB files.
---

Use Playwright MCP tools. This skill owns Odoo (`172.16.24.14:8069`). Do **not** apply playwright-browser public-web rules (stop-on-Odoo, screenshot after every navigation) except **Visual fallback** below.

**Snapshot budget (important).** An Odoo form with chatter is 500–700 lines in a full a11y `snapshot`, and the kanban/list views dump every record. Re-snapshotting after each step is what makes this skill time out. **Prefer the selectors below.** Do not dump the whole page.

# Visual fallback

1. Click/type the verified selector. If it fails, retry **the same selector once**.
2. If it still fails, `take_screenshot` **once**, read the image, and continue the current action from a visible control (button label, modal, required-field marker).
3. Do not take two screenshots in a row. Do not replace this step with a full-page a11y snapshot of a form or kanban.
4. If the screenshot still does not unlock the step, stop and report. Do not switch to a playwright-browser task, and do not open a second browser.

If Discuss or another Odoo app chrome is already visible (Home Menu waffle present), skip Sign in.

# Endpoints (frozen)

- URL: `http://172.16.24.14:8069/` (login form is `/web/login`)
- Email field: `programmer`
- Password: `Njupt@241`
- Do not invent another URL, database, or password. Do not open the **Apps** installer.

# Verified selectors (use these, do not guess)

| Target | Selector |
|---|---|
| Email field | `#login` (placeholder `Email`) |
| Password field | `#password` |
| Log in button | `button[type=submit]` (text **Log in**) |
| Logged-in marker | `.o_main_navbar` |
| Home Menu waffle | `button[title="Home Menu"]` (== `.o_navbar_apps_menu`) |
| App inside Home Menu | `a.o_app:has-text("Employees")` |
| New (kanban) | `button.o-kanban-button-new:visible` |
| New (list view) | `button.o_list_button_add:visible` |
| New (form header) | `button.o_form_button_create >> nth=0` (2 copies) |
| Save | `button.o_form_button_save` (icon; accessible name **Save manually**) |
| Discard | `button.o_form_button_cancel` |
| Modal **Stay here / Discard changes** | `.modal button:has-text("Stay here")` / `.modal button:has-text("Discard changes")` |
| Search (list/kanban only) | `.o_searchview_input` (placeholder `Search...`) |
| Applications app-nav | navbar dropdown **Applications** → `a.dropdown-item:has-text("All Applications")` |
| Modal | `.modal`, title `.modal-title` |
| Modal **Create** | `.modal button:has-text("Create")` |
| Modal **Discard** | `.modal button:has-text("Discard")` |
| Many2one input | `#<field>_0` (e.g. `#job_id_0`) — Odoo sets **id**, not `name` |
| Many2one existing value | `.o-autocomplete--dropdown-item:not(.o_m2o_dropdown_option):has-text("Sales")` |
| Many2one **Create** row | `.o_m2o_dropdown_option_create` |
| Many2one **Search More** row | `.o_m2o_dropdown_option_search_more` |

Odoo renders desktop **and** mobile copies of some controls. If a click hits a *strict mode violation*, append `:visible` or `>> nth=0`, or scope to `.o_control_panel` / `.modal`.

# Sign in

1. Navigate to `http://172.16.24.14:8069/web/login`.
2. Fill `#login` with `programmer`.
3. Fill `#password` with `Njupt@241`.
4. Click `button[type=submit]` (**Log in**).
5. Wait until `.o_main_navbar` is visible and the URL contains `/web`. If `#login` is still shown, stop.
   - Do **not** test `getByRole('button', { name: 'Home Menu' })`; it is usually false. Use `button[title="Home Menu"]`.

# Home Menu

1. Click `button[title="Home Menu"]`.
2. Click `a.o_app:has-text("<App>")`. Known apps: **Discuss**, **To-do**, **Calendar**, **Contacts**, **Project**, **Email Marketing**, **Surveys**, **Employees**, **Recruitment**.
3. Wait for the app header (purple **New**, Inbox, etc.). If the navbar already shows the app, skip this section.

# Shared form rules

- **Many2one** (Department, Job Position record, Manager, Coach, Applied Job): click the input `#<field>_0`, type the value, wait for `.o-autocomplete--dropdown-item`, and click the **existing** option (`.o-autocomplete--dropdown-item:not(.o_m2o_dropdown_option):has-text("<value>")`). Two rows may match the same name — append `>> nth=0`. If only `.o_m2o_dropdown_option_create` matches, click that. Do not press Enter blindly.
- **Save**: after a real create/update click `button.o_form_button_save`. Success = the URL gains `&id=<n>`, the page title changes, and the Save button **hides**. If Save stays visible, a required field is missing (e.g. **Subject** on an applicant) — fill it and Save again; if it still will not save, click `button.o_form_button_cancel` (**Discard changes**) and stop. Do not loop Save.
- **Unwanted new form / modal**: click `button.o_form_button_cancel`, or in a dialog `.modal button:has-text("Discard changes")` (the error/unsaved-changes dialog uses **Stay here** / **Discard changes**, not the form buttons). Never Save.
- **Search**: click `.o_searchview_input`, type the query, press Enter, wait for the kanban/list to refresh.
- **Open a kanban record**: click the card `div` whose title matches the name; scroll until it is in view.
- If a modal is left open, click **Close** or `.modal button:has-text("Discard")` before continuing.

# Employees

Navbar: **Employees** | **Departments** | Reporting | Configuration. Kanban of cards; left sidebar **DEPARTMENT**.

## search employee

`{name: ...}` or `{query: ...}`

1. Open **Employees**.
2. Type the name into `.o_searchview_input`. Press Enter.
3. Confirm a card with that name, or an empty kanban. Stop if missing when a hit was required.

## open employee

`{name: ...}`

1. Search if needed, then click the card whose name matches.
2. Wait for the form (`#name_0` **Employee's Name**). Confirm its value.

## add employee

`{name: ...}` required. Optional: `job position`, `work email`, `work phone`, `work mobile`, `department`, `manager`, `tags`.

1. Open **Employees**. Click `button.o-kanban-button-new:visible`. Wait for the form.
2. **Employee's Name**: `#name_0` (placeholder `Employee's Name`). Fill `name`.
3. Do **not** use `#job_title_0` (placeholder **Job Position** under the name) — that is free text.
4. **Job Position record**: it is `#job_id_0`. Click it, type `job position`, then pick the existing or `.o_m2o_dropdown_option_create` option.
5. **Work Email**: `#work_email_0`. Fill `work email`.
6. Other fields by exact id: **Work Phone** `#work_phone_0`, **Work Mobile** `#mobile_phone_0`, **Department** `#department_id_0`, **Manager** `#parent_id_0`, **Coach** `#coach_id_0`, **Tags** `#category_ids_0`.
7. Click `button.o_form_button_save`. Confirm it hides and the breadcrumb no longer says **New**.

## update employee

`{name: ...}` required, plus fields to change.

1. Open the employee.
2. Change only the provided fields (same ids as add; still skip `#job_title_0`).
3. Click `button.o_form_button_save`. Confirm it hides.

## delete employee

`{name: ...}` required.

1. Open the employee.
2. Click the action menu: `.o_cp_action_menus button` (⋮ beside **New**).
3. Click **Delete**, then **Delete** in the dialog.
4. Success: search returns no card. If already gone, treat as done.

## departments

1. Open **Employees**. Click navbar **Departments**.
2. Inspect a row/card. Add only when `{name: ...}` is set: **New** → fill → `button.o_form_button_save`. Otherwise read-only.

# Recruitment

Navbar: **Recruitment** | **Applications** | Reporting | Configuration. Kanban title **Job Positions**. Each card: title, star, ⋮, **N New Applications**, link **N To Recruit**.

## create job posting

`{job position: ...}` required. Optional: `email address` (application alias).

1. Open **Recruitment**. Click `button.o-kanban-button-new:visible`. A `.modal` **Create a Job Position** opens.
2. **Job Position**: `.modal #name_0` (placeholder `e.g. Sales Manager`). Fill `job position`.
3. If `email address` is set: split at `@`. Prefix → `.modal #alias_name_0` (`e.g. sales-manager`). Domain → `.modal #alias_domain_id_0` (`e.g. domain.com`). If there is no `@`, put the whole value in the prefix box.
4. Click `.modal button:has-text("Create")` (not **Discard**).
5. Confirm a kanban card titled `job position`. If the modal is still open, Create failed; stop.

## update job posting

`{job position: ...}` plus fields to change.

1. Open **Recruitment**. Click the **N To Recruit** link (`a[name=edit_job]`) on the matching card (or the title).
2. Edit **Job Position** (clear first if replacing). **Department**: many2one `#department_id_0`. **Email Alias**: the two inputs `#alias_name_0` / `#alias_domain_id_0`.
3. Click `button.o_form_button_save`. Confirm it hides.

## delete job posting

`{job position: ...}` required.

1. Open **Recruitment**. Click **N To Recruit** (`a[name=edit_job]`) on the matching card.
2. Click `.o_cp_action_menus button`, then **Delete**, then **Delete** in the dialog.
3. Search for the title: success = no card. If already gone, stop successfully.

## view applications

Optional `{job position: ...}`.

1. Open **Recruitment**.
2. On the matching card (or the first), click **N New Applications** (`button[name=324]`).
3. Wait for the application kanban/list. Do not create an applicant unless the prompt has applicant fields.

## create applicant

`{name: ...}` required. Optional: `job position`, `email`, `phone`.

**Known instance defect.** Creating an applicant **with a Job Position in one step** fails on this Odoo: the **New** stage auto-sends the *Application Acknowledgement* template and there is **no outgoing mail server / sender address** configured. Odoo shows **"Oh snap! Unable to send message, please configure the sender's email address."** and the save **rolls back** (the record is **not** created). Work around it by creating the applicant **without** the Job Position, then adding the Job Position in a second save — the template fires only on create.

1. Go to **Applications**: navbar dropdown **Applications** → `a.dropdown-item:has-text("All Applications")` (or use **view applications**).
2. Click **New** (`button.o_list_button_add:visible` in list view; `button.o-kanban-button-new:visible` in kanban).
3. **Applicant's Name** is `#partner_name_0` (placeholder `e.g. John Doe`) — **not** `#name_0`.
4. **Subject / Application** is `#name_0` (textarea, placeholder `e.g. Sales Manager 2 year experience`). It is **required**; set it (use `name` when no subject is given). Without it, Save silently does nothing.
5. **Email** is `#email_from_0`; fill `email` / `phone` if provided.
6. **Leave `#job_id_0` empty on this first save.** Click `button.o_form_button_save`. Success = URL gains `&id=<n>`, title updates, Save hides.
7. Now set **Applied Job** `#job_id_0` (click, type `job position`, pick the existing `>> nth=0` or the **Create** option). Click `button.o_form_button_save` again. Success = Save hides and the header shows the job. No acknowledgement email is sent on update.
8. If the "Unable to send message" dialog still appears, click `.modal button:has-text("Stay here")`, confirm the record is absent, and retry from step 2 **without** `#job_id_0`.

# Discuss

1. Open **Discuss**.
2. **read inbox**: click **Inbox**. Read the empty state or the first thread. Optional **Mark all read**.
3. **post message**: click the channel: `button.o-mail-DiscussSidebarChannel:has-text("general")` (or `{channel: ...}`). If `min_words` is set, write original English of at least that many words about `topic` / the `body` outline (no lorem ipsum, no invented secrets). Click the composer `textarea.o-mail-Composer-input` (placeholder `Message #general…`), type the text, then click `button.o-mail-Composer-send` or press Enter.
4. **search messages**: click **Search Messages**, type `{query: ...}`.
5. If the composer is missing, the thread is not open — click the channel again.

# Calendar

1. Open **Calendar**.
2. **view**: confirm Week/Today chrome. Stop if the prompt is view-only.
3. **create event** when `{title: ...}` is set: click `button.o-calendar-button-new >> nth=0`, fill the title `#name_0` (placeholder `e.g. Business Lunch`) and any attendees (`+ Add Attendees`) from the prompt, then click `button.o_form_button_save`. Do not save an empty event.

# Contacts

1. Open **Contacts**.
2. Search, or click **New** only when `{name: ...}` is set. Fill **Name** and optional **Email** / **Phone**. Click `button.o_form_button_save`.

# Surveys / To-do / Project / Email Marketing

Open the app from Home Menu. Use `.o_searchview_input` or click a card. Click **New** only when the prompt supplies a title/name. For Surveys, **Try It** is allowed for read-only traffic. Do not uninstall modules. Do not open **Apps**.

# Recovery (symptom → fix)

- **Selector still fails after one retry** → **Visual fallback**: `take_screenshot` once, read it, continue. Do not dump a full a11y snapshot of the form or kanban.
- **Snapshot huge / "truncated"** → stop snapshotting; use the selector table; scope to `.o_form_view` / `.modal`.
- **strict mode violation (2+ matches)** → append `:visible` or `>> nth=0`; scope to `.o_control_panel` / `.modal`.
- **`input[name="x_0"]` matches nothing** → Odoo uses `id`; use `#x_0`.
- **New button not found in a list** → use `button.o_list_button_add:visible` (kanban uses `button.o-kanban-button-new:visible`; form header uses `button.o_form_button_create >> nth=0`).
- **Save stays visible / does nothing** → a required field is missing (applicant **Subject** `#name_0` is a common one). Fill it and Save again; if it still fails, Discard.
- **"Oh snap! Unable to send message, please configure the sender's email address."** (applicant create) → the New-stage acknowledgement template has no sender. Click `.modal button:has-text("Stay here")`, then create **without** `#job_id_0` and add the job in a second save (see **create applicant**).
- **Error / unsaved-changes modal** → its buttons are **Stay here** / **Discard changes**; use `.modal button:has-text("Discard changes")`, not `button.o_form_button_cancel`.
- **`.o_searchview_input` matches nothing** → you are on a form view; search only exists in list/kanban. Go back or open the app first.
- **Many2one typed but no dropdown** → click the input first, then type; if only **Create** appears, click `.o_m2o_dropdown_option_create`.
- **Many2one dropdown has duplicate names** → append `>> nth=0`.
- **Modal will not close / leftover** → click `.modal button:has-text("Discard")` or **Close**, then continue.
- **Home Menu lookup fails** → use `button[title="Home Menu"]`, not `getByRole('button', { name: 'Home Menu' })`.
- **Discuss composer missing** → click the channel again to open the thread.
- **Login form still shown after Log in** → re-fill `#login` / `#password` and click `button[type=submit]` once, then stop.
- **Tool timeout** → wait, retry the same step once, then stop.

# Anti-patterns

- Do not take a screenshot until the verified selector has failed twice.
- Do not switch to a playwright-browser task or open a second browser on Odoo.
- Do not type the job record into `#job_title_0` (free text under Employee's Name).
- Do not create an applicant **with** `#job_id_0` in the first save; it triggers the acknowledgement template and rolls back. Create first, add the job second.
- Do not bypass a form failure with raw `call_kw` / `run_code_unsafe`; use the two-phase applicant recipe.
- Do not skip `button.o_form_button_save` after a real create/update.
- Do not loop Save when it stays visible; fill the required field or Discard.
- Do not invent Odoo URLs, databases, or passwords.
- Do not use `run_code_unsafe` / raw `evaluate` to click when a selector above matches.
- Do not block on `getByRole('button', { name: 'Home Menu' })`; use the waffle `title='Home Menu'`.

# Idempotency

- Search + open the same employee twice is safe.
- Add/create is **not** idempotent: use the unique `{name}` / `{job position}` from the prompt. Do not create a second record because the first save looked slow.
- Update: writing the same values again is safe.
- Delete: if search returns no card, the delete already succeeded.
