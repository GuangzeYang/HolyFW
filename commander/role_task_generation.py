#!/usr/bin/env python3
"""Shared model-backed role task generation workflow."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from common import (
    build_role_task_prompt,
    candidate_task_path,
    extract_json_object,
    load_task_file,
    role_tasks_are_complete,
    save_json_atomic,
    validate_generated_task_file,
)
try:
    from agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError
except ImportError:
    from commander.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError

try:
    from logging_setup import append_agent_output_log, write_agent_response_log
except ImportError:
    from commander.logging_setup import append_agent_output_log, write_agent_response_log


StatusCallback = Callable[[str], None]
DependencyContextBuilder = Callable[[dict[str, Any], str], str]
DependencyOrderValidator = Callable[[dict[str, Any], str, list[dict[str, Any]]], tuple[bool, str | None]]


@dataclass(slots=True)
class RoleTaskGenerationResult:
    success: bool
    output_file: Path | None
    failure_reason: str | None
    stats: dict[str, int]



def _read_domain_context(domain_resource_path: Path) -> str:
    if not domain_resource_path.exists():
        return ""
    with open(domain_resource_path, encoding="utf-8") as f:
        return f.read()



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
        "上一轮输出未通过校验，请根据下面的失败原因重新生成完整 JSON。",
        f"失败原因：{reason}",
        "请重新安排完整时间序列，不要复用上一轮的分钟分布。",
    ]
    lowered = reason.lower()
    if "random minute ratio too low" in lowered:
        lines.append(
            "必须确保至少 80% 的任务分钟数不是 5 的倍数，避免大量使用 xx:00、xx:05、xx:10、xx:15、xx:20、xx:25、xx:30、xx:35、xx:40、xx:45、xx:50、xx:55。"
        )
    if "strictly increasing" in lowered:
        lines.append(
            "同一角色下的任务时间必须按 JSON 数组中的顺序严格递增，后一个任务的 time 必须严格晚于前一个任务，不能重复、倒退或并列。"
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



def _build_optional_dependency_text(
    context_builder: DependencyContextBuilder | None,
    persisted_data: dict[str, Any],
    role: str,
) -> str:
    if context_builder is None:
        return ""
    text = context_builder(persisted_data, role)
    return text if isinstance(text, str) else ""



def _validate_cross_role_dependencies(
    order_validator: DependencyOrderValidator | None,
    persisted_data: dict[str, Any],
    role: str,
    candidate_tasks: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    if order_validator is None:
        return True, None
    return order_validator(persisted_data, role, candidate_tasks)



def generate_role_tasks(
    *,
    source: str,
    final_file: Path,
    logs_dir: Path,
    domain_resource_path: Path,
    roles: tuple[str, ...] | list[str],
    min_tasks_per_role: int,
    max_tasks_per_role: int,
    min_non_five_ratio: float,
    max_attempts: int,
    agent_client: AgentRequestABC,
    emit_status: StatusCallback,
) -> RoleTaskGenerationResult:
    candidate_file = candidate_task_path(final_file)
    _cleanup_file(candidate_file)
    domain_context = _read_domain_context(domain_resource_path)
    if not domain_context.strip():
        emit_status(f"Warning: domain resource is empty or missing at {domain_resource_path}")

    normalized_roles = tuple(roles)
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
    total_elapsed_seconds = 0.0

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
            min_tasks_per_role=min_tasks_per_role,
            max_tasks_per_role=max_tasks_per_role,
            min_non_five_ratio=min_non_five_ratio,
        )
    }
    if len(completed_roles) == len(normalized_roles) and normalized_roles:
        emit_status(f"Unified task file already exists: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, stats)

    dependency_context_builder, dependency_order_validator = _load_dependency_provider()

    for role in normalized_roles:
        role_candidate_file = _role_candidate_path(candidate_file, role)
        if role in completed_roles:
            emit_status(f"Skipping role '{role}' because valid tasks already exist")
            continue

        related_context = _build_optional_dependency_text(dependency_context_builder, persisted_data, role)
        role_prompt = build_role_task_prompt(
            domain_context,
            min_tasks_per_role=min_tasks_per_role,
            max_tasks_per_role=max_tasks_per_role,
            roles=(role,),
            dependency_context=related_context if isinstance(related_context, str) else "",
        )

        role_succeeded = False
        retry_feedback = ""
        emit_status(f"Generating tasks for role '{role}'")

        for attempt in range(1, max_attempts + 1):
            attempt_prompt = role_prompt
            if retry_feedback:
                attempt_prompt = f"{role_prompt}\n\n# 上一轮修正要求\n{retry_feedback}"
            emit_status(f"Generation attempt {attempt}/{max_attempts} for role '{role}'")
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=attempt,
                prompt=attempt_prompt,
                note="request_started",
                provider=agent_client.provider_name,
                model=agent_client.model,
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="api_request",
                request_state="started",
                expected_output_file=str(role_candidate_file),
                role=role,
            )
            write_agent_response_log(
                logs_dir,
                source=source,
                attempt=attempt,
                note="request_started",
                prompt_text=attempt_prompt,
                provider=agent_client.provider_name,
                model=agent_client.model,
                role=role,
                request_state="started",
            )
            try:
                _cleanup_file(role_candidate_file)
                response = agent_client.request_completion(attempt_prompt)
                total_elapsed_seconds += response.elapsed_seconds
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=attempt_prompt,
                    note="request_finished",
                    provider=agent_client.provider_name,
                    model=response.model,
                    status_code=response.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="api_response",
                    elapsed_seconds=response.elapsed_seconds,
                    request_state="finished",
                    expected_output_file=str(role_candidate_file),
                    role=role,
                    finish_reason=response.finish_reason,
                )
                write_agent_response_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    note="api_response",
                    prompt_text=attempt_prompt,
                    provider=agent_client.provider_name,
                    model=response.model,
                    status_code=response.status_code,
                    role=role,
                    finish_reason=response.finish_reason,
                    response_text=response.response_text,
                    raw_response_text=response.raw_response_text,
                    request_state="finished",
                )
                if not response.response_text.strip():
                    stats["empty_response"] += 1
                    last_failure_reason = f"Model returned an empty response for role '{role}'"
                    append_agent_output_log(
                        logs_dir,
                        source=source,
                        attempt=attempt,
                        prompt=attempt_prompt,
                        note="empty_response",
                        provider=agent_client.provider_name,
                        model=response.model,
                        response_text=response.response_text,
                        status_code=response.status_code,
                        request_timeout_seconds=agent_client.request_timeout_seconds,
                        failure_stage="api_response",
                        elapsed_seconds=response.elapsed_seconds,
                        expected_output_file=str(role_candidate_file),
                        role=role,
                        finish_reason=response.finish_reason,
                    )
                    emit_status(last_failure_reason)
                    continue

                if response.finish_reason == "length":
                    stats["parse_fail"] += 1
                    last_failure_reason = _truncation_reason(role, response.finish_reason)
                    append_agent_output_log(
                        logs_dir,
                        source=source,
                        attempt=attempt,
                        prompt=attempt_prompt,
                        note="parse_fail: truncated_response",
                        provider=agent_client.provider_name,
                        model=response.model,
                        response_text=response.response_text,
                        status_code=response.status_code,
                        request_timeout_seconds=agent_client.request_timeout_seconds,
                        failure_stage="response_parse",
                        elapsed_seconds=response.elapsed_seconds,
                        expected_output_file=str(role_candidate_file),
                        role=role,
                        finish_reason=response.finish_reason,
                    )
                    emit_status(last_failure_reason)
                    continue

                parsed = extract_json_object(response.response_text)
                if parsed is None:
                    stats["parse_fail"] += 1
                    last_failure_reason = _parse_failure_reason(role)
                    append_agent_output_log(
                        logs_dir,
                        source=source,
                        attempt=attempt,
                        prompt=attempt_prompt,
                        note="parse_fail",
                        provider=agent_client.provider_name,
                        model=response.model,
                        response_text=response.response_text,
                        status_code=response.status_code,
                        request_timeout_seconds=agent_client.request_timeout_seconds,
                        failure_stage="response_parse",
                        elapsed_seconds=response.elapsed_seconds,
                        expected_output_file=str(role_candidate_file),
                        role=role,
                        finish_reason=response.finish_reason,
                    )
                    emit_status(last_failure_reason)
                    continue

                save_json_atomic(role_candidate_file, parsed)
                failure_type, reason, data, validated_file_size = validate_generated_task_file(
                    role_candidate_file,
                    min_tasks_per_role=min_tasks_per_role,
                    max_tasks_per_role=max_tasks_per_role,
                    min_non_five_ratio=min_non_five_ratio,
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
                    append_agent_output_log(
                        logs_dir,
                        source=source,
                        attempt=attempt,
                        prompt=attempt_prompt,
                        note=f"{failure_type}: {reason}",
                        provider=agent_client.provider_name,
                        model=response.model,
                        response_text=response.response_text,
                        status_code=response.status_code,
                        request_timeout_seconds=agent_client.request_timeout_seconds,
                        failure_stage="candidate_validation",
                        elapsed_seconds=response.elapsed_seconds,
                        expected_output_file=str(role_candidate_file),
                        detected_output_file=str(role_candidate_file),
                        file_exists=True,
                        file_size=validated_file_size,
                        role=role,
                        finish_reason=response.finish_reason,
                    )
                    emit_status(f"Generated candidate failed {failure_type} for role '{role}': {reason}")
                    continue

                assert data is not None
                persisted_data[role] = data.get(role, [])
                save_json_atomic(final_file, persisted_data)
                completed_roles.add(role)
                _cleanup_file(role_candidate_file)
                role_succeeded = True
                emit_status(f"Persisted validated tasks for role '{role}' to {final_file}")
                break
            except AgentTimeoutError as exc:
                stats["api_timeout"] += 1
                last_failure_reason = f"Role '{role}' request timed out: {exc}"
                write_agent_response_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    note="api_timeout",
                    prompt_text=attempt_prompt,
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    status_code=exc.status_code,
                    role=role,
                    error_text=exc.response_text or str(exc),
                    request_state="failed",
                )
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=attempt_prompt,
                    note="api_timeout",
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    error_text=exc.response_text or str(exc),
                    status_code=exc.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="api_request",
                    elapsed_seconds=exc.elapsed_seconds,
                    expected_output_file=str(role_candidate_file),
                    role=role,
                )
                emit_status(last_failure_reason)
            except AgentRequestError as exc:
                stats["api_error"] += 1
                last_failure_reason = f"Role '{role}' request failed: {exc}"
                if exc.response_text:
                    write_agent_response_log(
                        logs_dir,
                        source=source,
                        attempt=attempt,
                        note="api_error",
                        prompt_text=attempt_prompt,
                        provider=agent_client.provider_name,
                        model=agent_client.model,
                        status_code=exc.status_code,
                        role=role,
                        raw_response_text=exc.response_text,
                        error_text=exc.response_text,
                        request_state="failed",
                    )
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=attempt_prompt,
                    note="api_error",
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    error_text=exc.response_text or str(exc),
                    status_code=exc.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="api_request",
                    elapsed_seconds=exc.elapsed_seconds,
                    expected_output_file=str(role_candidate_file),
                    role=role,
                )
                emit_status(last_failure_reason)
            except Exception as exc:
                stats["runtime_error"] += 1
                last_failure_reason = f"Error generating tasks for role '{role}': {exc}"
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=attempt_prompt,
                    note="runtime_error",
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    error_text=str(exc),
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="runtime_exception",
                    expected_output_file=str(role_candidate_file),
                    role=role,
                )
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
        failure_type, reason, data, validated_file_size = validate_generated_task_file(
            final_file,
            min_tasks_per_role=min_tasks_per_role,
            max_tasks_per_role=max_tasks_per_role,
            min_non_five_ratio=min_non_five_ratio,
            roles=normalized_roles,
            preserve_generated_times=True,
        )
        if failure_type is not None:
            stats[failure_type] = stats.get(failure_type, 0) + 1
            last_failure_reason = reason
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=1,
                prompt="\n\n".join(sorted(normalized_roles)),
                note=f"{failure_type}: {reason}",
                provider=agent_client.provider_name,
                model=agent_client.model,
                response_text=json.dumps(persisted_data, ensure_ascii=False),
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="candidate_validation",
                elapsed_seconds=total_elapsed_seconds,
                expected_output_file=str(final_file),
                detected_output_file=str(final_file),
                file_exists=True,
                file_size=validated_file_size,
            )
            emit_status(f"Generated candidate failed {failure_type}: {reason}")
            return RoleTaskGenerationResult(False, None, last_failure_reason, stats)

        assert data is not None
        save_json_atomic(final_file, data)
        append_agent_output_log(
            logs_dir,
            source=source,
            attempt=1,
            prompt="\n\n".join(sorted(normalized_roles)),
            note="success",
            provider=agent_client.provider_name,
            model=agent_client.model,
            response_text=json.dumps(data, ensure_ascii=False),
            request_timeout_seconds=agent_client.request_timeout_seconds,
            failure_stage="promoted_final_file",
            elapsed_seconds=total_elapsed_seconds,
            expected_output_file=str(final_file),
            detected_output_file=str(final_file),
            file_exists=True,
            file_size=validated_file_size,
        )
        emit_status(f"Successfully generated unified tasks: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, stats)
    finally:
        _cleanup_file(candidate_file)
