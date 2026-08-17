---
name: exchange-use
description: Use when sending, viewing, or replying to Exchange mail in Outlook on the web. Depends on playwright-browser. Do not use for Odoo, SMB, or generic web search.
---

Load and follow `playwright-browser` before any browser step.

# Sign in

1. Open the browser with Playwright MCP and go to https://i1-mail1-c02.ndrtest.local/owa/
2. If a certificate warning appears, click Advanced, then click Continue.
3. Enter `ndrtest\manager` in the username field.
4. Enter `Njupt@241` in the password field.
5. Click Sign in.
6. Wait until Inbox is visible. If already signed in, skip this section.

# Recipient rules

- Short name without `@`: append `@ndrtest.local`.
- Full address with `@`: type it unchanged.
- After each To or Cc address, press Enter to lock it.
- Do not skip Enter. Do not paste several addresses as one string.

# Send email

Do this for `send email` tasks, not for replies.

1. Click the down-arrow beside New, then click Email message.
2. Click To, type each recipient, press Enter after each one.
3. If Cc is required, click Cc, type each address, press Enter after each one.
4. Click Add a subject, then type the subject.
5. Click the message body, type the body, then click Send.
6. Open Sent Items and confirm the new message is listed. If it is missing, the send failed; stop.

# View email

1. Click Inbox.
2. Click the first message under Filter, or the message named in `{target}`.
3. Open More actions next to Reply all and click Mark as read.
4. Read the body on the right. Do not send a reply unless the task says reply.

# Reply to email

1. Complete View email first.
2. Click Reply all.
3. If Cc is required, click Cc and lock each address with Enter.
4. Type the reply at the top of the body. Keep the original quoted text.
5. Click Send. Confirm the reply appears in Sent Items.

# Anti-patterns

- Do not use Gmail or a local mail client.
- Do not guess another role's password.
- Do not close the browser until send/view/reply is verified.
