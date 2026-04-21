#!/usr/bin/env python3
"""Generate daily role task sequences using the DeepSeek API."""

from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from target_config import load_all_roles
except ImportError:
    from commander.target_config import load_all_roles

try:
    from deepseek_client import build_deepseek_config
    from role_task_generation import generate_role_tasks_via_deepseek
    from runtime_config import (
        get_generator_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.deepseek_client import build_deepseek_config
    from commander.role_task_generation import generate_role_tasks_via_deepseek
    from commander.runtime_config import (
        get_generator_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )

from common import build_controlled_task_file_paths


def main() -> int:
    runtime_config = load_runtime_config()
    scanner_config = get_scanner_config(runtime_config)
    generator_config = get_generator_config(runtime_config)
    paths_config = get_paths_config(runtime_config)
    target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
    roles = load_all_roles(target_ini_path)

    output_dir = resolve_config_relative_path(scanner_config["data_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = resolve_config_relative_path(paths_config["logs_dir"])
    domain_resource_path = resolve_config_relative_path(paths_config["domain_resource_file"])

    _, output_file = build_controlled_task_file_paths(output_dir, date.today())

    result = generate_role_tasks_via_deepseek(
        source="generate_role_task",
        final_file=output_file,
        logs_dir=logs_dir,
        domain_resource_path=domain_resource_path,
        roles=roles,
        min_tasks_per_role=generator_config["min_tasks_per_role"],
        max_tasks_per_role=generator_config["max_tasks_per_role"],
        min_non_five_ratio=generator_config["min_non_five_ratio"],
        max_attempts=generator_config["max_attempts"],
        deepseek_config=build_deepseek_config(generator_config),
        emit_status=lambda message: print(message, flush=True),
    )
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
