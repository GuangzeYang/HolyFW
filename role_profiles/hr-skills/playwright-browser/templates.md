# playwright-browser prompt templates

Public web only. Do not open Exchange OWA or Odoo with this skill. Site and copy are supplied by the caller. This skill does not hard-code business URLs except the Bing/Baidu search recipe.

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

New tab plus form:

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. goto, {url: https://www.bing.com} 2. new tab, {url: https://www.example.com} 3. click, {name: More information} 4. back. Verify: example.com heading is visible. Close the browser after verification."
```
