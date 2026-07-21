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
    role: {role.lower(), f"{role.lower()}@ndrtest.local"}
    for role in ("hr", "manager", "programmer", "accountancy")
}
FIELD_PATTERNS = {
    field_name: re.compile(rf"\b{field_name}\s*:\s*([^,}}\r\n]*)", re.IGNORECASE)
    for field_name in ("recipient", "to", "cc")
}
SEND_EMAIL_ACTION_PATTERN = re.compile(r"\bsend\s+email\b", re.IGNORECASE)


def build_dependency_context(task_data: dict[str, Any], target_role: str) -> str:
    """Return structured dependency facts for the target role."""
    events = collect_dependency_events(task_data, target_role)
    if not events:
        return ""

    grouped: dict[str, list[str]] = {}
    for event in events:
        grouped.setdefault(event["source_role"], []).append(
            f"{event['time']} sent an email to {target_role}"
        )
    return (
        "Related dependency facts (for inferring implicit relationships and ordering only): "
        f"{json.dumps(grouped, ensure_ascii=False)}"
    )


def _task_snippet(text: str, max_len: int = 100) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _format_dependency_violation(
    *,
    target_role: str,
    candidate_index: int,
    candidate_time: str,
    candidate_task_text: str,
    dep_role: str,
    dep_index: int,
    dep_time: str,
    dep_task_text: str,
) -> str:
    """Return a human-readable dependency ordering failure for model retry prompts."""
    return (
        "Cross-role dependency order is invalid. Adjust only this role's task time or order "
        "to satisfy the dependency; do not change tasks already saved for other roles.\n"
        f"Dependency source: role '{dep_role}', existing task {dep_index + 1} "
        f"(array index {dep_index}), starts at {dep_time}.\n"
        f"Source task summary: {_task_snippet(dep_task_text)}.\n"
        f"Current candidate: role '{target_role}', candidate task {candidate_index + 1} "
        f"(array index {candidate_index}), starts at {candidate_time}.\n"
        f"Candidate task summary: {_task_snippet(candidate_task_text)}.\n"
        f"Reason: the candidate starts at {candidate_time}, which is earlier than or equal to "
        f"the dependency source task's start time of {dep_time}; it must start after that email task.\n"
        f"Required constraint: this '{target_role}' task must start strictly later than {dep_time}, "
        "remain strictly ordered with the role's other tasks, and stay within the permitted work period."
    )


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
        dep_idx = int(earliest.get("source_task_index", -1))
        reason = _format_dependency_violation(
            target_role=target_role,
            candidate_index=index,
            candidate_time=str(task.get("time", "")),
            candidate_task_text=task_text,
            dep_role=str(earliest.get("source_role", "")),
            dep_index=dep_idx,
            dep_time=str(earliest.get("time", "")),
            dep_task_text=str(earliest.get("task", "")),
        )
        return False, reason

    return True, None


def collect_dependency_events(task_data: dict[str, Any], target_role: str) -> list[dict[str, Any]]:
    """Collect email events from other roles that may affect the target role."""
    events: list[dict[str, Any]] = []
    for source_role, tasks in task_data.items():
        if not isinstance(source_role, str) or source_role == target_role:
            continue
        if not isinstance(tasks, list):
            continue
        for source_task_index, task in enumerate(tasks):
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
            event["source_task_index"] = source_task_index
            events.append(event)
    events.sort(key=lambda item: item["minute"])
    return events


def _extract_email_event(
    source_role: str,
    target_role: str,
    task_text: str,
    time_text: str,
) -> dict[str, Any] | None:
    lowered = task_text.lower()
    if "exchange-use skill" not in lowered or SEND_EMAIL_ACTION_PATTERN.search(task_text) is None:
        return None

    recipients = set()
    for field_name in FIELD_PATTERNS:
        recipients.update(_extract_field_values(task_text, field_name))
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
