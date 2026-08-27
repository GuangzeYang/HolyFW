"""Fill empty attacker task slots in batches of five via the model client."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from common import extract_react_finish_json, repair_json_text
from common.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError

from attacker.task_file import completed_task_texts, empty_slot_indices

DEFAULT_BATCH_SIZE = 5
ATTACKER_PACKAGE_DIR = Path(__file__).resolve().parent

FillClient = AgentRequestABC
logger = logging.getLogger(__name__)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_package_file(*relative: str) -> Path | None:
    candidate = ATTACKER_PACKAGE_DIR.joinpath(*relative)
    if candidate.is_file():
        return candidate
    return None


def resolve_generator_system_path() -> Path | None:
    return resolve_package_file("generator_system.md")


def resolve_prompt_template_path() -> Path | None:
    return resolve_package_file("attacker_prompt_template.md")


def resolve_state_json_path() -> Path | None:
    installed = Path.home() / ".config" / "opencode" / "skills" / "ad-attack" / "state.json"
    if installed.is_file():
        return installed
    return resolve_package_file("skills", "ad-attack", "state.json")


def _task_text_from_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        raw = item.get("task") or item.get("text") or item.get("content")
        if isinstance(raw, str):
            return raw.strip()
    return ""


def _extract_json_array(text: str) -> list[Any] | None:
    if not text:
        return None
    decoder = json.JSONDecoder()
    repaired = repair_json_text(text)
    for blob in (repaired, text):
        for idx, char in enumerate(blob):
            if char != "[":
                continue
            try:
                data, _ = decoder.raw_decode(blob, idx)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                return data
    return None


def parse_generated_tasks(response_text: str) -> list[str]:
    """Accept a JSON array, {\"tasks\": [...]}, or a ReAct Finish object."""
    if not response_text or not str(response_text).strip():
        return []
    text = str(response_text)
    finish = extract_react_finish_json(text)
    candidates: list[Any] = []
    if isinstance(finish, dict):
        if isinstance(finish.get("tasks"), list):
            candidates = finish["tasks"]
        elif isinstance(finish.get("task"), str):
            candidates = [finish]
    if not candidates:
        array = _extract_json_array(text)
        if array is not None:
            candidates = array
    if not candidates:
        try:
            parsed = json.loads(repair_json_text(text))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
            candidates = parsed["tasks"]
        elif isinstance(parsed, list):
            candidates = parsed
    texts = [_task_text_from_item(item) for item in candidates]
    return [item for item in texts if item]


def build_generation_messages(
    *,
    batch_size: int,
    tasks: list[dict[str, str]],
    system_prompt: str,
    prompt_template: str,
    state: dict[str, Any],
) -> tuple[str, str]:
    payload = {
        "batch_size": int(batch_size),
        "known_completed_tasks": completed_task_texts(tasks),
        "prompt_template": prompt_template,
        "state": state,
        "output": (
            "Return a JSON object {\"tasks\": [string, ...]} with exactly "
            f"{int(batch_size)} English ad-attack task strings. "
            "One technique per string. Follow the prompt template grammar. "
            "Reference only objects that exist in state."
        ),
    }
    user_text = json.dumps(payload, ensure_ascii=False, indent=2)
    return system_prompt.strip(), user_text


def request_task_batch(
    *,
    batch_size: int,
    tasks: list[dict[str, str]],
    agent_client: FillClient,
    system_prompt: str,
    prompt_template: str,
    state: dict[str, Any],
    max_attempts: int = 5,
) -> list[str]:
    last_error = "empty model response"
    attempts = max(1, int(max_attempts))
    logger.info("Requesting %s attacker task string(s) from DeepSeek (max_attempts=%s)", batch_size, attempts)
    for attempt in range(1, attempts + 1):
        logger.info("DeepSeek fill attempt %s/%s", attempt, attempts)
        system_text, user_text = build_generation_messages(
            batch_size=batch_size,
            tasks=tasks,
            system_prompt=system_prompt,
            prompt_template=prompt_template,
            state=state,
        )
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]
        try:
            response = agent_client.request_completion(
                user_text,
                messages=messages,
                response_format={"type": "json_object"} if attempt == attempts else None,
            )
        except AgentTimeoutError as exc:
            last_error = str(exc)
            logger.warning("DeepSeek fill attempt %s/%s timed out: %s", attempt, attempts, exc)
            continue
        except AgentRequestError as exc:
            last_error = str(exc)
            logger.warning("DeepSeek fill attempt %s/%s failed: %s", attempt, attempts, exc)
            continue
        parsed = parse_generated_tasks(response.response_text)
        if len(parsed) >= batch_size:
            logger.info("DeepSeek returned %s task string(s)", batch_size)
            return parsed[:batch_size]
        if parsed:
            logger.info("DeepSeek returned %s task string(s) (requested %s)", len(parsed), batch_size)
            return parsed
        last_error = f"attempt {attempt}: no task strings in model response"
        logger.warning("%s", last_error)
    logger.error("Failed to generate %s attacker tasks: %s", batch_size, last_error)
    raise RuntimeError(f"Failed to generate {batch_size} attacker tasks: {last_error}")


def fill_next_batch(
    tasks: list[dict[str, str]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    agent_client: FillClient,
    system_prompt: str = "",
    prompt_template: str = "",
    state: dict[str, Any] | None = None,
    max_attempts: int = 5,
    request_batch: Callable[..., list[str]] | None = None,
) -> list[dict[str, str]]:
    indices = empty_slot_indices(tasks)
    if not indices:
        return tasks
    count = min(int(batch_size), len(indices))
    logger.info("Filling %s empty attacker slot(s)", count)
    requester = request_batch or request_task_batch
    contents = requester(
        batch_size=count,
        tasks=tasks,
        agent_client=agent_client,
        system_prompt=system_prompt,
        prompt_template=prompt_template,
        state=state or {},
        max_attempts=max_attempts,
    )
    for index, text in zip(indices[:count], contents):
        tasks[index]["task"] = text
    logger.info("Wrote %s task string(s) into empty slots", min(count, len(contents)))
    return tasks


def load_generation_resources() -> tuple[str, str, dict[str, Any]]:
    system_path = resolve_generator_system_path()
    template_path = resolve_prompt_template_path()
    state_path = resolve_state_json_path()
    system_prompt = _read_text(system_path) if system_path else ""
    prompt_template = _read_text(template_path) if template_path else ""
    state = _load_json_object(state_path) if state_path else {}
    if not system_prompt.strip():
        raise FileNotFoundError("attacker generator_system.md was not found")
    if not prompt_template.strip():
        raise FileNotFoundError("attacker_prompt_template.md was not found")
    return system_prompt, prompt_template, state
