#!/usr/bin/env python3
"""Application service for role task scanning and dispatch."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

try:
    from policies import PendingSelectionPolicy, task_needs_dispatch
    from repository import DailyTaskRepository
except ImportError:
    from commander.policies import PendingSelectionPolicy, task_needs_dispatch
    from commander.repository import DailyTaskRepository


class TaskScanService:
    """Scan role task lists and dispatch eligible tasks using injected dependencies."""

    def __init__(
        self,
        repository: DailyTaskRepository,
        selection_policy: PendingSelectionPolicy,
        dispatch_task: Callable[[str, str, str | None], bool],
    ):
        self.repository = repository
        self.selection_policy = selection_policy
        self.dispatch_task = dispatch_task

    def process_roles(
        self,
        tasks_by_role: dict[str, Any],
        roles: tuple[str, ...],
        role_pointers: dict[str, int],
        date_str: str,
    ) -> None:
        """Process one scan cycle over all roles with pointer-based scheduling."""
        for role_key in roles:
            tasks_any = tasks_by_role.get(role_key)
            if not isinstance(tasks_any, list) or not tasks_any:
                continue
            tasks: list[dict[str, Any]] = tasks_any

            pointer = self._ensure_pointer(role_key, tasks, role_pointers)
            pointer = self._rewind_pointer_if_earlier_pending(role_key, tasks, pointer, role_pointers)
            if pointer >= len(tasks):
                continue

            while pointer < len(tasks):
                pointer = self._rewind_pointer_if_earlier_pending(role_key, tasks, pointer, role_pointers)
                if pointer >= len(tasks):
                    break

                if self.repository.has_active_waiting_task(role_key, date_str):
                    logging.debug(f"Role {role_key} has waiting task, pausing pointer at index {pointer}")
                    break

                task = tasks[pointer]
                if not isinstance(task, dict):
                    logging.warning(f"Invalid task format at {role_key}[{pointer}], skipping")
                    pointer += 1
                    role_pointers[role_key] = pointer
                    continue

                if not task_needs_dispatch(task):
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                    continue

                now = datetime.now()
                task_time_raw = task.get("time")
                task_time = self._parse_task_datetime(task_time_raw if isinstance(task_time_raw, str) else "", now)
                if task_time is None:
                    logging.warning(f"Invalid task time for {role_key}[{pointer}], skipping")
                    task["is_load"] = True
                    self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        {"is_load": True},
                        only_if_no_task_id=True,
                    )
                    pointer += 1
                    role_pointers[role_key] = pointer
                    continue

                # Keep earliest pending task; do not skip historical tasks.
                if task_time > now:
                    break

                task_text_value = task.get("task")
                task_text = task_text_value if isinstance(task_text_value, str) else ""
                truncated = task_text[:50] + "..." if task_text and len(task_text) > 50 else task_text

                # Reading task marks is_load=True before dispatch.
                marked_loaded = False
                if not task.get("is_load", False):
                    marked_loaded = self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        {"is_load": True},
                        only_if_no_task_id=True,
                    )
                    latest = self.repository.get_task_by_index(date_str, role_key, pointer)
                    if latest is not None:
                        task.update(latest)
                    if marked_loaded:
                        logging.info(
                            f"Marked task loaded for {role_key}[{pointer}]: {task.get('time')} - {truncated}"
                        )
                    elif not task_needs_dispatch(task):
                        pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                        continue

                logging.info(f"Dispatching task for {role_key}[{pointer}]: {task.get('time')} - {truncated}")
                success = self.dispatch_task(role_key, task_text, task.get("time"))
                if success:
                    # Prevent stale in-memory snapshot from selecting this task again.
                    task["status"] = "waiting"
                    if not task.get("task_id"):
                        task["task_id"] = "__dispatched__"
                    if not task.get("issued_at"):
                        task["issued_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    logging.info(f"Successfully dispatched task for {role_key}[{pointer}]")
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                    continue

                # On dispatch failure, keep pointer unchanged for retry.
                if marked_loaded:
                    task["is_load"] = False
                failure_message = f"Dispatch failed at {datetime.now().isoformat(timespec='seconds')}"
                task["report_message"] = failure_message
                update_fields: dict[str, Any] = {
                    "report_message": failure_message,
                }
                if marked_loaded:
                    update_fields["is_load"] = False
                self.repository.update_task_fields_by_index(
                    date_str,
                    role_key,
                    pointer,
                    update_fields,
                    only_if_no_task_id=True,
                )
                logging.error(f"Failed to dispatch task for {role_key}[{pointer}], pointer unchanged")
                break

    def _parse_task_datetime(self, task_time_str: str, now: datetime) -> datetime | None:
        if not isinstance(task_time_str, str) or ":" not in task_time_str:
            return None
        try:
            hour, minute = map(int, task_time_str.split(":", 1))
            return datetime(now.year, now.month, now.day, hour, minute)
        except (ValueError, AttributeError):
            return None

    def _ensure_pointer(
        self,
        role_name: str,
        tasks: list[dict[str, Any]],
        role_pointers: dict[str, int],
    ) -> int:
        pointer = role_pointers.get(role_name)
        if not isinstance(pointer, int) or pointer < 0 or pointer >= len(tasks):
            next_idx = self.selection_policy.find_next_pending_index(tasks, 0)
            pointer = len(tasks) if next_idx is None else next_idx
            role_pointers[role_name] = pointer
        return pointer

    def _rewind_pointer_if_earlier_pending(
        self,
        role_name: str,
        tasks: list[dict[str, Any]],
        pointer: int,
        role_pointers: dict[str, int],
    ) -> int:
        earliest_idx = self.selection_policy.find_next_pending_index(tasks, 0)
        if earliest_idx is None:
            pointer = len(tasks)
            role_pointers[role_name] = pointer
            return pointer

        if earliest_idx < pointer:
            logging.info(
                f"Rewinding pointer for role {role_name}: {pointer} -> {earliest_idx}"
            )
            pointer = earliest_idx
            role_pointers[role_name] = pointer
        return pointer

    def _move_pointer_after_success(
        self,
        role_name: str,
        tasks: list[dict[str, Any]],
        current_index: int,
        role_pointers: dict[str, int],
    ) -> int:
        next_idx = self.selection_policy.find_next_pending_index(tasks, current_index + 1)
        pointer = len(tasks) if next_idx is None else next_idx
        role_pointers[role_name] = pointer
        return pointer
