#!/usr/bin/env python3
"""Generate daily role task sequences using opencode CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from common import (
    build_role_task_prompt,
    extract_json_object,
    normalize_role_tasks,
    validate_role_tasks,
)

try:
    from runtime_config import (
        get_generator_config,
        get_opencode_paths,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_generator_config,
        get_opencode_paths,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


def main() -> int:
    runtime_config = load_runtime_config()
    scanner_config = get_scanner_config(runtime_config)
    generator_config = get_generator_config(runtime_config)
    paths_config = get_paths_config(runtime_config)

    min_tasks_per_role = generator_config["min_tasks_per_role"]
    max_generate_attempts = generator_config["max_attempts"]
    opencode_timeout_sec = generator_config["opencode_timeout_seconds"]
    quality_ratio = generator_config["min_non_five_ratio"]

    output_dir = resolve_config_relative_path(scanner_config["data_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today()
    output_file = output_dir / f"tasks_{today.month:02d}-{today.day:02d}.json"
    if output_file.exists():
        print(f"Unified task file already exists: {output_file}")
        return 0

    domain_resource_path = resolve_config_relative_path(paths_config["domain_resource_file"])
    domain_context = ""
    if domain_resource_path.exists():
        try:
            with open(domain_resource_path, encoding="utf-8") as f:
                domain_context = f.read()
        except OSError as e:
            print(f"Warning: Failed to read domain resource: {e}")
    else:
        print(f"Warning: domain resource not found at {domain_resource_path}")

    prompt = build_role_task_prompt(domain_context, min_tasks_per_role=min_tasks_per_role)
    opencode_paths = get_opencode_paths(runtime_config)

    saw_timeout = False
    saw_nonzero_exit = False
    saw_missing_binary = False

    for attempt in range(1, max_generate_attempts + 1):
        print(f"Generation attempt {attempt}/{max_generate_attempts}")
        for cmd in opencode_paths:
            try:
                print(f"Trying opencode at: {cmd}")
                result = subprocess.run(
                    [cmd, "run", prompt],
                    capture_output=True,
                    text=True,
                    timeout=opencode_timeout_sec,
                    shell=False,
                )
                if result.returncode != 0:
                    saw_nonzero_exit = True
                    print(f"opencode at {cmd} failed with exit code {result.returncode}")
                    if result.stderr:
                        print(f"stderr: {result.stderr[:300]}")
                    continue

                parsed = extract_json_object(result.stdout)
                if parsed is None:
                    print("No JSON found in opencode response")
                    print(f"stdout: {result.stdout[:500]}")
                    continue

                data = normalize_role_tasks(parsed, min_tasks_per_role=min_tasks_per_role)
                valid, reason = validate_role_tasks(
                    data,
                    min_tasks_per_role=min_tasks_per_role,
                    min_non_five_ratio=quality_ratio,
                )
                if not valid:
                    print(f"Generated tasks failed quality checks: {reason}")
                    continue

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"Successfully generated unified tasks: {output_file} using {cmd}")
                return 0
            except subprocess.TimeoutExpired:
                saw_timeout = True
                print(f"opencode at {cmd} timed out after {opencode_timeout_sec}s")
                continue
            except FileNotFoundError:
                saw_missing_binary = True
                print(f"opencode not found at: {cmd}")
                continue
            except Exception as e:
                print(f"Error running opencode at {cmd}: {e}")
                continue

    if saw_timeout:
        print(
            f"Could not generate qualified role tasks: opencode timed out after {opencode_timeout_sec}s"
        )
    elif saw_nonzero_exit:
        print("Could not generate qualified role tasks: opencode exited with non-zero status")
    elif saw_missing_binary:
        print(f"Could not generate qualified role tasks: opencode not found. Tried paths: {opencode_paths}")
    else:
        print(f"Could not generate qualified role tasks. Tried paths: {opencode_paths}")

    fallback_data = normalize_role_tasks({}, min_tasks_per_role=min_tasks_per_role)
    valid, reason = validate_role_tasks(
        fallback_data,
        min_tasks_per_role=min_tasks_per_role,
        min_non_five_ratio=quality_ratio,
    )
    if not valid:
        print(f"Fallback task generation validation failed: {reason}")
        return 1

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(fallback_data, f, ensure_ascii=False, indent=2)
    print(f"Generated fallback unified tasks: {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
