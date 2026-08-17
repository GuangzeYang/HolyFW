---
name: playwright-browser
description: Use when a task must control a browser through Playwright MCP, including Bing search, page visits, scrolling, clicking, and form input. Always load this skill before exchange-use or odoo-use. Do not write browser automation code.
---

# Tools

Call existing Playwright MCP tools only. Do not start a second browser with a script.

# Search

1. Check the current URL.
2. If it is not Bing, navigate to `https://www.bing.com` and wait for load.
3. If Bing is unreachable, navigate to `https://www.baidu.com`.
4. Do not search on Google or on an unrelated page.

# Tabs

1. Before opening a tab, close finished tabs.
2. To use a new tab, list tabs, then activate the target tab id.
3. After a tab's task is done, close that tab.
4. After the whole task, close the browser.

# Human-like pacing

- Wait for each page to finish rendering before the next action.
- Type text in small chunks. Pause between click, type, and scroll.
- After a new page loads, scroll once before extracting content.
- Move the mouse near a control, then click with a small offset. Do not click the exact center immediately.
- Clear a search box that already has text before typing.

# Failures

- Browser closed: start a new browser and retry the current step once.
- Tool timeout: wait, retry once, then stop if it fails again.
- 404/403 after a click: refresh once, then go back or close the tab.
- Close ads or pop-ups before continuing.

# Anti-patterns

- Do not load `/demo-skill`.
- Do not keep unused tabs open until the end of the day.
- Do not chain clicks with no wait.
