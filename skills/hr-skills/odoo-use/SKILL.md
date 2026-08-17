---
name: odoo-use
description: Open a browser with Playwright and sign in to the Odoo system. This skill depends on the 'playwright-browser' skill; read and use the 'playwright-browser' skill before execution.
---

# Dependency

Load the `playwright-browser` skill before use.

# Sign in to Odoo

Follow the "Sign in to Odoo" steps only when sign-in is required; otherwise, skip these steps.

1. Use the 'playwright-browser' skill to start the browser and navigate to http://172.16.24.14:8069/.
2. Enter `hr` in the input below the “Email” text.
3. Enter `Njupt@241` in the input below the “Password” text.
4. Click the “Log in” button.

# Employees module

Employee information is defined in a **JSON-like format**, for example: {name: DemoNew1, position: Sales, work email: 123987@demo.com}.

## Add an employee

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Employees” in the list to open the employees page.
3. **Start creating**: Click the “New” button in the upper-left corner of the employees page.
4. **Fill in the form**:

   - Name: Enter the new employee's name in the “Employee Name” input.
   - Position: Enter the new employee's position in the input next to the “Job Position” label.
     - Note: There is also a “Job Position” directly below “Employee Name”, but it is not where information should be entered. The correct input is next to the lower “Job Position” label.
     - After entering the position, if the drop-down list contains only an item with the “Create” keyword, click the first item, namely “Create xxx”.
   - Work email: Enter the new employee's email address in the input to the right of “Work Email”.
5. **Save**: Click the “Save” icon (cloud- or disk-shaped) at the top of the page. It is a `button` element whose label contains the “Save manually” keyword.

## Delete an employee

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Employees” in the list to open the employees page.
3. **Select the employee**: Find the name of the employee to delete in the employee table displayed on the page, hover over the employee's `div`, and click it to open the employee information page.
   - Note: If the employee is not displayed when searching by name, scroll the page until the employee is within the viewport, then locate the employee.
4. **Delete the employee**:
   - Open the employee settings: Click the gear icon next to the “New” button at the top. The gear icon is a `button` element. A drop-down list appears.
   - Open the deletion dialog: Click the “Delete” button in the newly displayed drop-down list. In the DOM, it is a `span` element. A dialog then appears.
   - Delete: Click the “Delete” button in the dialog.
5. **Verify the result**: If the name shown in the employee information after deletion is not the deleted employee's name, consider the deletion successful.

## Modify employee information

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Employees” in the list to open the employees page.
3. **Select the employee**: Find the name of the employee to modify in the employee table displayed on the page, hover over the employee's `div`, and click it to open the employee information page.
   - Note: If the employee is not displayed when searching by name, scroll the page until the employee is within the viewport, then locate the employee.
4. **Modify the information**: The information page contains a group of form-like `div` elements. Modify the corresponding fields based on the provided information. Refer to “Add an employee” for the locations of the information-editing controls.

# Recruitment module

Recruitment information is defined in a **JSON-like format**, for example: {job position: Human Resources Manager, department: Human Resources Department, email address: 123987@demo.com, work location: Nanjing}.

## Create a recruitment position

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Recruitment” in the list to open the recruitment page.
3. **Start creating**: Click the “New” button in the upper-left corner of the recruitment page. A dialog appears.
4. **Fill in the recruitment information**:
   - Job position: Enter the job position name in the input to the right of the “Job Position” text label.
   - Email: Enter the email address in the input to the right of the “Application Email” text label.
5. **Create**: Click the “Create” button at the bottom of the dialog to create the recruitment position.
6. **Verify the result**: Confirm that the newly created position name appears in the recruitment information table on the recruitment page.

## Modify recruitment information

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Recruitment” in the list to open the recruitment page.
3. **Open the recruitment details**: The recruitment page displays a recruitment information table. Each cell in the table has a hyperlink containing the “Recruitment” keyword. Find the corresponding cell based on the recruitment information, then click the hyperlink to open the recruitment details.
4. **Modify the information**:
   - Job position: Enter the job position in the text area below the “Job Position” text label.
     - Note: If the text area contains existing content, clear it before entering the new content.
   - Department: Enter the department in the input to the right of the “Department” label.
   - Email address: Enter the email address in the input to the right of the “Email Alias” text label.
     - Note: The email address input is divided into two inputs at the “@” symbol. Split the address accordingly when entering it.
5. **Save the changes**: Click the “Save manually” button next to the “New” button at the top. It is a cloud-shaped icon.
6. **Verify the result**: Confirm that the “Save manually” button next to the “New” button at the top, which is a cloud-shaped icon, is hidden and no longer visible on the page.

## View recruitment applications

1. **Open the module**: Click the nine-dot icon (Home Menu) to the left of “Discuss” in the upper-left corner of the page.
2. **Select the application**: Click “Recruitment” in the list to open the recruitment page.
3. **View applications**: The recruitment page displays a recruitment information table. Each cell in the table has a button containing the “New Applications” keyword. Find the corresponding cell based on the recruitment information, then click the button to open the application view.
