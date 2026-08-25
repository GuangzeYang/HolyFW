# exchange-use prompt templates

Pass the quoted string to `opencode run`. Load this skill on the accountancy host. Values in `{...}` are JSON-like (no quotation marks around keys or values). Omit unused keys. If a prompt uses a short mailbox (`manager`), the agent must type `manager@ndrtest.local` into OWA — never the short name.

## Grammar

```text
Use the exchange-use skill, open the Exchange mailbox, <action>, {<field>: <value>, ...}
```

| `<action>` | Fields |
|---|---|
| `send email` | `recipient` (required), `subject` (required), `min_words` (required, 300-800), `body` (optional short outline), `cc`, `bcc`, `attachment` |
| `view email` | `target`, `folder` |
| `reply` | `min_words` (required, 300-800), `body` (optional short outline), `target`, `cc` |
| `reply all` | `min_words` (required, 300-800), `body` (optional short outline), `target`, `cc` |
| `forward` | `recipient` (required), `min_words` (required, 300-800), `target`, `body` (optional short outline), `cc` |
| `search` | `query` (required), `target` |
| `delete` | `target`, `folder` |
| `move` | `folder` (required destination), `target` |
| `flag` | `target` |
| `mark unread` | `target` |
| `save draft` | `min_words` (required, 300-800), `body` (optional short outline), `recipient`, `subject` |
| `open calendar` | none |
| `open people` | none |
| `open tasks` | none |

`target` defaults to `first email` when omitted on view/reply/forward/delete/flag.

## Examples

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager, subject: Weekly staffing update, min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager@ndrtest.local, cc: programmer, subject: Onboarding pack, min_words: 400, attachment: C:\Users\accountancy\Desktop\offer.pdf}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, reply, {target: Staffing update, min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, reply all, {min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, forward, {target: first email, recipient: hr, min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, search, {query: onboarding}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, move, {target: first email, folder: Deleted Items}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, open calendar"
```
