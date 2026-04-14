#!/usr/bin/env python3
"""Domain rules for task status transitions."""

from __future__ import annotations

from typing import Final

STATUS_PLANNED: Final[str] = "planned"
STATUS_WAITING: Final[str] = "waiting"
STATUS_SUCCESSED: Final[str] = "successed"
STATUS_FAILED: Final[str] = "failed"

_TERMINAL_STATUSES: Final[set[str]] = {STATUS_SUCCESSED, STATUS_FAILED}


def normalize_status(value: object) -> str:
    """Normalize unknown status values into a known baseline."""
    if isinstance(value, str) and value in {
        STATUS_PLANNED,
        STATUS_WAITING,
        STATUS_SUCCESSED,
        STATUS_FAILED,
    }:
        return value
    return STATUS_PLANNED


def can_transition(current: str, target: str) -> bool:
    """Return whether status transition is legal."""
    if current == target:
        return True
    if current == STATUS_PLANNED and target == STATUS_WAITING:
        return True
    if current == STATUS_WAITING and target in _TERMINAL_STATUSES:
        return True
    return False


def move_to_waiting(current: object) -> tuple[bool, str]:
    """Validate transition into waiting state."""
    normalized = normalize_status(current)
    if can_transition(normalized, STATUS_WAITING):
        return True, STATUS_WAITING
    return False, normalized


def apply_report_transition(current: object, report_status: str) -> tuple[bool, str]:
    """Validate transition into successed/failed when processing reports."""
    normalized = normalize_status(current)
    if report_status not in {STATUS_SUCCESSED, STATUS_FAILED}:
        return False, normalized
    if can_transition(normalized, report_status):
        return True, report_status
    return False, normalized
