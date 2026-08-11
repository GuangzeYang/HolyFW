#!/usr/bin/env python3
"""Shared model-backed role task generation workflow."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from common import (
    build_role_task_prompt,
    build_role_task_time_remediation_prompt,
    candidate_task_path,
    collect_task_indices_outside_work_windows,
    extract_json_object,
    load_task_file,
    normalize_role_tasks,
    role_tasks_are_complete,
    save_json_atomic,
    validate_generated_task_file,
    verify_time_remediation_payload,
)
try:
    from agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError
except ImportError:
    from commander.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError

try:
    from logging_setup import write_interactive_log
except ImportError:
    from commander.logging_setup import write_interactive_log


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
        "The previous output failed validation. Regenerate the complete JSON using the failure reason below.",
        f"Failure reason: {reason}",
        "Create a new complete time sequence; do not reuse the previous minute distribution.",
    ]
    lowered = reason.lower()
    if "random minute ratio too low" in lowered:
        lines.append(
            "At least 80% of task minute values must not be divisible by 5. Avoid excessive use of xx:00, xx:05, xx:10, xx:15, xx:20, xx:25, xx:30, xx:35, xx:40, xx:45, xx:50, and xx:55."
        )
    if "strictly increasing" in lowered:
        lines.append(
            "Within a role, task times must be strictly increasing in JSON array order. Every later task's time must be strictly later than the preceding task; times cannot repeat, move backward, or be equal."
        )
    return "\n".join(lines)


TIME_REMEDIATION_MAX_SUB_ATTEMPTS = 3


def _try_time_remediation_for_role(
    *,
    agent_client: AgentRequestABC,
    role: str,
    role_rows: list[dict[str, Any]],
    bad_indices: list[int],
    initial_failure_reason: str,
    min_tasks_per_role: int,
    max_tasks_per_role: int,
    min_non_five_ratio: float,
    role_candidate_file: Path,
    logs_dir: Path,
    source: str,
    attempt: int,
    emit_status: StatusCallback,
) -> tuple[str | None, str | None, dict[str, Any] | None, int, float]:
    """Re-validate after up to TIME_REMEDIATION_MAX_SUB_ATTEMPTS LLM fixes for out-of-window times."""
    prior_fb = ""
    current_rows = list(role_rows)
    current_bad = list(bad_indices)
    last_reason = initial_failure_reason
    last_size = 0
    extra_elapsed = 0.0

    for sub_k in range(1, TIME_REMEDIATION_MAX_SUB_ATTEMPTS + 1):
        if not current_bad:
            break
        fix_prompt = build_role_task_time_remediation_prompt(
            role=role,
            old_tasks=current_rows,
            bad_indices=current_bad,
            min_tasks_per_role=min_tasks_per_role,
            max_tasks_per_role=max_tasks_per_role,
            validation_reason=last_reason,
            prior_feedback=prior_fb,
        )
        emit_status(f"Time remediation sub-attempt {sub_k}/{TIME_REMEDIATION_MAX_SUB_ATTEMPTS} for role '{role}'")
        try:
            resp2 = agent_client.request_completion(fix_prompt)
        except AgentTimeoutError as exc:
            prior_fb = f"Request timed out: {exc}"
            continue
        except AgentRequestError as exc:
            prior_fb = f"Request failed: {exc}"
            continue

        extra_elapsed += resp2.elapsed_seconds
        write_interactive_log(
            logs_dir,
            role=role,
            attempt=attempt,
            provider=agent_client.provider_name,
            model=resp2.model,
            status_code=resp2.status_code,
            finish_reason=resp2.finish_reason,
            response_text=resp2.response_text,
            raw_response_text=resp2.raw_response_text,
            request_state="finished",
            caller=source,
        )
        if not resp2.response_text.strip():
            prior_fb = "The model returned an empty response"
            continue
        if resp2.finish_reason == "length":
            prior_fb = _truncation_reason(role, resp2.finish_reason)
            continue
        parsed2 = extract_json_object(resp2.response_text)
        if parsed2 is None:
            prior_fb = "Could not parse JSON from the model output"
            continue
        new_tasks = parsed2.get(role)
        ok_merge, merge_err = verify_time_remediation_payload(current_rows, new_tasks, current_bad)
        if not ok_merge:
            prior_fb = merge_err or "Merge validation failed"
            continue

        padded = normalize_role_tasks(
            {role: new_tasks},
            min_tasks_per_role=min_tasks_per_role,
            roles=(role,),
            preserve_generated_times=True,
        )
        save_json_atomic(role_candidate_file, padded)
        failure_type, reason, data, validated_file_size = validate_generated_task_file(
            role_candidate_file,
            min_tasks_per_role=min_tasks_per_role,
            max_tasks_per_role=max_tasks_per_role,
            min_non_five_ratio=min_non_five_ratio,
            roles=(role,),
            preserve_generated_times=True,
        )
        last_size = validated_file_size
        if failure_type is None and data is not None:
            emit_status(f"Time remediation succeeded for role '{role}' on sub-attempt {sub_k}")
            return None, None, data, validated_file_size, extra_elapsed
        last_reason = reason or failure_type or "validation failed"
        prior_fb = last_reason
        pr = padded.get(role, [])
        if isinstance(pr, list):
            current_rows = [dict(x) for x in pr if isinstance(x, dict)]
        current_bad = collect_task_indices_outside_work_windows(current_rows)

    return "quality_fail", last_reason, None, last_size, extra_elapsed


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
    save_final_file: Callable[[Path, dict[str, Any]], None] = save_json_atomic,
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
                attempt_prompt = f"{role_prompt}\n\n# Previous correction requirements\n{retry_feedback}"
            emit_status(f"Generation attempt {attempt}/{max_attempts} for role '{role}'")
            try:
                _cleanup_file(role_candidate_file)
                response = agent_client.request_completion(attempt_prompt)
                total_elapsed_seconds += response.elapsed_seconds
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

                parsed = extract_json_object(response.response_text)
                if parsed is None:
                    stats["parse_fail"] += 1
                    last_failure_reason = _parse_failure_reason(role)
                    emit_status(last_failure_reason)
                    continue

                save_json_atomic(role_candidate_file, parsed)
                normalized_for_scan = normalize_role_tasks(
                    parsed,
                    min_tasks_per_role=min_tasks_per_role,
                    roles=(role,),
                    preserve_generated_times=True,
                )
                raw_rows = normalized_for_scan.get(role, [])
                role_rows_snapshot: list[dict[str, Any]] = [
                    x for x in raw_rows if isinstance(x, dict)
                ] if isinstance(raw_rows, list) else []

                failure_type, reason, data, validated_file_size = validate_generated_task_file(
                    role_candidate_file,
                    min_tasks_per_role=min_tasks_per_role,
                    max_tasks_per_role=max_tasks_per_role,
                    min_non_five_ratio=min_non_five_ratio,
                    roles=(role,),
                    preserve_generated_times=True,
                )
                if failure_type is not None:
                    bad_idx = collect_task_indices_outside_work_windows(role_rows_snapshot)
                    if bad_idx:
                        (
                            failure_type,
                            reason,
                            data,
                            validated_file_size,
                            remediation_elapsed,
                        ) = _try_time_remediation_for_role(
                            agent_client=agent_client,
                            role=role,
                            role_rows=role_rows_snapshot,
                            bad_indices=bad_idx,
                            initial_failure_reason=reason or failure_type or "",
                            min_tasks_per_role=min_tasks_per_role,
                            max_tasks_per_role=max_tasks_per_role,
                            min_non_five_ratio=min_non_five_ratio,
                            role_candidate_file=role_candidate_file,
                            logs_dir=logs_dir,
                            source=source,
                            attempt=attempt,
                            emit_status=emit_status,
                        )
                        total_elapsed_seconds += remediation_elapsed
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
            emit_status(f"Generated candidate failed {failure_type}: {reason}")
            return RoleTaskGenerationResult(False, None, last_failure_reason, stats)

        assert data is not None
        save_final_file(final_file, data)
        emit_status(f"Successfully generated unified tasks: {final_file}")
        return RoleTaskGenerationResult(True, final_file, None, stats)
    finally:
        _cleanup_file(candidate_file)
