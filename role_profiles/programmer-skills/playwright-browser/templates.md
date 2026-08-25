# playwright-browser prompt templates

Site and copy are supplied by the caller. This skill does not hard-code business URLs except the Bing/Baidu search recipe.

## Grammar

```text
Use the playwright-browser skill, open the browser, then execute:
1. <op> [, {key: value}]
2. <op> [, {key: value}]
...
Verify: <observable result>
Close the browser after verification.
```

`<op>` vocabulary: `goto` | `search` | `click` | `type` | `fill` | `scroll` | `wait` | `select` | `press` | `check` | `uncheck` | `upload` | `download` | `extract` | `follow` | `hover` | `back` | `forward` | `reload` | `new tab`.

## Examples

Visit and read:

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. goto, {url: https://www.example.com} 2. scroll, {direction: down} 3. extract. Verify: main heading is visible. Close the browser after verification."
```

Search then open a hit and keep browsing:

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: Windows Active Directory backup} 2. follow, {nth: 1} 3. scroll, {direction: down} 4. click, {name: Next} 5. extract. Verify: article text is non-empty. Close the browser after verification."
```

Search, pick by title, then use a page control (random content belongs in the prompt):

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: REPLACE_QUERY} 2. follow, {name: REPLACE_TITLE_FRAGMENT} 3. click, {name: REPLACE_BUTTON} 4. fill, {name: REPLACE_FIELD, text: REPLACE_VALUE} 5. press, {key: Enter}. Verify: REPLACE_VISIBLE_RESULT. Close the browser after verification."
```

New tab plus form:

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. goto, {url: https://www.bing.com} 2. new tab, {url: https://www.example.com} 3. click, {name: More information} 4. back. Verify: example.com heading is visible. Close the browser after verification."
```

Download / upload (paths come from the caller):

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. goto, {url: REPLACE_URL} 2. download, {name: Download} . Verify: a file finished downloading. Close the browser after verification."
```
