#!/usr/bin/env python3
"""TCP commander: receive task completion reports and update per-day JSON task files.

task_id: only hyphen-free hex from UUID (uuid.uuid4().hex), length 8..32.
task_ref first segment: YYYY-MM-DD or MM-DD (latter expands with current year).
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import json
import socket
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from dispatch_client import DispatchClient
from logging_setup import configure_daily_logging
from policies import EarliestPendingSelectionPolicy
from repository import DailyTaskRepository
from role_file_service import RoleTaskFileService
from scanner_service import TaskScanService
from target_config import load_all_roles

try:
    from runtime_config import (
        get_dispatch_config,
        get_generator_config,
        get_logging_config,
        get_opencode_paths,
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
        get_opencode_paths,
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
    stdout: str | None,
    stderr: str | None,
) -> dict[str, Any]:
    return repository.update_task_report(task_ref, status, message, exit_code, stdout, stderr)


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
    logging.info(f"Commander connected from {addr}")
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
        out = payload.get("stdout")
        err_out = payload.get("stderr")
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
        if out is not None and not isinstance(out, str):
            send_line(conn, {"ok": False, "error": "stdout must be a string"})
            return
        if err_out is not None and not isinstance(err_out, str):
            send_line(conn, {"ok": False, "error": "stderr must be a string"})
            return

        result = handle_report(
            repository, task_ref, status, msg, exit_code, out, err_out
        )
        send_line(conn, result)
        if result.get("ok"):
            logging.info(f"Task {task_ref} reported as {status}")
        else:
            logging.error(f"Task {task_ref} report failed: {result.get('error')}")
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
    ):
        self.repository = repository
        self.data_dir = repository.data_dir
        self.dispatch_client = DispatchClient(
            dispatch_script,
            timeout_seconds=dispatch_timeout_seconds,
        )
        self.role_pointers = {}  # {"hr": 0, "finance": 0, ...}
        self.last_date = None
        self.roles = roles
        self.scan_interval_seconds = scan_interval_seconds
        self.role_file_service = RoleTaskFileService(
            self.data_dir,
            self.roles,
            min_tasks_per_role=generator_config["min_tasks_per_role"],
            min_non_five_ratio=generator_config["min_non_five_ratio"],
            max_attempts=generator_config["max_attempts"],
            opencode_timeout_sec=generator_config["opencode_timeout_seconds"],
            opencode_paths=generator_config["opencode_paths"],
            domain_resource_file=domain_resource_file,
        )
        self.selection_policy = EarliestPendingSelectionPolicy()
        self.scan_service = TaskScanService(
            repository=self.repository,
            selection_policy=self.selection_policy,
            dispatch_task=self.dispatch_client.dispatch,
        )
    
    def _get_role_task_file(self) -> Path:
        """Return path to unified daily tasks file tasks_MM-DD.json."""
        return self.role_file_service.get_today_role_task_file()
    
    def _ensure_role_file(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        return self.role_file_service.ensure_role_file(role_file)
    
    def _generate_role_tasks(self, role_file: Path) -> bool:
        """Generate role tasks using opencode CLI."""
        return self.role_file_service.generate_role_tasks(role_file)
    
    def _load_role_tasks(self, role_file: Path) -> dict:
        """Load role tasks from file."""
        return self.role_file_service.load_role_tasks(role_file)
    
    def _save_role_tasks(self, role_file: Path, data: dict) -> None:
        """Save role tasks to file."""
        self.role_file_service.save_role_tasks(role_file, data)
    
    def _scan_one_cycle(self):
        """One scan cycle: check and dispatch tasks."""
        current_date = date.today().isoformat()
        if current_date != self.last_date:
            logging.info(f"Date changed to {current_date}, clearing role pointers")
            self.role_pointers.clear()
            self.last_date = current_date
        
        # Get role task file
        role_file = self._get_role_task_file()
        logging.debug(f"Checking role task file: {role_file}")
        if not self._ensure_role_file(role_file):
            logging.warning(f"Failed to ensure role file {role_file}")
            return
        
        # Load tasks
        tasks_by_role = self._load_role_tasks(role_file)
        logging.debug(f"Loaded tasks for {len(tasks_by_role)} roles")

        self.scan_service.process_roles(
            tasks_by_role=tasks_by_role,
            roles=self.roles,
            role_pointers=self.role_pointers,
            save_role_tasks=lambda data: self._save_role_tasks(role_file, data),
        )
    
    def start(self):
        """Start scanning thread."""
        import threading
        def scan_loop():
            logging.info("Task scanner thread started")
            while True:
                try:
                    self._scan_one_cycle()
                except Exception as e:
                    logging.error(f"Exception in scan_loop: {e}", exc_info=True)
                time.sleep(self.scan_interval_seconds)
        
        thread = threading.Thread(target=scan_loop, daemon=True)
        thread.start()
        logging.info("Task scanner thread created")


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
    dispatch_script: Path,
) -> None:
    data_dir = data_dir.resolve()
    repository = DailyTaskRepository(
        data_dir,
        lock_timeout=lock_timeout_seconds,
        max_store_text=max_store_text,
    )
    roles = load_all_roles(target_ini_path)
    # Start automatic task scanner
    logging.info(f"Initializing TaskScanner with data_dir={data_dir}")
    scanner = TaskScanner(
        repository,
        roles,
        dispatch_script=dispatch_script,
        dispatch_timeout_seconds=dispatch_timeout_seconds,
        scan_interval_seconds=scan_interval_seconds,
        generator_config=generator_config,
        domain_resource_file=domain_resource_file,
    )
    scanner.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(listen_backlog)
    logging.info(f"Listening on {host}:{port}, data_dir={data_dir}")
    try:
        while True:
            conn, addr = sock.accept()
            t = threading.Thread(
                target=handle_commander,
                args=(
                    conn,
                    addr,
                    repository,
                    max_line_bytes,
                    recv_chunk_bytes,
                    socket_timeout_seconds,
                ),
                daemon=True,
            )
            t.start()
    finally:
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
    return parser


def main() -> None:
    args = build_parser().parse_args()

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
    log_file = configure_daily_logging(
        logs_dir,
        "commander",
        level_name=logging_config["level"],
        backup_count=logging_config["backup_count"],
        rotation_interval_days=logging_config["rotation_interval_days"],
    )

    target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
    dispatch_script = resolve_config_relative_path(paths_config["dispatch_script"])
    domain_resource_file = resolve_config_relative_path(paths_config["domain_resource_file"])

    generator_config = dict(generator_config)
    generator_config["opencode_paths"] = get_opencode_paths(runtime_config)

    logging.info(f"Starting commander, logs: {log_file}")

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
        dispatch_script=dispatch_script,
    )


if __name__ == "__main__":
    main()
