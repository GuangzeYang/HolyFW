#!/usr/bin/env python3
"""Generate daily role task sequences using the configured model client."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from target_config import load_daily_generation_roles
except ImportError:
    from commander.target_config import load_daily_generation_roles

try:
    from deepseek_client import build_deepseek_client
    from role_task_generation import generate_role_tasks
    from runtime_config import (
        get_generator_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.deepseek_client import build_deepseek_client
    from commander.role_task_generation import generate_role_tasks
    from commander.runtime_config import (
        get_generator_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )

from common import build_controlled_task_file_paths, load_task_file


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily HolyFW office-role task files.")
    parser.add_argument(
        "--statistic",
        action="store_true",
        help="After generation succeeds, print time lists and write a 30-minute bin chart.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for --statistic artifacts (default: repository root).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    runtime_config = load_runtime_config()
    scanner_config = get_scanner_config(runtime_config)
    generator_config = get_generator_config(runtime_config)
    paths_config = get_paths_config(runtime_config)
    target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
    roles = load_daily_generation_roles(target_ini_path)
    if not roles:
        raise ValueError(
            "No daily-generation roles found in commander.ini "
            "(on-demand roles such as victim are excluded)"
        )

    output_dir = resolve_config_relative_path(scanner_config["data_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = resolve_config_relative_path(paths_config["logs_dir"])
    domain_resource_path = resolve_config_relative_path(paths_config["domain_resource_file"])
    constraints_resource_path = resolve_config_relative_path(
        paths_config["task_generation_constraints_file"]
    )

    interval = generator_config["generation_retry_interval_seconds"]
    agent_client = build_deepseek_client(generator_config)
    emit_status = lambda message: print(message, flush=True)

    try:
        while True:
            _, output_file = build_controlled_task_file_paths(output_dir, date.today())
            result = generate_role_tasks(
                source="generate_role_task",
                final_file=output_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=constraints_resource_path,
                roles=roles,
                max_attempts=generator_config["max_attempts"],
                agent_client=agent_client,
                emit_status=emit_status,
                time_model_config=generator_config["time_model"],
                target_ini_path=target_ini_path,
            )
            if result.success:
                if args.statistic:
                    try:
                        from time_model import (
                            _statistic_output_dir,
                            format_statistic_report,
                            write_role_schedule_statistics_from_tasks,
                        )
                    except ImportError:
                        from commander.time_model import (
                            _statistic_output_dir,
                            format_statistic_report,
                            write_role_schedule_statistics_from_tasks,
                        )
                    payload = write_role_schedule_statistics_from_tasks(
                        load_task_file(output_file),
                        roles=roles,
                        day=date.today().isoformat(),
                        output_dir=_statistic_output_dir(args.output_dir),
                    )
                    print(format_statistic_report(payload), flush=True)
                return 0
            print(
                f"Generation did not succeed; retrying in {interval} seconds. "
                f"Reason: {result.failure_reason!r}",
                flush=True,
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Interrupted.", flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
