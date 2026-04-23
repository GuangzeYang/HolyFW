#!/usr/bin/env python3
"""Optional cross-role dependency extraction for task generation."""

from __future__ import annotations

import json
import re
from typing import Any

try:
    from common import parse_hhmm_to_minute
except ImportError:
    from common import parse_hhmm_to_minute

ROLE_EMAIL_ALIASES = {
    role: {role.lower(), f"{role.lower()}@edrtest.local"}
    for role in ("hr", "manager", "programmer", "accountancy")
}
FIELD_PATTERNS = {
    "\u6536\u4ef6\u4eba": re.compile(r"\u6536\u4ef6\u4eba[:\uff1a]([^\uff0c}\n]*)"),
    "\u6284\u9001": re.compile(r"\u6284\u9001[:\uff1a]([^\uff0c}\n]*)"),
}


def build_dependency_context(task_data: dict[str, Any], target_role: str) -> str:
    """Return structured dependency facts for the target role."""
    events = collect_dependency_events(task_data, target_role)
    if not events:
        return ""

    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(event["source_role"], []).append(f"{event['time']} \u5411 {target_role} \u53d1\u9001\u90ae\u4ef6")
    return f"\u5173\u8054\u4f9d\u8d56\u4e8b\u5b9e\uff08\u4ec5\u7528\u4e8e\u63a8\u65ad\u9690\u5f0f\u5173\u8054\u548c\u524d\u540e\u65f6\u5e8f\uff09\uff1a {json.dumps(grouped, ensure_ascii=False)}"



def validate_dependency_order(
    task_data: dict[str, Any],
    target_role: str,
    candidate_tasks: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    """Reject candidate tasks that appear before the related upstream action exists."""
    events = collect_dependency_events(task_data, target_role)
    if not events:
        return True, None

    for index, task in enumerate(candidate_tasks):
        if not isinstance(task, dict):
            continue
        task_text = task.get("task")
        if not isinstance(task_text, str) or not task_text.strip():
            continue
        minute = parse_hhmm_to_minute(task.get("time"))
        if minute is None:
            continue

        matched = [event for event in events if _candidate_task_matches_event(task_text, event)]
        if not matched:
            continue
        if any(event["minute"] < minute for event in matched):
            continue

        earliest = min(matched, key=lambda item: item["minute"])
        return (
            False,
            f"Role '{target_role}' task#{index} depends on {earliest['source_role']} action at {earliest['time']} but is scheduled at {task.get('time')}",
        )

    return True, None



def collect_dependency_events(task_data: dict[str, Any], target_role: str) -> list[dict[str, Any]]:
    """Collect email events from other roles that may affect the target role."""
    events: list[dict[str, Any]] = []
    for source_role, tasks in task_data.items():
        if not isinstance(source_role, str) or source_role == target_role:
            continue
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_text = task.get("task")
            if not isinstance(task_text, str) or not task_text.strip():
                continue
            minute = parse_hhmm_to_minute(task.get("time"))
            if minute is None:
                continue
            event = _extract_email_event(source_role, target_role, task_text, str(task.get("time")))
            if event is None:
                continue
            event["minute"] = minute
            events.append(event)
    events.sort(key=lambda item: item["minute"])
    return events



def _extract_email_event(source_role: str, target_role: str, task_text: str, time_text: str) -> dict[str, Any] | None:
    lowered = task_text.lower()
    if "exchange-use skill" not in lowered or "\u53d1\u9001\u90ae\u4ef6" not in task_text:
        return None

    recipients = set()
    recipients.update(_extract_field_values(task_text, "\u6536\u4ef6\u4eba"))
    recipients.update(_extract_field_values(task_text, "\u6284\u9001"))
    aliases = ROLE_EMAIL_ALIASES.get(target_role, {target_role.lower()})
    if not any(alias in recipients for alias in aliases):
        return None

    return {
        "relation": "email",
        "source_role": source_role,
        "time": time_text,
        "task": task_text,
        "aliases": tuple(sorted(ROLE_EMAIL_ALIASES.get(source_role, {source_role.lower()}))),
    }



def _candidate_task_matches_event(task_text: str, event: dict[str, Any]) -> bool:
    lowered = task_text.lower()
    if event.get("relation") != "email":
        return False
    if "exchange-use skill" not in lowered:
        return False
    return any(alias in lowered for alias in event.get("aliases", ()))



def _extract_field_values(task_text: str, field_name: str) -> list[str]:
    pattern = FIELD_PATTERNS[field_name]
    matches = [value.strip().lower() for value in pattern.findall(task_text)]
    return [value for value in matches if value]
