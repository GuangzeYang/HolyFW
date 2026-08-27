"""Attacker daily task list: JSON array with task_id plus four business fields."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
import json

from common import assign_task_id, existing_task_id, parse_hhmm_to_minute, save_json_atomic, strip_opencode_run_prefix
from commander.schedule_shift import SCHEDULE_SHIFT_KEY

TASK_FIELDS = ("task_id", "task", "planned_time", "started_at", "completed_at")


def tasks_file_path(data_dir: Path, day: date | None = None) -> Path:
    target = day or date.today()
    return data_dir / f"tasks_{target.month:02d}-{target.day:02d}.json"


def empty_task_item(planned_time: str) -> dict[str, str]:
    item = {
        "task": "",
        "planned_time": planned_time,
        "started_at": "",
        "completed_at": "",
    }
    assign_task_id(item)
    return item


def tasks_from_schedule(schedule: list[str]) -> list[dict[str, str]]:
    return [empty_task_item(item) for item in schedule]


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def normalize_task_item(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("Each attacker task must be an object")
    planned = _as_text(raw.get("planned_time") or raw.get("time"))
    if parse_hhmm_to_minute(planned) is None:
        raise ValueError(f"Invalid planned_time: {planned!r}")
    task_text = strip_opencode_run_prefix(_as_text(raw.get("task")))
    item = {
        "task": task_text,
        "planned_time": planned,
        "started_at": _as_text(raw.get("started_at")),
        "completed_at": _as_text(raw.get("completed_at")),
        "task_id": existing_task_id(raw.get("task_id")),
    }
    assign_task_id(item)
    return item


def load_attacker_payload(path: Path) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    if not path.exists():
        return [], None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid attacker task JSON in {path}: {exc}") from exc
    stamp: dict[str, Any] | None = None
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
        rows = parsed["tasks"]
        raw_stamp = parsed.get(SCHEDULE_SHIFT_KEY)
        if isinstance(raw_stamp, dict):
            stamp = dict(raw_stamp)
    else:
        raise ValueError(f"Attacker task file must be a JSON list or {{\"tasks\": [...]}}: {path}")
    return [normalize_task_item(item) for item in rows], stamp


def load_attacker_tasks(path: Path) -> list[dict[str, str]]:
    tasks, _stamp = load_attacker_payload(path)
    return tasks


def save_attacker_tasks(
    path: Path,
    tasks: list[dict[str, str]],
    *,
    shift: dict[str, Any] | None = None,
) -> None:
    normalized = [normalize_task_item(item) for item in tasks]
    if shift:
        save_json_atomic(path, {"tasks": normalized, SCHEDULE_SHIFT_KEY: dict(shift)})
    else:
        save_json_atomic(path, normalized)


def task_has_content(item: dict[str, str]) -> bool:
    return bool(_as_text(item.get("task")))


def task_is_complete(item: dict[str, str]) -> bool:
    return bool(_as_text(item.get("completed_at")))


def pending_ready(tasks: list[dict[str, str]]) -> list[dict[str, str]]:
    return [item for item in tasks if task_has_content(item) and not task_is_complete(item)]


def empty_slot_indices(tasks: list[dict[str, str]]) -> list[int]:
    return [index for index, item in enumerate(tasks) if not task_has_content(item)]


def all_completed(tasks: list[dict[str, str]]) -> bool:
    return bool(tasks) and all(task_is_complete(item) for item in tasks)


def completed_task_texts(tasks: list[dict[str, str]]) -> list[str]:
    return [_as_text(item.get("task")) for item in tasks if task_is_complete(item) and task_has_content(item)]


def raw_tasks_missing_ids(path: Path) -> bool:
    """True when the on-disk file has rows without a valid task_id."""
    if not path.is_file():
        return False
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
        rows = parsed["tasks"]
    else:
        return False
    return any(not isinstance(item, dict) or not existing_task_id(item.get("task_id")) for item in rows)
