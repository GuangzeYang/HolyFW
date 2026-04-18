#!/usr/bin/env python3
"""Role task file service for ensure/load/save/generate workflows."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

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
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_generator_config,
        get_opencode_paths,
        get_paths_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


class RoleTaskFileService:
    """Manage the daily unified role task file lifecycle."""

    LEGACY_ROLE_KEY_MAP = {
        "finance": "accountancy",
        "ceo": "manager",
        "developer": "programmer",
        "it": "programmer",
    }

    def __init__(
        self,
        data_dir: Path,
        roles: tuple[str, ...],
        min_tasks_per_role: int | None = None,
        min_non_five_ratio: float | None = None,
        max_attempts: int | None = None,
        opencode_timeout_sec: int | None = None,
        opencode_paths: list[str] | None = None,
        domain_resource_file: Path | None = None,
    ):
        if (
            min_tasks_per_role is None
            or min_non_five_ratio is None
            or max_attempts is None
            or opencode_timeout_sec is None
            or opencode_paths is None
            or domain_resource_file is None
        ):
            runtime_config = load_runtime_config()
            generator_config = get_generator_config(runtime_config)
            paths_config = get_paths_config(runtime_config)
            if min_tasks_per_role is None:
                min_tasks_per_role = generator_config["min_tasks_per_role"]
            if min_non_five_ratio is None:
                min_non_five_ratio = generator_config["min_non_five_ratio"]
            if max_attempts is None:
                max_attempts = generator_config["max_attempts"]
            if opencode_timeout_sec is None:
                opencode_timeout_sec = generator_config["opencode_timeout_seconds"]
            if opencode_paths is None:
                opencode_paths = get_opencode_paths(runtime_config)
            if domain_resource_file is None:
                domain_resource_file = resolve_config_relative_path(paths_config["domain_resource_file"])

        self.data_dir = data_dir
        self.roles = roles
        self.min_tasks_per_role = min_tasks_per_role
        self.min_non_five_ratio = min_non_five_ratio
        self.max_attempts = max_attempts
        self.opencode_timeout_sec = opencode_timeout_sec
        self.opencode_paths = opencode_paths
        self.domain_resource_file = domain_resource_file

    def _migrate_legacy_role_keys(self, data: dict[str, Any]) -> list[str]:
        migrated: list[str] = []
        for old_role, new_role in self.LEGACY_ROLE_KEY_MAP.items():
            old_tasks = data.get(old_role)
            if not isinstance(old_tasks, list):
                continue

            new_tasks = data.get(new_role)
            if not isinstance(new_tasks, list):
                new_tasks = []
                data[new_role] = new_tasks

            moved_count = len(old_tasks)
            if moved_count:
                new_tasks.extend(old_tasks)
            del data[old_role]
            migrated.append(f"{old_role}->{new_role} ({moved_count})")
        return migrated

    def get_today_role_task_file(self) -> Path:
        today = date.today().isoformat()
        month_day = today[5:]
        return self.data_dir / f"tasks_{month_day}.json"

    def ensure_role_file(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        if role_file.exists():
            try:
                with open(role_file, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    logging.warning(f"Role file {role_file} is not a dict")
                    return False

                migrated_roles = self._migrate_legacy_role_keys(data)
                if migrated_roles:
                    with open(role_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logging.info(
                        f"Role file {role_file} migrated legacy role keys: {', '.join(migrated_roles)}"
                    )

                has_runtime_fields = False
                for tasks in data.values():
                    if not isinstance(tasks, list):
                        continue
                    for item in tasks:
                        if not isinstance(item, dict):
                            continue
                        status_value = item.get("status")
                        task_id_value = item.get("task_id")
                        issued_value = item.get("issued_at")
                        if (
                            status_value in {"waiting", "successed", "failed"}
                            or bool(task_id_value)
                            or bool(issued_value)
                            or bool(item.get("is_load", False))
                        ):
                            has_runtime_fields = True
                            break
                    if has_runtime_fields:
                        break

                # Do not normalize live task files to avoid overwriting runtime state.
                if has_runtime_fields:
                    missing_roles: list[str] = []
                    schema_updated = False
                    healed_stuck_tasks = 0
                    for role in self.roles:
                        tasks = data.get(role)
                        if not isinstance(tasks, list):
                            data[role] = []
                            missing_roles.append(role)
                            schema_updated = True
                            continue

                        for item in tasks:
                            if not isinstance(item, dict):
                                continue
                            if "description" in item:
                                del item["description"]
                                schema_updated = True
                            base_desc = item.get("task") if isinstance(item.get("task"), str) else ""
                            status_value = item.get("status")
                            task_id_value = item.get("task_id")
                            if (
                                status_value == "planned"
                                and not task_id_value
                                and bool(item.get("is_load", False))
                            ):
                                item["is_load"] = False
                                healed_stuck_tasks += 1
                                schema_updated = True

                            defaults = {
                                "time": "",
                                "is_load": False,
                                "task": base_desc,
                                "task_id": "",
                                "status": "planned",
                                "issued_at": "",
                                "expiry_time": "",
                                "completed_at": "",
                                "report_message": "",
                                "exit_code": None,
                                "stdout": "",
                                "stderr": "",
                            }
                            for key, value in defaults.items():
                                if key not in item:
                                    item[key] = value
                                    schema_updated = True
                    if missing_roles:
                        logging.warning(
                            f"Role file {role_file} missing role lists {missing_roles}; auto-filled as empty lists"
                        )
                    if healed_stuck_tasks:
                        logging.warning(
                            f"Role file {role_file} healed {healed_stuck_tasks} stuck planned tasks with is_load=true"
                        )
                    if schema_updated:
                        with open(role_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        logging.info(f"Role file {role_file} runtime schema auto-backfilled")
                    logging.info(f"Role file {role_file} exists and contains runtime fields")
                    return True

                valid, reason = validate_role_tasks(
                    data,
                    min_tasks_per_role=self.min_tasks_per_role,
                    min_non_five_ratio=self.min_non_five_ratio,
                    roles=self.roles,
                )
                if not valid:
                    logging.warning(f"Role file {role_file} quality check failed: {reason}")
                    normalized = normalize_role_tasks(
                        data,
                        min_tasks_per_role=self.min_tasks_per_role,
                        roles=self.roles,
                    )
                    valid_after_fix, reason_after_fix = validate_role_tasks(
                        normalized,
                        min_tasks_per_role=self.min_tasks_per_role,
                        min_non_five_ratio=self.min_non_five_ratio,
                        roles=self.roles,
                    )
                    if not valid_after_fix:
                        logging.warning(
                            f"Failed to normalize role file {role_file}: {reason_after_fix}"
                        )
                        return False
                    with open(role_file, "w", encoding="utf-8") as f:
                        json.dump(normalized, f, ensure_ascii=False, indent=2)
                    logging.info(f"Role file {role_file} was normalized and repaired")
                logging.info(f"Role file {role_file} exists and is valid")
                return True
            except (json.JSONDecodeError, OSError) as e:
                logging.warning(f"Role file {role_file} is corrupted: {e}")

        logging.info(f"Generating new role task file: {role_file}")
        return self.generate_role_tasks(role_file)

    def generate_role_tasks(self, role_file: Path) -> bool:
        """Generate role tasks using opencode CLI."""
        try:
            import subprocess

            domain_resource_path = self.domain_resource_file
            domain_context = ""
            if domain_resource_path.exists():
                with open(domain_resource_path, encoding="utf-8") as f:
                    domain_context = f.read()
                logging.info(f"Read domain resource from {domain_resource_path}")
            else:
                logging.warning(f"Domain resource not found: {domain_resource_path}")

            min_tasks_per_role = self.min_tasks_per_role
            max_attempts = self.max_attempts
            opencode_timeout_sec = self.opencode_timeout_sec
            prompt = build_role_task_prompt(
                domain_context,
                min_tasks_per_role=min_tasks_per_role,
                roles=self.roles,
            )
            opencode_paths = list(self.opencode_paths)

            saw_timeout = False
            saw_nonzero_exit = False
            saw_missing_binary = False

            for attempt in range(1, max_attempts + 1):
                logging.info(f"Role task generation attempt {attempt}/{max_attempts}")
                for cmd in opencode_paths:
                    try:
                        logging.info(f"Trying opencode at: {cmd}")
                        result = subprocess.run(
                            [cmd, "run", prompt],
                            capture_output=True,
                            text=True,
                            timeout=opencode_timeout_sec,
                            shell=False,
                        )
                        if result.returncode != 0:
                            saw_nonzero_exit = True
                            logging.warning(f"opencode at {cmd} failed with exit code {result.returncode}")
                            continue

                        parsed = extract_json_object(result.stdout)
                        if parsed is None:
                            logging.error(f"No JSON found in opencode response from {cmd}")
                            logging.error(f"stdout (first 500 chars): {result.stdout[:500]}")
                            continue

                        data = normalize_role_tasks(
                            parsed,
                            min_tasks_per_role=min_tasks_per_role,
                            roles=self.roles,
                        )
                        valid, reason = validate_role_tasks(
                            data,
                            min_tasks_per_role=min_tasks_per_role,
                            min_non_five_ratio=self.min_non_five_ratio,
                            roles=self.roles,
                        )
                        if not valid:
                            logging.warning(f"Generated role tasks failed quality checks: {reason}")
                            continue

                        with open(role_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        logging.info(f"Successfully generated role tasks file: {role_file} using {cmd}")
                        return True
                    except subprocess.TimeoutExpired:
                        saw_timeout = True
                        logging.warning(f"opencode at {cmd} timed out after {opencode_timeout_sec}s")
                        continue
                    except FileNotFoundError:
                        saw_missing_binary = True
                        logging.debug(f"opencode not found at: {cmd}")
                        continue
                    except Exception as e:
                        logging.warning(f"Error running opencode at {cmd}: {e}")
                        continue

            if saw_timeout:
                logging.error(
                    f"opencode execution timed out after {opencode_timeout_sec}s. "
                    "Consider increasing timeout or reducing prompt complexity."
                )
            elif saw_nonzero_exit:
                logging.error("opencode returned non-zero exit code for all attempts")
            elif saw_missing_binary:
                logging.error(f"Could not find opencode binary. Tried paths: {opencode_paths}")
            else:
                logging.error(f"Could not execute opencode. Tried paths: {opencode_paths}")

            fallback_data = normalize_role_tasks(
                {},
                min_tasks_per_role=min_tasks_per_role,
                roles=self.roles,
            )
            valid, reason = validate_role_tasks(
                fallback_data,
                min_tasks_per_role=min_tasks_per_role,
                min_non_five_ratio=self.min_non_five_ratio,
                roles=self.roles,
            )
            if not valid:
                logging.error(f"Fallback task generation failed validation: {reason}")
                return False
            with open(role_file, "w", encoding="utf-8") as f:
                json.dump(fallback_data, f, ensure_ascii=False, indent=2)
            logging.warning("Generated fallback unified tasks because opencode output was unusable")
            return True
        except Exception as e:
            logging.error(f"Exception in generate_role_tasks: {e}")
            return False

    def load_role_tasks(self, role_file: Path) -> dict[str, Any]:
        try:
            with open(role_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Failed to load role tasks from {role_file}: {e}")
            return {}

    def save_role_tasks(self, role_file: Path, data: dict[str, Any]) -> None:
        try:
            with open(role_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logging.debug(f"Saved role tasks to {role_file}")
        except OSError as e:
            logging.error(f"Failed to save role tasks to {role_file}: {e}")
