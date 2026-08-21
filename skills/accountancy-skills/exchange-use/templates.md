# exchange-use prompt templates

Pass the quoted string to `opencode run`. Load this skill on the accountancy host. Values in `{...}` are JSON-like (no quotation marks around keys or values). Omit unused keys. If a prompt uses a short mailbox (`manager`), the agent must type `manager@ndrtest.local` into OWA — never the short name.

## Grammar

```text
Use the exchange-use skill, open the Exchange mailbox, <action>, {<field>: <value>, ...}
```

| `<action>` | Fields |
|---|---|
| `send email` | `recipient` (required), `subject`, `body`, `cc`, `bcc`, `attachment` |
| `view email` | `target`, `folder` |
| `reply` | `body` (required), `target`, `cc` |
| `reply all` | `body` (required), `target`, `cc` |
| `forward` | `recipient` (required), `target`, `body`, `cc` |
| `search` | `query` (required), `target` |
| `delete` | `target`, `folder` |
| `move` | `folder` (required destination), `target` |
| `flag` | `target` |
| `mark unread` | `target` |
| `save draft` | `body`, `recipient`, `subject` |
| `open calendar` | none |
| `open people` | none |
| `open tasks` | none |

`target` defaults to `first email` when omitted on view/reply/forward/delete/flag.

## Examples

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager, subject: Weekly staffing update, body: Please review headcount this week.}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager@ndrtest.local, cc: programmer, subject: Onboarding pack, body: Offer letter is attached., attachment: C:\Users\accountancy\Desktop\offer.pdf}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, reply, {target: Staffing update, body: Received. I will collect the figures this afternoon.}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, reply all, {body: Thanks, I will follow up with the accountancy folder.}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, forward, {target: first email, recipient: hr, body: FYI for payroll.}"
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
