#!/usr/bin/env python3
"""Common utilities for HolyFramework commander and soldier components."""

import json
import math
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
WORK_WINDOWS = ((9 * 60, 12 * 60), (13 * 60 + 30, 18 * 60))


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


def build_controlled_task_file_paths(
    data_dir: Path,
    target_date: date | None = None,
) -> tuple[Path, Path]:
    """Return candidate + final paths for a day's generated role tasks."""
    day = target_date or date.today()
    stem = f"tasks_{day.month:02d}-{day.day:02d}"
    candidate = data_dir / f"{stem}.candidate.json"
    final = data_dir / f"{stem}.json"
    return candidate, final


def candidate_task_path(final_path: Path) -> Path:
    """Return the controlled candidate path for a final tasks file path."""
    if final_path.suffix == ".json":
        return final_path.with_name(f"{final_path.stem}.candidate.json")
    return final_path.with_name(f"{final_path.name}.candidate.json")


def load_task_file(path: Path) -> dict[str, Any]:
    """Load an existing task JSON file, returning {} when it does not exist."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            parsed = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid task JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Failed to read task JSON {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"Task JSON root must be an object in {path}")
    return parsed


def role_tasks_are_complete(
    data: dict[str, Any],
    role: str,
    *,
    min_tasks_per_role: int,
    max_tasks_per_role: int,
    min_non_five_ratio: float,
) -> bool:
    """Return True when a role entry is already a complete, valid stored task list."""
    tasks = data.get(role)
    if not isinstance(tasks, list):
        return False
    valid, _ = validate_role_tasks(
        {role: tasks},
        min_tasks_per_role=min_tasks_per_role,
        max_tasks_per_role=max_tasks_per_role,
        min_non_five_ratio=min_non_five_ratio,
        roles=(role,),
    )
    return valid


def validate_generated_task_file(
    file_path: Path,
    min_tasks_per_role: int,
    max_tasks_per_role: int,
    min_non_five_ratio: float,
    roles: tuple[str, ...] | list[str],
    preserve_generated_times: bool = False,
) -> tuple[str | None, str | None, dict[str, Any] | None, int]:
    """Load, normalize and validate a generated candidate task file."""
    try:
        file_size = file_path.stat().st_size
    except OSError:
        return "file_missing", f"Candidate task file not found: {file_path}", None, 0

    try:
        with open(file_path, encoding="utf-8") as f:
            parsed = json.load(f)
    except json.JSONDecodeError as exc:
        return "parse_fail", f"{exc.__class__.__name__}: {exc}", None, file_size
    except OSError as exc:
        return "parse_fail", f"{exc.__class__.__name__}: {exc}", None, file_size

    if not isinstance(parsed, dict):
        return "schema_fail", "Generated JSON must be a dictionary", None, file_size

    normalized = normalize_role_tasks(
        parsed,
        min_tasks_per_role=min_tasks_per_role,
        roles=roles,
        preserve_generated_times=preserve_generated_times,
    )
    valid, reason = validate_role_tasks(
        normalized,
        min_tasks_per_role=min_tasks_per_role,
        max_tasks_per_role=max_tasks_per_role,
        min_non_five_ratio=min_non_five_ratio,
        roles=roles,
    )
    if not valid:
        failure_type = classify_validation_failure(reason)
        return failure_type, reason, None, file_size
    return None, None, normalized, file_size


def promote_candidate_task_file(candidate_file: Path, final_file: Path, data: dict[str, Any]) -> None:
    """Persist normalized tasks to the final path and remove the candidate file."""
    save_json_atomic(final_file, data)
    if candidate_file.exists():
        try:
            candidate_file.unlink()
        except OSError:
            pass


def _normalize_roles(roles: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if roles is None:
        raise ValueError("roles must be provided explicitly")
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
    if not normalized:
        raise ValueError("roles must contain at least one non-empty role name")
    return tuple(normalized)


def format_task_generation_constraints(
    constraints_template: str,
    *,
    roles: tuple[str, ...] | list[str] | None = None,
    min_tasks_per_role: int = 18,
    max_tasks_per_role: int = 18,
) -> str:
    """Fill placeholders in the task-generation constraints markdown template."""
    role_names = _normalize_roles(roles)
    role_display = ", ".join(role_names)
    output_format = ", ".join(f'"{role}": [tasks]' for role in role_names)
    target_tasks = math.ceil((min_tasks_per_role + max_tasks_per_role) / 2)
    non_five_min = max(1, (min_tasks_per_role * 4) // 5)
    # Drop authoring-only HTML comments before sending text to the model.
    template_body = re.sub(
        r"<!--.*?-->",
        "",
        constraints_template,
        flags=re.DOTALL,
    ).strip()
    return template_body.format(
        role_display=role_display,
        target_tasks=target_tasks,
        output_format=output_format,
        non_five_min=non_five_min,
    ).strip()


def build_role_task_prompt(
    domain_context: str,
    constraints_template: str,
    min_tasks_per_role: int = 18,
    max_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
    dependency_context: str = "",
) -> str:
    """Build a constrained prompt for role task generation.

    constraints_template is the raw markdown from task_generation_constraints.md
    (or equivalent), with {role_display}, {target_tasks}, {output_format}, and
    {non_five_min} placeholders.

    dependency_context is inserted between domain_context and the hard-requirements block.
    When empty or whitespace-only, nothing is inserted between domain_context and the hard requirements.
    """
    hard_requirements = format_task_generation_constraints(
        constraints_template,
        roles=roles,
        min_tasks_per_role=min_tasks_per_role,
        max_tasks_per_role=max_tasks_per_role,
    )

    dep = dependency_context if isinstance(dependency_context, str) else ""

    lines: list[str] = [
        "Using the domain context below, generate a full-day sequence of work tasks for every role.",
        "All required context has already been provided. Do not ask for more background or clarification.",
        "Return exactly one JSON object. Do not output anything else or wrap the response in a Markdown code block.",
        "",
        domain_context,
        "",
    ]
    if dep.strip():
        lines.append(dep.strip())
        lines.append("")
    if hard_requirements:
        lines.append(hard_requirements)
    return "\n".join(lines)



def classify_validation_failure(reason: str | None) -> str:
    """Classify validation failures into schema vs quality buckets."""
    if not reason:
        return "schema_fail"
    schema_markers = (
        "Generated JSON must be a dictionary",
        "Missing roles:",
        "data is not a list",
        "is not an object",
        "missing fields:",
        "has empty task",
    )
    if any(marker in reason for marker in schema_markers):
        return "schema_fail"
    return "quality_fail"




def _looks_like_role_task_root(data: dict[str, Any]) -> bool:
    """Return True when a decoded object resembles the expected role-task root."""
    if not data:
        return False

    for key, value in data.items():
        if not isinstance(key, str) or not key.strip():
            return False
        if not isinstance(value, list):
            return False
        for item in value:
            if not isinstance(item, dict):
                return False
    return True


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first valid role-task JSON object from model output text."""
    if not text:
        return None

    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and _looks_like_role_task_root(data):
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
    """True if minute-of-day lies in any work window (closed intervals on both ends)."""
    for start, end in WORK_WINDOWS:
        if start <= value <= end:
            return True
    return False


def _next_work_minute(value: int) -> int | None:
    """Clamp or snap minute-of-day to the next valid work minute; None if after last window."""
    if value < WORK_WINDOWS[0][0]:
        return WORK_WINDOWS[0][0]
    for start, end in WORK_WINDOWS:
        if start <= value <= end:
            return value
    for start, _ in WORK_WINDOWS:
        if value < start:
            return start
    return None


def collect_task_indices_outside_work_windows(tasks: list[Any]) -> list[int]:
    """Return indices whose task start time (time field) is missing, invalid, or outside work windows."""
    bad: list[int] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            bad.append(index)
            continue
        raw_time = task.get("time")
        minute = parse_hhmm_to_minute(raw_time) if isinstance(raw_time, str) else None
        if minute is None or not _in_work_window(minute):
            bad.append(index)
    return bad


def build_role_task_time_remediation_prompt(
    *,
    role: str,
    old_tasks: list[dict[str, Any]],
    bad_indices: list[int],
    min_tasks_per_role: int,
    max_tasks_per_role: int,
    validation_reason: str = "",
    prior_feedback: str = "",
) -> str:
    """Prompt for LLM to fix only times (for bad indices) or delete bad rows; other rows unchanged."""
    target_tasks = math.ceil((min_tasks_per_role + max_tasks_per_role) / 2)
    non_five_min = max(1, (min_tasks_per_role * 4) // 5)
    bad_set = sorted(set(bad_indices))
    lines: list[str] = [
        "You are a task schedule correction assistant. Below is one role's full-day task array; each item includes its array index, time, is_load, and task.",
        "The previous complete array failed automatic validation because some task start-time values are outside the allowed working periods.",
        "",
        "The allowed working periods are the closed intervals 09:00–12:00 (including 12:00) and 13:30–18:00 (including 18:00).",
        "Times strictly between 12:00 and 13:30 are invalid.",
        "",
        f"Role: {role}",
        f"After correction, this role must contain exactly {target_tasks} tasks, matching ceil((min_tasks_per_role + max_tasks_per_role) / 2).",
        "Delete as few items as possible. If deletion leaves too few tasks, the correction fails; prefer changing time values to preserve the required count.",
        "The corrected list must still have strictly increasing time values in array order, and at least 80% of task minute values must not be divisible by 5 (at least "
        f"{non_five_min} such tasks).",
        "",
        "Hard constraints (must be followed):",
        "1. For an item whose index is in the invalid-index set, you may only change that item's time or delete the entire item from the array.",
        "2. For every item whose index is not in the invalid-index set, time, is_load, and task must remain exactly as provided below. Do not rewrite it or change its relative order; after invalid items are deleted, all remaining items must preserve their chronological relative order.",
        "3. Output exactly one JSON object with the top-level format {\"<role>\": [task array]}. Do not include Markdown or explanations.",
        "4. Every task object must contain time, is_load, and task. Any additional fields must remain compatible with the standard task structure.",
        "",
        f"Invalid indices (0-based, matching the Current tasks array below): {bad_set}",
    ]
    if validation_reason.strip():
        lines.extend(["", "Validation failure summary:", validation_reason.strip()])
    if prior_feedback.strip():
        lines.extend(["", "The previous correction failed. Continue correcting it:", prior_feedback.strip()])
    lines.extend(["", "Current tasks (with indices for reference):", json.dumps(_tasks_with_indices(old_tasks), ensure_ascii=False)])
    return "\n".join(lines)


def _tasks_with_indices(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, t in enumerate(tasks):
        row = {"_index": i, "time": t.get("time"), "is_load": t.get("is_load"), "task": t.get("task")}
        out.append(row)
    return out


def verify_time_remediation_payload(
    old_tasks: list[dict[str, Any]],
    new_tasks: list[Any],
    bad_indices: list[int] | set[int],
) -> tuple[bool, str | None]:
    """Ensure non-bad old rows appear unchanged in order; bad rows are only time-changed or omitted."""
    if not isinstance(new_tasks, list):
        return False, "remediated payload is not a list"
    bad_set = set(bad_indices)
    i, j = 0, 0
    n_old, n_new = len(old_tasks), len(new_tasks)
    while i < n_old:
        old_row = old_tasks[i]
        if not isinstance(old_row, dict):
            return False, f"old task#{i} is not an object"
        if i not in bad_set:
            if j >= n_new:
                return False, f"missing unchanged row for old index {i}"
            new_row = new_tasks[j]
            if not isinstance(new_row, dict):
                return False, f"new task#{j} is not an object"
            if (
                new_row.get("time") != old_row.get("time")
                or bool(new_row.get("is_load")) != bool(old_row.get("is_load"))
                or new_row.get("task") != old_row.get("task")
            ):
                return False, f"unchanged row mismatch at old#{i} vs new#{j}"
            i += 1
            j += 1
        else:
            if j < n_new:
                new_row = new_tasks[j]
                if (
                    isinstance(new_row, dict)
                    and new_row.get("task") == old_row.get("task")
                    and bool(new_row.get("is_load")) == bool(old_row.get("is_load"))
                ):
                    i += 1
                    j += 1
                else:
                    i += 1
            else:
                i += 1
    if j != n_new:
        return False, f"extra rows in remediated output after new#{j}"
    return True, None


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


def normalize_role_tasks(
    data: dict[str, Any],
    min_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
    preserve_generated_times: bool = False,
) -> dict[str, Any]:
    """Normalize role tasks with deterministic structure, count floor, and jittered times."""
    role_names = _normalize_roles(roles)
    normalized: dict[str, Any] = {}

    for role in role_names:
        items = data.get(role)
        if not isinstance(items, list):
            items = []

        descriptions: list[str] = []
        load_flags: list[bool] = []
        explicit_times: list[str | None] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = item.get("task")
            if not isinstance(desc, str) or not desc.strip():
                continue
            descriptions.append(desc.strip())
            load_flags.append(bool(item.get("is_load", False)))
            raw_time = item.get("time")
            if isinstance(raw_time, str) and parse_hhmm_to_minute(raw_time) is not None:
                explicit_times.append(raw_time)
            else:
                explicit_times.append(None)

        target_count = len(descriptions)
        if preserve_generated_times and target_count:
            ordered_items: list[tuple[int, str, bool, str | None]] = []
            can_preserve_ordered_times = True
            for desc, is_load, raw_time in zip(descriptions, load_flags, explicit_times):
                minute = parse_hhmm_to_minute(raw_time) if isinstance(raw_time, str) else None
                if minute is None:
                    can_preserve_ordered_times = False
                    break
                ordered_items.append((minute, desc, is_load, raw_time))
            if can_preserve_ordered_times:
                ordered_items.sort(key=lambda item: item[0])
                descriptions = [item[1] for item in ordered_items]
                load_flags = [item[2] for item in ordered_items]
                explicit_times = [item[3] for item in ordered_items]

        preserved_times: list[str] = []
        if preserve_generated_times and target_count:
            preserved_times = [value for value in explicit_times[:target_count] if isinstance(value, str)]
            if len(preserved_times) != target_count:
                preserved_times = []

        if not preserved_times:
            schedule = _build_schedule(target_count)
            if len(schedule) < target_count:
                target_count = len(schedule)
                descriptions = descriptions[:target_count]
                load_flags = load_flags[:target_count]
        else:
            schedule = []

        role_tasks: list[dict[str, Any]] = []
        for i in range(target_count):
            role_tasks.append(
                {
                    "time": preserved_times[i] if preserved_times else minute_to_hhmm(schedule[i]),
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
    max_tasks_per_role: int = 18,
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
        if len(tasks) > max_tasks_per_role:
            return False, f"Role '{role}' has too many tasks: {len(tasks)} > {max_tasks_per_role}"

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
