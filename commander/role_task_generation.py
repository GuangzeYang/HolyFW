#!/usr/bin/env python3
"""Shared model-backed role task generation workflow."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from common import (
    candidate_task_path,
    extract_react_finish_json,
    format_task_generation_constraints,
    load_task_file,
    normalize_role_tasks,
    role_tasks_are_complete,
    save_json_atomic,
    validate_generated_task_file,
)
try:
    from agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError
    from prompt_catalog import assemble_generation_payload, build_react_generation_messages, load_prompt_catalog
    from role_dependency_provider import build_backward_items
    from target_config import load_role_time_model
    from time_model import TimeModelConfig, generate_schedule, zip_tasks_with_schedule
except ImportError:
    from commander.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError
    from commander.prompt_catalog import assemble_generation_payload, build_react_generation_messages, load_prompt_catalog
    from commander.role_dependency_provider import build_backward_items
    from commander.target_config import load_role_time_model
    from commander.time_model import TimeModelConfig, generate_schedule, zip_tasks_with_schedule

try:
    from logging_setup import write_interactive_log
except ImportError:
    from commander.logging_setup import write_interactive_log


StatusCallback = Callable[[str], None]
ScheduleBuilder = Callable[[str, int], list[str]]
DependencyContextBuilder = Callable[[dict[str, Any], str], str]
DependencyOrderValidator = Callable[[dict[str, Any], str, list[dict[str, Any]]], tuple[bool, str | None]]


@dataclass(slots=True)
class RoleTaskGenerationResult:
    success: bool
    output_file: Path | None
    failure_reason: str | None
    stats: dict[str, int]


def _read_text_resource(path: Path) -> str:
    if not path.exists():
        return ""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _cleanup_file(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.unlink()
    except OSError:
        pass


def _role_candidate_path(candidate_file: Path, role: str) -> Path:
    return candidate_file.with_name(f"{candidate_file.stem}_{role}{candidate_file.suffix}")


def _truncation_reason(role: str, finish_reason: str | None) -> str:
    suffix = f" (finish_reason={finish_reason})" if finish_reason else ""
    return f"Model response for role '{role}' was truncated by provider{suffix}"


def _parse_failure_reason(role: str) -> str:
    return f"Model response for role '{role}' did not contain a valid JSON object"


def _build_retry_feedback(reason: str | None) -> str:
    if not reason:
        return ""
    lines = [
        "The previous output failed validation. Regenerate the complete JSON using the failure reason below.",
        f"Failure reason: {reason}",
        "Do not invent timestamps. Return exactly task_count items. Put responses in later schedule slots.",
    ]
    lowered = reason.lower()
    if "does not match schedule" in lowered or "too few" in lowered or "too many" in lowered:
        lines.append("The number of task items must equal task_count and the schedule length.")
    if "cross-role dependency" in lowered or "strictly later" in lowered:
        lines.append(
            "Do not put that item's response_actions in forbidden_slot_indices. "
            "Use any allowed_slot_indices slot, or independent work if that list is empty."
        )
    return "\n".join(lines)


def _load_dependency_provider() -> tuple[DependencyContextBuilder | None, DependencyOrderValidator | None]:
    module = None
    for module_name in ("commander.role_dependency_provider", "role_dependency_provider"):
        try:
            module = importlib.import_module(module_name)
            break
        except ModuleNotFoundError:
            continue
    if module is None:
        return None, None
    context_builder = getattr(module, "build_dependency_context", None)
    order_validator = getattr(module, "validate_dependency_order", None)
    if not callable(context_builder):
        context_builder = None
    if not callable(order_validator):
        order_validator = None
    return context_builder, order_validator


def _validate_cross_role_dependencies(
    order_validator: DependencyOrderValidator | None,
    persisted_data: dict[str, Any],
    role: str,
    candidate_tasks: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    if order_validator is None:
        return True, None
    return order_validator(persisted_data, role, candidate_tasks)


def _json_time_model_defaults() -> dict[str, Any]:
    try:
        from runtime_config import get_generator_config, load_runtime_config
    except ImportError:
        from commander.runtime_config import get_generator_config, load_runtime_config
    return get_generator_config(load_runtime_config())["time_model"]


def _resolved_time_model_mapping(
    role: str,
    time_model_config: dict[str, Any] | None,
    target_ini_path: Path | None,
) -> dict[str, Any]:
    defaults = time_model_config if time_model_config is not None else _json_time_model_defaults()
    if target_ini_path is None:
        return dict(defaults)
    return load_role_time_model(target_ini_path, role, defaults)


def _schedule_time_model(
    role: str,
    time_model_config: dict[str, Any] | None,
    target_ini_path: Path | None,
) -> TimeModelConfig:
    return TimeModelConfig.from_mapping(
        _resolved_time_model_mapping(role, time_model_config, target_ini_path)
    )


def _role_task_count(
    role: str,
    time_model_config: dict[str, Any] | None,
    target_ini_path: Path | None,
    override: int | None,
) -> int:
    if override is not None:
        return int(override)
    mapping = _resolved_time_model_mapping(role, time_model_config, target_ini_path)
    return int(mapping["tasks_per_role"])


def _max_schedule_count() -> int:
    try:
        from runtime_config import WORKDAY_MINUTES, get_generator_config, load_runtime_config
    except ImportError:
        from commander.runtime_config import WORKDAY_MINUTES, get_generator_config, load_runtime_config
    min_internal = int(get_generator_config(load_runtime_config())["min_internal"])
    return max(1, WORKDAY_MINUTES // min_internal)


def _default_schedule(
    role: str,
    expected_count: int,
    time_model_config: dict[str, Any] | None,
    target_ini_path: Path | None,
) -> list[str]:
    return generate_schedule(
        expected_count,
        role=role,
        day=date.today(),
        config=_schedule_time_model(role, time_model_config, target_ini_path),
        max_count=_max_schedule_count(),
    )


def _stored_role_count(data: dict[str, Any], role: str) -> int:
    tasks = data.get(role)
    return len(tasks) if isinstance(tasks, list) else 0


def generate_role_tasks(
    *,
    source: str,
    final_file: Path,
    logs_dir: Path,
    domain_resource_path: Path,
    constraints_resource_path: Path,
    roles: tuple[str, ...] | list[str],
    max_attempts: int,
    agent_client: AgentRequestABC,
    emit_status: StatusCallback,
    save_final_file: Callable[[Path, dict[str, Any]], None] = save_json_atomic,
    prompt_resources_dir: Path | None = None,
    tasks_per_role: int | None = None,
    time_model_config: dict[str, Any] | None = None,
    target_ini_path: Path | None = None,
    schedule_builder: ScheduleBuilder | None = None,
) -> RoleTaskGenerationResult:
    candidate_file = candidate_task_path(final_file)
    _cleanup_file(candidate_file)
    domain_fallback = _read_text_resource(domain_resource_path)
    if not domain_fallback.strip():
        emit_status(f"Warning: domain resource is empty or missing at {domain_resource_path}")
    constraints_template = _read_text_resource(constraints_resource_path)
    if not constraints_template.strip():
        emit_status(
            f"Warning: task-generation constraints are empty or missing at {constraints_resource_path}"
        )

    catalog = load_prompt_catalog(prompt_resources_dir)
    normalized_roles = tuple(roles)
    expected_counts = {
        role: _role_task_count(role, time_model_config, target_ini_path, tasks_per_role)
        for role in normalized_roles
    }
    stats = {
        "api_timeout": 0,
        "api_error": 0,
        "empty_response": 0,
        "parse_fail": 0,
        "schema_fail": 0,
        "quality_fail": 0,
        "runtime_error": 0,
    }
    last_failure_reason: str | None = None

    try:
        persisted_data = load_task_file(final_file)
    except ValueError as exc:
        stats["runtime_error"] += 1
        last_failure_reason = str(exc)
        emit_status(last_failure_reason)
        return RoleTaskGenerationResult(False, None, last_failure_reason, stats)

    completed_roles = {
        role
        for role in normalized_roles
        if role_tasks_are_complete(
            persisted_data,
            role,
            tasks_per_role=_stored_role_count(persisted_data, role),
        )
    }
    if len(completed_roles) == len(normalized_roles) and normalized_roles:
        emit_status(f"Unified task file already exists: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, stats)

    _, dependency_order_validator = _load_dependency_provider()

    for role in normalized_roles:
        role_candidate_file = _role_candidate_path(candidate_file, role)
        if role in completed_roles:
            emit_status(f"Skipping role '{role}' because valid tasks already exist")
            continue

        expected_count = expected_counts[role]
        schedule = (
            schedule_builder(role, expected_count)
            if schedule_builder is not None
            else _default_schedule(role, expected_count, time_model_config, target_ini_path)
        )
        task_count = len(schedule)
        if task_count <= 0:
            last_failure_reason = f"Time model produced no time nodes for role '{role}'"
            emit_status(last_failure_reason)
            stats["runtime_error"] += 1
            return RoleTaskGenerationResult(False, None, last_failure_reason, stats)
        emit_status(
            f"Sampled {task_count} time nodes for role '{role}' (expected {expected_count})"
        )

        backward = build_backward_items(persisted_data, role, schedule)
        payload = assemble_generation_payload(
            role=role,
            task_count=task_count,
            schedule=schedule,
            backward=backward,
            catalog=catalog,
            domain_fallback=domain_fallback,
        )
        system_prompt = format_task_generation_constraints(
            constraints_template,
            roles=(role,),
            tasks_per_role=task_count,
        )
        role_succeeded = False
        retry_feedback = ""
        emit_status(f"Generating tasks for role '{role}'")

        for attempt in range(1, max_attempts + 1):
            system_text, user_text = build_react_generation_messages(
                constraints_template=system_prompt,
                payload=payload,
                retry_feedback=retry_feedback,
            )
            use_json_object = attempt == max_attempts and max_attempts > 1
            if use_json_object:
                user_text = (
                    user_text
                    + "\n\nReturn only the JSON object. Do not include Thought or Action lines."
                )
            messages = [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ]
            emit_status(f"Generation attempt {attempt}/{max_attempts} for role '{role}'")
            try:
                _cleanup_file(role_candidate_file)
                response = agent_client.request_completion(
                    user_text,
                    messages=messages,
                    response_format={"type": "json_object"} if use_json_object else None,
                )
                write_interactive_log(
                    logs_dir,
                    role=role,
                    attempt=attempt,
                    provider=agent_client.provider_name,
                    model=response.model,
                    status_code=response.status_code,
                    finish_reason=response.finish_reason,
                    response_text=response.response_text,
                    raw_response_text=response.raw_response_text,
                    request_state="finished",
                    caller=source,
                )
                if not response.response_text.strip():
                    stats["empty_response"] += 1
                    last_failure_reason = f"Model returned an empty response for role '{role}'"
                    emit_status(last_failure_reason)
                    continue

                if response.finish_reason == "length":
                    stats["parse_fail"] += 1
                    last_failure_reason = _truncation_reason(role, response.finish_reason)
                    emit_status(last_failure_reason)
                    continue

                parsed = extract_react_finish_json(response.response_text)
                if parsed is None:
                    stats["parse_fail"] += 1
                    last_failure_reason = _parse_failure_reason(role)
                    emit_status(last_failure_reason)
                    continue

                raw_rows = parsed.get(role)
                if not isinstance(raw_rows, list):
                    stats["schema_fail"] += 1
                    last_failure_reason = f"Role '{role}' data is not a list"
                    retry_feedback = _build_retry_feedback(last_failure_reason)
                    emit_status(last_failure_reason)
                    continue
                if len(raw_rows) != task_count:
                    stats["schema_fail"] += 1
                    last_failure_reason = (
                        f"Role '{role}' task count {len(raw_rows)} does not match schedule length {task_count}"
                    )
                    retry_feedback = _build_retry_feedback(last_failure_reason)
                    emit_status(last_failure_reason)
                    continue

                try:
                    zipped = zip_tasks_with_schedule(
                        [item if isinstance(item, dict) else {} for item in raw_rows],
                        schedule,
                    )
                except ValueError as exc:
                    stats["schema_fail"] += 1
                    last_failure_reason = str(exc)
                    retry_feedback = _build_retry_feedback(last_failure_reason)
                    emit_status(last_failure_reason)
                    continue

                save_json_atomic(role_candidate_file, {role: zipped})
                failure_type, reason, data, _validated_file_size = validate_generated_task_file(
                    role_candidate_file,
                    tasks_per_role=task_count,
                    roles=(role,),
                    preserve_generated_times=True,
                )
                if failure_type is None and data is not None:
                    dependency_ok, dependency_reason = _validate_cross_role_dependencies(
                        dependency_order_validator,
                        persisted_data,
                        role,
                        data.get(role, []),
                    )
                    if not dependency_ok:
                        failure_type = "quality_fail"
                        reason = dependency_reason

                if failure_type is not None:
                    stats[failure_type] = stats.get(failure_type, 0) + 1
                    last_failure_reason = f"Role '{role}' validation failed: {reason}"
                    retry_feedback = _build_retry_feedback(reason)
                    emit_status(f"Generated candidate failed {failure_type} for role '{role}': {reason}")
                    continue

                assert data is not None
                persisted_data[role] = data.get(role, [])
                save_final_file(final_file, persisted_data)
                completed_roles.add(role)
                _cleanup_file(role_candidate_file)
                role_succeeded = True
                emit_status(f"Persisted validated tasks for role '{role}' to {final_file}")
                break
            except AgentTimeoutError as exc:
                stats["api_timeout"] += 1
                last_failure_reason = f"Role '{role}' request timed out: {exc}"
                write_interactive_log(
                    logs_dir,
                    role=role,
                    attempt=attempt,
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    status_code=exc.status_code,
                    error_text=exc.response_text or str(exc),
                    request_state="failed",
                    caller=source,
                )
                emit_status(last_failure_reason)
            except AgentRequestError as exc:
                stats["api_error"] += 1
                last_failure_reason = f"Role '{role}' request failed: {exc}"
                if exc.response_text:
                    write_interactive_log(
                        logs_dir,
                        role=role,
                        attempt=attempt,
                        provider=agent_client.provider_name,
                        model=agent_client.model,
                        status_code=exc.status_code,
                        raw_response_text=exc.response_text,
                        error_text=exc.response_text,
                        request_state="failed",
                        caller=source,
                    )
                emit_status(last_failure_reason)
            except Exception as exc:
                stats["runtime_error"] += 1
                last_failure_reason = f"Error generating tasks for role '{role}': {exc}"
                emit_status(last_failure_reason)

        _cleanup_file(role_candidate_file)
        if not role_succeeded:
            emit_status(
                "Failed to generate valid role tasks after maximum attempts: "
                + ", ".join(f"{key}={value}" for key, value in stats.items())
            )
            if last_failure_reason:
                emit_status(f"Last validation failure reason: {last_failure_reason}")
            return RoleTaskGenerationResult(False, None, last_failure_reason, stats)

    try:
        realized_counts = {
            role: _stored_role_count(persisted_data, role) for role in normalized_roles
        }
        failure_type, reason, data, _validated_file_size = validate_generated_task_file(
            final_file,
            tasks_per_role=realized_counts,
            roles=normalized_roles,
            preserve_generated_times=True,
        )
        if failure_type is not None:
            stats[failure_type] = stats.get(failure_type, 0) + 1
            last_failure_reason = reason
            emit_status(f"Generated candidate failed {failure_type}: {reason}")
            return RoleTaskGenerationResult(False, None, last_failure_reason, stats)

        assert data is not None
        save_final_file(final_file, data)
        emit_status(f"Successfully generated unified tasks: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, stats)
    finally:
        _cleanup_file(candidate_file)
