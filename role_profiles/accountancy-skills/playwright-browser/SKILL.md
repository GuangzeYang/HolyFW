---
name: playwright-browser
description: Use when a task must drive a browser through Playwright MCP for navigation, search, clicking, typing, forms, tabs, downloads, and in-page follow-up. Always load before exchange-use or odoo-use. Do not write browser automation code. Do not use for SMB files.
---

# Tools

Call existing Playwright MCP tools only. Do not launch a second browser with a script. Do not load `/demo-skill`.

Take an accessibility snapshot (or equivalent element list) **before** each click/type when the target is not already focused. Prefer the control’s visible name, role, and placeholder over CSS. If two matches exist, pick the one in the main content, not the header chrome, unless the step names the header.

# Session

1. If no browser is open, open one.
2. List tabs. Reuse a tab whose URL already matches the next `goto` / `search` target. Otherwise open a tab only when the step is `new tab`.
3. Close a tab when that tab’s steps are done and another tab still has work. After the **whole** prompt is verified, close the browser.
4. Do not close the browser after the first click.

# OWA overlay (when the URL contains `/owa/`)

These rules override Human-like pacing, `type`, and `press` on Outlook Web App.

- Do **not** type in chunks. Fill each field in one shot.
- Do **not** press **Escape**. It opens **Discard message** instead of closing Suggested contacts.
- Do **not** pass `submit: true` on recipient `type` except a single **Enter** after the full SMTP address.
- After Enter on To/Cc/Bcc, click the **button** `Use this address: <the smtp just typed>`. Do not click Search Directory. Do not click a leftover `Use this address:` for an earlier recipient. Then snapshot. Ignore leftover `div[ispopup="1"]` if the Suggested contacts box is gone.
- If a click fails with *intercepts pointer events*, click that **Use this address:** button, snapshot, retry once.
- After any OWA dialog (**Discard message** → **Don't discard**), snapshot again. Never reuse element refs from before the dialog.
- Close OWA popups with the labelled button (**Don't discard**, **OK**, **Send**). Not Escape.

# Human-like pacing

- Wait until the page is loaded (network idle or main landmark visible) before acting.
- Type in short chunks. Pause between click, type, and scroll.
- After a navigation, scroll once before extracting text.
- Move near a control, then click with a small offset. Do not click the exact center immediately.
- Clear a field that already has text before typing a replacement.
- Close ads, cookie banners, and pop-ups before continuing (Accept / Close / X / Skip). Certificate interstitial: Advanced → Continue.

# Failures

- Browser closed: open a new browser and retry the **current** step once.
- Tool timeout: wait, retry once, then stop.
- 404/403 after a click: reload once, then Back, then stop if still failed.
- Control not found: snapshot again, scroll, retry once, then stop. Do not invent a different site.

# Search recipe (only when an op is `search`)

Do not use this recipe for Exchange or Odoo (those skills have their own URLs).

1. If the current URL is not Bing, `goto` `https://www.bing.com` and wait for `#sb_form_q`.
2. If Bing fails to load, `goto` `https://www.baidu.com` and use `#kw`.
3. Do not use Google as the search engine.
4. Fill the search box with `{query}`. Press Enter (or click the search submit).
5. Wait for the result list (`#b_results` on Bing). If there are **no** result headings (`#b_results h2 a` count is 0) after one wait: snapshot, then `goto` `https://www.baidu.com` and search with `#kw`. Do not `follow` `{nth}` when the hit list is empty.
6. Further ops (`follow`, `click`, `scroll`, `extract`) run **on the result page or the opened hit**, not on a new blank search.

# Primitive ops

Execute the numbered list in the prompt, in order. Each item is one op. Unknown ops: stop.

Shared optional fields: `name` (accessible name / visible text), `role`, `nth` (1-based), `url`, `query`, `text`, `key`, `path`, `direction`, `amount`, `timeout_seconds`.

## goto

Navigate to `{url}`. Wait for load. If `{url}` is omitted and the prompt named a site in natural language, use that URL only when it is explicit; otherwise stop.

## back / forward / reload

Browser history or reload. Wait for load.

## new tab

Open a tab. Optional `{url}`. Then activate it.

## search

Run the Search recipe with `{query}`. Optional `{nth}`: after results load, follow the Nth main result (see `follow`).

## click

Snapshot. Click the control matching `{name}` / `{role}` / `{nth}`. If the prompt describes the control in words (`the blue Submit button`), match that text.

## hover

Move to the control and wait for hover UI (menus, tooltips).

## type

Click the field matching `{name}` if not focused. Type `{text}` in chunks. Do not clear unless the prompt says replace.

## fill

Clear the field, then type `{text}` (or `{value}`). Use for replacements.

## press

Press `{key}` (`Enter`, `Tab`, `Escape`, `ArrowDown`, …).

## select

On a `<select>` or listbox, choose `{text}` / `{value}`.

## check / uncheck

Toggle a checkbox or switch matching `{name}` to the requested state.

## upload

On a file input or file-chooser, set `{path}`. Do not invent paths.

## download

Click the control that starts the download (`{name}`). Wait for the download. Record the saved path in the result.

## scroll

`{direction}`: `down` | `up` | `top` | `bottom`. Optional `{amount}` in pixels. If `{name}` is set, scroll that element into view instead.

## wait

Wait `{timeout_seconds}` (default 2) or until `{name}` is visible.

## extract

Return visible text of the main content, or of `{name}` if set. Do not dump the whole DOM.

## follow

On a list of links (search hits, articles, pagination):

1. If `{nth}` is set, click that result heading/link (skip ads / “Sponsored”).
2. If `{name}` or `{query}` is set, click the first result whose title contains it.
3. Wait for the new page. Then continue with later ops **on that page**.

## back

Same as primitive `back` — return from a followed page when a later step needs the previous list.

# In-page follow-up (default)

After `goto`, `search`, or `follow`, keep going with the remaining ops on the **current** page: scroll, click in-article buttons, open a second link, fill a form, paginate (`Next` / page number), switch tabs on the site (`Images` / `News` only if the prompt says so). The prompt’s later lines are the source of truth for which widgets to touch. Site identity is never hard-coded in this skill except Bing/Baidu for `search`.

# Verify

After the last op, satisfy `Verify:` from the prompt (URL contains …, heading visible, extracted text non-empty, file downloaded). If Verify is missing, confirm the last op’s obvious success (page loaded, click produced a navigation or enabled state). Only then close the browser.

# Anti-patterns

- Do not load `/demo-skill`.
- Do not search on Google.
- Do not chain clicks with no wait and no snapshot.
- Do not keep unused tabs until the end of the day.
- Do not start a second Playwright via a Node script.
- Do not treat Exchange OWA or Odoo as generic search; those have their own skills.
