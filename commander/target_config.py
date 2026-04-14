#!/usr/bin/env python3
"""Target endpoint config loader for dispatch."""

from __future__ import annotations

import configparser
from pathlib import Path

DEFAULT_CONFIG_NAME = "commander.ini"


def default_config_path() -> Path:
    """Return default config file path (same directory as script)."""
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_NAME


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
