#!/usr/bin/env python3
"""Shared model-backed role task generation workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common import (
    build_role_task_prompt,
    candidate_task_path,
    extract_json_object,
    promote_candidate_task_file,
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
    if final_file.exists():
        emit_status(f"Unified task file already exists: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, {})

    candidate_file = candidate_task_path(final_file)
    domain_context = _read_domain_context(domain_resource_path)
    if not domain_context.strip():
        emit_status(f"Warning: domain resource is empty or missing at {domain_resource_path}")

    prompt = build_role_task_prompt(
        domain_context,
        min_tasks_per_role=min_tasks_per_role,
        max_tasks_per_role=max_tasks_per_role,
        roles=roles,
    )
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

    for attempt in range(1, max_attempts + 1):
        emit_status(f"Generation attempt {attempt}/{max_attempts}")
        try:
            if candidate_file.exists():
                try:
                    candidate_file.unlink()
                except OSError:
                    pass

            response = agent_client.request_completion(prompt)
            write_agent_response_log(
                logs_dir,
                source=source,
                attempt=attempt,
                note="api_response",
                prompt_text=prompt,
                provider=agent_client.provider_name,
                model=response.model,
                status_code=response.status_code,
                response_text=response.response_text,
                raw_response_text=response.raw_response_text,
            )
            if not response.response_text.strip():
                stats["empty_response"] += 1
                last_failure_reason = "Model returned an empty response"
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=prompt,
                    note="empty_response",
                    provider=agent_client.provider_name,
                    model=response.model,
                    response_text=response.response_text,
                    status_code=response.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="api_response",
                    elapsed_seconds=response.elapsed_seconds,
                    expected_output_file=str(candidate_file),
                )
                emit_status(last_failure_reason)
                continue

            parsed = extract_json_object(response.response_text)
            if parsed is None:
                stats["parse_fail"] += 1
                last_failure_reason = "Model response did not contain a valid JSON object"
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=prompt,
                    note="parse_fail",
                    provider=agent_client.provider_name,
                    model=response.model,
                    response_text=response.response_text,
                    status_code=response.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="response_parse",
                    elapsed_seconds=response.elapsed_seconds,
                    expected_output_file=str(candidate_file),
                )
                emit_status(last_failure_reason)
                continue

            save_json_atomic(candidate_file, parsed)
            failure_type, reason, data, validated_file_size = validate_generated_task_file(
                candidate_file,
                min_tasks_per_role=min_tasks_per_role,
                max_tasks_per_role=max_tasks_per_role,
                min_non_five_ratio=min_non_five_ratio,
                roles=roles,
            )
            if failure_type is not None:
                stats[failure_type] = stats.get(failure_type, 0) + 1
                last_failure_reason = reason
                append_agent_output_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    prompt=prompt,
                    note=f"{failure_type}: {reason}",
                    provider=agent_client.provider_name,
                    model=response.model,
                    response_text=response.response_text,
                    status_code=response.status_code,
                    request_timeout_seconds=agent_client.request_timeout_seconds,
                    failure_stage="candidate_validation",
                    elapsed_seconds=response.elapsed_seconds,
                    expected_output_file=str(candidate_file),
                    detected_output_file=str(candidate_file),
                    file_exists=True,
                    file_size=validated_file_size,
                )
                emit_status(f"Generated candidate failed {failure_type}: {reason}")
                continue

            assert data is not None
            promote_candidate_task_file(candidate_file, final_file, data)
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=attempt,
                prompt=prompt,
                note="success",
                provider=agent_client.provider_name,
                model=response.model,
                response_text=response.response_text,
                status_code=response.status_code,
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="promoted_final_file",
                elapsed_seconds=response.elapsed_seconds,
                expected_output_file=str(candidate_file),
                detected_output_file=str(candidate_file),
                file_exists=True,
                file_size=validated_file_size,
            )
            emit_status(f"Successfully generated unified tasks: {final_file}")
            return RoleTaskGenerationResult(True, final_file, None, stats)
        except AgentTimeoutError as exc:
            stats["api_timeout"] += 1
            last_failure_reason = str(exc)
            write_agent_response_log(
                logs_dir,
                source=source,
                attempt=attempt,
                note="api_timeout",
                prompt_text=prompt,
                provider=agent_client.provider_name,
                model=agent_client.model,
                status_code=exc.status_code,
                error_text=exc.response_text or str(exc),
            )
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=attempt,
                prompt=prompt,
                note="api_timeout",
                provider=agent_client.provider_name,
                model=agent_client.model,
                error_text=exc.response_text or str(exc),
                status_code=exc.status_code,
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="api_request",
                elapsed_seconds=exc.elapsed_seconds,
                expected_output_file=str(candidate_file),
            )
            emit_status(f"Agent request timed out: {exc}")
        except AgentRequestError as exc:
            stats["api_error"] += 1
            last_failure_reason = str(exc)
            if exc.response_text:
                write_agent_response_log(
                    logs_dir,
                    source=source,
                    attempt=attempt,
                    note="api_error",
                    prompt_text=prompt,
                    provider=agent_client.provider_name,
                    model=agent_client.model,
                    status_code=exc.status_code,
                    raw_response_text=exc.response_text,
                    error_text=exc.response_text,
                )
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=attempt,
                prompt=prompt,
                note="api_error",
                provider=agent_client.provider_name,
                model=agent_client.model,
                error_text=exc.response_text or str(exc),
                status_code=exc.status_code,
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="api_request",
                elapsed_seconds=exc.elapsed_seconds,
                expected_output_file=str(candidate_file),
            )
            emit_status(f"Agent request failed: {exc}")
        except Exception as exc:
            stats["runtime_error"] += 1
            last_failure_reason = str(exc)
            append_agent_output_log(
                logs_dir,
                source=source,
                attempt=attempt,
                prompt=prompt,
                note="runtime_error",
                provider=agent_client.provider_name,
                model=agent_client.model,
                error_text=str(exc),
                request_timeout_seconds=agent_client.request_timeout_seconds,
                failure_stage="runtime_exception",
                expected_output_file=str(candidate_file),
            )
            emit_status(f"Error generating role tasks: {exc}")

    emit_status(
        "Failed to generate valid role tasks after maximum attempts: "
        + ", ".join(f"{key}={value}" for key, value in stats.items())
    )
    if last_failure_reason:
        emit_status(f"Last validation failure reason: {last_failure_reason}")
    return RoleTaskGenerationResult(False, None, last_failure_reason, stats)
