#!/usr/bin/env python3
"""TCP commander: receive task completion reports and update per-day JSON task files.

task_id is assigned when the daily file is generated (uuid.uuid4().hex, 16 chars).
Reports are matched by task_id even if the task_ref date is wrong.
task_ref first segment: YYYY-MM-DD or MM-DD (latter expands with current year).
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import socket
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

try:
    from commander.dispatch_client import DispatchClient
    from commander.logging_setup import (
        configure_commander_root_logging,
        log_extra,
        reattach_commander_dated_file_handler,
    )
    from commander.policies import EarliestPendingSelectionPolicy
    from commander.repository import DailyTaskRepository
    from commander.role_file_service import RoleTaskFileService
    from commander.scanner_service import TaskScanService
    from commander.target_config import load_all_roles, load_daily_generation_roles
except ImportError:
    from dispatch_client import DispatchClient
    from logging_setup import configure_commander_root_logging, log_extra, reattach_commander_dated_file_handler
    from policies import EarliestPendingSelectionPolicy
    from repository import DailyTaskRepository
    from role_file_service import RoleTaskFileService
    from scanner_service import TaskScanService
    from target_config import load_all_roles, load_daily_generation_roles
from common import parse_task_ref

try:
    from runtime_config import (
        get_dispatch_config,
        get_generator_config,
        get_logging_config,
        get_paths_config,
        get_scanner_config,
        get_server_config,
        get_storage_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_dispatch_config,
        get_generator_config,
        get_logging_config,
        get_paths_config,
        get_scanner_config,
        get_server_config,
        get_storage_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
import logging

def send_line(conn: socket.socket, obj: dict[str, Any]) -> None:
    conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def handle_report(
    repository: DailyTaskRepository,
    task_ref: str,
    status: str,
    message: str | None,
    exit_code: int | None,
) -> dict[str, Any]:
    return repository.update_task_report(task_ref, status, message, exit_code)


def recv_one_line(conn: socket.socket, max_line_bytes: int, recv_chunk_bytes: int) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(recv_chunk_bytes)
        if not chunk:
            break
        buf += chunk
        if len(buf) > max_line_bytes:
            raise ValueError("Request too long")
    if not buf:
        return b""
    line, sep, _ = buf.partition(b"\n")
    if not sep:
        raise ValueError("Did not receive complete line")
    return line


def handle_commander(
    conn: socket.socket,
    addr: tuple,
    repository: DailyTaskRepository,
    max_line_bytes: int,
    recv_chunk_bytes: int,
    socket_timeout_seconds: int,
) -> None:
    logging.debug("Commander connected from %s", addr)
    try:
        conn.settimeout(socket_timeout_seconds)
        try:
            raw = recv_one_line(conn, max_line_bytes, recv_chunk_bytes)
        except ValueError as e:
            send_line(conn, {"ok": False, "error": str(e)})
            return
        if not raw.strip():
            send_line(conn, {"ok": False, "error": "Empty request"})
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            send_line(conn, {"ok": False, "error": "JSON parsing failed"})
            return
        if not isinstance(payload, dict):
            send_line(conn, {"ok": False, "error": "Request body must be a JSON object"})
            return
        task_ref = payload.get("task_ref")
        status = payload.get("status")
        msg = payload.get("message")
        exit_code = payload.get("exit_code")
        if not isinstance(task_ref, str):
            send_line(conn, {"ok": False, "error": "Missing or invalid task_ref"})
            return
        if not isinstance(status, str):
            send_line(conn, {"ok": False, "error": "Missing or invalid status"})
            return
        if msg is not None and not isinstance(msg, str):
            send_line(conn, {"ok": False, "error": "message must be a string"})
            return
        if exit_code is not None and not isinstance(exit_code, int):
            send_line(conn, {"ok": False, "error": "exit_code must be an integer"})
            return

        result = handle_report(
            repository, task_ref, status, msg, exit_code
        )
        send_line(conn, result)
        parsed_ref, parse_error = parse_task_ref(task_ref)
        role_name = "system"
        task_id = task_ref
        role_index: int | None = None
        if parse_error is None and parsed_ref is not None:
            task_day, role_name, task_id = parsed_ref
            role_index = repository.find_task_index(task_day, role_name, task_id)
        extras = log_extra(role_name, role_index)
        if result.get("ok"):
            if status == "successed":
                logging.info("Success — %s", task_id, extra=extras)
            elif status == "failed":
                logging.error("Failed — %s", task_id, extra=extras)
            else:
                logging.debug("Reported as %s — %s", status, task_id, extra=extras)
        else:
            logging.debug(
                "Report rejected — %s — %s",
                task_id,
                result.get("error"),
                extra=extras,
            )
    finally:
        conn.close()


class TaskScanner:
    """Scans and dispatches role tasks automatically."""
    
    def __init__(
        self,
        repository: DailyTaskRepository,
        roles: tuple[str, ...],
        dispatch_script: Path,
        dispatch_timeout_seconds: int,
        scan_interval_seconds: int,
        generator_config: dict[str, Any],
        domain_resource_file: Path,
        constraints_resource_file: Path,
        logs_dir: Path,
        log_level_name: str,
        periodic_hook: Callable[[], None] | None = None,
        max_dispatch_lateness_minutes: int = 6,
        debug: bool = False,
        target_ini_path: Path | None = None,
        generation_roles: tuple[str, ...] | None = None,
        statistic_output_dir: Path | None = None,
        base_time: int = 9,
    ):
        self.repository = repository
        self.data_dir = repository.data_dir
        self.logs_dir = logs_dir.resolve()
        self.log_level_name = log_level_name
        self._periodic_hook = periodic_hook
        self._attached_log_date = date.today()
        self.dispatch_client = DispatchClient(
            dispatch_script,
            timeout_seconds=dispatch_timeout_seconds,
            target_ini_path=target_ini_path,
        )
        self.role_pointers = {}  # {"hr": 0, "accountancy": 0, "manager": 0, "programmer": 0, ...}
        self.last_date = None
        self.roles = roles
        self.generation_roles = generation_roles if generation_roles is not None else roles
        self.scan_interval_seconds = scan_interval_seconds
        self.max_dispatch_lateness_minutes = max_dispatch_lateness_minutes
        self.base_time = int(base_time)
        self.generation_retry_interval_seconds = generator_config["generation_retry_interval_seconds"]
        self.role_file_service = RoleTaskFileService(
            self.data_dir,
            self.generation_roles,
            max_attempts=generator_config["max_attempts"],
            agent_client=None,
            domain_resource_file=domain_resource_file,
            constraints_resource_file=constraints_resource_file,
            logs_dir=self.logs_dir,
            repository=self.repository,
            time_model_config=generator_config["time_model"],
            target_ini_path=target_ini_path,
            statistic_output_dir=statistic_output_dir,
            base_time=self.base_time,
        )
        self.selection_policy = EarliestPendingSelectionPolicy()
        self.scan_service = TaskScanService(
            repository=self.repository,
            selection_policy=self.selection_policy,
            dispatch_task=self.dispatch_client.dispatch,
            max_dispatch_lateness_minutes=self.max_dispatch_lateness_minutes,
            debug=debug,
        )
    
    def _active_task_date(self) -> str:
        """ISO date of the task file to generate/scan, pinned across midnight wrap."""
        try:
            from schedule_shift import resolve_active_task_day
        except ImportError:
            from commander.schedule_shift import resolve_active_task_day
        return resolve_active_task_day(self.repository.load_day)

    def _get_role_task_file(self) -> Path:
        """Return path to the active unified daily tasks file."""
        return self.role_file_service.get_role_task_file(self._active_task_date())
    
    def _ensure_role_file(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        return self.role_file_service.ensure_role_file(role_file)
    
    def _generate_role_tasks(self, role_file: Path) -> bool:
        """Generate role tasks using the configured model client."""
        return self.role_file_service.generate_role_tasks(role_file)
    
    def _load_role_tasks(self, role_file: Path) -> dict:
        """Load role tasks from file."""
        return self.role_file_service.load_role_tasks(role_file)
    
    def _save_role_tasks(self, role_file: Path, data: dict) -> None:
        """Save role tasks to file."""
        self.role_file_service.save_role_tasks(role_file, data)
    
    def ensure_commander_log_file_for_today(self) -> None:
        """If the calendar date changed, attach root file logging to commander_YYYY-MM-DD.log."""
        today = date.today()
        if self._attached_log_date == today:
            return
        new_path = reattach_commander_dated_file_handler(
            self.logs_dir,
            self.log_level_name,
            target_day=today,
        )
        self._attached_log_date = today
        logging.info(f"Switched commander file log to {new_path}")

    def sync_role_pointers_for_calendar_date(self) -> str:
        """Clear per-role scan pointers when the active task day changes. Returns that ISO date."""
        current_date = self._active_task_date()
        if current_date != self.last_date:
            logging.info(f"Date changed to {current_date}, clearing role pointers")
            self.role_pointers.clear()
            self.last_date = current_date
        return current_date

    def run_role_task_file_scan_pass(self, date_str: str) -> None:
        """Load today's unified task file (if present) and run one dispatch scan pass.

        Task file generation is driven by the generation retry thread, not each scan.
        """
        role_file = self.role_file_service.get_role_task_file(date_str)
        logging.debug(f"Checking role task file: {role_file}")
        if not role_file.exists():
            logging.debug(f"Role task file not present yet, skipping scan cycle: {role_file}")
            return

        self.role_file_service.apply_base_time_shift(role_file)

        for expired in self.repository.expire_waiting_tasks(date_str):
            task_ref = expired["task_ref"]
            reason = expired["reason"]
            logging.error("Expired waiting task %s: %s", task_ref, reason)

        tasks_by_role = self._load_role_tasks(role_file)
        logging.debug(f"Loaded tasks for {len(tasks_by_role)} roles")

        self.scan_service.process_roles(
            tasks_by_role=tasks_by_role,
            roles=self.roles,
            role_pointers=self.role_pointers,
            date_str=date_str,
        )

    def _default_periodic_hook(self) -> None:
        self.ensure_commander_log_file_for_today()
        date_str = self.sync_role_pointers_for_calendar_date()
        self.run_role_task_file_scan_pass(date_str)

    def _invoke_periodic_hook(self) -> None:
        if self._periodic_hook is not None:
            self._periodic_hook()
        else:
            self._default_periodic_hook()

    def start(self):
        """Start scanning thread and role-task generation retry thread."""
        def scan_loop():
            logging.info("Task scanner thread started")
            while True:
                try:
                    self._invoke_periodic_hook()
                except Exception as e:
                    logging.error(f"Exception in scan_loop: {e}", exc_info=True)
                time.sleep(self.scan_interval_seconds)

        def generation_retry_loop():
            logging.info("Role task generation retry thread started")
            while True:
                try:
                    self.ensure_commander_log_file_for_today()
                    role_file = self._get_role_task_file()
                    if not self.generation_roles:
                        logging.debug(
                            "Skipping daily generation; no office roles configured (on-demand only)"
                        )
                    else:
                        self._ensure_role_file(role_file)
                except Exception as e:
                    logging.error(f"Exception in generation_retry_loop: {e}", exc_info=True)
                time.sleep(self.generation_retry_interval_seconds)

        threading.Thread(target=scan_loop, daemon=True).start()
        logging.debug("Task scanner thread created")
        threading.Thread(target=generation_retry_loop, daemon=True).start()
        logging.debug("Role task generation retry thread created")


def serve(
    host: str,
    port: int,
    data_dir: Path,
    max_store_text: int,
    lock_timeout_seconds: int,
    max_line_bytes: int,
    recv_chunk_bytes: int,
    socket_timeout_seconds: int,
    listen_backlog: int,
    scan_interval_seconds: int,
    dispatch_timeout_seconds: int,
    target_ini_path: Path,
    generator_config: dict[str, Any],
    domain_resource_file: Path,
    constraints_resource_file: Path,
    dispatch_script: Path,
    logs_dir: Path,
    log_level_name: str,
    worker_threads: int,
    max_dispatch_lateness_minutes: int = 6,
    debug: bool = False,
    statistic_output_dir: Path | None = None,
    base_time: int = 9,
) -> None:
    data_dir = data_dir.resolve()
    repository = DailyTaskRepository(
        data_dir,
        lock_timeout=lock_timeout_seconds,
        max_store_text=max_store_text,
    )
    roles = load_all_roles(target_ini_path)
    generation_roles = load_daily_generation_roles(target_ini_path)
    logging.debug("Initializing TaskScanner with data_dir=%s", data_dir)
    scanner = TaskScanner(
        repository,
        roles,
        dispatch_script=dispatch_script,
        dispatch_timeout_seconds=dispatch_timeout_seconds,
        scan_interval_seconds=scan_interval_seconds,
        generator_config=generator_config,
        domain_resource_file=domain_resource_file,
        constraints_resource_file=constraints_resource_file,
        logs_dir=logs_dir,
        log_level_name=log_level_name,
        max_dispatch_lateness_minutes=max_dispatch_lateness_minutes,
        debug=debug,
        target_ini_path=target_ini_path,
        generation_roles=generation_roles,
        statistic_output_dir=statistic_output_dir,
        base_time=base_time,
    )
    scanner.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(listen_backlog)
    logging.info("Listening on %s:%s", host, port)
    try:
        executor = ThreadPoolExecutor(max_workers=worker_threads)
        while True:
            conn, addr = sock.accept()
            executor.submit(
                handle_commander,
                conn,
                addr,
                repository,
                max_line_bytes,
                recv_chunk_bytes,
                socket_timeout_seconds,
            )
    finally:
        if "executor" in locals():
            executor.shutdown(wait=False, cancel_futures=True)
        sock.close()


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser for commander service."""
    parser = argparse.ArgumentParser(description="Task completion receipt service")
    parser.add_argument("--host", default=None, help="listen address (default from commander/config.json)")
    parser.add_argument("--port", type=int, default=None, help="listen port (default from commander/config.json)")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="tasks_MM-DD.json directory (default from commander/config.json)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help=(
            "enable DEBUG logging and expire planned tasks that are later than "
            "the configured dispatch window"
        ),
    )
    parser.add_argument(
        "--statistic",
        action="store_true",
        help="After today's role task file is ready, print time lists and write a 30-minute bin chart.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for --statistic artifacts (default: repository root).",
    )
    parser.add_argument(
        "--base-time",
        type=_parse_base_time_arg,
        default=None,
        help="Hour (0-23) when the generated 09:00 workday should start. Default from config (9).",
    )
    return parser


def _parse_base_time_arg(value: str) -> int:
    try:
        hour = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("base_time must be an integer 0..23") from exc
    if not 0 <= hour <= 23:
        raise argparse.ArgumentTypeError("base_time must be an integer 0..23")
    return hour


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    runtime_config = load_runtime_config()
    server_config = get_server_config(runtime_config)
    scanner_config = get_scanner_config(runtime_config)
    storage_config = get_storage_config(runtime_config)
    dispatch_config = get_dispatch_config(runtime_config)
    generator_config = get_generator_config(runtime_config)
    paths_config = get_paths_config(runtime_config)
    logging_config = get_logging_config(runtime_config)

    host = args.host if args.host is not None else server_config["host"]
    port = args.port if args.port is not None else server_config["port"]
    data_dir = args.data_dir if args.data_dir is not None else resolve_config_relative_path(scanner_config["data_dir"])

    logs_dir = resolve_config_relative_path(paths_config["logs_dir"])
    log_level_name = "DEBUG" if args.debug else logging_config["level"]
    log_file = configure_commander_root_logging(logs_dir, level_name=log_level_name)

    target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
    dispatch_script = resolve_config_relative_path(paths_config["dispatch_script"])
    domain_resource_file = resolve_config_relative_path(paths_config["domain_resource_file"])
    constraints_resource_file = resolve_config_relative_path(
        paths_config["task_generation_constraints_file"]
    )

    logging.info("Starting commander, logs: %s", log_file)
    logging.info("Commander workspace: %s", logs_dir.parent)
    logging.info("Task file directory: %s", data_dir.resolve())
    try:
        from common.llm_catalog import format_enabled_llm_log, llm_json_path

        logging.info("%s catalog=%s", format_enabled_llm_log(), llm_json_path())
    except (FileNotFoundError, ValueError) as exc:
        logging.error("LLM catalog: %s", exc)
    resolved_base_time = args.base_time if args.base_time is not None else scanner_config["base_time"]
    logging.info("Schedule base_time: %s", resolved_base_time)

    statistic_output_dir = None
    if args.statistic:
        from common.time_model import _statistic_output_dir
        statistic_output_dir = _statistic_output_dir(args.output_dir)
        logging.info("Schedule statistics enabled; artifacts under %s", statistic_output_dir)

    serve(
        host,
        port,
        data_dir,
        max_store_text=storage_config["max_store_text"],
        lock_timeout_seconds=storage_config["lock_timeout_seconds"],
        max_line_bytes=server_config["max_line_bytes"],
        recv_chunk_bytes=server_config["recv_chunk_bytes"],
        socket_timeout_seconds=server_config["socket_timeout_seconds"],
        listen_backlog=server_config["listen_backlog"],
        scan_interval_seconds=scanner_config["scan_interval_seconds"],
        dispatch_timeout_seconds=dispatch_config["client_timeout_seconds"],
        target_ini_path=target_ini_path,
        generator_config=generator_config,
        domain_resource_file=domain_resource_file,
        constraints_resource_file=constraints_resource_file,
        dispatch_script=dispatch_script,
        logs_dir=logs_dir,
        log_level_name=log_level_name,
        worker_threads=server_config["worker_threads"],
        max_dispatch_lateness_minutes=scanner_config["max_dispatch_lateness_minutes"],
        debug=args.debug,
        statistic_output_dir=statistic_output_dir,
        base_time=resolved_base_time,
    )


if __name__ == "__main__":
    main()
