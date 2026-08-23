#!/usr/bin/env python3
"""Task selection policies for scanner flow."""

from __future__ import annotations

from typing import Any, Protocol


class PendingSelectionPolicy(Protocol):
    """Policy interface for selecting next pending task index."""

    def find_next_pending_index(self, tasks: list[dict[str, Any]], start_index: int = 0) -> int | None:
        ...


def task_is_issued(task: dict[str, Any]) -> bool:
    """Return True when a task has already been dispatched (or finished)."""
    if not isinstance(task, dict):
        return False
    status = task.get("status")
    if status in ("waiting", "successed", "failed"):
        return True
    return bool(task.get("issued_at"))


def task_needs_dispatch(task: dict[str, Any]) -> bool:
    """Return True when task still requires dispatch."""
    if not isinstance(task, dict):
        return False
    if task_is_issued(task):
        return False
    if not task.get("is_load", False):
        return True
    status = task.get("status")
    return status in (None, "", "planned")


class EarliestPendingSelectionPolicy:
    """Default policy: choose earliest pending task from the pointer onward."""

    def find_next_pending_index(self, tasks: list[dict[str, Any]], start_index: int = 0) -> int | None:
        for idx in range(max(0, start_index), len(tasks)):
            task = tasks[idx]
            if isinstance(task, dict) and task_needs_dispatch(task):
                return idx
        return None
