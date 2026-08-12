<!--
Runtime prompt constraints for role-task generation.
Placeholders filled by build_role_task_prompt via str.format:
  {role_display}   - comma-separated role names for this request
  {target_tasks}   - ceil((min_tasks_per_role + max_tasks_per_role) / 2)
  {output_format}  - e.g. "hr": [tasks], "programmer": [tasks]
  {non_five_min}   - minimum count of tasks whose minute is not divisible by 5
Literal curly braces in examples must be doubled ({{ / }}).
-->
Hard requirements (all must be satisfied):
1. The output must include every role: {role_display}.
2. Generate exactly {target_tasks} tasks for each role. This target is calculated as ceil((min+max)/2) from min_tasks_per_role and max_tasks_per_role.
3. The top-level JSON object must use this format: {{{output_format}}}.
4. Every task item must use this format: {{"time":"09:15","is_load":false,"task":"..."}}.
5. Each task's time value is its start time, and only that start time is validated. The valid periods are the two closed intervals 09:00–12:00 (inclusive) and 13:30–18:00 (inclusive). Times strictly between 12:00 and 13:30, such as 12:01–13:29, are invalid.
6. Within each role, task times must be strictly increasing in JSON array order. Every later task's time must be strictly greater than the preceding task's time (> only, never equal or earlier).
7. At least 80% of task minute values must not be divisible by 5. Randomly vary the interval between adjacent tasks from 5 to 15 minutes.
8. Do not schedule most tasks at xx:00, xx:05, xx:10, xx:15, xx:20, xx:25, xx:30, xx:35, xx:40, xx:45, xx:50, or xx:55. With {target_tasks} total tasks, at least {non_five_min} task(s) must have a minute value that is not divisible by 5.
9. Every task must fit the corresponding role's duties and should expose observable network behavior where appropriate, such as Exchange, OA, SMB, FTP, or browser activity.
10. All task descriptions and all natural-language parameter values must be written in English.
11. Follow every task-content template and constraint in the domain context when writing task descriptions.
12. If Related dependency facts are included, use them only to infer implicit relationships and ordering between role tasks. For example, if HR sends the programmer an email at 09:00, the programmer may begin processing it only after 09:00, never before. Do not imitate the facts' quantity, format, wording, or time density.
13. Do not ask the user questions or request confirmation.
14. Do not output explanations, Markdown, code blocks, execution instructions, or retry suggestions.
15. Return only the JSON object itself.
