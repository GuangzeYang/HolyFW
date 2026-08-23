"""Attacker run loop: NHPP schedule, batch fill, serial local execution."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from common import attacker_workspace_dir
from common.deepseek_client import build_deepseek_client
from common.time_model import TimeModelConfig, generate_schedule
from commander.schedule_shift import (
    ORIGIN_HOUR,
    SCHEDULE_SHIFT_KEY,
    apply_base_time_shift,
    clock_wrap_day_offset,
    file_day_from_tasks_path,
    shifted_window_still_active,
    task_datetime_on_file_day,
    validate_base_time,
)

from attacker.execute import execute_task
from attacker.generation import DEFAULT_BATCH_SIZE, fill_next_batch, load_generation_resources
from attacker.task_file import (
    all_completed,
    empty_slot_indices,
    load_attacker_payload,
    normalize_task_item,
    pending_ready,
    save_attacker_tasks,
    tasks_file_path,
    tasks_from_schedule,
)

SleepFn = Callable[[float], None]
NowFn = Callable[[], datetime]
TIME_KEY = "planned_time"


def _naive(now: datetime) -> datetime:
    return now.replace(tzinfo=None) if now.tzinfo is not None else now


def _task_datetime(tasks: list[dict[str, str]], index: int, file_day: date) -> datetime | None:
    item = tasks[index]
    return task_datetime_on_file_day(
        item.get(TIME_KEY, ""),
        file_day,
        clock_wrap_day_offset(tasks, index, time_key=TIME_KEY),
    )


def resolve_config_path(override: Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    try:
        workspace = attacker_workspace_dir(package_hint=Path(__file__))
        candidate = workspace / "config.json"
        if candidate.is_file():
            return candidate
    except FileNotFoundError:
        pass
    packaged = Path(__file__).resolve().parent / "config.json"
    if packaged.is_file():
        return packaged
    raise FileNotFoundError("attacker/config.json not found")


def load_config(path: Path | None = None) -> dict[str, Any]:
    config_path = resolve_config_path(path)
    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError(f"attacker config root must be an object: {config_path}")
    return parsed


def resolve_workspace() -> Path:
    return attacker_workspace_dir(package_hint=Path(__file__))


def _path_from_config(workspace: Path, raw: str, default_name: str) -> Path:
    text = (raw or default_name).strip() or default_name
    path = Path(text)
    if path.is_absolute():
        return path
    return workspace / path


def config_base_time(loaded: dict[str, Any]) -> int:
    raw = loaded.get("base_time", ORIGIN_HOUR)
    try:
        return validate_base_time(int(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("attacker base_time must be an integer 0..23") from exc


def due_ready(
    tasks: list[dict[str, str]],
    now: datetime,
    *,
    file_day: date | None = None,
) -> list[dict[str, str]]:
    anchor = file_day or now.date()
    current = _naive(now)
    due: list[dict[str, str]] = []
    pending = {id(item) for item in pending_ready(tasks)}
    for index, item in enumerate(tasks):
        if id(item) not in pending:
            continue
        planned = _task_datetime(tasks, index, anchor)
        if planned is not None and planned <= current:
            due.append(item)
    return due


def seconds_until_next(
    tasks: list[dict[str, str]],
    now: datetime,
    *,
    file_day: date | None = None,
) -> float | None:
    anchor = file_day or now.date()
    current = _naive(now)
    waits: list[float] = []
    pending = {id(item) for item in pending_ready(tasks)}
    for index, item in enumerate(tasks):
        if id(item) not in pending:
            continue
        planned = _task_datetime(tasks, index, anchor)
        if planned is None:
            continue
        delta = (planned - current).total_seconds()
        if delta > 0:
            waits.append(delta)
    if not waits:
        return None
    return min(waits)


def apply_attacker_base_time(
    tasks: list[dict[str, str]],
    *,
    base_time: int,
    file_day: date,
    stamp: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any] | None, bool]:
    payload: dict[str, Any] = {"tasks": [dict(item) for item in tasks]}
    if stamp:
        payload[SCHEDULE_SHIFT_KEY] = dict(stamp)
    out, changed = apply_base_time_shift(
        payload,
        base_time,
        file_day=file_day.isoformat(),
        time_key=TIME_KEY,
    )
    shifted = out.get("tasks")
    if not isinstance(shifted, list):
        return tasks, stamp, False
    rows = [normalize_task_item(item) for item in shifted]
    new_stamp = out.get(SCHEDULE_SHIFT_KEY)
    return rows, new_stamp if isinstance(new_stamp, dict) else None, changed


def ensure_task_file(
    path: Path,
    *,
    expected_count: int,
    day: date,
    time_model: dict[str, Any],
    base_time: int,
    seed: int | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    if path.exists():
        tasks, stamp = load_attacker_payload(path)
    else:
        schedule = generate_schedule(
            expected_count,
            role="attacker",
            day=day,
            config=TimeModelConfig.from_mapping(time_model),
            seed=seed,
        )
        tasks = tasks_from_schedule(schedule)
        stamp = None
    tasks, stamp, changed = apply_attacker_base_time(
        tasks,
        base_time=base_time,
        file_day=day,
        stamp=stamp,
    )
    if changed or not path.exists():
        save_attacker_tasks(path, tasks, shift=stamp)
    return tasks, stamp


def resolve_run_day(
    data_dir: Path,
    *,
    now: datetime,
    explicit_day: date | None = None,
) -> date:
    if explicit_day is not None:
        return explicit_day
    today = now.date()
    yesterday = today - timedelta(days=1)
    path = tasks_file_path(data_dir, yesterday)
    if not path.is_file():
        return today
    tasks, stamp = load_attacker_payload(path)
    if not tasks:
        return today
    payload: dict[str, Any] = {"tasks": tasks}
    if stamp:
        payload[SCHEDULE_SHIFT_KEY] = stamp
    stamp_day = None
    if stamp and isinstance(stamp.get("file_day"), str):
        try:
            stamp_day = date.fromisoformat(str(stamp["file_day"]).strip())
        except ValueError:
            stamp_day = None
    file_day = stamp_day or date.fromisoformat(
        file_day_from_tasks_path(path, today=today)
    )
    if shifted_window_still_active(payload, file_day, now, time_key=TIME_KEY):
        return yesterday
    return today


def drain_due(
    tasks: list[dict[str, str]],
    *,
    now: datetime,
    execute_one: Callable[[dict[str, str]], None],
    file_day: date | None = None,
) -> int:
    ran = 0
    while True:
        due = due_ready(tasks, now, file_day=file_day)
        if not due:
            return ran
        execute_one(due[0])
        ran += 1


def step(
    tasks: list[dict[str, str]],
    *,
    now: datetime,
    batch_size: int,
    fill_batch: Callable[[list[dict[str, str]], int], list[dict[str, str]]],
    execute_one: Callable[[dict[str, str]], None],
    file_day: date | None = None,
) -> str:
    """Advance one scheduler decision. Returns filled, executed, wait, or done."""
    if all_completed(tasks):
        return "done"
    pending = pending_ready(tasks)
    if not pending:
        remaining = empty_slot_indices(tasks)
        if not remaining:
            return "done"
        fill_batch(tasks, min(batch_size, len(remaining)))
        return "filled"
    ran = drain_due(tasks, now=now, execute_one=execute_one, file_day=file_day)
    if ran:
        return "executed"
    return "wait"


def run_loop(
    *,
    config: dict[str, Any] | None = None,
    config_path: Path | None = None,
    day: date | None = None,
    now_fn: NowFn | None = None,
    sleep_fn: SleepFn | None = None,
    agent_client: Any | None = None,
    execute_one: Callable[[dict[str, str]], None] | None = None,
    fill_batch: Callable[[list[dict[str, str]], int], list[dict[str, str]]] | None = None,
    seed: int | None = None,
    base_time: int | None = None,
) -> int:
    loaded = config if config is not None else load_config(config_path)
    workspace = resolve_workspace()
    generator = loaded.get("generator") or {}
    time_model = dict(generator.get("time_model") or {})
    batch_size = int(loaded.get("batch_size") or DEFAULT_BATCH_SIZE)
    poll_interval = float(loaded.get("poll_interval_seconds") or 15)
    timeout_seconds = int((loaded.get("exec") or {}).get("timeout_seconds") or 900)
    paths = loaded.get("paths") or {}
    data_dir = _path_from_config(workspace, str(paths.get("data_dir") or ""), "role_task")
    logs_dir = _path_from_config(workspace, str(paths.get("logs_dir") or ""), "logs")
    resolved_base_time = validate_base_time(base_time) if base_time is not None else config_base_time(loaded)

    clock = now_fn or (lambda: datetime.now().astimezone())
    sleeper = sleep_fn or __import__("time").sleep
    target_day = resolve_run_day(data_dir, now=clock(), explicit_day=day)
    task_path = tasks_file_path(data_dir, target_day)
    expected = int(time_model.get("tasks_per_role") or 39)

    tasks, shift_stamp = ensure_task_file(
        task_path,
        expected_count=expected,
        day=target_day,
        time_model=time_model,
        base_time=resolved_base_time,
        seed=seed,
    )

    client = agent_client
    system_prompt = prompt_template = ""
    state: dict[str, Any] = {}

    def _fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
        nonlocal client, system_prompt, prompt_template, state
        if fill_batch is not None:
            filled = fill_batch(current, size)
            save_attacker_tasks(task_path, filled, shift=shift_stamp)
            return filled
        if client is None:
            client = build_deepseek_client(generator)
        system_prompt, prompt_template, state = load_generation_resources()
        filled = fill_next_batch(
            current,
            batch_size=size,
            agent_client=client,
            system_prompt=system_prompt,
            prompt_template=prompt_template,
            state=state,
            max_attempts=int(generator.get("max_attempts") or 5),
        )
        save_attacker_tasks(task_path, filled, shift=shift_stamp)
        return filled

    def _execute(item: dict[str, str]) -> None:
        if execute_one is not None:
            execute_one(item)
        else:
            execute_task(
                item,
                logs_dir=logs_dir,
                timeout_seconds=timeout_seconds,
                now=clock(),
                day=target_day,
            )
        save_attacker_tasks(task_path, tasks, shift=shift_stamp)

    while True:
        action = step(
            tasks,
            now=clock(),
            batch_size=batch_size,
            fill_batch=_fill,
            execute_one=_execute,
            file_day=target_day,
        )
        if action == "done":
            save_attacker_tasks(task_path, tasks, shift=shift_stamp)
            return 0
        if action == "wait":
            wait_s = seconds_until_next(tasks, clock(), file_day=target_day)
            delay = poll_interval if wait_s is None else min(poll_interval, max(1.0, wait_s))
            sleeper(delay)
