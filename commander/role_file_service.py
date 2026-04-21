#!/usr/bin/env python3
"""Role task file service for ensure/load/save/generate workflows."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

try:
    from deepseek_client import DeepSeekConfig, build_deepseek_config
    from role_task_generation import generate_role_tasks_via_deepseek
except ImportError:
    from commander.deepseek_client import DeepSeekConfig, build_deepseek_config
    from commander.role_task_generation import generate_role_tasks_via_deepseek

from common import (
    candidate_task_path,
    normalize_role_tasks,
    validate_role_tasks,
)


try:
    from runtime_config import (
        get_generator_config,
        get_paths_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_generator_config,
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
        max_tasks_per_role: int | None = None,
        min_non_five_ratio: float | None = None,
        max_attempts: int | None = None,
        deepseek_config: DeepSeekConfig | None = None,
        domain_resource_file: Path | None = None,
        logs_dir: Path | None = None,
    ):
        if (
            min_tasks_per_role is None
            or max_tasks_per_role is None
            or min_non_five_ratio is None
            or max_attempts is None
            or deepseek_config is None
            or domain_resource_file is None
            or logs_dir is None
        ):
            runtime_config = load_runtime_config()
            generator_config = get_generator_config(runtime_config)
            paths_config = get_paths_config(runtime_config)
            if min_tasks_per_role is None:
                min_tasks_per_role = generator_config["min_tasks_per_role"]
            if max_tasks_per_role is None:
                max_tasks_per_role = generator_config["max_tasks_per_role"]
            if min_non_five_ratio is None:
                min_non_five_ratio = generator_config["min_non_five_ratio"]
            if max_attempts is None:
                max_attempts = generator_config["max_attempts"]
            if deepseek_config is None:
                deepseek_config = build_deepseek_config(generator_config)
            if domain_resource_file is None:
                domain_resource_file = resolve_config_relative_path(paths_config["domain_resource_file"])
            if logs_dir is None:
                logs_dir = resolve_config_relative_path(paths_config["logs_dir"])

        self.data_dir = data_dir
        self.roles = roles
        self.min_tasks_per_role = min_tasks_per_role
        self.max_tasks_per_role = max_tasks_per_role
        self.min_non_five_ratio = min_non_five_ratio
        self.max_attempts = max_attempts
        self.deepseek_config = deepseek_config
        self.domain_resource_file = domain_resource_file
        self.logs_dir = logs_dir

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
                    max_tasks_per_role=self.max_tasks_per_role,
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
                        max_tasks_per_role=self.max_tasks_per_role,
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
        """Generate role tasks using the DeepSeek API."""
        try:
            result = generate_role_tasks_via_deepseek(
                source="role_file_service",
                final_file=role_file,
                logs_dir=self.logs_dir,
                domain_resource_path=self.domain_resource_file,
                roles=self.roles,
                min_tasks_per_role=self.min_tasks_per_role,
                max_tasks_per_role=self.max_tasks_per_role,
                min_non_five_ratio=self.min_non_five_ratio,
                max_attempts=self.max_attempts,
                deepseek_config=self.deepseek_config,
                emit_status=logging.info,
            )
            if result.success:
                return True

            logging.error(
                "Failed to generate valid role tasks after maximum attempts: "
                + ", ".join(f"{key}={value}" for key, value in result.stats.items())
            )
            if result.failure_reason:
                logging.error(f"Last validation failure reason: {result.failure_reason}")
            logging.error("Stopping commander because role task generation did not meet requirements.")
            import os
            os._exit(1)
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
