---
name: exchange-use
description: Open a browser with Playwright to use Exchange email. This skill depends on the 'playwright-browser' skill; read and use the 'playwright-browser' skill before execution.
---



## Access the mailbox

1. Use Playwright to open a browser and visit https://i1-mail1-c02.ndrtest.local/owa/
2. Enter the account ndrtest\manager, enter the password Njupt@241, and click "Sign in".
3. If you encounter a certificate authentication issue, click the "Advanced" button, and then click the "Continue" hyperlink.

## Send emails

[Recipient address processing rules]

After obtaining the recipient information from the user's prompt, strictly check and format it according to the following logic:

1. Handle an internal short name (without an "@" symbol): append "@ndrtest.local" to the end of the short name.
2. Handle a complete external email address (with an "@" symbol and a complete suffix): enter it exactly as provided.
3. Multiple recipients: apply the rules above to each recipient individually. After entering each correctly formatted address, press the "Enter" key once.
4. Multiple Cc recipients: apply the rules above to each Cc recipient individually. After entering each correctly formatted address, press the "Enter" key once.

------

All operations for sending emails must be performed according to the following steps, except when replying to an email:

1. Click the down-arrow button next to the "New" button to open the drop-down menu.
2. Click the "Email message" button and locate the "To" text box.
   - **Note: Strictly follow the [Recipient address processing rules] above to convert the user's input into a valid, complete email address before entering it.**
   - After entering the address, you must press the "Enter" key on the keyboard to confirm and lock the recipient address. If there are multiple recipients, repeat this entry and Enter-key action for each recipient.
3. [Handle Cc (perform as needed)] If the user's prompt requires someone to be "Cc"ed:
   - Locate the "Cc" text box.
   - **Likewise, strictly follow the [Recipient address processing rules] above to process and enter the Cc recipient's address.**
   - After entering the address, you must press the "Enter" key on the keyboard. If there are multiple Cc recipients, enter them one by one and press the Enter key after each one.
4. First click the "Add a subject" text box, and enter the email subject in the "Add a subject" text box (you must perform the click action to place the focus in the "subject" text box).
5. Enter the email content to be sent in the "message body" text box. Finally, click the "Send" button; the email is then sent successfully.
6. Determine whether the email was sent successfully.

## View emails

1. Open the inbox: After accessing the mailbox, click the "Inbox" button on the left.
2. Select an email: Under the "Filter" text label (a span element) in the middle of the page is the list of received emails, where each list item represents one email. Move the mouse smoothly to the first email and click it; the email's content is displayed on the right side of the page.
3. Mark it as read: Click the "More actions" button (down-arrow icon) next to the "Reply all" button, and then click the "Mark as read" button.
4. View the content: Read the mailbox content displayed on the right.



## Reply to emails

1. Open the reply interface: After viewing the email, click the "Reply all" button on the right side of the interface.

2. Confirm the recipients: Click the "To" span element with the mouse, and then click the "Cc" button.

3. [Handle Cc (perform as needed)]: If the user's prompt requires someone to be "Cc"ed:
   - Locate the "Cc" text box.
   - **Likewise, strictly follow the [Recipient address processing rules] in the Send emails section above to process and enter the Cc recipient's address.**
   - After entering the address, you must press the "Enter" key on the keyboard. If there are multiple Cc recipients, enter them one by one and press the Enter key after each one.

4. Enter the content: At the beginning of the "message body" text area, enter the content of the email reply, and finally click the "Send" button.
   - Replying to an email usually does not delete the content of the original email.
5. Verify the result: Determine whether the email was sent successfully.
