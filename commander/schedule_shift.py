#!/usr/bin/env python3
"""Post-generation HH:MM shift so a tester can move the 09:00 workday."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from common import parse_hhmm_to_minute

ORIGIN_HOUR = 9
SCHEDULE_SHIFT_KEY = "_schedule_shift"
_TASKS_FILENAME = re.compile(r"^tasks_(\d{2})-(\d{2})\.json$", re.IGNORECASE)


def validate_base_time(value: int, *, origin: str = "base_time") -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{origin} must be an integer 0..23")
    if not 0 <= value <= 23:
        raise ValueError(f"{origin} must be an integer 0..23")
    return value


def shift_hhmm(time_text: str, hour_delta: int) -> str | None:
    """Return HH:MM with hours shifted modulo 24. Minutes are unchanged."""
    minute_of_day = parse_hhmm_to_minute(time_text)
    if minute_of_day is None:
        return None
    hour, minute = divmod(minute_of_day, 60)
    new_hour = (hour + int(hour_delta)) % 24
    return f"{new_hour:02d}:{minute:02d}"


def file_day_from_tasks_path(path: Path, *, today: date | None = None) -> str:
    """Best-effort ISO date from tasks_MM-DD.json using today's year."""
    today = today or date.today()
    match = _TASKS_FILENAME.match(path.name)
    if match is None:
        return today.isoformat()
    month = int(match.group(1))
    day = int(match.group(2))
    try:
        return date(today.year, month, day).isoformat()
    except ValueError:
        return today.isoformat()


def stamp_base_time(data: dict[str, Any], origin_hour: int) -> int:
    stamp = data.get(SCHEDULE_SHIFT_KEY)
    if isinstance(stamp, dict) and "base_time" in stamp:
        try:
            return validate_base_time(int(stamp["base_time"]), origin="stamp base_time")
        except (TypeError, ValueError):
            return origin_hour
    return origin_hour


def _stamp_file_day(data: dict[str, Any]) -> str | None:
    stamp = data.get(SCHEDULE_SHIFT_KEY)
    if not isinstance(stamp, dict):
        return None
    raw = stamp.get("file_day")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        date.fromisoformat(raw.strip())
    except ValueError:
        return None
    return raw.strip()


def apply_base_time_shift(
    data: dict[str, Any],
    base_time: int,
    *,
    origin_hour: int = ORIGIN_HOUR,
    file_day: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Rewrite role task ``time`` fields. Returns (payload, changed)."""
    requested = validate_base_time(base_time)
    origin = validate_base_time(origin_hour, origin="origin_hour")
    current = stamp_base_time(data, origin)
    existing_day = _stamp_file_day(data)
    resolved_day = file_day or existing_day

    if current == requested:
        if existing_day == resolved_day and isinstance(data.get(SCHEDULE_SHIFT_KEY), dict):
            return data, False
        if requested == origin and SCHEDULE_SHIFT_KEY not in data and resolved_day is None:
            return data, False
        out = dict(data)
        stamp: dict[str, Any] = {"origin_hour": origin, "base_time": requested}
        if resolved_day:
            stamp["file_day"] = resolved_day
        out[SCHEDULE_SHIFT_KEY] = stamp
        return out, True

    hour_delta = requested - current
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == SCHEDULE_SHIFT_KEY:
            continue
        if not isinstance(value, list):
            out[key] = value
            continue
        rows: list[Any] = []
        for item in value:
            if not isinstance(item, dict):
                rows.append(item)
                continue
            row = dict(item)
            raw = row.get("time")
            shifted = shift_hhmm(str(raw or ""), hour_delta)
            if shifted is not None:
                row["time"] = shifted
            rows.append(row)
        out[key] = rows

    stamp = {"origin_hour": origin, "base_time": requested}
    if resolved_day:
        stamp["file_day"] = resolved_day
    out[SCHEDULE_SHIFT_KEY] = stamp
    return out, True


def clock_wrap_day_offset(tasks: list[Any], index: int) -> int:
    """Count HH:MM backward jumps in ``tasks[0..index]`` (array order)."""
    offset = 0
    previous: int | None = None
    last = min(index, len(tasks) - 1)
    for cursor in range(last + 1):
        item = tasks[cursor]
        if not isinstance(item, dict):
            continue
        minute = parse_hhmm_to_minute(str(item.get("time") or ""))
        if minute is None:
            continue
        if previous is not None and minute < previous:
            offset += 1
        previous = minute
    return offset


def task_datetime_on_file_day(
    time_text: str,
    file_day: date,
    day_offset: int = 0,
) -> datetime | None:
    minute_of_day = parse_hhmm_to_minute(time_text)
    if minute_of_day is None:
        return None
    hour, minute = divmod(minute_of_day, 60)
    day = file_day + timedelta(days=int(day_offset))
    return datetime(day.year, day.month, day.day, hour, minute)


def shifted_window_end(data: dict[str, Any], file_day: date) -> datetime | None:
    """Latest task datetime on the shifted timeline, or None when empty."""
    latest: datetime | None = None
    for key, tasks in data.items():
        if key == SCHEDULE_SHIFT_KEY or not isinstance(tasks, list):
            continue
        for index, item in enumerate(tasks):
            if not isinstance(item, dict):
                continue
            parsed = task_datetime_on_file_day(
                str(item.get("time") or ""),
                file_day,
                clock_wrap_day_offset(tasks, index),
            )
            if parsed is None:
                continue
            if latest is None or parsed > latest:
                latest = parsed
    return latest


def shifted_window_still_active(
    data: dict[str, Any],
    file_day: date,
    now: datetime,
) -> bool:
    end = shifted_window_end(data, file_day)
    return end is not None and now < end


def resolve_active_task_day(
    load_day: Any,
    *,
    now: datetime | None = None,
) -> str:
    """Return the ISO date whose task file should be scanned/generated.

    ``load_day`` is ``repository.load_day`` (or any ``(date_str) -> dict``).
    """
    now = now or datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    try:
        data = load_day(yesterday.isoformat())
    except Exception:
        data = {}
    if isinstance(data, dict) and data:
        stamp_day = _stamp_file_day(data)
        try:
            file_day = date.fromisoformat(stamp_day) if stamp_day else yesterday
        except ValueError:
            file_day = yesterday
        if shifted_window_still_active(data, file_day, now):
            return yesterday.isoformat()
    return today.isoformat()
