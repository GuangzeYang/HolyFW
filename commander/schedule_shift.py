#!/usr/bin/env python3
"""Post-generation HH:MM shift so a tester can move the 09:00 workday.

Compatibility shim: the implementation now lives in ``common/schedule_shift``.
This module re-exports every public name so existing commander callers (and
their ``from schedule_shift import ...`` / ``from commander.schedule_shift import
...`` fallback imports) keep working unchanged.
"""

from common.schedule_shift import (
    ORIGIN_HOUR,
    SCHEDULE_SHIFT_KEY,
    apply_base_time_shift,
    clock_wrap_day_offset,
    file_day_from_tasks_path,
    resolve_active_task_day,
    shift_hhmm,
    shifted_window_end,
    shifted_window_still_active,
    stamp_base_time,
    task_datetime_on_file_day,
    validate_base_time,
)

__all__ = [
    "ORIGIN_HOUR",
    "SCHEDULE_SHIFT_KEY",
    "apply_base_time_shift",
    "clock_wrap_day_offset",
    "file_day_from_tasks_path",
    "resolve_active_task_day",
    "shift_hhmm",
    "shifted_window_end",
    "shifted_window_still_active",
    "stamp_base_time",
    "task_datetime_on_file_day",
    "validate_base_time",
]
