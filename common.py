#!/usr/bin/env python3
"""Common utilities for HolyFramework commander and soldier components."""

import json
import os
import re
import random
import time
from datetime import date
from pathlib import Path
from typing import Any

DATE_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_MD = re.compile(r"^\d{2}-\d{2}$")
UUID_HEX_NO_HYPHEN = re.compile(r"^[0-9a-fA-F]{8,32}$")

ROLE_NAMES = ("hr", "accountancy", "manager", "programmer", "local")
ROLE_ALIASES = {
    "hr": ("hr", "HR", "human resources", "Human Resources"),
    "accountancy": ("accountancy", "finance", "accounting", "Accountancy"),
    "manager": ("manager", "ceo", "general manager", "Manager"),
    "programmer": ("programmer", "developer", "it", "Programmer"),
    "local": ("local", "local operations", "Local"),
}
WORK_WINDOWS = ((9 * 60, 12 * 60), (13 * 60 + 30, 18 * 60))

ROLE_FALLBACK_TASKS = {
    "hr": [
        "Review and categorize employee inquiry emails, then prepare an action list.",
        "Check today's HR approval workflow status in the OA system.",
        "Send follow-up emails to departments to confirm recruitment pipeline progress.",
        "Access \\resource\\HR and archive today's HR documents.",
        "Verify onboarding document completeness and send missing-item reminders.",
    ],
    "accountancy": [
        "Review bank notification emails and reconcile incoming payments.",
        "Recheck reimbursement approvals in OA and log discrepancies.",
        "Access \\resource\\Finance and update the payment schedule.",
        "Email business teams to confirm invoice and contract matching.",
        "Review daily AR/AP changes and prepare a summary email.",
    ],
    "manager": [
        "Review leadership update emails and mark high-priority items.",
        "Check key approvals and risk alerts in the OA system.",
        "Email department leads to confirm progress on key tasks for today.",
        "Access \\resource\\Executive and review the business dashboard.",
        "Reply to cross-team coordination emails and define execution timelines.",
    ],
    "programmer": [
        "Review team emails and update development task priorities.",
        "Access \\resource\\Developer to pull docs and scripts.",
        "Check pending merge requests and comments on the code platform.",
        "Review test feedback emails and enrich bug reproduction notes.",
        "Update R&D work logs and progress notes in OA.",
    ],
    "local": [
        "Review local environment task emails and update the execution checklist.",
        "Sign in to the local system console and verify service status.",
        "Run local directory inspection and record abnormal files.",
        "Sync local test results into the project daily report.",
        "Review local automation logs and mark pending items.",
    ],
}


def clean_old_files(dir_path: Path, pattern: str, days: int = 20) -> None:
    """Delete matched files older than the configured retention days."""
    if not dir_path.exists():
        return
    cutoff_time = time.time() - days * 86400
    for file_path in dir_path.glob(pattern):
        try:
            if os.path.getmtime(file_path) < cutoff_time:
                file_path.unlink()
        except OSError:
            pass  # Ignore deletion errors


def validate_task_id(task_id: str) -> str | None:
    """Task ID must be uuid.hex format (no hyphen), length 8-32."""
    if UUID_HEX_NO_HYPHEN.match(task_id):
        return None
    return "Task ID must be hyphen-free hex (uuid.uuid4().hex truncated or full 32 chars)"


def expand_date_segment(seg: str) -> tuple[str | None, str | None]:
    """YYYY-MM-DD or MM-DD -> normalized YYYY-MM-DD."""
    if DATE_FULL.match(seg):
        return seg, None
    if DATE_MD.match(seg):
        try:
            month_s, day_s = seg.split("-", 1)
            m, d = int(month_s), int(day_s)
            y = date.today().year
            date(y, m, d)  # validate
            return f"{y:04d}-{m:02d}-{d:02d}", None
        except (ValueError, OSError):
            return None, "task_ref date segment MM-DD is invalid"
    return None, "task_ref first segment must be YYYY-MM-DD or MM-DD"


def parse_task_ref(task_ref: str) -> tuple[tuple[str, str, str] | None, str | None]:
    """Parse ``(YYYY-MM-DD|MM-DD)_role_taskId``."""
    if not task_ref or not isinstance(task_ref, str):
        return None, "task_ref is empty or invalid"
    parts = task_ref.split("_")
    if len(parts) != 3:
        return None, (
            "task_ref format error: must be three segments date_role_taskId (taskId is uuid.hex, no hyphen)"
        )
    date_seg, role, task_id = parts[0], parts[1], parts[2]
    date_str, err = expand_date_segment(date_seg)
    if err:
        return None, err
    assert date_str is not None
    if "_" in role:
        return None, "task_ref format error: role name must not contain underscore"
    err = validate_task_id(task_id)
    if err:
        return None, err
    return (date_str, role, task_id), None


def tasks_path(data_dir: Path, date_str: str) -> Path:
    """Return path for tasks_MM-DD.json file."""
    month_day = date_str[5:] if len(date_str) >= 10 else date_str
    return data_dir / f"tasks_{month_day}.json"


def load_json_file(path: Path) -> dict[str, Any]:
    """Load JSON file, return empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Save JSON data atomically using temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    clean_old_files(path.parent, "tasks_*.json", days=20)


def _normalize_roles(roles: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if roles is None:
        return ROLE_NAMES
    normalized: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if not isinstance(role, str):
            continue
        role_name = role.strip().lower()
        if not role_name or role_name in seen:
            continue
        seen.add(role_name)
        normalized.append(role_name)
    return tuple(normalized) if normalized else ROLE_NAMES


def _role_display_name(role: str) -> str:
    aliases = ROLE_ALIASES.get(role)
    if aliases and len(aliases) >= 2:
        return aliases[-1]
    return role


def build_role_task_prompt(
    domain_context: str,
    min_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Build a constrained prompt for role task generation."""
    role_names = _normalize_roles(roles)
    role_display = ", ".join(_role_display_name(role) for role in role_names)
    output_format = ", ".join(f'"{role}": [tasks]' for role in role_names)

    return f'''Generate one full-day task sequence for each role using the following enterprise context:

{domain_context}

Hard requirements (all must be satisfied):
1. Include all roles exactly: {role_display}.
2. Generate at least {min_tasks_per_role} tasks for each role.
3. Output must be wrapped exactly with these boundary lines:
    JSON_START
    <single JSON object>
    JSON_END
4. Do not output anything before JSON_START or after JSON_END.
5. No explanations, no Markdown, no code fences, no prefixes/suffixes.
6. Do not call any tools. Do not output tokens like [TOOL_CALL], [/TOOL_CALL], or todowrite.
7. Ensure the JSON object is directly parseable by a standard JSON parser.
8. Each task item must use this format: {{"time":"09:15","is_load":false,"task":"..."}}.
9. Task times must be within work windows: 09:00-12:00 and 13:30-18:00.
10. Within each role, task times must be strictly increasing.
11. Add realistic randomization to task timing:
   - At least 80% of task minutes must not be multiples of 5.
   - Avoid fixed adjacent intervals; use varied gaps (recommended range: 12-35 minutes).
12. Tasks must match role responsibilities and should include observable network behaviors when appropriate (Exchange, OA, SMB, FTP, browser interactions).
13. Output format must be: {{{output_format}}}.
14. All task descriptions must be in English.
15. Task strings must not contain raw double quotes or unescaped backslashes that can break JSON parsing.'''


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first valid JSON object from model output text."""
    if not text:
        return None

    start_marker = "JSON_START"
    end_marker = "JSON_END"

    # Preferred path: parse content between explicit boundary markers.
    start_idx = text.find(start_marker)
    if start_idx != -1:
        start_idx += len(start_marker)
        end_idx = text.find(end_marker, start_idx)
        if end_idx != -1:
            candidate = text[start_idx:end_idx].strip()
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

    # Fallback: scan for the first decodable JSON object to tolerate noisy wrappers.
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    return None


def parse_hhmm_to_minute(value: str) -> int | None:
    """Parse HH:MM to minute offset in a day."""
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour, minute = map(int, value.split(":", 1))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def minute_to_hhmm(value: int) -> str:
    """Convert minute offset to HH:MM format."""
    hour, minute = divmod(int(value), 60)
    return f"{hour:02d}:{minute:02d}"


def _in_work_window(value: int) -> bool:
    for start, end in WORK_WINDOWS:
        if start <= value < end:
            return True
    return False


def _next_work_minute(value: int) -> int | None:
    if value < WORK_WINDOWS[0][0]:
        return WORK_WINDOWS[0][0]
    for start, end in WORK_WINDOWS:
        if start <= value < end:
            return value
    for start, _ in WORK_WINDOWS:
        if value < start:
            return start
    return None


def _next_non_five_minute(value: int, prev: int | None = None) -> int | None:
    cur = _next_work_minute(value)
    while cur is not None:
        if prev is not None and cur <= prev:
            cur = _next_work_minute(prev + 1)
            prev = None
            continue
        if cur % 5 != 0:
            return cur
        cur = _next_work_minute(cur + 1)
    return None


def _build_schedule(count: int, seed: int | None = None) -> list[int]:
    rng = random.Random(seed)
    if count <= 0:
        return []

    minutes: list[int] = []
    start = WORK_WINDOWS[0][0] + rng.randint(1, 11)
    current = _next_non_five_minute(start)
    if current is None:
        return []
    minutes.append(current)

    while len(minutes) < count:
        gap = rng.randint(12, 35)
        candidate = minutes[-1] + gap
        next_value = _next_non_five_minute(candidate, minutes[-1])
        if next_value is None:
            break
        minutes.append(next_value)

    while len(minutes) < count:
        fallback = _next_non_five_minute(minutes[-1] + 1, minutes[-1])
        if fallback is None:
            break
        minutes.append(fallback)

    return minutes[:count]


def _get_role_items(data: dict[str, Any], role: str) -> list:
    aliases = ROLE_ALIASES.get(role, (role,))
    for key in aliases:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def normalize_role_tasks(
    data: dict[str, Any],
    min_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Normalize role tasks with deterministic structure, count floor, and jittered times."""
    role_names = _normalize_roles(roles)
    normalized: dict[str, Any] = {}

    for role in role_names:
        items = _get_role_items(data, role)

        descriptions: list[str] = []
        load_flags: list[bool] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = item.get("task")
            if not isinstance(desc, str) or not desc.strip():
                continue
            descriptions.append(desc.strip())
            load_flags.append(bool(item.get("is_load", False)))

        target_count = max(min_tasks_per_role, len(descriptions))
        fallbacks = ROLE_FALLBACK_TASKS.get(role, ["Handle daily operational work and sync outcomes."])
        idx = 0
        while len(descriptions) < target_count:
            template = fallbacks[idx % len(fallbacks)]
            descriptions.append(template)
            load_flags.append(False)
            idx += 1

        schedule = _build_schedule(target_count)
        if len(schedule) < target_count:
            target_count = len(schedule)
            descriptions = descriptions[:target_count]
            load_flags = load_flags[:target_count]

        role_tasks: list[dict[str, Any]] = []
        for i in range(target_count):
            role_tasks.append(
                {
                    "time": minute_to_hhmm(schedule[i]),
                    "is_load": bool(load_flags[i]),
                    "task": descriptions[i],
                    "task_id": "",
                    "status": "planned",
                    "issued_at": "",
                    "expiry_time": "",
                    "completed_at": "",
                    "report_message": "",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            )

        normalized[role] = role_tasks

    return normalized


def validate_role_tasks(
    data: dict[str, Any],
    min_tasks_per_role: int = 18,
    min_non_five_ratio: float = 0.8,
    roles: tuple[str, ...] | list[str] | None = None,
) -> tuple[bool, str | None]:
    """Validate role tasks against structure and quality constraints."""
    role_names = _normalize_roles(roles)
    required_task_fields = {
        "time",
        "is_load",
        "task",
        "task_id",
        "status",
        "issued_at",
        "expiry_time",
        "completed_at",
        "report_message",
        "exit_code",
        "stdout",
        "stderr",
    }

    if not isinstance(data, dict):
        return False, "Generated JSON must be a dictionary"

    missing = set(role_names) - set(data.keys())
    if missing:
        return False, f"Missing roles: {sorted(missing)}"

    for role in role_names:
        tasks = data.get(role)
        if not isinstance(tasks, list):
            return False, f"Role '{role}' data is not a list"
        if len(tasks) < min_tasks_per_role:
            return False, f"Role '{role}' has too few tasks: {len(tasks)} < {min_tasks_per_role}"

        non_five = 0
        prev_minute: int | None = None
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                return False, f"Role '{role}' task#{index} is not an object"

            missing_fields = required_task_fields - set(task.keys())
            if missing_fields:
                return False, f"Role '{role}' task#{index} missing fields: {sorted(missing_fields)}"

            desc = task.get("task")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"Role '{role}' task#{index} has empty task"

            minute = parse_hhmm_to_minute(task.get("time"))
            if minute is None:
                return False, f"Role '{role}' task#{index} has invalid time format"
            if not _in_work_window(minute):
                return False, f"Role '{role}' task#{index} time out of work window"
            if prev_minute is not None and minute <= prev_minute:
                return False, f"Role '{role}' tasks are not strictly increasing"
            prev_minute = minute

            if minute % 5 != 0:
                non_five += 1

        ratio = non_five / len(tasks) if tasks else 0.0
        if ratio < min_non_five_ratio:
            return False, (
                f"Role '{role}' random minute ratio too low: {ratio:.2f} < {min_non_five_ratio:.2f}"
            )

    return True, None