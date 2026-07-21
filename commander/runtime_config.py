#!/usr/bin/env python3
"""Runtime configuration loader for commander modules."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

WORKDAY_MINUTES = 7 * 60

CONFIG_FILE_NAME = "config.json"


def default_runtime_config_path() -> Path:
    """Return the default config.json path under commander/."""
    return Path(__file__).resolve().parent / CONFIG_FILE_NAME


def resolve_config_relative_path(raw_path: str) -> Path:
    """Resolve a config path string against commander/ when it is relative."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Path value must be a non-empty string")
    expanded = Path(os.path.expanduser(raw_path.strip()))
    if expanded.is_absolute():
        return expanded.resolve()
    return (Path(__file__).resolve().parent / expanded).resolve()


def _dot_get(data: dict[str, Any], dot_path: str) -> Any:
    current: Any = data
    for segment in dot_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ValueError(f"Missing required config key: {dot_path}")
        current = current[segment]
    return current


def _read_required(data: dict[str, Any], dot_path: str, expected_type: type | tuple[type, ...]) -> Any:
    value = _dot_get(data, dot_path)
    if not isinstance(value, expected_type):
        type_name = (
            ", ".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise ValueError(f"Config key {dot_path} must be {type_name}, got {type(value).__name__}")
    return value


def _validate_comment_pairs(node: dict[str, Any], path: str = "") -> None:
    for key, value in node.items():
        if key.startswith("_comment"):
            if not isinstance(value, str) or not value.strip():
                where = path or "root"
                raise ValueError(f"Comment key {where}.{key} must be a non-empty string")
            continue

        comment_key = f"_comment_{key}"
        comment_value = node.get(comment_key)
        where = f"{path}.{key}" if path else key
        if not isinstance(comment_value, str) or not comment_value.strip():
            raise ValueError(f"Missing non-empty comment key {comment_key} for config key {where}")

        if isinstance(value, dict):
            _validate_comment_pairs(value, where)


def _validate_positive_int(data: dict[str, Any], dot_path: str) -> int:
    value = _read_required(data, dot_path, int)
    if value <= 0:
        raise ValueError(f"Config key {dot_path} must be > 0")
    return value


def _validate_schema(data: dict[str, Any]) -> None:
    _read_required(data, "server.host", str)
    _validate_positive_int(data, "server.port")
    _validate_positive_int(data, "server.max_line_bytes")
    _validate_positive_int(data, "server.recv_chunk_bytes")
    _validate_positive_int(data, "server.socket_timeout_seconds")
    _validate_positive_int(data, "server.listen_backlog")
    _validate_positive_int(data, "server.worker_threads")

    _read_required(data, "scanner.data_dir", str)
    _validate_positive_int(data, "scanner.scan_interval_seconds")
    _validate_positive_int(data, "scanner.max_dispatch_lateness_minutes")

    _validate_positive_int(data, "storage.lock_timeout_seconds")
    _validate_positive_int(data, "storage.max_store_text")

    soldier_timeout = _read_required(data, "dispatch.soldier_timeout_seconds", (int, float))
    if soldier_timeout <= 0:
        raise ValueError("Config key dispatch.soldier_timeout_seconds must be > 0")
    client_timeout = _validate_positive_int(data, "dispatch.client_timeout_seconds")
    if client_timeout < soldier_timeout + 5:
        raise ValueError(
            "Config key dispatch.client_timeout_seconds must be at least "
            "dispatch.soldier_timeout_seconds + 5"
        )
    _validate_positive_int(data, "dispatch.timeout_minutes")

    _validate_positive_int(data, "failure_policy.cooldown_seconds")
    _validate_positive_int(data, "failure_policy.max_consecutive_failures")
    _read_required(data, "failure_policy.state_file", str)

    email_enabled = _read_required(data, "email_alert.enabled", bool)
    _read_required(data, "email_alert.smtp_host", str)
    _validate_positive_int(data, "email_alert.smtp_port")
    email_sender = _read_required(data, "email_alert.sender", str)
    email_recipients = _read_required(data, "email_alert.recipients", list)
    _read_required(data, "email_alert.auth_code_env", str)
    _validate_positive_int(data, "email_alert.timeout_seconds")
    _validate_positive_int(data, "email_alert.retry_limit")
    if not all(isinstance(item, str) and item.strip() for item in email_recipients):
        raise ValueError("Config key email_alert.recipients must contain only non-empty strings")
    if email_enabled and (not email_sender.strip() or not email_recipients):
        raise ValueError(
            "Enabled email alerts require email_alert.sender and at least one recipient"
        )

    min_tasks_per_role = _validate_positive_int(data, "generator.min_tasks_per_role")
    max_tasks_per_role = _validate_positive_int(data, "generator.max_tasks_per_role")
    if max_tasks_per_role < min_tasks_per_role:
        raise ValueError("Config key generator.max_tasks_per_role must be >= generator.min_tasks_per_role")
    ratio = _read_required(data, "generator.min_non_five_ratio", (int, float))
    if ratio <= 0 or ratio > 1:
        raise ValueError("Config key generator.min_non_five_ratio must be in (0, 1]")
    _validate_positive_int(data, "generator.max_attempts")
    _validate_positive_int(data, "generator.generation_retry_interval_seconds")
    min_internal = _read_required(data, "generator.min_internal", int)
    if min_internal < 10:
        raise ValueError("Config key generator.min_internal must be >= 10")
    max_feasible_tasks = WORKDAY_MINUTES // min_internal
    target_tasks = math.ceil((min_tasks_per_role + max_tasks_per_role) / 2)
    if (
        min_tasks_per_role > max_feasible_tasks
        or max_tasks_per_role > max_feasible_tasks
        or target_tasks > max_feasible_tasks
    ):
        raise ValueError(
            "generator 任务数量与间隔: 按工作日 7 小时(420 分钟)、任务最小间隔 "
            f"min_internal={min_internal} 分钟估算，每角色单日最多可安排约 {max_feasible_tasks} 条任务。"
            f"当前 min_tasks_per_role={min_tasks_per_role}、max_tasks_per_role={max_tasks_per_role}、"
            f"ceil((min+max)/2)={target_tasks} 已超过该上限，请调低任务数量配置或减小 generator.min_internal。"
        )
    _read_required(data, "generator.api_base_url", str)
    _read_required(data, "generator.api_key", str)
    _read_required(data, "generator.model", str)
    _validate_positive_int(data, "generator.request_timeout_seconds")
    _validate_positive_int(data, "generator.max_tokens")

    _read_required(data, "paths.logs_dir", str)
    _read_required(data, "paths.target_ini_file", str)
    _read_required(data, "paths.dispatch_script", str)
    _read_required(data, "paths.domain_resource_file", str)

    _read_required(data, "logging.level", str)
    _validate_positive_int(data, "logging.backup_count")
    _validate_positive_int(data, "logging.rotation_interval_days")


def load_runtime_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load and validate runtime config from JSON."""
    resolved = (config_path or default_runtime_config_path()).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Runtime config not found: {resolved}")

    try:
        with open(resolved, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in runtime config {resolved}: {e}")

    if not isinstance(data, dict):
        raise ValueError("Runtime config root must be a JSON object")

    _validate_comment_pairs(data)
    _validate_schema(data)
    return data


def get_server_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": _read_required(data, "server.host", str),
        "port": _read_required(data, "server.port", int),
        "max_line_bytes": _read_required(data, "server.max_line_bytes", int),
        "recv_chunk_bytes": _read_required(data, "server.recv_chunk_bytes", int),
        "socket_timeout_seconds": _read_required(data, "server.socket_timeout_seconds", int),
        "listen_backlog": _read_required(data, "server.listen_backlog", int),
        "worker_threads": _read_required(data, "server.worker_threads", int),
    }


def get_scanner_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "data_dir": _read_required(data, "scanner.data_dir", str),
        "scan_interval_seconds": _read_required(data, "scanner.scan_interval_seconds", int),
        "max_dispatch_lateness_minutes": _read_required(
            data, "scanner.max_dispatch_lateness_minutes", int
        ),
    }


def get_storage_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "lock_timeout_seconds": _read_required(data, "storage.lock_timeout_seconds", int),
        "max_store_text": _read_required(data, "storage.max_store_text", int),
    }


def get_dispatch_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "soldier_timeout_seconds": float(_read_required(data, "dispatch.soldier_timeout_seconds", (int, float))),
        "client_timeout_seconds": _read_required(data, "dispatch.client_timeout_seconds", int),
        "timeout_minutes": _read_required(data, "dispatch.timeout_minutes", int),
    }


def get_failure_policy_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "cooldown_seconds": _read_required(data, "failure_policy.cooldown_seconds", int),
        "max_consecutive_failures": _read_required(
            data, "failure_policy.max_consecutive_failures", int
        ),
        "state_file": _read_required(data, "failure_policy.state_file", str),
    }


def get_email_alert_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": _read_required(data, "email_alert.enabled", bool),
        "smtp_host": _read_required(data, "email_alert.smtp_host", str),
        "smtp_port": _read_required(data, "email_alert.smtp_port", int),
        "sender": _read_required(data, "email_alert.sender", str),
        "recipients": list(_read_required(data, "email_alert.recipients", list)),
        "auth_code_env": _read_required(data, "email_alert.auth_code_env", str),
        "timeout_seconds": _read_required(data, "email_alert.timeout_seconds", int),
        "retry_limit": _read_required(data, "email_alert.retry_limit", int),
    }


def get_generator_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "min_tasks_per_role": _read_required(data, "generator.min_tasks_per_role", int),
        "max_tasks_per_role": _read_required(data, "generator.max_tasks_per_role", int),
        "min_non_five_ratio": float(_read_required(data, "generator.min_non_five_ratio", (int, float))),
        "max_attempts": _read_required(data, "generator.max_attempts", int),
        "generation_retry_interval_seconds": _read_required(
            data, "generator.generation_retry_interval_seconds", int
        ),
        "min_internal": _read_required(data, "generator.min_internal", int),
        "api_base_url": _read_required(data, "generator.api_base_url", str),
        "api_key": _read_required(data, "generator.api_key", str),
        "model": _read_required(data, "generator.model", str),
        "request_timeout_seconds": _read_required(data, "generator.request_timeout_seconds", int),
        "max_tokens": _read_required(data, "generator.max_tokens", int),
    }


def get_paths_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "logs_dir": _read_required(data, "paths.logs_dir", str),
        "target_ini_file": _read_required(data, "paths.target_ini_file", str),
        "dispatch_script": _read_required(data, "paths.dispatch_script", str),
        "domain_resource_file": _read_required(data, "paths.domain_resource_file", str),
    }


def get_logging_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": _read_required(data, "logging.level", str),
        "backup_count": _read_required(data, "logging.backup_count", int),
        "rotation_interval_days": _read_required(data, "logging.rotation_interval_days", int),
    }
