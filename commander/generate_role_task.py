#!/usr/bin/env python3
"""Generate daily role task sequences using opencode CLI."""

import json
import os
import platform
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


MIN_TASKS_PER_ROLE = 18
MAX_GENERATE_ATTEMPTS = 3
OPENCODE_TIMEOUT_SEC = 180


def get_opencode_paths() -> list[str]:
    """Return candidate opencode command paths by platform."""
    paths = ["opencode"]
    system = platform.system()
    if system == "Windows":
        paths.extend(
            [
                "C:\\Users\\21276\\AppData\\Roaming\\npm\\opencode",
                "C:\\Users\\21276\\AppData\\Roaming\\npm\\opencode.cmd",
                os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode"),
                os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode.cmd"),
            ]
        )
    elif system == "Linux":
        paths.extend(
            [
                "/usr/local/bin/opencode",
                "/usr/bin/opencode",
                os.path.expanduser("~/.npm/bin/opencode"),
                os.path.expanduser("~/.local/bin/opencode"),
            ]
        )
    return paths

def main() -> int:
    # Determine output directory
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / "role_task"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate filename
    today = date.today()
    output_file = output_dir / f"tasks_{today.month:02d}-{today.day:02d}.json"
    
    # Check if file already exists
    if output_file.exists():
        print(f"Unified task file already exists: {output_file}")
        return 0
    
    # Read domain resource as context
    domain_resource_path = script_dir.parent / "domain_resource.md"
    domain_context = ""
    if domain_resource_path.exists():
        try:
            with open(domain_resource_path, encoding="utf-8") as f:
                domain_context = f.read()
        except OSError as e:
            print(f"Warning: Failed to read domain resource: {e}")
    else:
        print(f"Warning: domain_resource.md not found at {domain_resource_path}")
    
    prompt = build_role_task_prompt(domain_context, min_tasks_per_role=MIN_TASKS_PER_ROLE)
    opencode_paths = get_opencode_paths()

    saw_timeout = False
    saw_nonzero_exit = False
    saw_missing_binary = False

    for attempt in range(1, MAX_GENERATE_ATTEMPTS + 1):
        print(f"Generation attempt {attempt}/{MAX_GENERATE_ATTEMPTS}")
        for cmd in opencode_paths:
            try:
                print(f"Trying opencode at: {cmd}")
                result = subprocess.run(
                    [cmd, "run", prompt],
                    capture_output=True,
                    text=True,
                    timeout=OPENCODE_TIMEOUT_SEC,
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

                data = normalize_role_tasks(parsed, min_tasks_per_role=MIN_TASKS_PER_ROLE)
                valid, reason = validate_role_tasks(
                    data,
                    min_tasks_per_role=MIN_TASKS_PER_ROLE,
                    min_non_five_ratio=0.8,
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
                print(f"opencode at {cmd} timed out after {OPENCODE_TIMEOUT_SEC}s")
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
            f"Could not generate qualified role tasks: opencode timed out after {OPENCODE_TIMEOUT_SEC}s"
        )
    elif saw_nonzero_exit:
        print("Could not generate qualified role tasks: opencode exited with non-zero status")
    elif saw_missing_binary:
        print(f"Could not generate qualified role tasks: opencode not found. Tried paths: {opencode_paths}")
    else:
        print(f"Could not generate qualified role tasks. Tried paths: {opencode_paths}")

    fallback_data = normalize_role_tasks({}, min_tasks_per_role=MIN_TASKS_PER_ROLE)
    valid, reason = validate_role_tasks(
        fallback_data,
        min_tasks_per_role=MIN_TASKS_PER_ROLE,
        min_non_five_ratio=0.8,
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