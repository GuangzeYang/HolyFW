#!/usr/bin/env python3
"""Target endpoint config loader for dispatch."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = "commander.ini"

TIME_MODEL_INI_KEYS = (
    "tasks_per_role",
    "mu_am_minutes",
    "mu_pm_minutes",
    "sigma_am_minutes",
    "sigma_pm_minutes",
    "a_am",
    "a_pm",
    "phi",
    "sigma_eta",
)

# Roles that remain dispatchable but are never included in the daily
# office-traffic generation quota (tasks_per_role in time-model defaults / INI).
# victim is driven by commander victim; attacker has its own package.
ON_DEMAND_ROLES = frozenset({"victim", "attacker"})


def default_config_path() -> Path:
    """Return default config file path (same directory as script)."""
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_NAME


def load_all_roles(config_path: Path) -> tuple[str, ...]:
    """Load all INI section names as roles."""
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")

    roles: list[str] = []
    seen: set[str] = set()
    for section in cp.sections():
        role = section.strip().lower()
        if not role or role in seen:
            continue
        seen.add(role)
        roles.append(role)

    if not roles:
        raise ValueError(f"No role sections found in config file {config_path}")

    return tuple(roles)


def load_daily_generation_roles(config_path: Path) -> tuple[str, ...]:
    """Load roles that participate in daily benign-traffic task generation."""
    return tuple(role for role in load_all_roles(config_path) if role not in ON_DEMAND_ROLES)


def load_target_config(config_path: Path, target: str) -> tuple[str, int]:
    """Load host and port for a role target from INI config file."""
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")

    target_lower = target.lower()
    if target_lower not in cp:
        raise ValueError(f"Target '{target}' not defined in config file {config_path}")

    sec = cp[target_lower]
    host = (sec.get("host") or "").strip()
    port_str = (sec.get("port") or "").strip()

    if not host:
        raise ValueError(f"Target '{target}' missing 'host' in config file")
    if not port_str:
        raise ValueError(f"Target '{target}' missing 'port' in config file")

    try:
        port = int(port_str)
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid port {port} for target '{target}', must be 1-65535")
    except ValueError as e:
        raise ValueError(f"Invalid port '{port_str}' for target '{target}': {e}")

    return host, port


def _parse_time_model_float(raw: str, *, role: str, key: str) -> float:
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid number '{raw}' for {key} on role '{role}'") from exc


def _parse_time_model_int(raw: str, *, role: str, key: str) -> int:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid number '{raw}' for {key} on role '{role}'") from exc
    if value <= 0 or not value.is_integer():
        raise ValueError(f"Invalid integer '{raw}' for {key} on role '{role}'")
    return int(value)


def load_role_time_model(
    config_path: Path,
    role: str,
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Return time-model params for a role: INI overlay on config.json defaults.

    Each key in ``TIME_MODEL_INI_KEYS`` uses the INI value when that option is
    present and non-empty; otherwise the matching ``defaults`` value is kept.
    ``avoid_five_minutes`` is JSON-only and is never taken from INI.
    """
    missing = [key for key in TIME_MODEL_INI_KEYS if key not in defaults]
    if missing:
        raise ValueError(f"Time-model defaults missing keys: {', '.join(missing)}")
    if "avoid_five_minutes" not in defaults:
        raise ValueError("Time-model defaults missing keys: avoid_five_minutes")

    merged: dict[str, Any] = dict(defaults)
    if not config_path.is_file():
        return merged

    cp = configparser.ConfigParser()
    cp.read(config_path, encoding="utf-8")
    target_lower = role.strip().lower()
    if target_lower not in cp:
        return merged

    sec = cp[target_lower]
    for key in TIME_MODEL_INI_KEYS:
        if key not in sec:
            continue
        raw = (sec.get(key) or "").strip()
        if not raw:
            continue
        if key == "tasks_per_role":
            merged[key] = _parse_time_model_int(raw, role=role, key=key)
        else:
            merged[key] = _parse_time_model_float(raw, role=role, key=key)
    return merged
