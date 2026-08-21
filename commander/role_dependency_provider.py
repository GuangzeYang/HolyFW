#!/usr/bin/env python3
"""Cross-role backward facts for sequential task generation."""

from __future__ import annotations

import re
from typing import Any

try:
    from common import parse_hhmm_to_minute
except ImportError:
    from common import parse_hhmm_to_minute

OFFICE_ROLES = ("hr", "manager", "programmer", "accountancy")
ROLE_EMAIL_ALIASES = {
    role: {role.lower(), f"{role.lower()}@ndrtest.local"}
    for role in OFFICE_ROLES
}
FIELD_PATTERNS = {
    field_name: re.compile(rf"\b{field_name}\s*:\s*([^,}}\r\n]*)", re.IGNORECASE)
    for field_name in ("recipient", "to", "cc")
}
SEND_EMAIL_ACTION_PATTERN = re.compile(r"\bsend\s+email\b", re.IGNORECASE)
REPLY_OR_VIEW_EMAIL_PATTERN = re.compile(
    r"\b(reply(?:\s+to\s+email|\s+all)?|view email|forward)\b",
    re.IGNORECASE,
)
EMAIL_RESPONSE_PATTERN = re.compile(
    r"\b(reply(?:\s+to\s+email|\s+all)?|view email|review email|forward)\b",
    re.IGNORECASE,
)
ODOO_EMAIL_ADDRESS_FIELD_PATTERN = re.compile(
    r"\bemail address\s*:\s*[^,}]+",
    re.IGNORECASE,
)
SMB_PUBLIC_OR_EXCHANGE_PATTERN = re.compile(
    r"Company_Data\\(Public|Exchange)|/Company_Data/(Public|Exchange)",
    re.IGNORECASE,
)
ROLE_TOKEN_PATTERN = {
    role: re.compile(rf"\b{re.escape(role)}\b", re.IGNORECASE) for role in OFFICE_ROLES
}
RESPONSE_ACTIONS_BY_RELATION = {
    "email": ("reply", "reply all", "view email", "review email", "forward"),
    "smb": ("smb-access",),
    "named": ("odoo-use",),
}


def collect_backward_events(task_data: dict[str, Any], target_role: str) -> list[dict[str, Any]]:
    """Collect prior-role tasks that involve the current role at exactly one endpoint."""
    target = target_role.strip().lower()
    events: list[dict[str, Any]] = []
    for source_role, tasks in task_data.items():
        if not isinstance(source_role, str) or source_role.strip().lower() == target:
            continue
        if not isinstance(tasks, list):
            continue
        for source_task_index, task in enumerate(tasks):
            if not isinstance(task, dict):
                continue
            task_text = task.get("task")
            if not isinstance(task_text, str) or not task_text.strip():
                continue
            time_text = str(task.get("time") or "")
            minute = parse_hhmm_to_minute(time_text)
            if minute is None:
                continue
            event = _extract_related_event(source_role.strip().lower(), target, task_text, time_text)
            if event is None:
                continue
            event["minute"] = minute
            event["source_task_index"] = source_task_index
            events.append(event)
    events.sort(key=lambda item: item["minute"])
    return events


def build_backward_items(
    task_data: dict[str, Any],
    target_role: str,
    schedule: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return JSON-serializable backward facts for the generation prompt."""
    items: list[dict[str, Any]] = []
    for event in collect_backward_events(task_data, target_role):
        relation = str(event.get("relation") or "")
        item: dict[str, Any] = {
            "from": list(event["from_roles"]),
            "to": list(event["to_roles"]),
            "time": event["time"],
            "task": event["task"],
            "relation": relation,
            "response_actions": list(RESPONSE_ACTIONS_BY_RELATION.get(relation, ())),
        }
        if schedule is not None:
            forbidden_indices, allowed_indices, forbidden_times, allowed_times = (
                _partition_schedule_slots(schedule, int(event["minute"]))
            )
            item["forbidden_slot_indices"] = forbidden_indices
            item["forbidden_times"] = forbidden_times
            item["allowed_slot_indices"] = allowed_indices
            item["allowed_times"] = allowed_times
        items.append(item)
    return items


def build_dependency_context(
    task_data: dict[str, Any],
    target_role: str,
    schedule: list[str] | None = None,
) -> str:
    """Return a compact JSON string of backward facts, or empty when none exist."""
    items = build_backward_items(task_data, target_role, schedule)
    if not items:
        return ""
    import json

    return json.dumps({"backward": items}, ensure_ascii=False)


def validate_dependency_order(
    task_data: dict[str, Any],
    target_role: str,
    candidate_tasks: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    """Reject response tasks whose zipped time is not strictly after the source."""
    events = collect_backward_events(task_data, target_role)
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
        later = [event for event in matched if event["minute"] < minute]
        if later:
            continue
        earliest = min(matched, key=lambda item: item["minute"])
        return False, _format_dependency_violation(
            target_role=target_role,
            candidate_index=index,
            candidate_time=str(task.get("time", "")),
            candidate_task_text=task_text,
            dep_role=str(earliest.get("source_role", "")),
            dep_index=int(earliest.get("source_task_index", -1)),
            dep_time=str(earliest.get("time", "")),
            dep_task_text=str(earliest.get("task", "")),
        )
    return True, None


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
    return (
        "Cross-role dependency order is invalid. Adjust only this role's task content "
        "so responses occupy later schedule slots; do not change tasks already saved for other roles.\n"
        f"Dependency source: role '{dep_role}', existing task {dep_index + 1} "
        f"(array index {dep_index}), starts at {dep_time}.\n"
        f"Source task summary: {_task_snippet(dep_task_text)}.\n"
        f"Current candidate: role '{target_role}', candidate task {candidate_index + 1} "
        f"(array index {candidate_index}), starts at {candidate_time}.\n"
        f"Candidate task summary: {_task_snippet(candidate_task_text)}.\n"
        f"Reason: the candidate starts at {candidate_time}, which is earlier than or equal to "
        f"the dependency source task's start time of {dep_time}; it must start after that related task.\n"
        f"Required constraint: this '{target_role}' response must start strictly later than {dep_time}, "
        "or the slot must be filled with independent work that is not a response to that source task."
    )


def _partition_schedule_slots(
    schedule: list[str],
    source_minute: int,
) -> tuple[list[int], list[int], list[str], list[str]]:
    """Split this role's schedule into slots at/before the source vs strictly later."""
    forbidden_indices: list[int] = []
    allowed_indices: list[int] = []
    forbidden_times: list[str] = []
    allowed_times: list[str] = []
    for index, time_text in enumerate(schedule):
        if not isinstance(time_text, str):
            continue
        minute = parse_hhmm_to_minute(time_text)
        if minute is None:
            continue
        if minute <= source_minute:
            forbidden_indices.append(index)
            forbidden_times.append(time_text)
        else:
            allowed_indices.append(index)
            allowed_times.append(time_text)
    return forbidden_indices, allowed_indices, forbidden_times, allowed_times


def _task_snippet(text: str, max_len: int = 100) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _extract_related_event(
    source_role: str,
    target_role: str,
    task_text: str,
    time_text: str,
) -> dict[str, Any] | None:
    email_event = _extract_email_event(source_role, target_role, task_text, time_text)
    if email_event is not None:
        return email_event
    smb_event = _extract_smb_event(source_role, target_role, task_text, time_text)
    if smb_event is not None:
        return smb_event
    return _extract_named_role_event(source_role, target_role, task_text, time_text)


def _extract_email_event(
    source_role: str,
    target_role: str,
    task_text: str,
    time_text: str,
) -> dict[str, Any] | None:
    lowered = task_text.lower()
    if "exchange-use skill" not in lowered:
        return None
    if SEND_EMAIL_ACTION_PATTERN.search(task_text) is None and REPLY_OR_VIEW_EMAIL_PATTERN.search(task_text) is None:
        return None
    recipients = set()
    for field_name in FIELD_PATTERNS:
        recipients.update(_extract_field_values(task_text, field_name))
    aliases = ROLE_EMAIL_ALIASES.get(target_role, {target_role.lower()})
    if not any(alias in recipients for alias in aliases):
        return None
    from_roles = [source_role]
    to_roles = [target_role]
    if not _one_endpoint_is_target(from_roles, to_roles, target_role):
        return None
    return {
        "relation": "email",
        "source_role": source_role,
        "from_roles": tuple(from_roles),
        "to_roles": tuple(to_roles),
        "time": time_text,
        "task": task_text,
        "aliases": tuple(sorted(ROLE_EMAIL_ALIASES.get(source_role, {source_role.lower()}))),
    }


def _extract_smb_event(
    source_role: str,
    target_role: str,
    task_text: str,
    time_text: str,
) -> dict[str, Any] | None:
    if "smb-access skill" not in task_text.lower():
        return None
    if SMB_PUBLIC_OR_EXCHANGE_PATTERN.search(task_text) is None:
        return None
    mentioned = _mentioned_roles(task_text)
    if target_role not in mentioned and target_role not in task_text.lower():
        return None
    from_roles = [source_role]
    to_roles = [target_role]
    if not _one_endpoint_is_target(from_roles, to_roles, target_role):
        return None
    return {
        "relation": "smb",
        "source_role": source_role,
        "from_roles": tuple(from_roles),
        "to_roles": tuple(to_roles),
        "time": time_text,
        "task": task_text,
        "aliases": tuple(sorted(ROLE_EMAIL_ALIASES.get(source_role, {source_role.lower()}))),
    }


def _extract_named_role_event(
    source_role: str,
    target_role: str,
    task_text: str,
    time_text: str,
) -> dict[str, Any] | None:
    if "odoo-use" not in task_text.lower():
        return None
    mentioned = _mentioned_roles(task_text)
    if target_role not in mentioned:
        return None
    from_roles = [source_role]
    to_roles = [target_role]
    if not _one_endpoint_is_target(from_roles, to_roles, target_role):
        return None
    return {
        "relation": "named",
        "source_role": source_role,
        "from_roles": tuple(from_roles),
        "to_roles": tuple(to_roles),
        "time": time_text,
        "task": task_text,
        "aliases": tuple(sorted(ROLE_EMAIL_ALIASES.get(source_role, {source_role.lower()}))),
    }


def _one_endpoint_is_target(from_roles: list[str], to_roles: list[str], target_role: str) -> bool:
    in_from = target_role in from_roles
    in_to = target_role in to_roles
    return in_from != in_to


def _mentioned_roles(task_text: str) -> set[str]:
    found: set[str] = set()
    # Odoo "email address" is an application alias, not a message to that mailbox.
    scanned = ODOO_EMAIL_ADDRESS_FIELD_PATTERN.sub(" ", task_text)
    lowered = scanned.lower()
    for role, pattern in ROLE_TOKEN_PATTERN.items():
        if pattern.search(scanned) or f"{role}@ndrtest.local" in lowered:
            found.add(role)
    return found


def _candidate_task_matches_event(task_text: str, event: dict[str, Any]) -> bool:
    lowered = task_text.lower()
    relation = event.get("relation")
    aliases = event.get("aliases", ())
    has_source_alias = any(alias in lowered for alias in aliases)
    if relation == "email":
        if "exchange-use skill" not in lowered:
            return False
        if EMAIL_RESPONSE_PATTERN.search(task_text) is None:
            return False
        if re.search(r"\b(reply(?:\s+to\s+email|\s+all)?|forward)\b", task_text, re.IGNORECASE):
            return True
        return has_source_alias
    if relation == "smb":
        return "smb-access skill" in lowered
    if "odoo-use" not in lowered:
        return False
    return has_source_alias


def _extract_field_values(task_text: str, field_name: str) -> list[str]:
    pattern = FIELD_PATTERNS[field_name]
    matches = [value.strip().lower() for value in pattern.findall(task_text)]
    return [value for value in matches if value]
