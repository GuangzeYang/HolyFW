# Handoff: Increase Benign Office Traffic Volume

Paste this file into a new Cursor session (remote environment). It is the full brief for the next piece of work. Do not treat this as a request to rewrite the whole framework.

**Language:** All new or edited code, prompts, skills, and JSON must stay in English. This handoff file is bilingual so a new session can start without prior chat history.

---

## 1. What to solve

HolyFW simulates a small AD company. Four office roles (`hr`, `accountancy`, `manager`, `programmer`) generate **benign** traffic. A separate `victim` path generates **malicious** (authorized adversary-emulation) traffic. The **end goal of the lab** is mixed-mode capture: benign + malicious traffic for NDR/EDR evaluation.

The current problem is **not** “too few scheduled events on paper”. It is that **executed benign traffic is too thin**: short sessions, tiny payloads, few protocols, long idle gaps. Malicious bursts then sit in a near-empty background and look unrealistic.

**Success looks like:** the same four office hosts, over a workday, produce denser, longer, multi-protocol office traffic (HTTP/S, MAPI/OWA, SMB, optionally FTP / Excel / PDF / Git) with larger file transfers — without turning the day into 200 identical “view first email” clicks, and without changing frozen lab IPs, ports, FQDNs, or credentials.

Victim / APT traffic is **out of scope** for this task. Do not mix victim techniques into the daily office quota.

---

## 2. How the system works (minimum)

```
generate_role_task.py
  → LLM writes task text (no timestamps)
  → commander zips context.schedule[i] onto task i
  → commander scanner dispatches due tasks to soldier hosts
  → soldier runs `opencode run` with the role’s skills
  → reports back; next task for that role waits until the previous waiting task is gone
```

- Daily file: `commander/role_task/tasks_MM-DD.json`
- Generation CLI: `commander/generate_role_task.py`
- Time model (thesis §3.4 NHPP, dual Gaussian, lunch 12:00–13:00, AR(1)): `commander/time_model.py`
- Work windows: `common.py` `WORK_WINDOWS = ((9*60, 12*60), (13*60, 18*60))`
- LLM must **not** invent `time`. Commander assigns `schedule[i]`.
- Count is exact `tasks_per_role` (currently **39** per office role). Validation fails if `len(tasks) != expected`.
- `avoid_five_minutes` is JSON-only in `commander/config.json`. Per-role overlays live in `commander/commander.ini`.

---

## 3. Root cause: tasks are atomic, not sessions

Observed generated tasks (`commander/role_task/tasks_08-17.json`) look like:

- `view email, {target: first email}`
- one SMB `dir` / tiny `.txt` create
- one Odoo add/update
- Playwright browse for **20 seconds**

Generation templates force one skill action per task:

- `commander/prompt_resources/skill_templates.json`
- `domain_resource.md` “Task Description Templates”
- `task_generation_constraints.md` (ReAct; each item is one `task` string)

Skills also tell the agent to **finish and close** (browser close after the task). Soldier exec timeout is already **900s** (`soldier/soldier.ini`), so longer sessions are allowed at runtime; generation simply never asks for them.

Density math: 39 tasks / ~480 work minutes ≈ **12 minutes average gap**, 4 hosts. Raising 39 → 80 without changing task shape mostly adds more tiny events, not bytes/flows.

### Unused lab channels

`domain_resource.md` lists resources that generation **does not** currently emit:

| Resource | On disk today | In generation templates |
|---|---|---|
| Exchange / OWA | yes (`exchange-use`) | yes |
| Odoo | yes (`odoo-use`) | yes |
| SMB | yes (`smb-access`) | yes |
| Playwright browse | yes | yes (short visit/search) |
| PDF skill | yes (`skills/*/pdf/`) | **no** |
| FTP | documented | **no skill + no template** |
| Excel MCP | documented | **no** |
| GitHub MCP | documented | **no** |

### Scheduler also keeps hosts idle

- Commander: **at most one `waiting` task per role** (`commander/scanner_service.py` + `repository.has_active_waiting_task`).
- Soldier `worker_threads = 3` exists but same-role concurrency is unused because of that waiting gate.
- Scanner interval: 60s. Dispatch waiting timeout: 20 minutes.

So even if you generate overlapping work, the dispatcher will not run two office tasks for the same role at once unless that gate is changed.

---

## 4. What not to do

- Do **not** “fix volume” by only increasing `tasks_per_role` to 100+ copies of `view first email`. Session count goes up; payload and protocol diversity stay fake.
- Do **not** change frozen lab endpoints or credentials (see §6).
- Do **not** put timestamps in LLM output; keep zip-with-`schedule[i]`.
- Do **not** fold `victim` into daily `tasks_per_role`.
- Do **not** invent new public internet targets that violate skill anti-patterns (Bing-first browse; no `/demo-skill`; no Google as primary search).
- Do not commit API keys. `commander/config.json` contains a DeepSeek key; never copy it into docs or git messages.

---

## 5. Recommended fix (priority order)

Implement in this order. Stop after a layer if traffic is already dense enough for mixed-mode capture.

### Layer 1 — Session / workflow tasks (highest ROI)

Change generation so **one scheduled task is a 5–15 minute multi-step job**, not one atomic click. Examples:

- Open mailbox → read several mails → reply with attachment → save attachment to SMB → update one Odoo record.
- Programmer: pull docs/code from SMB → edit a small file → write back to `IT-Dev` → email manager a progress note.

Touch:

- `commander/prompt_resources/skill_templates.json` — allow chained actions / `duration_minutes` / multi-skill workflows.
- `domain_resource.md` templates — same.
- `task_generation_constraints.md` — still one JSON item per schedule slot, but the `task` string may describe a **sequence**.
- Per-role `skills/*/SKILL.md` — stop “do one action and close”; allow staying in mailbox/Odoo/SMB for the whole task; close browser only at the end of the **session**.

Keep 39 slots if you want. Volume should jump because each `opencode run` lasts minutes and hits several protocols.

Soldier timeout 900s already supports this. If sessions approach 15+ minutes, revisit `dispatch.timeout_minutes` (20) so waiting expiry still covers the run.

### Layer 2 — Larger artifacts

NDR/EDR care about **bytes and file transfers**, not only event counts.

- Generate/copy tens of KB to a few MB: xlsx, pdf, zip, csv (pdf skill and Excel MCP already exist in the lab story).
- Exchange messages with attachments; park large files in `Company_Data\Exchange`.
- Browser: actually load pages / download resources; do not open-and-close.

### Layer 3 — Enable unused office channels

Add generation templates (and skills if missing) for FTP, Excel-on-share, PDF open/scroll/print, occasional git via GitHub MCP for programmer. Multi-protocol background is more valuable than more OWA clicks.

### Layer 4 — Ambient noise (optional, later)

Real PCs are not idle 12 minutes between `opencode run`s. A soldier-side light loop **outside** the LLM task slot (OWA refresh, periodic `Get-ChildItem`, DNS to internal FQDNs, keep one browser session) would fill gaps. This is a runtime change, not just prompt change. Do it after Layers 1–2 unless the remote lab already needs it.

### Layer 5 — Density / concurrency (last)

- Raising `tasks_per_role` to ~50–60 can fill afternoon gaps **after** tasks are session-shaped.
- Same-role parallel dispatch requires relaxing the one-waiting-task gate. Soldier already has 3 workers. Only do this if Layer 1 sessions still leave the host idle and you explicitly want overlap (mail + download at once).

`generator.min_internal` (10) is a **startup feasibility cap** (`floor(480/min_internal)`), not an enforced gap in sampling. Adjacent times may be 1 minute apart. Do not treat it as “minimum traffic spacing”.

---

## 6. Frozen lab facts (do not change)

These are already baked into skills and `commander/prompt_resources/`. Copy them; do not “improve” them.

| Service | Value |
|---|---|
| Odoo | `http://172.16.24.14:8069/` |
| OWA | `https://i1-mail1-c02.ndrtest.local/owa/` |
| SMB | `\\172.16.24.11\Company_Data\...` (PowerShell) |
| Domain accounts | `ndrtest\{role}` / password `Njupt@241` (Odoo login uses the role name as email field) |
| SMB FQDN in domain doc | `i2-dc0-c08.edrtest.local` (skills currently use the IP above; do not invent a third address) |

Office soldier hosts in `commander/commander.ini` (as of this handoff):

| Role | Host | Port |
|---|---|---|
| programmer | 172.20.64.31 | 38472 |
| accountancy | 172.20.64.32 | 38472 |
| manager | 172.20.64.33 | 38472 |
| hr | 172.20.64.34 | 38472 |

SMB path ACLs:

- HR: `HR-Private`, `Public`, `Exchange`
- Accountancy: `accountancy`, `Public`, `Exchange`
- Manager: `Management`, `Public`, `Exchange`
- Programmer: `IT-Dev`, `Public`, `Exchange`

Playwright: Bing first (`https://www.bing.com`), Baidu fallback; do not load `/demo-skill`.

---

## 7. Generation / time-model rules to preserve

- Prompt shape: domain / role / skills / task_count / context.{env, schedule, backward}.
- ReAct: `Thought:` then `Action: Finish` then one JSON object, no markdown fences.
- Each item: `{"is_load":false,"task":"..."}` — **no `time` field**.
- Backward items: exactly one of from/to is the current role; a response must use a **later** schedule slot than that item’s time.
- `tasks_per_role` default: `commander/config.json` `generator.time_model`; per-role override: same key in `commander.ini`. Loader: `commander/target_config.py` `load_role_time_model()` / `TIME_MODEL_INI_KEYS`.
- Time-model overlay keys: `tasks_per_role`, `mu_am_minutes`, `mu_pm_minutes`, `sigma_am_minutes`, `sigma_pm_minutes`, `a_am`, `a_pm`, `phi`, `sigma_eta`. Not `avoid_five_minutes`.
- Office roles only in daily generation. `victim` is on-demand via `commander/victim_campaign.py`.

---

## 8. Files to read first in the new session

1. This file.
2. `README.md` — commander/soldier workflow.
3. `domain_resource.md` — company story + templates + unused resources.
4. `task_generation_constraints.md` — hard generation prompt.
5. `commander/prompt_resources/skill_templates.json` — what the LLM is allowed to emit.
6. `commander/prompt_resources/roles/*.json` and `domain.json`.
7. `skills/{hr,accountancy,manager,programmer}-skills/{exchange-use,odoo-use,smb-access,playwright-browser}/SKILL.md`.
8. One real output: `commander/role_task/tasks_08-17.json` (hr + manager were generated; accountancy/programmer may still be missing on that date).
9. `commander/scanner_service.py` (waiting gate), `soldier/soldier.ini` (timeout / workers).

PDF skills exist but are **not** in the generation catalog. Victim skills: do not modify for this task.

---

## 9. Suggested implementation plan for the new agent

1. Confirm remote lab has the same skills installed on the four office hosts (OpenCode skill dirs).
2. Extend skill templates + `domain_resource.md` so a task may be a **multi-step English workflow** with a target duration (e.g. 5–15 minutes) and optional attachments / larger SMB writes.
3. Update the four office skill bundles so the agent **stays in session** (do not close browser after the first click; close at end of workflow).
4. Optionally add PDF / Excel / FTP / GitHub to the **generation catalog** only if those tools are actually installed on the remote soldiers. If a channel is missing on the host, do not emit tasks for it.
5. Keep count at 39 unless you also change validation and INI. Prefer longer tasks first.
6. Regenerate a daily file on the remote commander (`python generate_role_task.py` from `commander/`). Existing `tasks_MM-DD.json` **skips roles that already have a valid list** — rename/delete the file if you need a full regen.
7. Spot-check a few executed soldier logs: session length, protocols touched, file sizes — not just “39 tasks succeeded”.

Tests: `tests/test_role_task` / commander refactor / target_config overlays. If you change the required task string shape, update generation tests accordingly.

---

## 10. Current config snapshot (non-secret)

- `generator.time_model.tasks_per_role`: 39 (JSON and all four INI roles)
- Peaks: `mu_am_minutes` 630 (10:30), `mu_pm_minutes` 900 (15:00)
- `sigma_am_minutes` 50, `sigma_pm_minutes` 65, `a_am`/`a_pm` 1.0, `phi` 0.85, `sigma_eta` 0.18, `avoid_five_minutes` true
- DeepSeek: `https://api.deepseek.com`, model `deepseek-v4-pro`, timeout 300s, `max_tokens` 32776 (key stays in `config.json` only)
- Commander listen: `0.0.0.0:38471`; soldiers: port 38472

---

## 11. One-sentence prompt for the new chat

> Read `benign_traffic_volume_handoff.md` at the repo root. Implement Layer 1 (session-shaped office tasks) and Layer 2 (larger artifacts) so benign mixed-mode traffic is dense enough; do not change frozen IPs/credentials; do not spam atomic duplicate tasks; leave victim alone.
