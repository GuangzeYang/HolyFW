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
from datetime import date, datetime
from pathlib import Path
from typing import Any

from common import (
    build_role_task_prompt,
    extract_json_object,
    normalize_role_tasks,
    validate_role_tasks,
)
from repository import DailyTaskRepository
import logging
import logging.handlers

DEFAULT_PORT = 38471
MAX_LINE_BYTES = 65536
MAX_STORE_TEXT = 65536
















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


def recv_one_line(conn: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > MAX_LINE_BYTES:
            raise ValueError("Request too long")
    if not buf:
        return b""
    line, sep, _ = buf.partition(b"\n")
    if not sep:
        raise ValueError("Did not receive complete line")
    return line


def handle_commander(conn: socket.socket, addr: tuple, repository: DailyTaskRepository) -> None:
    logging.info(f"Commander connected from {addr}")
    try:
        conn.settimeout(120)
        try:
            raw = recv_one_line(conn)
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
    
    def __init__(self, repository: DailyTaskRepository):
        self.repository = repository
        self.data_dir = repository.data_dir
        self.role_pointers = {}  # {"hr": 0, "finance": 0, ...}
        self.last_date = None
        self.roles = ("hr", "finance", "ceo", "developer")
    
    def _get_role_task_file(self) -> Path:
        """Return path to unified daily tasks file tasks_MM-DD.json."""
        return self.repository.day_path(date.today().isoformat())
    
    def _ensure_role_file(self, role_file: Path) -> bool:
        """Ensure unified role task file exists, generate if missing."""
        if role_file.exists():
            try:
                with open(role_file, encoding="utf-8") as f:
                    data = json.load(f)
                # Validate structure
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
                        ):
                            has_runtime_fields = True
                            break
                    if has_runtime_fields:
                        break

                # Do not normalize live task files to avoid overwriting runtime state.
                if has_runtime_fields:
                    missing_roles: list[str] = []
                    schema_updated = False
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
                    if schema_updated:
                        with open(role_file, "w", encoding="utf-8") as f:
                            json.dump(data, f, ensure_ascii=False, indent=2)
                        logging.info(f"Role file {role_file} runtime schema auto-backfilled")
                    logging.info(f"Role file {role_file} exists and contains runtime fields")
                    return True

                valid, reason = validate_role_tasks(
                    data,
                    min_tasks_per_role=18,
                    min_non_five_ratio=0.8,
                )
                if not valid:
                    logging.warning(f"Role file {role_file} quality check failed: {reason}")
                    normalized = normalize_role_tasks(data, min_tasks_per_role=18)
                    valid_after_fix, reason_after_fix = validate_role_tasks(
                        normalized,
                        min_tasks_per_role=18,
                        min_non_five_ratio=0.8,
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
                pass
        
        # Generate new unified role tasks
        logging.info(f"Generating new role task file: {role_file}")
        return self._generate_role_tasks(role_file)
    
    def _generate_role_tasks(self, role_file: Path) -> bool:
        """Generate role tasks using opencode CLI."""
        try:
            import subprocess
            # Read domain resource as context
            domain_resource_path = Path(__file__).resolve().parent.parent / "domain_resource.md"
            domain_context = ""
            if domain_resource_path.exists():
                with open(domain_resource_path, encoding="utf-8") as f:
                    domain_context = f.read()
                logging.info(f"Read domain resource from {domain_resource_path}")
            else:
                logging.warning(f"Domain resource not found: {domain_resource_path}")
            min_tasks_per_role = 18
            max_attempts = 3
            opencode_timeout_sec = 180
            prompt = build_role_task_prompt(domain_context, min_tasks_per_role=min_tasks_per_role)
            
            # Try different opencode paths
            import platform
            opencode_paths = ["opencode"]  # First try PATH
            
            # Add platform-specific paths
            system = platform.system()
            if system == "Windows":
                opencode_paths.extend([
                    "C:\\Users\\21276\\AppData\\Roaming\\npm\\opencode",
                    "C:\\Users\\21276\\AppData\\Roaming\\npm\\opencode.cmd",
                    os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode"),
                    os.path.expanduser("~\\AppData\\Roaming\\npm\\opencode.cmd")
                ])
            elif system == "Linux":
                opencode_paths.extend([
                    "/usr/local/bin/opencode",
                    "/usr/bin/opencode",
                    os.path.expanduser("~/.npm/bin/opencode"),
                    os.path.expanduser("~/.local/bin/opencode")
                ])

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
                            shell=False
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

                        data = normalize_role_tasks(parsed, min_tasks_per_role=min_tasks_per_role)
                        valid, reason = validate_role_tasks(
                            data,
                            min_tasks_per_role=min_tasks_per_role,
                            min_non_five_ratio=0.8,
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
                        logging.warning(
                            f"opencode at {cmd} timed out after {opencode_timeout_sec}s"
                        )
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

            # Fallback: keep service available with local synthetic tasks.
            fallback_data = normalize_role_tasks({}, min_tasks_per_role=min_tasks_per_role)
            valid, reason = validate_role_tasks(
                fallback_data,
                min_tasks_per_role=min_tasks_per_role,
                min_non_five_ratio=0.8,
            )
            if not valid:
                logging.error(f"Fallback task generation failed validation: {reason}")
                return False
            with open(role_file, "w", encoding="utf-8") as f:
                json.dump(fallback_data, f, ensure_ascii=False, indent=2)
            logging.warning("Generated fallback unified tasks because opencode output was unusable")
            return True
        except Exception as e:
            logging.error(f"Exception in _generate_role_tasks: {e}")
            return False
    
    def _load_role_tasks(self, role_file: Path) -> dict:
        """Load role tasks from file."""
        try:
            with open(role_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.error(f"Failed to load role tasks from {role_file}: {e}")
            return {}
    
    def _save_role_tasks(self, role_file: Path, data: dict) -> None:
        """Save role tasks to file."""
        try:
            with open(role_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logging.debug(f"Saved role tasks to {role_file}")
        except OSError as e:
            logging.error(f"Failed to save role tasks to {role_file}: {e}")
    
    def _has_waiting_task(self, role: str) -> bool:
        """Check if role has waiting tasks in tasks_MM-DD.json."""
        return self.repository.has_active_waiting_task(role, date.today().isoformat())
    
    def _parse_task_datetime(self, task_time_str: str, now: datetime) -> datetime | None:
        """Parse task HH:MM time into a datetime for today."""
        if not isinstance(task_time_str, str) or ":" not in task_time_str:
            return None
        try:
            hour, minute = map(int, task_time_str.split(":", 1))
            return datetime(now.year, now.month, now.day, hour, minute)
        except (ValueError, AttributeError):
            return None

    def _needs_dispatch(self, task: dict[str, Any]) -> bool:
        """Whether a task still needs to be dispatched."""
        if not isinstance(task, dict):
            return False
        if not task.get("is_load", False):
            return True
        if task.get("task_id"):
            return False
        status = task.get("status")
        return status in (None, "", "planned")

    def _find_next_pending_index(self, tasks: list, start_index: int = 0) -> int | None:
        """Find next task index that still needs dispatch from start_index."""
        for idx in range(max(0, start_index), len(tasks)):
            task = tasks[idx]
            if isinstance(task, dict) and self._needs_dispatch(task):
                return idx
        return None

    def _ensure_pointer(self, role_name: str, tasks: list) -> int:
        """Ensure role pointer exists and is valid."""
        pointer = self.role_pointers.get(role_name)
        if not isinstance(pointer, int) or pointer < 0 or pointer >= len(tasks):
            next_idx = self._find_next_pending_index(tasks, 0)
            pointer = len(tasks) if next_idx is None else next_idx
            self.role_pointers[role_name] = pointer
        return pointer

    def _move_pointer_after_success(self, role_name: str, tasks: list, current_index: int) -> int:
        """Move pointer to next pending task after successful dispatch."""
        next_idx = self._find_next_pending_index(tasks, current_index + 1)
        pointer = len(tasks) if next_idx is None else next_idx
        self.role_pointers[role_name] = pointer
        return pointer
    
    def _dispatch_task(self, role: str, task_text: str, task_time: str | None = None) -> bool:
        """Dispatch task to soldier via dispatch.py."""
        try:
            import subprocess
            import sys
            # Build command for soldier: opencode run with task description
            command = f'opencode run "{task_text}"'
            
            # Call dispatch.py with appropriate arguments
            dispatch_script = Path(__file__).resolve().parent / "dispatch.py"
            args = [
                sys.executable,
                str(dispatch_script),
                "--target", role,
                "--command", command,
                "--task", task_text,
            ]
            if task_time:
                args.extend(["--planned-time", task_time])

            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                stderr_text = (result.stderr or "").strip()
                stdout_text = (result.stdout or "").strip()
                logging.error(
                    f"dispatch.py failed for role={role}, returncode={result.returncode}, "
                    f"stderr={stderr_text[:300]}, stdout={stdout_text[:300]}"
                )
                return False
            return True
        except Exception as e:
            logging.error(f"Exception when dispatching task for role={role}: {e}")
            return False
    
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
        
        # Process each role
        for role_key in self.roles:
            tasks = tasks_by_role.get(role_key)
            if not isinstance(tasks, list) or not tasks:
                continue

            pointer = self._ensure_pointer(role_key, tasks)
            if pointer >= len(tasks):
                continue

            while pointer < len(tasks):
                # Waiting only blocks current role, not others.
                if self._has_waiting_task(role_key):
                    logging.debug(f"Role {role_key} has waiting task, pausing pointer at index {pointer}")
                    break

                task = tasks[pointer]
                if not isinstance(task, dict):
                    logging.warning(f"Invalid task format at {role_key}[{pointer}], skipping")
                    pointer += 1
                    self.role_pointers[role_key] = pointer
                    continue

                now = datetime.now()
                task_time_raw = task.get("time")
                task_time = self._parse_task_datetime(task_time_raw if isinstance(task_time_raw, str) else "", now)
                if task_time is None:
                    logging.warning(f"Invalid task time for {role_key}[{pointer}], skipping")
                    task["is_load"] = True
                    self._save_role_tasks(role_file, tasks_by_role)
                    pointer += 1
                    self.role_pointers[role_key] = pointer
                    continue

                # Keep earliest pending task; do not skip historical tasks.
                if task_time > now:
                    break

                task_text = task.get("task", "")
                truncated = task_text[:50] + "..." if task_text and len(task_text) > 50 else task_text

                # Rule: reading task marks is_load=True before dispatch.
                if not task.get("is_load", False):
                    task["is_load"] = True
                    self._save_role_tasks(role_file, tasks_by_role)
                    logging.info(f"Marked task loaded for {role_key}[{pointer}]: {task.get('time')} - {truncated}")

                logging.info(f"Dispatching task for {role_key}[{pointer}]: {task.get('time')} - {truncated}")
                success = self._dispatch_task(role_key, task_text, task.get("time"))
                if success:
                    logging.info(f"Successfully dispatched task for {role_key}[{pointer}]")
                    pointer = self._move_pointer_after_success(role_key, tasks, pointer)
                    continue

                # Rule: on dispatch failure, keep pointer unchanged for retry.
                logging.error(f"Failed to dispatch task for {role_key}[{pointer}], pointer unchanged")
                break
    
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
                time.sleep(60)  # Scan every minute
        
        thread = threading.Thread(target=scan_loop, daemon=True)
        thread.start()
        logging.info("Task scanner thread created")


def serve(host: str, port: int, data_dir: Path) -> None:
    data_dir = data_dir.resolve()
    repository = DailyTaskRepository(data_dir, max_store_text=MAX_STORE_TEXT)
    # Start automatic task scanner
    logging.info(f"Initializing TaskScanner with data_dir={data_dir}")
    scanner = TaskScanner(repository)
    scanner.start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(32)
    logging.info(f"Listening on {host}:{port}, data_dir={data_dir}")
    try:
        while True:
            conn, addr = sock.accept()
            t = threading.Thread(
                target=handle_commander,
                args=(conn, addr, repository),
                daemon=True,
            )
            t.start()
    finally:
        sock.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Task completion receipt service")
    p.add_argument("--host", default="0.0.0.0", help="listen address")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="listen port")
    p.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "role_task",
        help="tasks_MM-DD.json directory (default: role_task/ under script directory)",
    )
    args = p.parse_args()
    
    logs_dir = Path(__file__).resolve().parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / f"commander_{date.today().isoformat()}.log"
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when='midnight', interval=1, backupCount=7, encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    logging.info(f"Starting commander, logs: {log_file}")
    serve(args.host, args.port, args.data_dir)


if __name__ == "__main__":
    main()
