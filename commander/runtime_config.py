#!/usr/bin/env python3
"""Runtime configuration loader for commander modules."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from common import commander_workspace_dir

WORKDAY_MINUTES = 8 * 60
MIN_INTERNAL_MINUTES = 5

CONFIG_FILE_NAME = "config.json"


def _package_dir() -> Path:
    return Path(__file__).resolve().parent


def default_runtime_config_path() -> Path:
    """Prefer workspace commander/config.json; fall back to the installed package copy."""
    try:
        workspace_cfg = commander_workspace_dir(package_hint=_package_dir()) / CONFIG_FILE_NAME
        if workspace_cfg.is_file():
            return workspace_cfg
    except FileNotFoundError:
        pass
    return _package_dir() / CONFIG_FILE_NAME


def resolve_config_relative_path(raw_path: str) -> Path:
    """Resolve a config path string against the workspace commander/ directory."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Path value must be a non-empty string")
    expanded = Path(os.path.expanduser(raw_path.strip()))
    if expanded.is_absolute():
        return expanded.resolve()
    candidate = (commander_workspace_dir(package_hint=_package_dir()) / expanded).resolve()
    if candidate.exists():
        return candidate
    try:
        from holyfw_assets import bundled_file
    except ImportError:
        return candidate
    fallback = bundled_file(Path(raw_path.strip()).name)
    if fallback is not None:
        return fallback.resolve()
    return candidate


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


def _validate_positive_int(data: dict[str, Any], dot_path: str) -> int:
    value = _read_required(data, dot_path, int)
    if value <= 0:
        raise ValueError(f"Config key {dot_path} must be > 0")
    return value


def _validate_hour_of_day(data: dict[str, Any], dot_path: str) -> int:
    value = _read_required(data, dot_path, int)
    if isinstance(value, bool) or not 0 <= value <= 23:
        raise ValueError(f"Config key {dot_path} must be an integer 0..23")
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
    _validate_hour_of_day(data, "scanner.base_time")

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

    tasks_per_role = _validate_positive_int(data, "generator.time_model.tasks_per_role")
    _validate_positive_int(data, "generator.max_attempts")
    _validate_positive_int(data, "generator.generation_retry_interval_seconds")
    min_internal = _read_required(data, "generator.min_internal", int)
    if min_internal < MIN_INTERNAL_MINUTES:
        raise ValueError(
            f"Config key generator.min_internal must be >= {MIN_INTERNAL_MINUTES}"
        )
    max_feasible_tasks = WORKDAY_MINUTES // min_internal
    if tasks_per_role > max_feasible_tasks:
        raise ValueError(
            "Generator task count and interval are not feasible: using an 8-hour (480-minute) workday "
            f"and min_internal={min_internal} minutes, each role can have at most about {max_feasible_tasks} tasks per day. "
            f"The configured generator.time_model.tasks_per_role={tasks_per_role} exceeds that limit. "
            "Reduce generator.time_model.tasks_per_role or lower generator.min_internal."
        )
    _read_required(data, "generator.api_base_url", str)
    _read_required(data, "generator.model", str)
    _validate_positive_int(data, "generator.request_timeout_seconds")
    _validate_positive_int(data, "generator.max_tokens")
    _validate_time_model(data)

    _read_required(data, "paths.logs_dir", str)
    _read_required(data, "paths.target_ini_file", str)
    _read_required(data, "paths.dispatch_script", str)
    _read_required(data, "paths.domain_resource_file", str)
    _read_required(data, "paths.task_generation_constraints_file", str)

    _read_required(data, "logging.level", str)
    _validate_positive_int(data, "logging.backup_count")
    _validate_positive_int(data, "logging.rotation_interval_days")


def _validate_time_model(data: dict[str, Any]) -> None:
    _validate_positive_int(data, "generator.time_model.tasks_per_role")
    _read_required(data, "generator.time_model.mu_am_minutes", (int, float))
    _read_required(data, "generator.time_model.mu_pm_minutes", (int, float))
    sigma_am = _read_required(data, "generator.time_model.sigma_am_minutes", (int, float))
    sigma_pm = _read_required(data, "generator.time_model.sigma_pm_minutes", (int, float))
    if sigma_am <= 0 or sigma_pm <= 0:
        raise ValueError("Config keys generator.time_model.sigma_*_minutes must be > 0")
    _read_required(data, "generator.time_model.a_am", (int, float))
    _read_required(data, "generator.time_model.a_pm", (int, float))
    phi = _read_required(data, "generator.time_model.phi", (int, float))
    if abs(float(phi)) >= 1:
        raise ValueError("Config key generator.time_model.phi must satisfy |phi| < 1")
    sigma_eta = _read_required(data, "generator.time_model.sigma_eta", (int, float))
    if float(sigma_eta) <= 0:
        raise ValueError("Config key generator.time_model.sigma_eta must be > 0")
    _read_required(data, "generator.time_model.avoid_five_minutes", bool)


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
        "base_time": _read_required(data, "scanner.base_time", int),
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
        "max_attempts": _read_required(data, "generator.max_attempts", int),
        "generation_retry_interval_seconds": _read_required(
            data, "generator.generation_retry_interval_seconds", int
        ),
        "min_internal": _read_required(data, "generator.min_internal", int),
        "api_base_url": _read_required(data, "generator.api_base_url", str),
        "model": _read_required(data, "generator.model", str),
        "request_timeout_seconds": _read_required(data, "generator.request_timeout_seconds", int),
        "max_tokens": _read_required(data, "generator.max_tokens", int),
        "time_model": {
            "tasks_per_role": int(_read_required(data, "generator.time_model.tasks_per_role", int)),
            "mu_am_minutes": float(_read_required(data, "generator.time_model.mu_am_minutes", (int, float))),
            "mu_pm_minutes": float(_read_required(data, "generator.time_model.mu_pm_minutes", (int, float))),
            "sigma_am_minutes": float(_read_required(data, "generator.time_model.sigma_am_minutes", (int, float))),
            "sigma_pm_minutes": float(_read_required(data, "generator.time_model.sigma_pm_minutes", (int, float))),
            "a_am": float(_read_required(data, "generator.time_model.a_am", (int, float))),
            "a_pm": float(_read_required(data, "generator.time_model.a_pm", (int, float))),
            "phi": float(_read_required(data, "generator.time_model.phi", (int, float))),
            "sigma_eta": float(_read_required(data, "generator.time_model.sigma_eta", (int, float))),
            "avoid_five_minutes": bool(_read_required(data, "generator.time_model.avoid_five_minutes", bool)),
        },
    }


def get_paths_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "logs_dir": _read_required(data, "paths.logs_dir", str),
        "target_ini_file": _read_required(data, "paths.target_ini_file", str),
        "dispatch_script": _read_required(data, "paths.dispatch_script", str),
        "domain_resource_file": _read_required(data, "paths.domain_resource_file", str),
        "task_generation_constraints_file": _read_required(
            data, "paths.task_generation_constraints_file", str
        ),
    }


def get_logging_config(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "level": _read_required(data, "logging.level", str),
        "backup_count": _read_required(data, "logging.backup_count", int),
        "rotation_interval_days": _read_required(data, "logging.rotation_interval_days", int),
    }
