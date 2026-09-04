#!/usr/bin/env python3
"""Application service for role task scanning and dispatch."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from common import existing_task_id, new_task_id

try:
    from logging_setup import log_extra
    from policies import PendingSelectionPolicy, task_needs_dispatch
    from repository import DailyTaskRepository
except ImportError:
    from commander.logging_setup import log_extra
    from commander.policies import PendingSelectionPolicy, task_needs_dispatch
    from commander.repository import DailyTaskRepository


class TaskScanService:
    """Scan role task lists and dispatch eligible tasks using injected dependencies."""

    def __init__(
        self,
        repository: DailyTaskRepository,
        selection_policy: PendingSelectionPolicy,
        dispatch_task: Callable[..., Any],
        max_dispatch_lateness_minutes: int = 6,
        debug: bool = False,
    ):
        self.repository = repository
        self.selection_policy = selection_policy
        self.dispatch_task = dispatch_task
        self.max_dispatch_lateness_minutes = max_dispatch_lateness_minutes
        self.debug = debug

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

            try:
                file_day = date.fromisoformat(date_str)
            except ValueError:
                file_day = date.today()

            while pointer < len(tasks):
                pointer = self._rewind_pointer_if_earlier_pending(role_key, tasks, pointer, role_pointers)
                if pointer >= len(tasks):
                    break

                if self.repository.has_active_waiting_task(role_key, date_str):
                    logging.debug(
                        "Waiting task present, pausing pointer at index %s",
                        pointer,
                        extra=log_extra(role_key, pointer),
                    )
                    break

                task = tasks[pointer]
                if not isinstance(task, dict):
                    logging.debug(
                        "Invalid task format, skipping",
                        extra=log_extra(role_key, pointer),
                    )
                    pointer += 1
                    role_pointers[role_key] = pointer
                    continue

                if not task_needs_dispatch(task):
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                    continue

                now = datetime.now()
                task_time_raw = task.get("time")
                task_time = self._parse_task_datetime(
                    task_time_raw if isinstance(task_time_raw, str) else "",
                    file_day=file_day,
                    day_offset=self._clock_wrap_day_offset(tasks, pointer),
                )
                if task_time is None:
                    logging.debug(
                        "Invalid task time, skipping",
                        extra=log_extra(role_key, pointer),
                    )
                    task["is_load"] = True
                    self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        {"is_load": True},
                        only_if_unissued=True,
                    )
                    pointer += 1
                    role_pointers[role_key] = pointer
                    continue

                # Future tasks wait for their planned time.
                if task_time > now:
                    break

                # Debug mode limits dispatches to the recent lateness window.
                if self.debug and task_time < now - timedelta(
                    minutes=self.max_dispatch_lateness_minutes
                ):
                    reason = (
                        f"Missed dispatch window (>{self.max_dispatch_lateness_minutes} minutes late); "
                        f"planned={task.get('time')} now={now.strftime('%H:%M:%S')}"
                    )
                    logging.debug(
                        "Expiring overdue task: %s",
                        reason,
                        extra=log_extra(role_key, pointer),
                    )
                    fields = {
                        "is_load": True,
                        "status": "failed",
                        "exit_code": -1,
                        "report_message": reason,
                        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                    self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        fields,
                        only_if_unissued=True,
                    )
                    task.update(fields)
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                    continue

                task_text_value = task.get("task")
                task_text = task_text_value if isinstance(task_text_value, str) else ""

                # Reading task marks is_load=True before dispatch.
                marked_loaded = False
                if not task.get("is_load", False):
                    marked_loaded = self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        {"is_load": True},
                        only_if_unissued=True,
                    )
                    latest = self.repository.get_task_by_index(date_str, role_key, pointer)
                    if latest is not None:
                        task.update(latest)
                    if not marked_loaded and not task_needs_dispatch(task):
                        pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                        continue

                task_id = existing_task_id(task.get("task_id")) or new_task_id()
                if task.get("task_id") != task_id:
                    self.repository.update_task_fields_by_index(
                        date_str,
                        role_key,
                        pointer,
                        {"task_id": task_id},
                    )
                    task["task_id"] = task_id
                extras = log_extra(role_key, pointer)
                logging.info("Running — %s", task_id, extra=extras)
                outcome = self.dispatch_task(
                    role_key,
                    task_text,
                    task.get("time"),
                    task_id=task_id,
                    role_index=pointer,
                )
                success = bool(outcome)
                if success:
                    # Prevent stale in-memory snapshot from selecting this task again.
                    task["status"] = "waiting"
                    resolved_id = str(getattr(outcome, "task_id", "") or task_id)
                    task["task_id"] = resolved_id or "__dispatched__"
                    if not task.get("issued_at"):
                        task["issued_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer, role_pointers)
                    # Dispatch at most one task per role in a scan pass.
                    break

                # On dispatch failure, keep pointer unchanged for retry.
                if marked_loaded:
                    task["is_load"] = False
                outcome_status = str(getattr(outcome, "status", "failed"))
                outcome_error = str(getattr(outcome, "error", "") or "")
                failure_message = (
                    f"Dispatch {outcome_status} at {datetime.now().isoformat(timespec='seconds')}"
                )
                if outcome_error:
                    failure_message += f": {outcome_error}"
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
                    only_if_unissued=True,
                )
                break

    def _clock_wrap_day_offset(self, tasks: list[dict[str, Any]], index: int) -> int:
        try:
            from schedule_shift import clock_wrap_day_offset
        except ImportError:
            from commander.schedule_shift import clock_wrap_day_offset
        return clock_wrap_day_offset(tasks, index)

    def _parse_task_datetime(
        self,
        task_time_str: str,
        now: datetime | None = None,
        *,
        file_day: date | None = None,
        day_offset: int = 0,
    ) -> datetime | None:
        if not isinstance(task_time_str, str) or ":" not in task_time_str:
            return None
        try:
            hour, minute = map(int, task_time_str.split(":", 1))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            if file_day is None:
                anchor = (now or datetime.now()).date()
            else:
                anchor = file_day
            day = anchor + timedelta(days=int(day_offset))
            return datetime(day.year, day.month, day.day, hour, minute)
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
            logging.debug(
                "Rewinding pointer: %s -> %s",
                pointer,
                earliest_idx,
                extra=log_extra(role_name, earliest_idx),
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
