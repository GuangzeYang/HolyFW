"""Attacker run loop: NHPP schedule, batch fill, serial local execution."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from common import attacker_workspace_dir, parse_hhmm_to_minute
from common.deepseek_client import build_deepseek_client
from common.time_model import TimeModelConfig, generate_schedule

from attacker.execute import execute_task
from attacker.generation import DEFAULT_BATCH_SIZE, fill_next_batch, load_generation_resources
from attacker.task_file import (
    all_completed,
    empty_slot_indices,
    load_attacker_tasks,
    pending_ready,
    save_attacker_tasks,
    tasks_file_path,
    tasks_from_schedule,
)

SleepFn = Callable[[float], None]
NowFn = Callable[[], datetime]


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


def due_ready(tasks: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
    current = now.hour * 60 + now.minute
    due: list[dict[str, str]] = []
    for item in pending_ready(tasks):
        planned = parse_hhmm_to_minute(item.get("planned_time", ""))
        if planned is not None and planned <= current:
            due.append(item)
    return due


def seconds_until_next(tasks: list[dict[str, str]], now: datetime) -> float | None:
    current = now.hour * 60 + now.minute
    waits: list[int] = []
    for item in pending_ready(tasks):
        planned = parse_hhmm_to_minute(item.get("planned_time", ""))
        if planned is None:
            continue
        if planned > current:
            waits.append(planned - current)
    if not waits:
        return None
    return float(min(waits) * 60)


def ensure_task_file(
    path: Path,
    *,
    expected_count: int,
    day: date,
    time_model: dict[str, Any],
    seed: int | None = None,
) -> list[dict[str, str]]:
    if path.exists():
        return load_attacker_tasks(path)
    schedule = generate_schedule(
        expected_count,
        role="attacker",
        day=day,
        config=TimeModelConfig.from_mapping(time_model),
        seed=seed,
    )
    tasks = tasks_from_schedule(schedule)
    save_attacker_tasks(path, tasks)
    return tasks


def drain_due(
    tasks: list[dict[str, str]],
    *,
    now: datetime,
    execute_one: Callable[[dict[str, str]], None],
) -> int:
    ran = 0
    while True:
        due = due_ready(tasks, now)
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
    ran = drain_due(tasks, now=now, execute_one=execute_one)
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
    target_day = day or date.today()
    task_path = tasks_file_path(data_dir, target_day)
    expected = int(time_model.get("tasks_per_role") or 39)

    clock = now_fn or (lambda: datetime.now().astimezone())
    sleeper = sleep_fn or __import__("time").sleep

    tasks = ensure_task_file(
        task_path,
        expected_count=expected,
        day=target_day,
        time_model=time_model,
        seed=seed,
    )

    client = agent_client
    system_prompt = prompt_template = ""
    state: dict[str, Any] = {}

    def _fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
        nonlocal client, system_prompt, prompt_template, state
        if fill_batch is not None:
            filled = fill_batch(current, size)
            save_attacker_tasks(task_path, filled)
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
        save_attacker_tasks(task_path, filled)
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
        save_attacker_tasks(task_path, tasks)

    while True:
        action = step(
            tasks,
            now=clock(),
            batch_size=batch_size,
            fill_batch=_fill,
            execute_one=_execute,
        )
        if action == "done":
            save_attacker_tasks(task_path, tasks)
            return 0
        if action == "wait":
            wait_s = seconds_until_next(tasks, clock())
            delay = poll_interval if wait_s is None else min(poll_interval, max(1.0, wait_s))
            sleeper(delay)
