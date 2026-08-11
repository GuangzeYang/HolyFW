#!/usr/bin/env python3
"""Create or update daily task JSON, and dispatch a task to soldier (single-line JSON over TCP)."""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import json
import socket
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import logging

try:
    from common import validate_task_id
    from logging_setup import configure_subprocess_logging
    from repository import DailyTaskRepository
    from target_config import load_target_config
except ImportError:
    from common import validate_task_id
    from commander.logging_setup import configure_subprocess_logging
    from commander.repository import DailyTaskRepository
    from commander.target_config import load_target_config

try:
    from runtime_config import (
        get_dispatch_config,
        get_paths_config,
        get_scanner_config,
        get_storage_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_dispatch_config,
        get_paths_config,
        get_scanner_config,
        get_storage_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


def has_waiting_tasks(repository: DailyTaskRepository, date_str: str, role: str) -> bool:
    """Check role-scoped waiting tasks through repository."""
    return repository.has_active_waiting_task(role, date_str)


def append_task(
    repository: DailyTaskRepository,
    date_str: str,
    role: str,
    task_id: str,
    task_text: str,
    expiry_time: str,
    planned_time: str | None = None,
) -> None:
    repository.bind_dispatched_task(
        date_str=date_str,
        role=role,
        task_id=task_id,
        task_text=task_text,
        expiry_time=expiry_time,
        planned_time=planned_time,
    )


def send_to_soldier(
    soldier_host: str,
    soldier_port: int,
    task_ref: str,
    command: str,
    task_date: str,
    planned_time: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    if timeout is None:
        raise ValueError("send_to_soldier timeout must be provided")

    payload = {
        "task_ref": task_ref,
        "command": command,
        "task_date": task_date,
        "planned_time": planned_time,
    }
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    try:
        with socket.create_connection((soldier_host, soldier_port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(line.encode("utf-8"))
            logging.debug(
                "Running — %s — Dispatched to (%s,%s)",
                task_ref.split("_")[-1] if "_" in task_ref else task_ref,
                soldier_host,
                soldier_port,
            )
            buffer = b""
            while b"\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 65536:
                    return {"ok": False, "status": "rejected", "error": "Soldier response too long"}
    except OSError as e:
        logging.error(f"Failed to dispatch task {task_ref}: {e}")
        return {
            "ok": False,
            "status": "network_error",
            "task_ref": task_ref,
            "error": f"Connection failed: {e}",
        }
    if not buffer.strip():
        return {
            "ok": False,
            "status": "rejected",
            "task_ref": task_ref,
            "error": "Soldier closed without an acknowledgment",
        }
    try:
        response = json.loads(buffer.split(b"\n", 1)[0].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {
            "ok": False,
            "status": "rejected",
            "task_ref": task_ref,
            "error": "Soldier acknowledgment is not valid JSON",
        }
    if not isinstance(response, dict):
        return {
            "ok": False,
            "status": "rejected",
            "task_ref": task_ref,
            "error": "Soldier acknowledgment must be a JSON object",
        }
    response.setdefault("task_ref", task_ref)
    return response


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for one-shot dispatch command."""
    parser = argparse.ArgumentParser(description="Write daily task file and dispatch command to soldier")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="tasks_MM-DD.json directory (default from commander/config.json)",
    )
    parser.add_argument("--date", default=None, help="task date YYYY-MM-DD, default today (local)")
    parser.add_argument(
        "--target",
        required=True,
        help="target role name (must match a section in commander.ini, e.g., hr)",
    )
    parser.add_argument("--command", required=True, help="command line to execute on soldier (will be passed to shell)")
    parser.add_argument(
        "--task-id",
        default=None,
        help="task UUID hex (8-32 chars); auto-generated by default",
    )
    parser.add_argument("--task", default="", help="task text for today's task record")
    parser.add_argument(
        "--description",
        default=None,
        help="(deprecated) backward-compatible alias of --task",
    )
    parser.add_argument("--planned-time", default=None, help="planned task time HH:MM from unified role file")
    parser.add_argument(
        "--timeout-minutes",
        type=int,
        default=None,
        help="timeout for waiting tasks in minutes (default from commander/config.json)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="INI config file path (default: commander/commander.ini)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    runtime_config = load_runtime_config()
    dispatch_config = get_dispatch_config(runtime_config)
    scanner_config = get_scanner_config(runtime_config)
    storage_config = get_storage_config(runtime_config)
    paths_config = get_paths_config(runtime_config)
    
    # Determine config file path
    cfg = args.config if args.config is not None else resolve_config_relative_path(paths_config["target_ini_file"])
    
    # Load target configuration
    try:
        soldier_host, soldier_port = load_target_config(cfg, args.target)
    except FileNotFoundError as e:
        # Log to stderr since logging not yet set up
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    
    # Normalize target to lowercase for role name (strict matching)
    target_role = args.target.lower()

    # No dispatch_*.log file — commander owns dated execution logs. Keep stderr only.
    configure_subprocess_logging("WARNING")

    data_dir = args.data_dir.resolve() if args.data_dir is not None else resolve_config_relative_path(scanner_config["data_dir"])
    repository = DailyTaskRepository(
        data_dir,
        lock_timeout=storage_config["lock_timeout_seconds"],
        max_store_text=storage_config["max_store_text"],
    )
    if args.date:
        try:
            d = date.fromisoformat(args.date)
        except ValueError:
            logging.error("Invalid --date, must be YYYY-MM-DD")
            return 1
    else:
        d = date.today()
    date_str = d.isoformat()

    if args.task_id:
        task_id = args.task_id.strip()
        if not task_id:
            logging.error("--task-id cannot be empty")
            return 1
    else:
        task_id = uuid.uuid4().hex[:16]

    err = validate_task_id(task_id)
    if err:
        logging.error(err)
        return 1

    timeout_minutes = args.timeout_minutes if args.timeout_minutes is not None else dispatch_config["timeout_minutes"]

    expiry_time = (
        datetime.now().astimezone() + timedelta(minutes=timeout_minutes)
    ).isoformat()

    task_file = repository.day_path(date_str)
    repository.expire_waiting_tasks(date_str)
    if has_waiting_tasks(repository, date_str, target_role):
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "busy",
                    "error": f"Role {target_role} has an active waiting task",
                },
                ensure_ascii=False,
            )
        )
        return 2

    task_ref = f"{date_str}_{target_role}_{task_id}"
    task_text = (args.task or "").strip()
    if not task_text and isinstance(args.description, str):
        task_text = args.description.strip()

    append_task(
        repository,
        date_str,
        target_role,
        task_id,
        task_text,
        expiry_time,
        args.planned_time,
    )

    try:
        resp = send_to_soldier(
            soldier_host,
            soldier_port,
            task_ref,
            args.command,
            date_str,
            args.planned_time,
            timeout=dispatch_config["soldier_timeout_seconds"],
        )
    except OSError as e:
        logging.error(f"Dispatch failed: {e}")
        repository.rollback_dispatched_task(
            date_str,
            target_role,
            task_id,
            f"Dispatch failed before reaching soldier: {e}",
        )
        return 1

    if not resp.get("ok"):
        error = resp.get("error", "unknown")
        logging.error(f"Dispatch response indicates failure: {error}")
        repository.rollback_dispatched_task(
            date_str,
            target_role,
            task_id,
            f"Dispatch failed before reaching soldier: {error}",
        )
        print(json.dumps(resp, ensure_ascii=False))
        return 3 if resp.get("status") == "busy" else 1

    execution_deadline = resp.get("execution_deadline")
    if isinstance(execution_deadline, str) and execution_deadline:
        if not repository.update_task_expiry(
            date_str,
            target_role,
            task_id,
            execution_deadline,
        ):
            logging.warning(
                "Could not synchronize execution deadline for task %s: %s",
                task_ref,
                execution_deadline,
            )

    print(json.dumps(resp, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
