---
name: exchange-use
description: Use when sending, viewing, replying, forwarding, searching, or filing Exchange mail in Outlook on the web (OWA 2016). Depends on playwright-browser. Do not use for Odoo, SMB, or generic web search.
---

Use Playwright MCP tools. This skill owns `/owa/` pages. Do **not** apply playwright-browser public-web rules (stop-on-OWA, screenshot after every navigation) except **Visual fallback** below.

**Snapshot budget (important).** The Inbox list is virtualized (25 rows at a time) and every full a11y `snapshot` re-dumps the whole mailbox chrome — often 50 KB+. Re-snapshotting after each step is what makes this skill time out. **Do not dump the mail view. Use the selectors below.**

# Visual fallback

1. Click/type the verified selector. If it fails, retry **the same selector once**.
2. If it still fails, `take_screenshot` **once**, read the image, and continue the current action from a visible control (button label, dialog, certificate page).
3. Do not take two screenshots in a row. Do not replace this step with a full-page a11y snapshot of Inbox.
4. If the screenshot still does not unlock the step, stop and report. Do not switch to a playwright-browser task, and do not open a second browser.

If already signed in (folder list / Inbox visible), skip Sign in.

# Endpoints (frozen)

- Preferred URL: `https://i1-mail1-c02.ndrtest.local/owa/`
- Fallback logon URL: `https://172.16.24.12/owa/auth/logon.aspx`
- Username: `ndrtest\programmer`
- Password: `Njupt@241`
- Do not invent another host, mailbox, or password.

# Verified selectors (use these, do not guess)

| Target | Selector |
|---|---|
| Username | `#username` |
| Password | `#password` |
| Sign-in button | `div.signinbutton` |
| Mail app | `a:has-text("Mail")` |
| Folder row (Inbox / Sent Items / Drafts …) | `[role="treeitem"]:has-text("Inbox") >> nth=0` (always append `>> nth=0`; the Favorites copy and the mailbox copy both match) |
| Search button | `button[aria-label="Activate Search Textbox"]` |
| Search input | `input[role="combobox"][aria-label^="Search mail and people"]` |
| Exit search | `button:has-text("Exit search")` |
| Message rows (**message list only**) | `[role="listbox"][aria-label="conversation"] [role="option"]` |
| Nth message | `[role="listbox"][aria-label="conversation"] [role="option"] >> nth=<N-1>` (`target: 1` = first row) |
| Reading pane | `[aria-label="Reading Pane"]` |
| Reading-pane menu button | `button[aria-label="More Actions"]` (use `>> nth=0` if a conversation has several) |
| Pane menu items | `[role="menu"][aria-label="Context menu"] button:has-text("Forward")` (or `"Reply"`, `"Reply all"`, `"Flag"`, `"Mark as unread"`) |
| New message | `button[title="Write a new message (N)"]` (it has **title**, no `aria-label`) |
| Compose To / Cc | `input[aria-label="To"]` / `input[aria-label="Cc"]` |
| Show Bcc / Show Cc | `button[aria-label="Show Bcc"]` / `button[aria-label="Show Cc"]` |
| Subject | `input[aria-label^="Subject"]` |
| Body | `[role="textbox"][aria-label="Message body"]` |
| Send (compose) | `button[aria-label="Send"] >> nth=0` (3 **Send** buttons exist; `nth=0` is the compose toolbar) |
| Attach | `button[aria-label="Attach"]` |
| Discard (compose) | `button[aria-label="Discard"] >> nth=0` |
| Discard confirm | `button:has-text("This message will be deleted")` |
| Don't discard | `button:has-text("Don't discard")` |
| Recipient suggestion | `button:has-text("Use this address: <smtp>")` |
| Recipient success signal | `[role="status"]` — text `<smtp> added to the To line` |

**Scoping matters.** `[role="listbox"]` alone also matches hidden search-filter listboxes (`… search filter`), which makes clicks fail with *not visible* or *strict mode*. Always scope to `[role="listbox"][aria-label="conversation"]`.

A pane menu item has **two** matches (a wrapper `div[role=menuitem]` and the inner `button[role=menuitem]`). Target the **button** form above. If you still get a *strict mode violation*, append `>> nth=0`.

# Sign in (FQDN, then IP)

1. Navigate to `https://i1-mail1-c02.ndrtest.local/owa/`. If you get HTTP 4xx–5xx, a connection error, or no logon form after one wait, go to step 2.
2. Open `https://172.16.24.12/owa/auth/logon.aspx`. Do **not** open `https://172.16.24.12/owa/` first (HTTP 500 before a session exists).
3. Certificate interstitial (`ERR_CERT_*` / `chrome-error://chromewebdata/`): do **not** re-`goto`. Follow **Recovery**.
4. Username is usually pre-filled `ndrtest\programmer`; if empty, fill `#username`. Do not use `#passwordText`.
5. Fill `#password` with `Njupt@241`.
6. Click `div.signinbutton` (visible text **sign in**). If missing, press Enter in `#password`.
7. Wait until the title contains `Mail - programmer@ndrtest.local`. If the logon form is still shown, stop.

# Recipient rules (OWA people picker)

Fill every To / Cc / Bcc with the **full SMTP** address. `programmer` means `programmer@ndrtest.local`. Never a short alias.

For **each** address:

1. Click the field (`input[aria-label="To"]`, `"Cc"`, `"Bcc"`).
2. Type the entire SMTP address in one shot.
3. Press **Enter once**.
4. Success = `<smtp> added to the To line` appears in `[role="status"]` and a chip with the display name sits in the To row. **The text you typed then leaves the input — an empty input after Enter is success, not failure.**
5. If instead a visible `button:has-text("Use this address: <smtp>")` appears, click it.
6. If neither the status text nor a chip appears, click the field, type the SMTP again, press Enter, and click **Use this address** if offered.

- Cc missing → `button[aria-label="Show Cc"]`. Bcc missing → `button[aria-label="Show Bcc"]`. Do **not** click the Cc/Bcc buttons that open the recipient-selection window.
- Never press **Escape**: it opens **Discard message**. If that dialog appears, click **Don't discard**.
- **Do not loop.** The authoritative signal is `[role="status"]` containing `<smtp> added to the To line`; when it is present the recipient is locked even if the input looks empty and no clickable **Use this address** button is visible. Retry at most once.

# Select the target message (shared by view / reply / forward / delete / flag / move)

1. Folder: `[role="treeitem"]:has-text("Inbox") >> nth=0` (or the `folder` value). If search is active (`#path=/mail/search`, folder pane hidden), click `button:has-text("Exit search")` first.
2. Pick the row **inside the message list only** (`[role="listbox"][aria-label="conversation"] [role="option"]`):
   - `target: last` → click `... >> nth=0`, then press **`End`**. The reading pane jumps to the oldest message.
   - `target` is a number N → `... >> nth=<N-1>` (`target: 1` = first email).
   - `target` is `first email` (legacy) → same as omitted / `target: 1`.
   - `target` is other text → the row whose text contains it (use **search** if it is not in the first 25 rows).
   - `target` omitted → `... >> nth=0`.
3. Confirm the selection: `[aria-label="Reading Pane"]` shows the body and `button[aria-label="More Actions"]` is visible.

**Skip `[Draft]` rows.** A row whose text begins with `[Draft]` (any message in **Drafts**) **cannot be forwarded**: OWA hides/disables **Forward** and clicking the row opens the compose editor, not the reading pane. Match a non-draft message. If the only match for `target` is a draft, **stop and report the ambiguity** — do **not** move the message to another folder, edit it, or send a look-alike "new" email.

# Actions

Skip omitted fields. Stop on the first missing required control.

## forward

Required: `recipient`, `min_words`. Optional: `target` (default first email), `body`, `cc`.

1. Click **Mail** (`a:has-text("Mail")`), then select the message per **Select the target message**.
2. Click `button[aria-label="More Actions"]`.
3. Click `[role="menu"][aria-label="Context menu"] button:has-text("Forward")`. The compose title becomes `Fw: <subject>`.
   - **Do not** use the top toolbar **More** — its menu has no **Forward**.
4. Fill **To** (and `cc`) using **Recipient rules**.
5. Type the expanded body into `[role="textbox"][aria-label="Message body"]` (above the quoted message).
6. Click `button[aria-label="Send"] >> nth=0`.
7. Verify: open **Sent Items** (`[role="treeitem"]:has-text("Sent Items") >> nth=0`) and confirm the top row is addressed to `recipient` with the subject/body you wrote. OWA often hides the `Fw:` prefix in the list, so do not require it. If missing, wait once, reload the folder, then stop.

## reply / reply all

Required: `min_words`. Optional: `body`, `target`, `cc`.

1. Select the message per **Select the target message**.
2. Click `button[aria-label="More Actions"]`, then `...button:has-text("Reply")` or `"Reply all"`. Some threads only expose **Reply all** — use it. Do not fail because a control named exactly **Reply** is absent.
3. Fill `cc` if set; type the expanded body at the top of the compose body and keep the quoted original.
4. Click `button[aria-label="Send"] >> nth=0`, then verify in **Sent Items**.

## send email

Required: `recipient`, `subject`, `min_words`. Optional: `body`, `cc`, `bcc`, `attachment`.

1. Click **Mail**, then `button[title="Write a new message (N)"]` (the New button has **title**, not `aria-label`; plain `button:has-text("New")` can match several).
2. Fill **To** / **Cc** / **Bcc** (Recipient rules).
3. Fill `input[aria-label^="Subject"]`.
4. Type the expanded body into `[role="textbox"][aria-label="Message body"]`.
5. If `attachment`: see **Attachments** below — click **Attach** (`button[aria-label="Attach"]`), set an allowed path, wait for the file chip.
6. Click `button[aria-label="Send"] >> nth=0`.
7. Verify **Sent Items** for the top row addressed to `recipient` (the `subject` may render without any prefix).

# Attachments

`file_upload` only accepts paths under the workspace roots (the HolyFW folder and its `.playwright-mcp`). A UNC/share path (e.g. `\\172.16.24.11\...`) or any path outside those roots fails with *File access denied … outside allowed roots*.

1. Resolve the file:
   - Bare filename (e.g. `intern-kickoff.pptx`) → find it under the workspace root (`$env:USERPROFILE\Desktop\HolyFW`) or the Desktop.
   - UNC / share path → copy it next to the workspace: `Copy-Item -LiteralPath "<unc>" -Destination "$env:USERPROFILE\Desktop\HolyFW\.playwright-mcp\<name>" -Force`.
2. Click **Attach** (`button[aria-label="Attach"]`) → a file chooser opens.
3. `playwright_browser_file_upload` with the **allowed** path.
4. Wait until the file chip appears in compose (check `document.body.innerText` contains the filename), then Send.

## view email

Required: `target` (default first email). Optional: `folder`.

1. Select the target message per **Select the target message** (respect `folder`).
2. Read `[aria-label="Reading Pane"]`. Do not reply unless the action is reply/reply all/forward.

## search

Required: `query`. Optional: `target`.

1. Click **Mail**.
2. Click `button[aria-label="Activate Search Textbox"]`; the focused box is `input[role="combobox"][aria-label^="Search mail and people"]`.
3. Clear existing text, type `query`, press **Enter**.
4. Read the result rows `[role="listbox"][aria-label="conversation"] [role="option"]`. Click the first, or the one containing `target`. Skip `[Draft]` rows unless the task is about a draft.
5. Confirm the reading pane shows a body or an empty-result message. Do not invent hits.
6. To leave search and restore the folder pane (folder rows are hidden while search is active), click `button:has-text("Exit search")`.

## delete / move / flag / mark unread / save draft / attach and send / open calendar·people·tasks

- **delete**: select the message per **Select the target message**, click toolbar `button[aria-label="Delete (Del)"]`; confirm if asked; verify the row is gone.
- **move**: select, click `button[aria-label="Move To (V)"]`, click the destination folder, verify. Do not use move to "fix" a draft or any other message outside the task.
- **flag**: select, click `button[aria-label="More Actions"] >> nth=0`, then `[role="menu"][aria-label="Context menu"] button:has-text("Flag")`. If the item reads **Mark complete**, it is already flagged. For `target: last`, use the shared selection (press `End`). Do not hunt the inline hover flag icon.
- **mark unread**: select, click `button[aria-label="More Actions"] >> nth=0`, then `...button:has-text("Mark as unread")`.
- **save draft**: compose as **send email** but do **not** Send; open another folder so OWA autosaves, then open **Drafts** and confirm the row. Click **Discard** only if the prompt says discard.
- **attach and send**: **send email** with `attachment` required.
- **open calendar / people / tasks**: click `#O365_MainLink_NavMenu`, then the app. Do not create items unless the prompt supplies fields.

# Prose expansion

Commander sets `min_words` plus `subject` / `topic` or a one-sentence `body` outline. Before typing the body:

1. If `min_words` is present, write original English of at least that many whitespace-separated words about `subject` / `topic` / outline. No lorem ipsum; no invented credentials, hosts, or secrets.
2. Else if `body` is present, type `body` unchanged.
3. Type it in one shot with the normal `type` / `fill` (do **not** pass `slowly: true` — slow-typing a long body trips the tool timeout). Do not expand recipient, subject, paths, or queries.

# Recovery (symptom → fix)

- **Selector still fails after one retry** → **Visual fallback**: `take_screenshot` once, read it, continue. Do not dump a full a11y snapshot of Inbox.
- **A message-row click fails with *not visible* or *strict mode*** → you matched a hidden search-filter listbox. Scope to `[role="listbox"][aria-label="conversation"] [role="option"]`.
- **Snapshot output huge / "truncated"** → stop snapshotting; use the selector table.
- **strict mode violation (2+ matches)** → append `>> nth=0`, or use the exact button selector for that menu item.
- **`intercepts pointer events`** → a Suggested-contacts box is covering the field: click `button:has-text("Use this address: <smtp>")`, then retry the blocked click once.
- **`chrome-error://chromewebdata/` / `ERR_CERT_*` / "Your connection is not private"** → the interstitial is showing. Snapshot once, click **Advanced（高级）**, click **Proceed to 172.16.24.12 (unsafe)（继续前往…）**. Never re-`goto`; never click **Back to safety**; never open a second browser.
- **HTTP 500 on the FQDN** → retry on `https://172.16.24.12/owa/auth/logon.aspx`.
- **Reading pane stays "Select an item to read"** → click the row once more (scoped selector). If it still does not open, the row may be a draft (it opens the compose editor) — see the draft rule.
- **Forward missing / clicking the row opens the compose editor** → the target is a `[Draft]`. Stop and report; do not move or edit it.
- **A folder row (`treeitem`) is not visible / times out** → search is active. Click `button:has-text("Exit search")` first.
- **`File access denied … outside allowed roots` on attach** → copy the file under `$env:USERPROFILE\Desktop\HolyFW\.playwright-mcp\` and upload that path.
- **Discard message dialog** → click **Don't discard**.
- **To input empty with no chip** → check `[role="status"]` for "added to the To line"; if absent, re-type the full SMTP, press Enter, then click **Use this address:**.
- **Tool timeout** → wait, retry the same step once, then stop.

# Anti-patterns

- Do not take a screenshot until the verified selector has failed twice.
- Do not switch to a playwright-browser task or open a second browser on `/owa/`.
- Do not use Gmail, Outlook desktop, or a local mail client.
- Do not guess another role's password.
- Do not take full-page snapshots in the mail view; do not snapshot twice in a row.
- Do not match message rows with an unscoped `[role="listbox"]`; always add `[aria-label="conversation"]`.
- Do not forward, edit, or move a `[Draft]`; if the only `target` match is a draft, stop.
- Do not move messages between folders, or flag/unflag other messages, as a "workaround".
- Do not upload a file from outside the workspace roots; copy it into `.playwright-mcp` first.
- Do not type short aliases into To/Cc/Bcc.
- Do not press Escape in compose.
- Do not click the top toolbar **More** for Forward.
- Do not use `run_code_unsafe` / raw `evaluate` to click when a selector above matches.
- Do not open `https://172.16.24.12/owa/` as the first IP URL (HTTP 500).
- Do not re-`goto` after `ERR_CERT_*`; do not click **Back to safety**.
- Do not fail because toolbar **Reply** is missing; use **Reply all**.

# Idempotency

- Sign in: skip the form if Inbox is already visible.
- View / search the same subject twice is safe.
- Send / reply / forward always create a new message; use a unique `subject` to find it later.
- Mark as read / delete: skip if the control or the row is already gone.
