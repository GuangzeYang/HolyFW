#!/usr/bin/env python3
"""Role task file service for ensure/load/save/generate workflows."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

try:
    from repository import DailyTaskRepository
    from role_task_generation import generate_role_tasks
except ImportError:
    from commander.repository import DailyTaskRepository
    from commander.role_task_generation import generate_role_tasks

from common.agent_request_abc import AgentRequestABC
from common.deepseek_client import build_deepseek_client

from common import (
    candidate_task_path,
    load_task_file,
    normalize_role_tasks,
    role_tasks_are_complete,
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

    def __init__(
        self,
        data_dir: Path,
        roles: tuple[str, ...],
        tasks_per_role: int | None = None,
        max_attempts: int | None = None,
        agent_client: AgentRequestABC | None = None,
        domain_resource_file: Path | None = None,
        constraints_resource_file: Path | None = None,
        logs_dir: Path | None = None,
        repository: DailyTaskRepository | None = None,
        time_model_config: dict[str, Any] | None = None,
        target_ini_path: Path | None = None,
        prompt_resources_dir: Path | None = None,
        statistic_output_dir: Path | None = None,
        base_time: int = 9,
    ):
        if (
            max_attempts is None
            or agent_client is None
            or domain_resource_file is None
            or constraints_resource_file is None
            or logs_dir is None
        ):
            runtime_config = load_runtime_config()
            generator_config = get_generator_config(runtime_config)
            paths_config = get_paths_config(runtime_config)
            if max_attempts is None:
                max_attempts = generator_config["max_attempts"]
            if agent_client is None:
                agent_client = build_deepseek_client(generator_config)
            if domain_resource_file is None:
                domain_resource_file = resolve_config_relative_path(paths_config["domain_resource_file"])
            if constraints_resource_file is None:
                constraints_resource_file = resolve_config_relative_path(
                    paths_config["task_generation_constraints_file"]
                )
            if logs_dir is None:
                logs_dir = resolve_config_relative_path(paths_config["logs_dir"])
            if time_model_config is None:
                time_model_config = generator_config["time_model"]
            if target_ini_path is None:
                target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])

        self.data_dir = data_dir
        self.roles = roles
        self.tasks_per_role = tasks_per_role
        self.max_attempts = max_attempts
        self.agent_client = agent_client
        self.domain_resource_file = domain_resource_file
        self.constraints_resource_file = constraints_resource_file
        self.logs_dir = logs_dir
        self.time_model_config = time_model_config
        self.target_ini_path = target_ini_path
        self.prompt_resources_dir = prompt_resources_dir
        self.statistic_output_dir = statistic_output_dir
        self.base_time = int(base_time)
        self.repository = repository or DailyTaskRepository(self.data_dir)
        self._generation_lock = threading.Lock()
        self._statistic_written_date: str | None = None

    def get_today_role_task_file(self) -> Path:
        return self.get_role_task_file(date.today())

    def get_role_task_file(self, day: date | str) -> Path:
        iso = day.isoformat() if isinstance(day, date) else str(day).strip()
        return self.repository.day_path(iso)

    def apply_base_time_shift(self, role_file: Path) -> bool:
        """Rewrite task times for testing. No-op when base_time is 9 and unstamped."""
        if not role_file.exists():
            return False
        try:
            from schedule_shift import apply_base_time_shift, file_day_from_tasks_path
        except ImportError:
            from commander.schedule_shift import apply_base_time_shift, file_day_from_tasks_path
        try:
            data = self.repository.load_path(role_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logging.warning("Skipping schedule base_time shift; failed to load %s: %s", role_file, exc)
            return False
        if not isinstance(data, dict) or not data:
            return False
        file_day = file_day_from_tasks_path(role_file)
        try:
            shifted, changed = apply_base_time_shift(
                data,
                self.base_time,
                file_day=file_day,
            )
        except ValueError as exc:
            logging.warning("Skipping schedule base_time shift: %s", exc)
            return False
        if not changed:
            return True
        self.repository.save_path(role_file, shifted)
        logging.info(
            "Applied schedule base_time=%s to %s",
            self.base_time,
            role_file,
        )
        return True

    def _maybe_write_schedule_statistics(self, role_file: Path) -> None:
        if self.statistic_output_dir is None:
            return
        today = date.today().isoformat()
        if self._statistic_written_date == today:
            return
        try:
            data = self.repository.load_path(role_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logging.warning("Skipping schedule statistics; failed to load %s: %s", role_file, exc)
            return
        if not isinstance(data, dict):
            return
        try:
            from schedule_shift import ORIGIN_HOUR, apply_base_time_shift
        except ImportError:
            from commander.schedule_shift import ORIGIN_HOUR, apply_base_time_shift
        try:
            chart_data, _changed = apply_base_time_shift(data, ORIGIN_HOUR)
        except ValueError:
            chart_data = data
        from common.time_model import format_statistic_report, write_role_schedule_statistics_from_tasks
        try:
            payload = write_role_schedule_statistics_from_tasks(
                chart_data,
                roles=self.roles,
                day=today,
                output_dir=self.statistic_output_dir,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logging.warning("Skipping schedule statistics: %s", exc)
            return
        self._statistic_written_date = today
        logging.info("Wrote schedule statistics:\n%s", format_statistic_report(payload))

    def ensure_role_file(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        ok = self._ensure_role_file_core(role_file)
        if ok:
            self._maybe_write_schedule_statistics(role_file)
            self.apply_base_time_shift(role_file)
        return ok

    def _ensure_role_file_core(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        if role_file.exists():
            try:
                data = self.repository.load_path(role_file)
                if not isinstance(data, dict):
                    logging.warning(f"Role file {role_file} is not a dict")
                    return False

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
                        self.repository.save_path(role_file, data)
                        logging.info(f"Role file {role_file} runtime schema auto-backfilled")
                    logging.info(f"Role file {role_file} exists and contains runtime fields")
                    return True

                try:
                    from schedule_shift import ORIGIN_HOUR, stamp_base_time
                except ImportError:
                    from commander.schedule_shift import ORIGIN_HOUR, stamp_base_time
                if stamp_base_time(data, ORIGIN_HOUR) != ORIGIN_HOUR:
                    logging.info(
                        "Role file %s already has a schedule base_time shift; skipping work-window validation",
                        role_file,
                    )
                    return True

                completed_roles = [
                    role
                    for role in self.roles
                    if role_tasks_are_complete(
                        data,
                        role,
                        tasks_per_role=len(data[role]) if isinstance(data.get(role), list) else 0,
                    )
                ]
                if len(completed_roles) == len(self.roles):
                    logging.info(f"Role file {role_file} exists and is valid")
                    return True
                if completed_roles:
                    logging.info(
                        f"Role file {role_file} is partially complete for roles {completed_roles}; resuming generation"
                    )
                    return self.generate_role_tasks(role_file)

                actual_counts = {
                    role: len(data[role]) if isinstance(data.get(role), list) else 0
                    for role in self.roles
                }
                valid, reason = validate_role_tasks(
                    data,
                    tasks_per_role=actual_counts,
                    roles=self.roles,
                )
                if not valid:
                    logging.warning(f"Role file {role_file} quality check failed: {reason}")
                    normalized = normalize_role_tasks(
                        data,
                        roles=self.roles,
                    )
                    valid_after_fix, reason_after_fix = validate_role_tasks(
                        normalized,
                        tasks_per_role={
                            role: len(normalized[role]) if isinstance(normalized.get(role), list) else 0
                            for role in self.roles
                        },
                        roles=self.roles,
                    )
                    if not valid_after_fix:
                        logging.warning(
                            f"Failed to normalize role file {role_file}: {reason_after_fix}; resuming generation"
                        )
                        return self.generate_role_tasks(role_file)
                    self.repository.save_path(role_file, normalized)
                    logging.info(f"Role file {role_file} was normalized and repaired")
                logging.info(f"Role file {role_file} exists and is valid")
                return True
            except (json.JSONDecodeError, OSError) as e:
                logging.warning(f"Role file {role_file} is corrupted: {e}")

        logging.info(f"Generating new role task file: {role_file}")
        return self.generate_role_tasks(role_file)

    def generate_role_tasks(self, role_file: Path) -> bool:
        """Generate role tasks using the configured model client."""
        with self._generation_lock:
            try:
                result = generate_role_tasks(
                    source="role_file_service",
                    final_file=role_file,
                    logs_dir=self.logs_dir,
                    domain_resource_path=self.domain_resource_file,
                    constraints_resource_path=self.constraints_resource_file,
                    roles=self.roles,
                    tasks_per_role=self.tasks_per_role,
                    max_attempts=self.max_attempts,
                    agent_client=self.agent_client,
                    emit_status=logging.info,
                    save_final_file=self.repository.save_path,
                    time_model_config=self.time_model_config,
                    target_ini_path=self.target_ini_path,
                    prompt_resources_dir=self.prompt_resources_dir,
                )
                if result.success:
                    self._maybe_write_schedule_statistics(role_file)
                    self.apply_base_time_shift(role_file)
                    return True

                logging.error(
                    "Failed to generate valid role tasks after maximum attempts: "
                    + ", ".join(f"{key}={value}" for key, value in result.stats.items())
                )
                if result.failure_reason:
                    logging.error(f"Last validation failure reason: {result.failure_reason}")
                logging.error(
                    "Role task generation will be retried on the configured interval; "
                    "commander continues running."
                )
                return False
            except Exception as e:
                logging.error(f"Exception in generate_role_tasks: {e}")
                return False


    def load_role_tasks(self, role_file: Path) -> dict[str, Any]:
        try:
            data = self.repository.load_path(role_file)
            if not isinstance(data, dict):
                raise ValueError(f"Task JSON root must be an object in {role_file}")
            return data
        except ValueError as e:
            logging.error(f"Failed to load role tasks from {role_file}: {e}")
            return {}

    def save_role_tasks(self, role_file: Path, data: dict[str, Any]) -> None:
        try:
            self.repository.save_path(role_file, data)
            logging.debug(f"Saved role tasks to {role_file}")
        except OSError as e:
            logging.error(f"Failed to save role tasks to {role_file}: {e}")
