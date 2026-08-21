---
name: exchange-use
description: Use when sending, viewing, replying, forwarding, searching, or filing Exchange mail in Outlook on the web (OWA 2016). Depends on playwright-browser. Do not use for Odoo, SMB, or generic web search.
---

Load `playwright-browser`, then apply the **OWA overlay** in that skill. On `/owa/` pages those overlay rules beat generic pacing.

If already signed in (Inbox or folder list visible), skip Sign in.

# Endpoints (frozen)

- Preferred URL: `https://i1-mail1-c02.ndrtest.local/owa/`
- Fallback host: `172.16.24.12`
- Fallback logon URL: `https://172.16.24.12/owa/auth/logon.aspx`
- Username: `ndrtest\manager`
- Password: `Njupt@241`
- Do not invent another host, mailbox, or password.

# Sign in (FQDN, then IP)

1. Navigate to `https://i1-mail1-c02.ndrtest.local/owa/`. Wait until the logon form (`#username`) or Mail chrome loads.
2. **Degrade to IP** when any of these happen: connection closed / timeout / DNS failure / HTTP 4xx–5xx / logon form never appears after one wait. Then open `https://172.16.24.12/owa/auth/logon.aspx` (ignore the certificate name mismatch). Do **not** open `https://172.16.24.12/owa/` first — that path returns HTTP 500 before a session exists.
3. Certificate warning: click **Advanced**, then **Continue** (or equivalent Proceed). Retry the current URL once.
4. Username: click `#username` (labelled **Domain\user name:**). Fill `ndrtest\manager`. Do not use `#passwordText`.
5. Password: click `#password`. Fill the password above.
6. Click `div.signinbutton[role=button]` (visible text **sign in**, `onclick=clkLgn()`). If missing, press Enter in `#password`.
7. Wait until title contains `Mail - manager@ndrtest.local` and the folder list shows **Inbox**. After IP logon the hash is `/owa/#path=/mail`. If the logon form is still shown, stop.

# Recipient rules (OWA people picker)

Do this for every To / Cc / Bcc value. Extra recipients do not change the procedure.

**Type a full SMTP address.** Prompt `manager` means type `manager@ndrtest.local`. Never type a short alias.

**Use the field textbox** (`role=textbox`, `aria-label` To / Cc / Bcc). Do **not** click the buttons whose `aria-label` is `Cc button. Press Enter to open recipient selection window…` or `Bcc button. Press Enter to open recipient selection window…` — those open a separate people window.

**Show extra fields (not the people window):**
- Cc missing: click **Show Cc** (`aria-label=Show Cc`). Snapshot until a Cc textbox exists.
- Bcc missing: click **Show Bcc** (`aria-label=Show Bcc`; visible text is often just **Bcc**, on the right of the To row). Snapshot until a Bcc textbox exists.

For **each** address:

1. Snapshot. Click the textbox.
2. Type the **entire** SMTP address in one shot (no chunked typing, no `submit: true` except the Enter below).
3. Press **Enter once**.
4. Snapshot. A **Suggested contacts** box usually sits under the field and covers Cc / Subject / body. It contains a **button** whose name is `Use this address: <the smtp you just typed>` and a **Search Directory** button. Playwright MCP clicks on the next field **timeout** while that box is up (`subtree intercepts pointer events` from `_fp_7`).
5. **Click the button `Use this address: <that exact smtp>`.** Do not click Search Directory. Do not click `Use this address:` for a *previous* recipient — leftover markup stays in the DOM (`div[ispopup="1"]` can remain after the box is gone; ignore it).
6. Snapshot. Success: the field shows a recipient **pill** (chip with an X) **and** the Suggested contacts box for this smtp is gone. Then go to the next field. If **Use this address** is already gone after Enter but a pill is present (common on Cc/Bcc), continue — do not wait for the button.
7. If the pill shows *The address may not be valid*: click Remove on the pill, type the full SMTP again, Enter, then click **Use this address:** for that smtp. Do not wait for GAL. Do not `evaluate` the DOM. A short alias may **not** show that warning; still never type short names.

**Never press Escape** in compose. Escape opens **Discard message** (buttons **Discard** / **Don't discard**).

If **Discard message** is open: click **Don't discard** (*Return to the message for further editing*). Snapshot. Continue. Do not click the blue **Discard**.

If a later click times out with *intercepts pointer events*: the Suggested contacts box is still open. Click **Use this address:** for the smtp you last typed, snapshot, retry the blocked click once.

Never paste several addresses as one string.

# Dialogs and stale refs

After **any** popup, picker, or Don't-discard dialog: take a **new snapshot**. Previous `aria-ref` / `f3e…` targets are invalid. `find` Subject, Message body, Send, and Sent Items on the new snapshot. Do not reuse refs from before the dialog.

# Shared Mail layout

Verified on `https://172.16.24.12/owa/#path=/mail` (Office 365–style OWA).

Black top bar: **Mail**. App launcher is `#O365_MainLink_NavMenu` (**Open the app launcher to access your Office 365 apps**). Calendar / People / Tasks live in that launcher, not as a left vertical strip.

Left folder pane: search **Search Mail and People** (`Activate Search Textbox`), Favorites (**Inbox**, **Sent Items**, **Drafts**), then mailbox **manager** with **Inbox**, **Drafts**, **Sent Items**, **Deleted Items**, **Junk Email**, **Notes**.

Message list: **New** (plus; clicking it opens compose immediately — there is no **Email message** submenu), **More** (`...`), list header **Inbox** + **Filter**.

Empty reading pane: **Select an item to read**. After a conversation is selected: toolbar **Reply all**, **Delete**, **Archive**, **Junk**, **Sweep**, **Move to**, **Categories**, **Mark as unread**, **Mark as read**, **Flag for follow-up**, **Print**; in the pane **Mail Actions**: **Reply all**, **Reply**, **More Actions**. **Forward** is under **More** / **More Actions**.

Compose (right pane after **New**): To **textbox**; **Show Cc** / **Show Bcc** (`aria-label`; Bcc visible text is often **Bcc**); do not use the Cc/Bcc *recipient-selection* buttons. Subject placeholder **Add a subject**, body **Add a message or drag a file here**, blue **Send**, **Discard**, **Attach**. Two Send buttons exist (compose toolbar and bottom); either compose Send is fine.

Locate controls by visible name or `aria-label` first. Wait for the pane to finish rendering before the next click.

# Actions

Skip any field that the prompt omitted. Stop on the first missing required control.

## send email

Required: `recipient`, `subject`, `body`. Optional: `cc`, `bcc`, `attachment`.

1. Click **Mail** if it is not already selected (top bar or app launcher).
2. Click **New**. Compose opens in the right pane. Do not wait for an **Email message** menu item.
3. Lock every `recipient` with **Recipient rules** (full SMTP, Enter, click **Use this address:** for that smtp).
4. If `cc` is set: after the Suggested contacts box for To is gone, use the **Cc textbox** (click **Show Cc** first if the textbox is missing). Lock each cc address the same way.
5. If `bcc` is set: click **Show Bcc**, snapshot until a Bcc textbox exists, then lock each address the same way.
6. Snapshot. Click **Add a subject**. Type `subject` in one shot.
7. Click **Message body** (`Add a message or drag a file here`). Type `body` in one shot.
8. If `attachment` is set: click **Attach**. Set the file path from the prompt. Wait until a file chip appears.
9. Snapshot. Click the blue **Send** (compose Send, not a folder).
10. Wait until the compose pane is gone (list **New** is usable again). Then snapshot. Click **Sent Items** (Favorites row, not a buried duplicate if both exist). Confirm a row whose subject **starts with** `subject` (long or unicode subjects truncate in the list). If it is missing, wait once more and refresh the folder; if still missing, the send failed; stop.

## view email

Required: `target` (default: `first email`). Optional: `folder` (default: Inbox).

1. Click **Mail**, then the folder (`Inbox` unless `folder` is set).
2. If `target` is `first email`, click the first message row under the list (below Filter if shown).
3. Otherwise click the row whose subject or sender contains `target`. Scroll the list until it is visible. If it is not found, stop.
4. Wait until the reading pane shows the body.
5. If the message looks unread **and** toolbar **Mark as read** is visible, click it. If the control is missing, the item is already read or this row is not a message; continue. Same for **Flag for follow-up** / **Mark as unread**: missing control means skip, not task failure.
6. Read the body. Do not reply unless the prompt action is reply / reply all / forward.

## reply

Required: `body`. Optional: `target`, `cc`.

1. Complete **view email** for `target` (default first email).
2. Prefer **Reply** under reading-pane **Mail Actions**. On this OWA build the top toolbar usually shows **Reply all** and **not** **Reply**. If **Reply** is missing, click toolbar **Reply all** (same compose for a two-party thread). Do not fail the task because `getByRole('button', { name: 'Reply' })` is false.
3. If `cc` is set, lock each address with **Recipient rules**.
4. Type `body` at the **top** of the compose area. Keep the quoted original.
5. Click **Send**. Confirm the subject appears in **Sent Items**.

## reply all

Same as **reply**, but click **Reply all**.

## forward

Required: `recipient`, `body` optional. Optional: `target`, `cc`.

1. Complete **view email** for `target`.
2. Open **More Actions** in the reading pane (or toolbar **More**) and click **Forward**.
3. Fill **To** / **Cc** using Recipient rules.
4. Type `body` above the quoted message if provided.
5. Click **Send**. Confirm **Sent Items**.

## search

Required: `query`.

1. Click **Mail**.
2. Click **Search Mail and People** at the top of the folder pane (`Activate Search Textbox`). Do **not** wait for a placeholder named exactly `Search Mail` — that locator times out.
3. Clear any existing text. Type `query`. Press Enter.
4. Wait for the result list. Click the first row, or the row matching `target` if set.
5. Confirm the reading pane shows a body or an empty-result message. Do not invent hits.

## delete

Optional: `target`, `folder`.

1. Complete **view email**.
2. Click toolbar **Delete** (`Delete (Del)`).
3. If a confirmation dialog appears, confirm.
4. Verify the row is gone from the current folder or present in **Deleted Items**. If search already finds no row, delete succeeded (idempotent).

## move

Required: `folder` (destination). Optional: `target`.

1. Complete **view email**.
2. Click toolbar **Move to**.
3. Click the destination folder name.
4. Open that folder and confirm the message is listed.

## flag

Optional: `target`.

1. Complete **view email**.
2. If toolbar **Flag for follow-up** is missing, the item cannot be flagged from this view (often the first list `option` is not a mail, or the pane is still compose). Select a real message row first and retry once; if still missing, skip flag (do not fail the mailbox session).
3. Click **Flag for follow-up**. Confirm the flag icon is set.

## mark unread

Optional: `target`.

1. Complete **view email**.
2. Click toolbar **Mark as unread**.
3. Confirm the row looks unread.

## save draft

Required: `body`. Optional: `recipient`, `subject`.

1. Click **New**. Compose opens in the right pane (same as send email).
2. Fill any provided To / Subject / body. Do **not** click Send.
3. Click **Discard** only if the prompt says discard. Otherwise open another folder so OWA autosaves, then open **Drafts**.
4. Confirm a draft row exists.

## attach and send

Same as **send email** with `attachment` required. Path comes from the prompt. Do not invent a file path.

## open calendar / people / tasks

Click `#O365_MainLink_NavMenu` (app launcher), then **Calendar**, **People**, or **Tasks**. Calendar lands on `#path=/calendar/view/Month`. Wait for that module’s main pane. Do not create events or contacts unless the prompt gives fields; if it does, click **New**, fill only those fields, then save if a Save button is shown.

# Verify then close

Do not close the browser until the verification step for the action succeeded. Then close the browser (playwright-browser session end).

# Anti-patterns

- Do not use Gmail, Outlook desktop, or a local mail client.
- Do not guess another role’s password.
- Do not skip Enter after each recipient.
- Do not click Send on a reply task, or Reply on a view task.
- Do not continue if Sign in did not reach Mail.
- Do not open `https://172.16.24.12/owa/` as the first IP URL (HTTP 500). Use `/owa/auth/logon.aspx`.
- Do not fail because toolbar **Reply** is missing; use **Reply all**.
- Do not press Escape in compose.
- Do not type short aliases into To/Cc/Bcc.
- Do not wait on *may not be valid*; remove the pill and retype the full SMTP.
- Do not click the next field while a **Suggested contacts** / **Use this address:** box is covering compose. Leftover `div[ispopup="1"]` in the DOM after the box is gone is not a blocker.
- Do not click the Cc/Bcc button that opens a recipient-selection window.

# Idempotency

- Sign in: if Inbox is already visible, skip the logon form.
- View / search the same subject twice is safe.
- Send / reply / forward always create a new message; use a unique `subject` when the caller needs to find the mail later.
- Mark as read / delete: skip if the control or the row is already gone.
