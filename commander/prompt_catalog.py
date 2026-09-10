#!/usr/bin/env python3
"""Assemble structured generation prompts from prompt_resources catalogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from role_dependency_provider import compact_backward_for_prompt
except ImportError:
    from commander.role_dependency_provider import compact_backward_for_prompt

DEFAULT_PROMPT_RESOURCES_DIR = Path(__file__).resolve().parent / "prompt_resources"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def load_prompt_catalog(resources_dir: Path | None = None) -> dict[str, Any]:
    root = resources_dir or DEFAULT_PROMPT_RESOURCES_DIR
    domain = _load_json(root / "domain.json")
    templates = _load_json(root / "skill_templates.json")
    roles_dir = root / "roles"
    roles: dict[str, Any] = {}
    if roles_dir.is_dir():
        for path in sorted(roles_dir.glob("*.json")):
            payload = _load_json(path)
            role_name = str(payload.get("role") or path.stem).strip().lower()
            if role_name:
                roles[role_name] = payload
    return {"domain": domain, "skill_templates": templates, "roles": roles}


def _strip_examples(value: Any) -> Any:
    """Drop human-debug `example` keys so they never reach the generation LLM."""
    if isinstance(value, dict):
        return {key: _strip_examples(item) for key, item in value.items() if key != "example"}
    if isinstance(value, list):
        return [_strip_examples(item) for item in value]
    return value


def _skills_for_role(catalog: dict[str, Any], role: str) -> list[dict[str, Any]]:
    templates = catalog.get("skill_templates")
    if not isinstance(templates, dict):
        templates = {}
    role_info = catalog.get("roles", {}).get(role, {})
    names = role_info.get("skills") if isinstance(role_info, dict) else None
    if not isinstance(names, list) or not names:
        names = list(templates.keys())
    skills: list[dict[str, Any]] = []
    for name in names:
        if not isinstance(name, str):
            continue
        item = templates.get(name)
        entry: dict[str, Any] = {
            "catalog_use": (
                "FORMAT ONLY. Copy template/action/field grammar. "
                "Ignore this object's index. Do not copy its prose into a task."
            )
        }
        if isinstance(item, dict):
            stripped = _strip_examples(item)
            extra = dict(stripped) if isinstance(stripped, dict) else {"name": name}
            extra.pop("catalog_use", None)
            entry.update(extra)
        else:
            entry["name"] = name
        skills.append(entry)
    return skills


def assemble_generation_payload(
    *,
    role: str,
    task_count: int,
    schedule: list[str],
    backward: list[dict[str, Any]] | None = None,
    catalog: dict[str, Any] | None = None,
    resources_dir: Path | None = None,
    domain_fallback: str = "",
) -> dict[str, Any]:
    """Build json1: task_count, schedule, backward, plus role catalog fields."""
    loaded = catalog if catalog is not None else load_prompt_catalog(resources_dir)
    role_key = role.strip().lower()
    domain = loaded.get("domain") if isinstance(loaded.get("domain"), dict) else {}
    if not domain and domain_fallback.strip():
        domain = {"text": domain_fallback.strip()}
    role_info = loaded.get("roles", {}).get(role_key, {}) if isinstance(loaded.get("roles"), dict) else {}
    env = role_info.get("env") if isinstance(role_info, dict) else []
    if not isinstance(env, list):
        env = []
    schedule_list = list(schedule)
    return {
        "task_count": int(task_count),
        "schedule": schedule_list,
        "backward": compact_backward_for_prompt(backward),
        "role": role_key,
        "domain": domain,
        "duties": role_info.get("duties", "") if isinstance(role_info, dict) else "",
        "skill_catalog_contract": {
            "reference": "format",
            "use": [
                "template string",
                "action or op names",
                "required and optional field names",
                "key-omission and min_words rules",
            ],
            "do_not": [
                "walk skills[] in array order",
                "walk actions[] in listed order",
                "copy subjects, paths, names, bodies, queries, or other task content from the catalog or the format illustration",
            ],
        },
        "skills": _skills_for_role(loaded, role_key),
        "context": {
            "env": env,
        },
    }


def build_react_generation_messages(
    *,
    constraints_template: str,
    payload: dict[str, Any],
    retry_feedback: str = "",
) -> tuple[str, str]:
    """Return (system, user) messages for ReAct task generation."""
    role = str(payload.get("role") or "role")
    task_count = int(payload.get("task_count") or 0)
    schedule = payload.get("schedule") if isinstance(payload.get("schedule"), list) else []
    schedule_length = len(schedule) if schedule else int(payload.get("schedule_length") or task_count)
    system = constraints_template.strip()
    if not system:
        system = (
            "You generate office-role tasks. Reply in ReAct format. "
            "Thought: short plan. Action: Finish then one JSON object. "
            "Use only schedule times as object keys."
        )
    user_obj = dict(payload)
    user_lines = [
        f"Generate exactly {task_count} English task bodies for role '{role}'.",
        (
            f'len("{role}") == task_count == len(schedule) == {schedule_length}. '
            "Each item is {\"HH:MM\": \"<task>\"} using a time copied from schedule. "
            "The array must be strictly increasing by those time keys. Do not add is_load or other fields."
        ),
        "The skills array is an invocation-format catalog only. Do not follow its order. Do not copy its content.",
        "Avoid long runs of the same skill. A short related pair may sit together.",
        "Do not invent timestamps. Do not add extra items for backward replies; "
        "place a reply on a schedule time strictly later than that backward item's time. "
        "If no later schedule time exists, skip that response and use independent work.",
        "Return ReAct output only.",
        "",
        json.dumps(user_obj, ensure_ascii=False, indent=2),
    ]
    if retry_feedback.strip():
        user_lines.extend(["", "# Previous correction requirements", retry_feedback.strip()])
    return system, "\n".join(user_lines)
