---
name: playwright-browser
description: Use when a task must browse the public web through Playwright MCP (navigation, search, clicking, typing, forms, tabs, downloads, in-page follow-up). Do not use for Exchange OWA or Odoo. Do not write browser automation code. Do not use for SMB files.
---

# Tools

Call existing Playwright MCP tools only. Do not launch a second browser with a script. Do not load `/demo-skill`.

Prefer the control’s visible name, role, and placeholder over raw CSS. If two matches exist, pick the one in the main content, not the header chrome, unless the step names the header.

# Scope (public web only)

This skill drives **public web** traffic. Do **not** open Exchange or Odoo with it.

Stop immediately if the current or next URL contains any of:

- `/owa/`
- `172.16.24.12`
- `i1-mail1-c02`
- `172.16.24.14:8069`

Those pages belong to `exchange-use` or `odoo-use`. Do not continue with this skill’s ops. Do not open a second browser.

# Screenshot analysis (required on public web)

After every `goto`, `search`, and `follow` lands, and again before `Verify:`, call `take_screenshot` and read the image. Use it to confirm the title, result list, article, or form actually opened. On unknown sites, pick the next control from the screenshot plus visible name — do not invent placeholder control names.

An accessibility `snapshot` is allowed to locate a named control. Do not take two consecutive full a11y snapshots.

# Session

1. If no browser is open, open one.
2. List tabs. Reuse a tab whose URL already matches the next `goto` / `search` target. Otherwise open a tab only when the step is `new tab`.
3. Close a tab when that tab’s steps are done and another tab still has work. After the **whole** prompt is verified, close the browser.
4. Do not close the browser after the first click.

# Human-like pacing

- Wait until the page is loaded (network idle or main landmark visible) before acting.
- Type in short chunks. Pause between click, type, and scroll.
- After a navigation, screenshot, then scroll once before extracting text.
- Move near a control, then click with a small offset. Do not click the exact center immediately.
- Clear a field that already has text before typing a replacement.
- Close ads, cookie banners, and pop-ups before continuing (Accept / Close / X / Skip). Certificate interstitial: screenshot the current tab, click **Advanced（高级）**, then **Continue** / **Proceed to … (unsafe)（继续前往 …（不安全））**. Do not click **Back to safety** / **Return to safe connection（返回安全连接）**. Do not `goto` the same URL again.

# Failures

- Browser closed: open a new browser and retry the **current** step once.
- Tool timeout: wait, retry once, then stop.
- 404/403 after a click: reload once, then Back, then stop if still failed.
- *strict mode violation* (2+ matches): append `:visible` or `>> nth=0`, or scope to the main content. Do not fall back to `run_code_unsafe`.
- *intercepts pointer events*: a dropdown/popup covers the control — close or confirm it, then retry once.
- Control not found: `find` the name first; screenshot; retry once, then stop. Do not invent a different site.
- `ERR_CERT_*` / `chrome-error://chromewebdata/` / **Your connection is not private（您的连接不是私密连接）** / **Privacy error（隐私设置错误）**: the interstitial is already showing. Screenshot the current tab, click **Advanced（高级）**, screenshot, click **Proceed to … (unsafe)（继续前往 …（不安全））** (or **Continue** / **继续前往** and the current host). Do not re-`goto` the same URL. Do not click **Back to safety** / **Return to safe connection（返回安全连接）**. Do not open a second browser.

# Search recipe (only when an op is `search`)

Do not use this recipe for Exchange or Odoo.

1. If the current URL is not Bing, `goto` `https://www.bing.com` and wait for `#sb_form_q`.
2. If Bing fails to load, `goto` `https://www.baidu.com` and use `#kw`.
3. Do not use Google as the search engine.
4. Fill the search box with `{query}`. Press Enter (or click the search submit).
5. Wait for the result list (`#b_results` on Bing). Screenshot. If there are **no** result headings (`#b_results h2 a` count is 0) after one wait: `goto` `https://www.baidu.com` and search with `#kw`. Do not `follow` `{nth}` when the hit list is empty.
6. Further ops (`follow`, `click`, `scroll`, `extract`) run **on the result page or the opened hit**, not on a new blank search.

# Primitive ops

Execute the numbered list in the prompt, in order. Each item is one op. Unknown ops: stop.

**Ops are not tool names.** Map each op to an existing MCP tool: `click` → `playwright_browser_click`, `type` / `fill` → `playwright_browser_type`, `press` → `playwright_browser_press_key`, `goto` / `back` / `forward` / `reload` / `new tab` → `playwright_browser_navigate` (`_back`, `tabs`), `select` → `playwright_browser_select_option`, `hover` → `playwright_browser_hover`, `wait` → `playwright_browser_wait_for`, `upload` → `playwright_browser_file_upload`, and **`extract` → `playwright_browser_evaluate` (scoped) or `playwright_browser_find`**. There is **no** `playwright_browser_extract`, `playwright_browser_follow`, or `playwright_browser_search` tool — calling them fails the run.

Shared optional fields: `name` (accessible name / visible text), `role`, `nth` (1-based), `url`, `query`, `text`, `key`, `path`, `direction`, `amount`, `timeout_seconds`.

## goto

Navigate to `{url}`. Wait for load. Screenshot. If `{url}` is omitted and the prompt named a site in natural language, use that URL only when it is explicit; otherwise stop. Refuse OWA / Odoo URLs (see **Scope**).

## back / forward / reload

Browser history or reload. Wait for load.

## new tab

Open a tab. Optional `{url}`. Then activate it. Refuse OWA / Odoo URLs.

## search

Run the Search recipe with `{query}`. Optional `{nth}`: after results load, follow the Nth main result (see `follow`).

## click

Snapshot if needed to locate `{name}` / `{role}` / `{nth}`. Click that control. If the prompt describes the control in words (`the blue Submit button`), match that text.

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
3. Wait for the new page. Screenshot. Then continue with later ops **on that page**.

## back

Same as primitive `back` — return from a followed page when a later step needs the previous list.

# In-page follow-up (default)

After `goto`, `search`, or `follow`, keep going with the remaining ops on the **current** page: scroll, click in-article buttons, open a second link, fill a form, paginate (`Next` / page number), switch tabs on the site (`Images` / `News` only if the prompt says so). The prompt’s later lines are the source of truth for which widgets to touch. Site identity is never hard-coded in this skill except Bing/Baidu for `search`.

# Verify

After the last op, take a screenshot, then satisfy `Verify:` from the prompt (URL contains …, heading visible, extracted text non-empty, file downloaded). If Verify is missing, confirm the last op’s obvious success (page loaded, click produced a navigation or enabled state). Only then close the browser.

# Anti-patterns

- Do not load `/demo-skill`.
- Do not search on Google.
- Do not open `/owa/`, `172.16.24.12`, `i1-mail1-c02`, or Odoo `172.16.24.14:8069`.
- Do not skip `take_screenshot` after `goto` / `search` / `follow` or before Verify.
- Do not chain clicks with no wait and no screenshot.
- Do not keep unused tabs until the end of the day.
- Do not start a second Playwright via a Node script.
- Do not treat Exchange OWA or Odoo as generic search; those have their own skills.
