# HolyFramework

HolyFramework is a distributed task execution framework designed for enterprise intranet scenarios. The project uses `commander` to generate and schedule daily tasks, while `soldier` executes those tasks on different hosts and reports the results, continuously producing observable business traffic that resembles routine office activity.

The task-generation pipeline uses the DeepSeek API by default, but the invocation layer is abstracted in `common/agent_request_abc.py` so that other model implementations can be added later. Domain scenarios, role responsibilities, and task templates are defined in `domain_resource.md`. Hard requirements for the generation prompt live in `task_generation_constraints.md`. Both are runtime resources read when building model prompts.

## What the Project Does

The goal of this project is to simulate a realistic small-enterprise intranet in which different roles perform routine, responsibility-appropriate tasks on different hosts, continuously generating normal and observable network activity.

Key features include:

- Automatically generates a daily task file, organizing a full day of tasks by role.
- Periodically scans the task schedule and dispatches due tasks to the corresponding hosts.
- Uses `soldier` to execute commands and report results to `commander`.
- Continuously writes task status, output, and error information back to the shared task file.
- Supports manual task generation, manual task dispatch, and manual result reporting.
- Dynamically defines the role set through `commander.ini`, where each section represents one role.
- Encapsulates model requests behind an abstract interface; the default implementation is currently `common/deepseek_client.py`.

## Workflow

```mermaid
flowchart TD
    operator[Operator] --> soldierStart[Start soldier]
    operator --> commanderStart[Start commander]
    commanderStart --> generateTasks[Generate tasks_MM-DD.json]
    generateTasks --> scanLoop[Scan tasks by interval]
    scanLoop --> dispatchTask[Dispatch due task to target soldier]
    dispatchTask --> soldierExec[Soldier executes command]
    soldierExec --> reportBack[Report result to commander]
    reportBack --> updateState[Update task status and output]
```

## Directory Structure

```text
HolyFramework/
├── README.md                              # Main project documentation and current entry point
├── common/                                # Shared path, task-format, time-model, and LLM client utilities
│   ├── __init__.py                        # Validation, workspace paths, and task-file helpers
│   ├── time_model.py                      # Thesis 3.4 NHPP schedule generator
│   ├── agent_request_abc.py               # Abstract model-request interface
│   └── deepseek_client.py                 # Default DeepSeek API client
├── domain_resource.md                     # Domain scenarios and task-template resource read at runtime
├── task_generation_constraints.md         # Hard-requirement prompt template read at runtime
├── requirements.txt                       # Python dependencies
├── role_profiles/                         # Bundled OpenCode config, AGENTS.md, and per-role skill packs
│   ├── opencode.json                      # MCP servers, permissions, and DeepSeek provider env key
│   ├── AGENTS.md                          # Role-stamped OpenCode agent rules
│   ├── accountancy-skills/                # Accountancy host Skills
│   ├── hr-skills/                         # HR host Skills
│   ├── manager-skills/                    # Manager host Skills
│   ├── programmer-skills/                 # Programmer host Skills
│   └── attacker-skills/                   # Attacker host Skills plus generator prompts
├── commander/
│   ├── host_build.py                      # commander build: provider env-key merge and OpenCode cache clear
│   ├── commander.py                       # Main entry point: TCP server, scanner thread, and dependency wiring
│   ├── generate_role_task.py              # Standalone entry point for generating the daily task file
│   ├── dispatch.py                        # CLI for manually dispatching a single task
│   ├── dispatch_client.py                 # Subprocess adapter used by the scanner to invoke dispatch.py
│   ├── scanner_service.py                 # Main scanning and scheduling workflow
│   ├── role_file_service.py               # Daily task-file generation, repair, loading, and saving
│   ├── prompt_catalog.py                  # Assembles domain/role/skills/context prompts
│   ├── prompt_resources/                  # Compact generation catalog (not full SKILL.md bodies)
│   ├── role_task_generation.py            # Task generation: ReAct parse, zip times, validate
│   ├── repository.py                      # Task-file repository: I/O, locking, and status updates
│   ├── domain.py                          # Task status-transition rules
│   ├── policies.py                        # Task-selection policies; selects the earliest pending task by default
│   ├── target_config.py                   # Loads roles and target-host configuration from commander.ini
│   ├── victim_campaign.py                 # On-demand victim campaign: one technique per step
│   ├── runtime_config.py                  # Loads and validates config.json
│   ├── logging_setup.py                   # Commander and dispatch logging setup
│   ├── config.json                        # Main commander configuration file
│   ├── commander.ini                      # Role-to-soldier host/port mapping
│   ├── role_task/                         # Directory containing shared daily task files
│   └── logs/                              # Commander and dispatch log directory
├── attacker/
│   ├── cli.py                             # Default command starts the attacker scheduler
│   ├── runtime.py                         # NHPP schedule, batch-of-5 fill, serial local execution
│   ├── generation.py                      # LLM batch requests using skill prompts and state.json
│   ├── execute.py                         # Local opencode run plus per-task result log
│   ├── task_file.py                       # Attacker task-list JSON schema
│   ├── config.json                        # Time model, batch size, and model settings
│   ├── role_task/                         # Daily attacker task lists
│   └── logs/                              # Per-task Agent result and exit-code JSONL
├── soldier/
│   ├── soldier.py                         # Main program: receives tasks, executes commands, and reports results
│   ├── soldier.ini                        # Soldier configuration file
│   ├── logs/                              # soldier_YYYY-MM-DD.log lifecycle files
│   └── runtime/                           # Task Markdown transcripts and report queues
└── tests/
    ├── test_attacker_runtime.py           # Attacker batch fill, serial execution, and result logs
    ├── test_commander_logging_hook.py     # Commander log-switch hook tests
    ├── test_commander_refactor.py         # Core commander regression tests
    ├── test_common_work_windows.py        # Shared work-window utility tests
    ├── test_role_dependency_provider.py   # Role dependency-provider tests
    ├── test_soldier_runtime.py             # Soldier runtime tests
    └── test_victim_campaign.py            # On-demand victim campaign state tests
```

## Core Concepts

### Commander

`commander` generates tasks, maintains daily task files, scans for due tasks, and dispatches them to the target `soldier`. It also receives execution results and writes their status back to the same task file.

### Soldier

`soldier` is the task execution endpoint. It listens for TCP requests from `commander`, executes the received commands, and then reports status, output, and error information back to `commander`.

### Attacker

`attacker` is a standalone scheduler on the attacker host. It uses the same NHPP time-node model as office roles, writes a day's task list, asks the model for at most five task strings at a time, and runs due tasks locally with `opencode run --auto`. It does not go through commander dispatch. Install skills first with `attacker build`. Scheduler progress is written to the console and to `attacker/logs/attacker_YYYY-MM-DD.log`.

Each attacker task object has:

- `task`
- `planned_time`
- `started_at`
- `completed_at`

Per-task Agent output and exit code are appended to `attacker/logs/tasks_YYYY-MM-DD.jsonl`.

### Shared Task File

Daily task files are stored at `commander/role_task/tasks_MM-DD.json` and organize tasks by role. Each task entry contains at least the following fields:

- `time`
- `is_load`
- `task`
- `task_id` (assigned when the daily file is generated, not at dispatch)
- `status`
- `issued_at`
- `expiry_time`
- `completed_at`
- `report_message`
- `exit_code`
- `stdout`
- `stderr`

Status transitions follow `planned -> waiting -> successed/failed`. `task_id` is unique across days; commander looks up reports by that ID if the date in `task_ref` does not match the task file.

### Role Source

The role set is no longer hard-coded. Instead, it is derived from the sections in `commander/commander.ini`. For example:

```ini
[hr]
host = 192.168.14.72
port = 38472

[accountancy]
host = 192.168.14.73
port = 38472
```

This means:

- `hr` and `accountancy` are two role names.
- Each role maps to a target `soldier` host and listening port.
- To add or remove a role, only the sections in `commander.ini` need to be changed.

## Basic Usage

### 1. Environment Requirements

Install the project from the repository root (dependencies are declared in `pyproject.toml`):

```bash
pip install .
```

For local development, use an editable install:

```bash
pip install -e .
```

This provides the `commander`, `soldier`, and `attacker` commands. Python dependencies are `filelock`, `colorlog`, and `matplotlib`.

You also need:

- Python 3.10+
- Node.js / `npx` on each soldier host (`soldier build` installs Playwright through npx) and on the attacker host (`attacker build` does the same)
- An executable `opencode` command available on the system
- Network connectivity between `commander` and every `soldier`

### 2. Configuration

Before running the project, review at least these three configuration files:

#### `commander/config.json`

This is the main `commander` configuration file. It controls listening, scanning, storage, dispatch, task generation, paths, and logging. The current default configuration includes:

- `commander` listens on `0.0.0.0:38471`
- Scan interval: `60` seconds
- Task generation uses DeepSeek by default:
  - `api_base_url`
  - `model`
  - `request_timeout_seconds`

The DeepSeek API key is read from the `DEEPSEEK_API_KEY` environment variable, not from `commander/config.json`. `commander build` writes `{env:DEEPSEEK_API_KEY}` into `~/.config/opencode/opencode.json` so OpenCode uses the same variable. See the generator section below.

#### `commander/commander.ini`

Defines the mapping between roles and target hosts. Each section represents one role and must contain:

- `host`
- `port`

Optional time-model keys overlay `generator.time_model` in `config.json` per role (`tasks_per_role`, peak/width/AR(1) parameters). Omitted keys use the JSON defaults. `avoid_five_minutes` is JSON-only.

#### `soldier/soldier.ini`

Configures the `soldier` listening address, the `commander` address used for reporting, and the execution timeout:

```ini
[commander]
ip = 127.0.0.1
port = 38471

[listen]
bind = 0.0.0.0
port = 38472

[exec]
timeout = 900
```

### 3. Startup Order

Start `soldier` first, then start `commander`. After `pip install .` the commands are:

#### Install OpenCode skills/MCP on a soldier host

Run once per role host. Copies that role's skills from `role_profiles/<role>-skills/` into `~/.config/opencode/skills/` (overwriting existing skill directories), merges MCP servers, the DeepSeek `provider.options.apiKey` `{env:DEEPSEEK_API_KEY}` block, and an explicit `permission` object (`*`, `doom_loop`, and `external_directory["*"]` all `allow`) into `~/.config/opencode/opencode.json` and into `opencode.jsonc` when that file already exists, writes a role-stamped `~/.config/opencode/AGENTS.md`, deletes `%USERPROFILE%\.cache\opencode` (not `auth.json`), and installs Playwright Chromium if `npx playwright` is missing. Soldier then runs tasks as `opencode run --auto` with `OPENCODE_PERMISSION` set so workspace-outside paths (Desktop, UNC shares) do not wait for Enter. Do not leave an old `opencode serve` process attached to these hosts; each task starts a fresh `opencode run`.

```bash
soldier build hr
soldier build hr --test
```

`--test` runs after a successful install. It checks that OpenCode loads (`opencode --version` and `~/.config/opencode/opencode.json`), that each installed skill and merged MCP is present, then runs `opencode run --auto` with one representative prompt per skill and per MCP (same argv and `OPENCODE_PERMISSION` as production). Prompts are taken from `role_profiles/<role>-skills/PROMPT_TEMPLATES.md`, preferring view/list/search/extract examples when they exist. Skills without an `opencode run` example (for example `pdf`) are skipped. Output is printed to the terminal only (`Target`, `Command`, `Result`); nothing is written under soldier `runtime/` or log files. Each live prompt can take up to 900 seconds. A failed check leaves the installed config in place and exits 1.

Roles: `hr`, `accountancy`, `manager`, `programmer`, `victim`.

#### Install OpenCode skills/MCP on an attacker host

Same OpenCode install as soldier (skills, MCP, `AGENTS.md`, cache clear, Playwright), but only for `attacker-skills`. Run once on the attacker host:

```bash
attacker build
attacker build --test
```

`--test` is the same live check as on soldier, using `attacker-skills` (the `ad-attack` prompt is `discovery.orientation`). `soldier build attacker` is rejected; soldier is for office roles and victim only.

#### Write DeepSeek provider env-key config on the commander host

Does not install skills, `AGENTS.md`, Playwright, or MCP servers. Merges only `provider.deepseek.options.apiKey` (`{env:DEEPSEEK_API_KEY}`) into `~/.config/opencode/opencode.json` (and `opencode.jsonc` if that file exists) and deletes `%USERPROFILE%\.cache\opencode` (not `auth.json`). The process still needs `DEEPSEEK_API_KEY` set at runtime.

```bash
commander build
commander build --test
```

`commander build --test` only checks that OpenCode starts and that the DeepSeek provider can complete a short smoke prompt. It does not install or exercise skills or MCP servers.

#### Start soldier

```bash
soldier listen
```

Optional arguments:

```bash
soldier listen --bind 0.0.0.0 --listen-port 38472 --commander-host 127.0.0.1 --commander-port 38471
```

On Windows, `soldier listen` does not start Sysmon collection. Log collection is manual-only so `soldier` can start cleanly from a normal user session. The `--no-sysmon` flag is accepted for compatibility but has no effect.

#### Collect Sysmon logs

Prerequisite on each host that should record Sysmon events: Sysmon is already installed and running. The collector does not restart Sysmon; it records an observe timestamp and waits. Run it manually from an Administrator PowerShell when logs are needed:

```bash
sysmon-collect
python -m sysmon_collector
```

At local 00:00 the collector exports the previous 24 hours under `soldier/logs/sysmon/` (override with `HOLYFW_SYSMON_LOG_DIR`):

- `sysmon_YYYY-MM-DD.evtx` — Sysmon Operational
- `security_logon_YYYY-MM-DD.evtx` — Security logon and authentication events (4624/4625/4768/4776 and related IDs)

If Sysmon is not running when observed, midnight export still continues for both channels.

#### Start commander

```bash
commander
```

Optional arguments:

```bash
commander --host 0.0.0.0 --port 38471 --data-dir ./role_task --debug
```

Without installing, from the repository root:

```bash
python soldier/soldier.py listen
python commander/commander.py
python -m attacker.cli
```

By default, tasks wait until their planned time and are then dispatched in generated order,
regardless of how late they are. With `--debug`, tasks more than
`scanner.max_dispatch_lateness_minutes` late are marked failed instead of being dispatched.

#### Start attacker

On the attacker host, install skills once with `attacker build`, set `DEEPSEEK_API_KEY`, then start the scheduler. With no subcommand it runs immediately, same as `attacker run`:

```bash
attacker build
attacker
```

`attacker` samples the day's time nodes, writes `attacker/role_task/tasks_MM-DD.json`, requests up to five task strings from the model whenever no filled task is waiting, and executes due or overdue tasks serially with local `opencode run --auto`. Inspect the list with `attacker show`.

`base_time` (0–23) shifts the generated 09:00 workday the same way commander does. Set it in `attacker/config.json` or pass `--base-time` (`attacker --base-time 21`). Times that wrap past midnight stay on the next calendar day and are not treated as already due.

### 4. Common Utility Commands

#### Manually generate the daily task file

```bash
commander generate
```

#### Manually dispatch a task

```bash
commander dispatch --target hr --task "Check email with Exchange"
```

#### On-demand victim campaign (not daily generation)

`victim` is excluded from the daily `tasks_per_role` quota even if it appears in `commander.ini`. Run one technique at a time. Prefer `step` on the victim host so `~/.holyfw/campaign_state.json` is updated from OpenCode output:

```powershell
commander victim step --task "Use the penetration-test skill on the victim host, run observe for the reconnaissance phase, {run_id: recon-001, approved target: <DC_IP>, technique: domain users and trusts, traffic objective: LDAP queries to the approved DC, success criteria: sanitized user and trust counts saved, cleanup: not applicable}"
commander victim show
commander victim step
```

The second `step` (no `--task`) uses `next_task` from campaign state when the skill returned one after a privilege block or successful bounded step. From commander you can instead `commander victim dispatch --task "..."` after enabling `[victim]` in `commander.ini`; the state file is still written on the victim by the skill.

Replace `<DC_IP>` with an operator-approved domain controller. Do not paste hashes or passwords into the task string.

#### Manually report a result from soldier

```bash
soldier report --task-ref "2026-04-21_hr_a1b2c3d4e5f67890" --status successed --exit-code 0
```

#### View or lift character circuit breaker status

```powershell
commander breaker status
commander breaker reset --role hr
```

#### Manually retry records that ultimately failed to report.

```powershell
soldier replay-failed-reports
```

## Advanced Usage

## `commander/config.json` Parameters

### server

Controls the `commander` TCP listener:

- `host`: Listening address
- `port`: Listening port
- `max_line_bytes`: Maximum size in bytes of a single request
- `recv_chunk_bytes`: Chunk size for each socket read
- `socket_timeout_seconds`: Connection timeout
- `listen_backlog`: Listener backlog
- `worker_threads`: Maximum number of workers that handle `soldier` report connections; defaults to `6`

### scanner

Controls scanning behavior:

- `data_dir`: Shared task-file directory
- `scan_interval_seconds`: Scan interval in seconds

### storage

Controls task-file writes and output storage:

- `lock_timeout_seconds`: File-lock timeout
- `max_store_text`: Maximum number of characters stored for stdout/stderr

### dispatch

Controls task dispatch:

- `soldier_timeout_seconds`: TCP timeout used by `dispatch.py` when connecting to `soldier`
- `client_timeout_seconds`: Timeout for the `dispatch.py` subprocess invoked by `commander`; must be at least 5 seconds longer than `soldier_timeout_seconds`
- `timeout_minutes`: Waiting-expiration period written to a task after dispatch

### failure_policy

Limit consecutive failures by role：

- `cooldown_seconds`: The pause duration after the first and second failures; currently set to `300` seconds.
- `max_consecutive_failures`: The threshold for triggering the circuit breaker based on consecutive failures; currently set to `3`.
- `state_file`: The file used to persist the circuit breaker state; restarting Commander will not bypass the circuit breaker.

Once the threshold is reached, the role will not initiate new tasks for the remainder of the day. A `busy` status simply indicates that the Soldier has hit its concurrency limit and does not count as a failure; while a successful result resets the consecutive failure count, a triggered circuit breaker must be manually reset or will only clear upon the change of date.

### email_alert

An alert can be sent via QQ Mail SMTP when a role circuit-breaker trips. This feature is disabled by default; to enable it, you must:

1. Enable the SMTP service in your QQ Mail account and generate an authorization code.
2. Fill in the `sender` and `recipients` fields in `commander/config.json` and set `enabled=true`.
3. Store the authorization code in an environment variable rather than writing it to the configuration file:

```powershell
$env:HOLYFW_QQ_SMTP_AUTH_CODE = "Your QQ Mail SMTP authorization code"
```

By default, the system uses `smtp.qq.com:465` with SSL. Email delivery failures will not prevent the role circuit-breaker from tripping, nor will they trigger `opencode`.

### generator

Controls task generation:

- `max_attempts`: Number of generation attempts
- `api_base_url`: Model API URL
- `model`: Model name
- `request_timeout_seconds`: Timeout for a single model request

The DeepSeek API key is not stored in `config.json`. Set it in the process environment before `commander serve`, `commander generate`, or OpenCode:

```powershell
$env:DEEPSEEK_API_KEY = "..."
```

Then run `commander build` so `~/.config/opencode/opencode.json` points at that variable:

```json
"provider": {
  "deepseek": {
    "options": {
      "apiKey": "{env:DEEPSEEK_API_KEY}"
    }
  }
}
```

If `DEEPSEEK_API_KEY` is missing or blank, Python client construction fails with an error naming that variable.

### paths

Controls path resolution. Relative paths are resolved from the `commander/` directory:

- `logs_dir`
- `target_ini_file`
- `dispatch_script`
- `domain_resource_file`
- `task_generation_constraints_file`

For example, `domain_resource_file` currently defaults to `../domain_resource.md`, and `task_generation_constraints_file` defaults to `../task_generation_constraints.md`, so the root-level markdown resources are read during task generation.

### logging

Controls logging:

- `level`
- `backup_count`
- `rotation_interval_days`

## Advanced `commander.ini` Details

`commander.ini` is more than an address table: it also defines the role set itself.

`load_all_roles()` in `commander/target_config.py` reads every section name as a role, so:

- Adding a section adds a role.
- Removing a section removes that role from scheduling.
- Section names are normalized to lowercase.
- `victim` and `attacker` remain omitted from daily office-role generation (`load_daily_generation_roles()`). Drive victim with `victim_campaign.py`. Drive attacker with the `attacker` command.

## Advanced `soldier.ini` Details

### commander Section

Determines which `commander` receives result reports from the `soldier`:

- `ip`
- `port`

### listen Section

Determines the address on which `soldier` receives tasks:

- `bind`
- `port`
- `worker_threads`：Maximum limit on concurrent active tasks; currently `3`. New tasks receive a `busy` response immediately upon reaching the limit.

For distributed deployments, ensure that `bind` is not limited to the local loopback address and that the port is reachable from the `commander` host.

### exec Section

Sets the timeout for a single command execution:

- `timeout`：单任务执行超时，当前为 `900` 秒；超时会终止 shell、opencode 和 node 的完整进程树

## Parameter Precedence and Overrides

### commander

`commander.py` supports the following overrides:

- `--host` overrides `server.host`
- `--port` overrides `server.port`
- `--data-dir` overrides `scanner.data_dir`
- `--debug` enables the configured dispatch-lateness window; without it, overdue tasks remain dispatchable

### dispatch

`dispatch.py` supports the following overrides:

- `--data-dir` overrides `scanner.data_dir`
- `--timeout-minutes` overrides `dispatch.timeout_minutes`
- `--config` overrides `paths.target_ini_file`

### soldier

Command-line arguments in `soldier.py` take precedence over `soldier.ini`:

- `--config`
- In `listen` mode: `--bind`, `--listen-port`, `--commander-host`, and `--commander-port`
- In `report` mode: `--host` and `--port`

## Model-Based Task Generation Pipeline

The current task-generation workflow is:

1. `common/time_model.py` samples a strictly increasing work-window schedule from the thesis 3.4 NHPP (dual Gaussian intensity, lunch mask, AR(1) busyness). `tasks_per_role` is the expected daily count E[N]; the realized list length is random.
2. `commander/role_task_generation.py` counts that list, then builds a ReAct prompt from `commander/prompt_resources/` (domain, role skills, env, schedule, backward facts) with `task_count = len(schedule)`.
3. The LLM returns exactly that many English task bodies and must not invent timestamps. Output is `Thought` then `Action: Finish` plus JSON.
4. Commander zips algorithm times onto the task list, validates content and cross-role response order, and persists `tasks_MM-DD.json`.

Working hours are 09:00-12:00 and 13:00-18:00 (lunch 12:00-13:00). Backward items use `{from, to, time, task}` where exactly one endpoint is the role being generated.

In this design:

- `common/agent_request_abc.py` defines the common model-request interface.
- `common/deepseek_client.py` is the current default implementation.
- To add another model client, implement the interface and inject the new client; the main task-generation workflow does not need to be rewritten.

## Logs and Runtime Artifacts

### commander

Logs are stored in `commander/logs/`. Common files include:

- `commander_YYYY-MM-DD.log` (switches by calendar day: when the date changes, a periodic hook reattaches the root file logger to that day's file)
- `agent_responses_YYYY-MM-DD/` interactive AI logs named `{role}_attemptN_*_interactive.log` (one file per finished model interaction)

Format: `time - LEVEL - role[index] - message`. Production (`INFO`) records task start (`Running — <task_id>`) and end (`Success` / `Failed`). Pass `--debug` for detailed dispatch and scan process logs. Dispatch no longer writes a separate `dispatch_*.log`.

### soldier

File logs live under `soldier/logs/`; per-task transcripts live under `soldier/runtime/`:

- `soldier_YYYY-MM-DD.log` — lifecycle log (`time - LEVEL - task_id - message`): receive time, start time, full `opencode run --auto ...` command, finish time, outcome (`Success` / `Fail` / `Error`), report time, and report result (`ok`, `queued: ...`, or `send failed: ...`)
- Console — only `Received` plus the outcome: `Success` (INFO), `Fail` with reason (WARNING, OpenCode started but non-zero or timeout), `Error` with the exception (ERROR, soldier could not start OpenCode)
- `runtime/tasks/YYYY-MM-DD/<task_id>.md` — Markdown transcript with YAML-like frontmatter and literal stdout/stderr (not JSON-escaped `\n`). The date folder is the `task_ref` date. Soldier still claims by `task_id` across dates.

Operational state (not the per-task transcript) also lives under `soldier/runtime/`:

- `pending_reports.jsonl` — reports waiting to retry to commander
- `failed_reports.jsonl` — reports that still failed after three retries

### attacker

Attacker records live under `attacker/logs/`:

- `attacker_YYYY-MM-DD.log` — scheduler log (`time - LEVEL - logger - message`) for fill, wait, execute, and completion
- `tasks_YYYY-MM-DD.jsonl` — one JSON object per executed task with `planned_time`, `task`, `result`, and `exit_code`

## Important Notes

- `domain_resource.md` and `task_generation_constraints.md` are runtime resources; do not delete them as though they were ordinary documentation.
- `soldier` executes received commands and should be deployed only in a controlled environment.
- In distributed deployments, carefully check the listening addresses, report-back addresses, and firewall configuration for `commander` and `soldier`.
- Task times are based on each host's system clock. Keep clocks synchronized across hosts in distributed deployments.
- Both `commander` and `soldier` use bounded thread pools for TCP processing, with a default maximum of 6 concurrent workers.
- Dispatch binds a task as `waiting` before sending it to `soldier`. If sending fails, the task is rolled back to a retryable state.
- Commander reports still truncate `soldier` stdout/stderr to the configured limit. The per-task Markdown under `soldier/runtime/tasks/` keeps the full OpenCode transcript with real newlines.
- If `soldier` cannot report to `commander`, the report is added to a local queue and retried up to three times in the background.
- The generator uses the DeepSeek API by default. Set `DEEPSEEK_API_KEY` in the environment, run `commander build` so OpenCode reads `{env:DEEPSEEK_API_KEY}`, and review the `generator` section in `commander/config.json` for non-secret model settings.

## Development and Regression Testing

Run full unittest discovery from the repository root:

```bash
python -m unittest discover -s tests -p "test*.py"
```

The tests cover core paths including the `commander` task repository, generation validation, dispatch rollback, log switching, `soldier` output truncation, and report retries. Network instability, real multi-host deployments, and system-level process supervision should still be validated through integration and operational exercises.

When extending the project, start with these entry points:

- `commander/commander.py`
- `commander/scanner_service.py`
- `commander/role_file_service.py`
- `common/time_model.py`
- `commander/role_task_generation.py`
- `common/agent_request_abc.py`
- `attacker/runtime.py`
- `soldier/soldier.py`
