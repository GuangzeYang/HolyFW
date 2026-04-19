#!/usr/bin/env python3
"""Task file repository for unified daily tasks_MM-DD.json storage."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from common import load_json_file, parse_task_ref, save_json_atomic, tasks_path

try:
    from runtime_config import get_storage_config, load_runtime_config
except ImportError:
    from commander.runtime_config import get_storage_config, load_runtime_config

try:
    from domain import apply_report_transition, move_to_waiting
except ImportError:
    from commander.domain import apply_report_transition, move_to_waiting


class DailyTaskRepository:
    """Repository for daily task file reads/writes with file locking."""

    def __init__(
        self,
        data_dir: Path,
        lock_timeout: int | None = None,
        max_store_text: int | None = None,
    ):
        if lock_timeout is None or max_store_text is None:
            runtime_config = load_runtime_config()
            storage_config = get_storage_config(runtime_config)
            if lock_timeout is None:
                lock_timeout = storage_config["lock_timeout_seconds"]
            if max_store_text is None:
                max_store_text = storage_config["max_store_text"]

        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_timeout = lock_timeout
        self.max_store_text = max_store_text

    def day_path(self, date_str: str) -> Path:
        return tasks_path(self.data_dir, date_str)

    def load_day(self, date_str: str) -> dict[str, Any]:
        return load_json_file(self.day_path(date_str))

    def save_day(self, date_str: str, data: dict[str, Any]) -> None:
        save_json_atomic(self.day_path(date_str), data)

    def has_active_waiting_task(self, role: str, date_str: str | None = None) -> bool:
        date_key = date_str or date.today().isoformat()
        path = self.day_path(date_key)
        if not path.exists():
            return False

        lock_path = str(path) + ".lock"
        try:
            with FileLock(lock_path, timeout=self.lock_timeout):
                data = load_json_file(path)
        except Exception:
            return False

        tasks = data.get(role)
        if not isinstance(tasks, list):
            return False

        now = datetime.now().astimezone()
        for item in tasks:
            if not isinstance(item, dict) or item.get("status") != "waiting":
                continue
            expiry_time_str = item.get("expiry_time")
            if not expiry_time_str:
                continue
            try:
                expiry_time = datetime.fromisoformat(expiry_time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if now < expiry_time:
                return True
        return False

    def bind_dispatched_task(
        self,
        date_str: str,
        role: str,
        task_id: str,
        task_text: str,
        expiry_time: str,
        planned_time: str | None = None,
    ) -> None:
        path = self.day_path(date_str)
        lock_path = str(path) + ".lock"
        with FileLock(lock_path, timeout=self.lock_timeout):
            data = load_json_file(path)
            tasks = data.get(role)
            if not isinstance(tasks, list):
                tasks = []
                data[role] = tasks

            matched: dict[str, Any] | None = None
            for item in tasks:
                if not isinstance(item, dict):
                    continue
                if not item.get("is_load", False):
                    continue
                if item.get("task_id"):
                    continue
                if planned_time and item.get("time") != planned_time:
                    continue
                if item.get("task") != task_text:
                    continue
                matched = item
                break

            if matched is None:
                for item in tasks:
                    if not isinstance(item, dict):
                        continue
                    if not item.get("is_load", False):
                        continue
                    if item.get("task_id"):
                        continue
                    if planned_time and item.get("time") != planned_time:
                        continue
                    matched = item
                    break

            if matched is None:
                matched = {
                    "time": planned_time or "",
                    "is_load": True,
                    "task": task_text,
                }
                tasks.append(matched)

            issued = datetime.now().astimezone().isoformat()
            matched["task_id"] = task_id
            if task_text:
                matched["task"] = task_text
            allowed, next_status = move_to_waiting(matched.get("status"))
            if not allowed:
                raise ValueError(
                    f"Invalid status transition to waiting for role={role}, task_id={task_id}"
                )
            matched["status"] = next_status
            matched["issued_at"] = issued
            matched["expiry_time"] = expiry_time
            save_json_atomic(path, data)

    def get_task_by_index(self, date_str: str, role: str, index: int) -> dict[str, Any] | None:
        """Read one task by role/index under lock."""
        path = self.day_path(date_str)
        lock_path = str(path) + ".lock"
        with FileLock(lock_path, timeout=self.lock_timeout):
            data = load_json_file(path)
            tasks = data.get(role)
            if not isinstance(tasks, list):
                return None
            if index < 0 or index >= len(tasks):
                return None
            item = tasks[index]
            if not isinstance(item, dict):
                return None
            return dict(item)

    def update_task_fields_by_index(
        self,
        date_str: str,
        role: str,
        index: int,
        fields: dict[str, Any],
        *,
        only_if_no_task_id: bool = False,
    ) -> bool:
        """Atomically update one task by role/index and save when changed."""
        path = self.day_path(date_str)
        lock_path = str(path) + ".lock"
        with FileLock(lock_path, timeout=self.lock_timeout):
            data = load_json_file(path)
            tasks = data.get(role)
            if not isinstance(tasks, list):
                return False
            if index < 0 or index >= len(tasks):
                return False
            item = tasks[index]
            if not isinstance(item, dict):
                return False
            if only_if_no_task_id and item.get("task_id"):
                return False

            changed = False
            for key, value in fields.items():
                if item.get(key) != value:
                    item[key] = value
                    changed = True

            if not changed:
                return False

            save_json_atomic(path, data)
            return True

    def update_task_report(
        self,
        task_ref: str,
        status: str,
        message: str | None,
        exit_code: int | None,
        stdout: str | None,
        stderr: str | None,
    ) -> dict[str, Any]:
        parsed, err = parse_task_ref(task_ref)
        if err:
            return {"ok": False, "error": err}
        if status not in ("successed", "failed"):
            return {"ok": False, "error": "status must be successed or failed"}

        assert parsed is not None
        date_str, role, task_id = parsed
        path = self.day_path(date_str)
        lock_path = str(path) + ".lock"

        with FileLock(lock_path, timeout=self.lock_timeout):
            data = load_json_file(path)
            if role not in data:
                return {"ok": False, "error": f"Role does not exist: {role}"}
            tasks = data[role]
            if not isinstance(tasks, list):
                return {"ok": False, "error": f"Task list format error under role {role}"}

            found = False
            for item in tasks:
                if isinstance(item, dict) and item.get("task_id") == task_id:
                    allowed, next_status = apply_report_transition(item.get("status"), status)
                    if not allowed:
                        current_status = item.get("status")
                        return {
                            "ok": False,
                            "error": (
                                f"Invalid status transition for task {task_id}: "
                                f"{current_status} -> {status}"
                            ),
                        }
                    item["status"] = next_status
                    item["completed_at"] = datetime.now().astimezone().isoformat()
                    if message is not None:
                        item["report_message"] = message
                    if exit_code is not None:
                        item["exit_code"] = exit_code
                    if stdout is not None:
                        item["stdout"] = self._truncate(stdout)
                    if stderr is not None:
                        item["stderr"] = self._truncate(stderr)
                    found = True
                    break

            if not found:
                return {"ok": False, "error": f"Task not found: {task_id}"}
            save_json_atomic(path, data)

        return {"ok": True}

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_store_text:
            return text
        return text[: self.max_store_text - 20] + "\n...[truncated]"
