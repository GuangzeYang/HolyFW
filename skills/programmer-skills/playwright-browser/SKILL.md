---
name: playwright-browser
description: Use this skill for any task that requires controlling a browser through Playwright MCP, including web searches, page visits, content extraction, form completion, link clicking, and all other browser automation operations. Bing must be used as the search engine, tab lifecycles must be managed by promptly closing tabs that are no longer in use, and human-like behavior must be simulated during all operations.
---

# Playwright MCP Browser Operation Rules

This skill controls the browser through the existing Playwright MCP tools. No code needs to be written; all operations are completed by calling the tools provided by MCP. The following rules must be followed strictly in every browser task.



## Dependency Loading

This skill depends on `/demo-skill`. When using this skill, you must also load and use it!!



# General Operating Principles

- **Tool selection**: Perform operations with the browser automation tools available in the environment, such as Playwright MCP or Selenium, giving priority to the most stable and direct tool.
- **Page preparation**: Before starting any operation, make sure that the browser is open and ready for use. If the browser is not open, start it first.
- **Navigation confirmation**: Check the current page URL before performing an operation to make sure that the correct page is open. If another page is required, use a navigation tool.

---

## 1. Search Engine Rules: Bing Is Mandatory

- After initially opening the browser and before starting a search task, **first check the current page URL** to determine whether it is the Bing search engine at `bing.com`. If the current page is not Bing, including Google, Baidu, or any other page, **do not search directly on the current page**. You must first navigate manually through the address bar to `https://www.bing.com`, wait for the page to finish loading, and then perform the search.
- When Bing is inaccessible, such as because of a timeout or a 404 response, use the same navigation procedure to go to `https://www.baidu.com`.

---

## 2. Tab Lifecycle Management

- **Evaluate and close**: Before opening a new tab, evaluate whether any old tabs with completed tasks can be closed. A tab is considered old when no subsequent operation will visit it again.
- Switching rule: When a task must be performed in a newly opened tab, the first step must call a tool that retrieves the tab list, such as `playwright_browser_tabs`, and locate the target tab ID. The second step must call a tool that activates or switches to the tab, such as `playwright_browser_activate_tab` or a similar tool for switching views.
- Loss-prevention rule: If the task on the current page is not complete but a temporary navigation is required, first create a new tab, keep the old tab open, and switch the display to the new tab.
- Cleanup rule: As soon as a tab's task is complete and its content has been obtained, the very next step must call a tool that closes the tab, such as `playwright_browser_close_tab`.
- Close the browser: After all tasks have finished, call a tool that closes the browser process, such as `playwright_browser_close` or an equivalent exit or close tool, to completely end the browser session and release system resources.

---

## 3. Human-Like Operating Habits

All browser operations must simulate real user behavior. Follow these requirements:

**Pacing and waiting**
- After every page transition or load, wait until the page is fully rendered before performing the next operation. Do not trigger multiple actions continuously in rapid succession.
- When entering text, type it one character at a time. Do not fill an entire block of text at once.
- Leave an appropriate pause between actions such as clicking, typing, and scrolling.

**Page browsing**
- After visiting a new page, first scroll down appropriately to browse the page content, and then perform the target operation.
- Do not extract data immediately after opening a page. Simulate a user "taking a look" at the page.

**Clicking behavior**

- Before clicking a link or button, move the mouse smoothly near the target element, then move it to the target position and click.
- Do not hit the exact center of an element directly. Simulate a natural offset in the mouse landing position.

**Search input**

- After entering keywords in the search box, pause briefly before pressing Enter or clicking the search button to simulate a user's habit of confirming the input.
- If the search box already contains old content, clear it before entering new keywords.

**Handling problems**

- If clicking a link opens a webpage whose content does not display normally, such as a page with a 404 or 403 status code, first try refreshing it once. If the result still does not change, close the tab or go back.
- When performing operations such as clicking, scrolling, or keyboard input, check whether advertisements or other pop-ups are blocking the webpage. If so, close them before continuing.

---

## 4. Exception Handling



1. **Browser closed**: If the browser is found to have been closed during execution, reset the current execution state, meaning the browser operation steps, and perform the close-browser operation once more. Then immediately start a new browser process and execute the task again.
2. **Tool call failure**: If a tool call fails because of a timeout, error, or similar issue, wait briefly and retry once. If it still fails, try an alternative tool or adjust the operation steps.
3. **Network exception**: If a network timeout or connection error occurs, first check whether the current page loaded normally, and refresh the page or navigate to it again if necessary.







## 5. Operation Sequence Summary

When performing any browser task, check the following in order:

1. **Confirm the search engine**: When a search is required, first confirm that the browser is on Bing. If not, navigate to Bing.
2. **Pause before operations**: Leave a natural interval before each operation. Do not perform actions continuously in rapid succession.
3. **Browse after page load**: After a new page has finished loading, scroll and browse it before performing the target operation.
4. **Close upon completion**: Close a tab immediately after its task is complete.
5. **Clean up at the end**: After all tasks are complete, close every tab that is no longer needed.
