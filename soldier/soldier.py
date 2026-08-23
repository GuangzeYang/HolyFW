#!/usr/bin/env python3
"""Soldier: listen for commander-dispatched tasks (JSON + shell command), execute, report back.

Also supports manual one-shot ``report`` subcommand.
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import argparse
import configparser
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Mapping, Sequence
import logging

from filelock import FileLock, Timeout as FileLockTimeout

try:
    import colorlog
except ImportError:
    colorlog = None

from common import (
    HOLYFW_ROOT_ENV,
    clean_old_files,
    locate_holyfw_root,
    soldier_workspace_dir,
    strip_opencode_run_prefix,
    validate_task_id,
    expand_date_segment,
    parse_task_ref,
    DATE_FULL,
    DATE_MD,
    UUID_HEX_NO_HYPHEN,
)

DEFAULT_PORT = 38471
DEFAULT_LISTEN_PORT = 38472
DEFAULT_CONFIG_NAME = "soldier.ini"
SUBPROCESS_TIMEOUT_DEFAULT = 900
DEFAULT_WORKER_THREADS = 3
MAX_LINE_BYTES = 65536
MAX_COMMAND_OUTPUT_BYTES = 65536
LOGS_DIR_NAME = "logs"
RUNTIME_DIR_NAME = "runtime"
TASK_RECORDS_DIR_NAME = "tasks"
PENDING_REPORTS_FILE_NAME = "pending_reports.jsonl"
FAILED_REPORTS_FILE_NAME = "failed_reports.jsonl"
REPORT_RETRY_LIMIT = 3
REPORT_RETRY_INTERVAL_SECONDS = 60
REPORT_SOCKET_TIMEOUT_SECONDS = 60
RUNNING_STALE_GRACE_SECONDS = 120
PROCESS_TREE_KILL_TIMEOUT_SECONDS = 30
SOLDIER_DATED_FILE_HANDLER_NAME = "soldier_dated_file"
SOLDIER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(task)s - %(message)s"
LOG_DATE_CHECK_INTERVAL_SECONDS = 1.0
_PENDING_REPORTS_LOCK = threading.Lock()
_ACTIVE_PROCESSES_LOCK = threading.Lock()
_ACTIVE_PROCESSES: dict[int, subprocess.Popen] = {}
_SHUTTING_DOWN = threading.Event()


class _TaskDefaultFilter(logging.Filter):
    """Ensure every record has a ``task`` attribute for the soldier formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task") or not record.task:
            record.task = "system"
        return True


def task_extra(task_id: str | None = None) -> dict[str, str]:
    return {"task": (task_id or "system").strip() or "system"}


def _plain_log_formatter() -> logging.Formatter:
    return logging.Formatter(SOLDIER_LOG_FORMAT)


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.addFilter(_TaskDefaultFilter())
    plain_formatter = _plain_log_formatter()
    if colorlog is not None:
        color_formatter = colorlog.ColoredFormatter(
            "%(log_color)s" + SOLDIER_LOG_FORMAT,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
        console_handler.setFormatter(color_formatter)
    else:
        console_handler.setFormatter(plain_formatter)
    return console_handler


def reattach_soldier_dated_file_handler(
    logs_dir: Path,
    level: int = logging.INFO,
    *,
    target_day: date | None = None,
    logger: logging.Logger | None = None,
) -> Path:
    """Replace only the soldier dated file handler with the file for target_day."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = target_day or date.today()
    log_file = logs_dir / f"soldier_{day.isoformat()}.log"

    root = logger or logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == SOLDIER_DATED_FILE_HANDLER_NAME:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.addFilter(_TaskDefaultFilter())
    file_handler.setFormatter(_plain_log_formatter())
    file_handler.name = SOLDIER_DATED_FILE_HANDLER_NAME
    root.addHandler(file_handler)
    return log_file


def configure_soldier_root_logging(logs_dir: Path, level: int = logging.INFO) -> Path:
    """Configure soldier logging for long-running daily log files."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(level)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    logger.addHandler(_build_console_handler(level))
    return reattach_soldier_dated_file_handler(logs_dir, level, logger=logger)


def soldier_data_dir() -> Path:
    """Writable soldier/ directory in the source workspace, never site-packages."""
    return soldier_workspace_dir(package_hint=Path(__file__).resolve().parent)


def get_logs_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or soldier_data_dir()
    path = root / LOGS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_runtime_dir(base_dir: Path | None = None) -> Path:
    root = base_dir or soldier_data_dir()
    path = root / RUNTIME_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_task_records_dir(base_dir: Path | None = None) -> Path:
    path = get_runtime_dir(base_dir) / TASK_RECORDS_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_record_path(task_id: str, base_dir: Path | None = None) -> Path:
    return get_task_records_dir(base_dir) / f"{task_id}.json"


def _read_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class TaskRecordFile:
    """Exclusive per-task JSON file. The handle stays open until complete/abort/close."""

    def __init__(self, path: Path, lock: FileLock, handle: IO[bytes], record: dict) -> None:
        self.path = path
        self.record = record
        self._lock: FileLock | None = lock
        self._handle: IO[bytes] | None = handle

    def persist(self) -> None:
        if self._handle is None or self._handle.closed:
            raise OSError(f"Task record file is closed: {self.path}")
        self.record["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload = (json.dumps(self.record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self._handle.seek(0)
        self._handle.write(payload)
        self._handle.truncate()
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def complete(self, report: dict, *, status: str, exit_code: int, stdout_text: str, stderr_text: str, message: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.record["status"] = "completed"
        self.record["completed_at"] = now
        self.record["report"] = report
        self.record["exit_code"] = exit_code
        self.record["stdout"] = stdout_text
        self.record["stderr"] = stderr_text
        self.record["message"] = message or ""
        self.record["result_status"] = status
        self.persist()

    def abort(self) -> None:
        self.close()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None and not handle.closed:
            try:
                handle.close()
            except OSError:
                pass
        lock = self._lock
        self._lock = None
        if lock is not None and lock.is_locked:
            try:
                lock.release()
            except OSError:
                pass


@dataclass
class ClaimResult:
    status: str
    record: dict | None = None
    handle: TaskRecordFile | None = None


def claim_task_execution(
    date_str: str,
    task_id: str,
    task_ref: str,
    command: str,
    received_at: str,
    stale_after_seconds: int,
    *,
    base_dir: Path | None = None,
) -> ClaimResult:
    """Open ``runtime/tasks/{task_id}.json`` exclusively for the life of the task."""
    path = task_record_path(task_id, base_dir)
    lock = FileLock(str(path) + ".lock", timeout=0)
    try:
        lock.acquire()
    except FileLockTimeout:
        existing = _read_json_object(path)
        return ClaimResult("running", existing or {"status": "running", "task_id": task_id})

    handle: IO[bytes] | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        handle = path.open("r+b")
        handle.seek(0)
        raw_bytes = handle.read()
        raw = raw_bytes.decode("utf-8") if raw_bytes else ""
        existing: dict = {}
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                existing = parsed
        if existing.get("status") == "completed":
            handle.close()
            handle = None
            lock.release()
            return ClaimResult("completed", existing)
        if existing.get("status") == "running":
            logging.warning("Task %s has leftover running state; allowing re-execution", task_ref)

        record = {
            "task_id": task_id,
            "task_ref": task_ref,
            "date": date_str,
            "status": "running",
            "received_at": received_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "execution_deadline": (
                datetime.now(timezone.utc) + timedelta(seconds=stale_after_seconds)
            ).isoformat(),
        }
        owned = TaskRecordFile(path, lock, handle, record)
        owned.persist()
        clean_old_files(get_task_records_dir(base_dir), "*.json", days=20)
        return ClaimResult("claimed", record, owned)
    except Exception:
        if handle is not None and not handle.closed:
            handle.close()
        if lock.is_locked:
            lock.release()
        raise


def append_task_execution_log(
    *,
    task_id: str,
    task_ref: str,
    date_str: str,
    received_at: str,
    command: str,
    status: str,
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
    message: str | None = None,
    base_dir: Path | None = None,
    argv: list[str] | None = None,
    task: str | None = None,
) -> Path:
    """Write a completed task record keyed by task_id (used by tests and late writers)."""
    prompt = strip_opencode_run_prefix(task if task is not None else command)
    path = task_record_path(task_id, base_dir)
    record = {
        "received_at": received_at,
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task_id": task_id,
        "task_ref": task_ref,
        "date": date_str,
        "task": prompt,
        "command": prompt,
        "argv": list(argv) if argv is not None else [],
        "status": "completed",
        "result_status": status,
        "exit_code": exit_code,
        "message": message or "",
        "stdout": stdout_text,
        "stderr": stderr_text,
        "report": {
            "task_ref": task_ref,
            "status": status,
            "exit_code": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def save_task_record(
    task_id: str,
    date_str: str,
    content: dict,
    received_at: str,
    stdout: str,
    stderr: str,
) -> Path:
    command = ""
    task_ref = task_id
    if isinstance(content, dict):
        command = str(content.get("command") or "")
        task_ref = str(content.get("task_ref") or task_id)
    return append_task_execution_log(
        task_id=task_id,
        task_ref=task_ref,
        date_str=date_str,
        received_at=received_at,
        command=command,
        status="unknown",
        exit_code=-1,
        stdout_text=stdout,
        stderr_text=stderr,
        message="legacy save_task_record",
    )


def save_command_output(
    task_id: str,
    received_at: str,
    task_ref: str,
    command: str,
    status: str,
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
) -> Path:
    date_str = task_ref.split("_", 1)[0] if "_" in task_ref else date.today().isoformat()
    return append_task_execution_log(
        task_id=task_id,
        task_ref=task_ref,
        date_str=date_str,
        received_at=received_at,
        command=command,
        status=status,
        exit_code=exit_code,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
    )


def pending_reports_path(base_dir: Path | None = None) -> Path:
    return get_runtime_dir(base_dir) / PENDING_REPORTS_FILE_NAME


def failed_reports_path(base_dir: Path | None = None) -> Path:
    return get_runtime_dir(base_dir) / FAILED_REPORTS_FILE_NAME


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def enqueue_pending_report(
    commander_host: str,
    commander_port: int,
    payload: dict,
    last_error: str,
    *,
    base_dir: Path | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    record = {
        "commander_host": commander_host,
        "commander_port": commander_port,
        "payload": payload,
        "attempts": 0,
        "last_error": last_error,
        "created_at": now,
        "updated_at": now,
    }
    with _PENDING_REPORTS_LOCK:
        _append_jsonl(pending_reports_path(base_dir), record)


def _load_report_queue(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                logging.warning(f"Skipping invalid pending report line in {path}")
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _write_report_queue(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def process_pending_reports_once(base_dir: Path | None = None) -> None:
    pending_path = pending_reports_path(base_dir)
    failed_path = failed_reports_path(base_dir)
    with _PENDING_REPORTS_LOCK:
        records = _load_report_queue(pending_path)
        if not records:
            return

        keep: list[dict] = []
        failed: list[dict] = []
        for record in records:
            payload = record.get("payload")
            host = record.get("commander_host")
            port = record.get("commander_port")
            attempts = int(record.get("attempts") or 0)
            if not isinstance(payload, dict) or not isinstance(host, str) or not isinstance(port, int):
                record["last_error"] = "Invalid pending report record"
                record["attempts"] = attempts + 1
                failed.append(record)
                continue

            _, err = send_report(host, port, payload)
            if err is None:
                logging.info(f"Pending report delivered to commander: {payload.get('task_ref')}")
                continue

            attempts += 1
            record["attempts"] = attempts
            record["last_error"] = err
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            if attempts >= REPORT_RETRY_LIMIT:
                failed.append(record)
                logging.error(
                    f"Pending report exceeded retry limit for {payload.get('task_ref')}: {err}"
                )
            else:
                keep.append(record)
                logging.warning(
                    f"Pending report retry {attempts}/{REPORT_RETRY_LIMIT} failed for "
                    f"{payload.get('task_ref')}: {err}"
                )

        _write_report_queue(pending_path, keep)
        for record in failed:
            _append_jsonl(failed_path, record)


def replay_failed_reports_once(base_dir: Path | None = None) -> tuple[int, int]:
    """Manually retry terminal failed-report records once."""
    path = failed_reports_path(base_dir)
    with _PENDING_REPORTS_LOCK:
        records = _load_report_queue(path)
        keep: list[dict] = []
        delivered = 0
        for record in records:
            payload = record.get("payload")
            host = record.get("commander_host")
            port = record.get("commander_port")
            if not isinstance(payload, dict) or not isinstance(host, str) or not isinstance(port, int):
                keep.append(record)
                continue
            _, error = send_report(host, port, payload)
            if error is None:
                delivered += 1
                logging.info("Replayed failed report: %s", payload.get("task_ref"))
            else:
                record["last_error"] = error
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                keep.append(record)
                logging.error("Failed-report replay still failed for %s: %s", payload.get("task_ref"), error)
        _write_report_queue(path, keep)
        return delivered, len(keep)


def start_report_retry_thread(base_dir: Path | None = None) -> None:
    def retry_loop() -> None:
        logging.info(
            f"Soldier pending report retry thread started; limit={REPORT_RETRY_LIMIT}"
        )
        while True:
            try:
                process_pending_reports_once(base_dir)
            except Exception as exc:
                logging.error(f"Exception in report retry loop: {exc}", exc_info=True)
            time.sleep(REPORT_RETRY_INTERVAL_SECONDS)

    threading.Thread(target=retry_loop, daemon=True).start()


def _read_temp_output_limited(temp_file, max_bytes: int) -> tuple[str, bool]:
    temp_file.seek(0)
    data = temp_file.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    if truncated:
        text += "\n...[truncated]"
    return text, truncated


def _register_active_process(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES[proc.pid] = proc


def _unregister_active_process(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.pop(proc.pid, None)


def terminate_process_tree(proc: subprocess.Popen, reason: str) -> None:
    """Terminate the shell and all descendants, with Windows-specific tree killing."""
    if proc.poll() is not None:
        return
    logging.warning("Terminating process tree pid=%s reason=%s", proc.pid, reason)
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=PROCESS_TREE_KILL_TIMEOUT_SECONDS,
            )
            if result.returncode != 0 and proc.poll() is None:
                logging.error(
                    "taskkill failed for pid=%s code=%s stderr=%s",
                    proc.pid,
                    result.returncode,
                    (result.stderr or "").strip(),
                )
                proc.kill()
        except (OSError, subprocess.TimeoutExpired) as exc:
            logging.error("taskkill exception for pid=%s: %s", proc.pid, exc)
            if proc.poll() is None:
                proc.kill()
    else:
        try:
            os.killpg(proc.pid, 15)
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                if proc.poll() is None:
                    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def terminate_all_active_processes(reason: str) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        processes = list(_ACTIVE_PROCESSES.values())
    for proc in processes:
        try:
            terminate_process_tree(proc, reason)
        except Exception as exc:
            logging.error("Failed to terminate active pid=%s: %s", proc.pid, exc, exc_info=True)


def _command_label(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        return command
    parts = [str(part) for part in command]
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return " ".join(parts)


def resolve_opencode_executable() -> str:
    found = shutil.which("opencode")
    if not found:
        raise FileNotFoundError("opencode executable not found on PATH")
    return found


OPENCODE_PERMISSION_ALLOW: dict[str, object] = {
    "*": "allow",
    "doom_loop": "allow",
    "external_directory": {"*": "allow"},
}


def build_opencode_argv(prompt: str) -> list[str]:
    return [resolve_opencode_executable(), "run", "--auto", prompt]


def opencode_run_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["OPENCODE_PERMISSION"] = json.dumps(OPENCODE_PERMISSION_ALLOW, separators=(",", ":"))
    return env


def dispatch_prompt_from_payload(payload: dict) -> str:
    raw = payload.get("task")
    if not isinstance(raw, str) or not raw.strip():
        raw = payload.get("command")
    if not isinstance(raw, str):
        return ""
    return strip_opencode_run_prefix(raw)


def execute_command(
    command: str | Sequence[str],
    timeout_sec: int,
    max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
    *,
    task_ref: str = "",
    env: Mapping[str, str] | None = None,
) -> tuple[str, str, int, str, str | None]:
    """Execute a command in its own process group and clean the whole tree on timeout."""
    if _SHUTTING_DOWN.is_set():
        return "", "soldier is shutting down", -1, "failed", "Execution cancelled during shutdown"
    popen_options: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if env is not None:
        popen_options["env"] = dict(env)
    if isinstance(command, str):
        popen_target: str | list[str] = command
        popen_options["shell"] = True
    else:
        popen_target = [str(part) for part in command]
        popen_options["shell"] = False
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    with tempfile.TemporaryFile() as stdout_tmp, tempfile.TemporaryFile() as stderr_tmp:
        popen_options["stdout"] = stdout_tmp
        popen_options["stderr"] = stderr_tmp
        try:
            proc = subprocess.Popen(popen_target, **popen_options)
        except OSError as exc:
            return "", str(exc), -1, "failed", f"Execution failed: {exc}"

        _register_active_process(proc)
        try:
            if _SHUTTING_DOWN.is_set():
                terminate_process_tree(proc, "soldier shutdown race")
                return "", "soldier is shutting down", -1, "failed", "Execution cancelled during shutdown"
            try:
                exit_code = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                terminate_process_tree(proc, f"task timeout: {task_ref or _command_label(command)[:80]}")
                out, out_truncated = _read_temp_output_limited(stdout_tmp, max_output_bytes)
                err_out, err_truncated = _read_temp_output_limited(stderr_tmp, max_output_bytes)
                if not err_out:
                    err_out = "timeout"
                msg = f"Command timeout (>{timeout_sec}s); process tree terminated"
                if out_truncated or err_truncated:
                    msg += "; output truncated"
                return out, err_out, -1, "failed", msg

            out, out_truncated = _read_temp_output_limited(stdout_tmp, max_output_bytes)
            err_out, err_truncated = _read_temp_output_limited(stderr_tmp, max_output_bytes)
            ok_run = exit_code == 0
            status = "successed" if ok_run else "failed"
            msg = None if ok_run else f"Command exit code {exit_code}"
            if out_truncated or err_truncated:
                msg = f"{msg}; output truncated" if msg else "output truncated"
            return out, err_out, exit_code, status, msg
        finally:
            _unregister_active_process(proc)








def task_ref_full(date_str: str, role: str, task_id: str) -> str:
    return f"{date_str}_{role}_{task_id}"


def default_config_path() -> Path:
    try:
        candidate = soldier_data_dir() / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
    except FileNotFoundError:
        pass
    return Path(__file__).resolve().parent / DEFAULT_CONFIG_NAME


def load_commander_from_ini(path: Path) -> tuple[str | None, int | None]:
    if not path.is_file():
        return None, None
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    if "commander" not in cp:
        return None, None
    sec = cp["commander"]
    raw_ip = (sec.get("ip") or sec.get("host") or "").strip()
    host = raw_ip or None
    raw_port = (sec.get("port") or "").strip()
    if not raw_port:
        return host, None
    try:
        port = int(raw_port)
    except ValueError:
        return host, None
    return host, port


def load_listen_from_ini(path: Path) -> tuple[str | None, int | None]:
    if not path.is_file():
        return None, None
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    if "listen" not in cp:
        return None, None
    sec = cp["listen"]
    bind = (sec.get("bind") or sec.get("host") or "0.0.0.0").strip()
    raw_port = (sec.get("port") or "").strip()
    if not raw_port:
        return bind, None
    try:
        port = int(raw_port)
    except ValueError:
        return bind, None
    return bind, port


def load_exec_timeout(path: Path) -> int | None:
    if not path.is_file():
        return None
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    if "exec" not in cp:
        return None
    raw = (cp["exec"].get("timeout") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def load_worker_threads(path: Path) -> int:
    if not path.is_file():
        return DEFAULT_WORKER_THREADS
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    raw = ""
    if "listen" in cp:
        raw = (cp["listen"].get("worker_threads") or "").strip()
    if not raw:
        return DEFAULT_WORKER_THREADS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_WORKER_THREADS
    return value if value > 0 else DEFAULT_WORKER_THREADS


def resolve_endpoint(
    args_host: str | None,
    args_port: int | None,
    config_path: Path,
) -> tuple[str, int]:
    ini_host, ini_port = load_commander_from_ini(config_path)
    host = args_host if args_host is not None else (ini_host or "127.0.0.1")
    port = args_port if args_port is not None else (ini_port if ini_port is not None else DEFAULT_PORT)
    return host, port


def resolve_listen(
    args_bind: str | None,
    args_port: int | None,
    config_path: Path,
) -> tuple[str, int]:
    ini_bind, ini_port = load_listen_from_ini(config_path)
    bind = args_bind if args_bind is not None else (ini_bind or "0.0.0.0")
    port = args_port if args_port is not None else (ini_port if ini_port is not None else DEFAULT_LISTEN_PORT)
    return bind, port


def send_report(
    commander_host: str,
    commander_port: int,
    payload: dict,
) -> tuple[dict | None, str | None]:
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    try:
        with socket.create_connection(
            (commander_host, commander_port),
            timeout=REPORT_SOCKET_TIMEOUT_SECONDS,
        ) as sock:
            sock.settimeout(REPORT_SOCKET_TIMEOUT_SECONDS)
            sock.sendall(line.encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_LINE_BYTES:
                    return None, "Response too long"
    except OSError as e:
        return None, f"Failed to connect to commander: {e}"
    if not buf.strip():
        return None, "No response from commander"
    resp_line = buf.split(b"\n", 1)[0]
    try:
        resp = json.loads(resp_line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "Response is not valid JSON"
    if not isinstance(resp, dict):
        return None, "Response format error"
    return resp, None


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


def send_dispatch_response(conn: socket.socket, payload: dict) -> bool:
    try:
        conn.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        return True
    except OSError as exc:
        logging.warning("Failed to send dispatch acknowledgment: %s", exc)
        return False


def handle_dispatch_connection(
    conn: socket.socket,
    commander_host: str,
    commander_port: int,
    timeout_sec: int,
) -> None:
    logging.debug("Dispatch connection accepted")
    claim: ClaimResult | None = None
    try:
        conn.settimeout(timeout_sec + RUNNING_STALE_GRACE_SECONDS)
        try:
            raw = recv_one_line(conn)
        except ValueError as e:
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    )
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return
        if not raw.strip():
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": "Empty request"}, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    )
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": "JSON parsing failed"}, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    )
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return
        if not isinstance(payload, dict):
            try:
                conn.sendall(
                    (
                        json.dumps({"ok": False, "error": "Request body must be a JSON object"}, ensure_ascii=False)
                        + "\n"
                    ).encode("utf-8")
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return
        task_ref = payload.get("task_ref")
        prompt = dispatch_prompt_from_payload(payload)
        task_date_override = payload.get("task_date")
        if not isinstance(task_ref, str):
            try:
                conn.sendall(
                    (
                        json.dumps({"ok": False, "error": "Missing or invalid task_ref"}, ensure_ascii=False)
                        + "\n"
                    ).encode("utf-8")
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return
        if not prompt:
            try:
                conn.sendall(
                    (
                        json.dumps({"ok": False, "error": "Missing or invalid task"}, ensure_ascii=False)
                        + "\n"
                    ).encode("utf-8")
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return

        parsed, perr = parse_task_ref(task_ref)
        if perr:
            try:
                conn.sendall(
                    (json.dumps({"ok": False, "error": perr}, ensure_ascii=False) + "\n").encode(
                        "utf-8"
                    )
                )
            except OSError as os_err:
                logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
            return

        assert parsed is not None
        date_str, role, task_id = parsed
        if task_date_override is not None:
            if not isinstance(task_date_override, str) or not DATE_FULL.match(
                task_date_override.strip()
            ):
                try:
                    conn.sendall(
                        (
                            json.dumps(
                                {"ok": False, "error": "task_date must be YYYY-MM-DD"},
                                ensure_ascii=False,
                            )
                            + "\n"
                        ).encode("utf-8")
                    )
                except OSError as os_err:
                    logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
                return
            date_str = task_date_override.strip()
        full_ref = task_ref_full(date_str, role, task_id)
        received_at = datetime.now().astimezone().isoformat(timespec="seconds")
        extras = task_extra(task_id)
        logging.info("Received — %s", prompt, extra=extras)
        claim = claim_task_execution(
            date_str,
            task_id,
            full_ref,
            prompt,
            received_at,
            timeout_sec + RUNNING_STALE_GRACE_SECONDS,
        )
        previous_state = claim.record
        if claim.status == "completed":
            previous_report = previous_state.get("report") if isinstance(previous_state, dict) else None
            if not isinstance(previous_report, dict):
                logging.warning(
                    "Duplicate completed without saved report; ignoring",
                    extra=extras,
                )
                send_dispatch_response(
                    conn,
                    {
                        "ok": False,
                        "status": "rejected",
                        "task_ref": full_ref,
                        "error": "Completed state has no saved report",
                    },
                )
                return
            send_dispatch_response(
                conn,
                {
                    "ok": True,
                    "status": "completed",
                    "task_ref": full_ref,
                    "execution_deadline": str(previous_state.get("execution_deadline") or ""),
                },
            )
            logging.info("Duplicate already completed; replaying saved report", extra=extras)
            _, serr = send_report(commander_host, commander_port, previous_report)
            if serr:
                logging.error("Failed to replay completed report: %s", serr, extra=extras)
                try:
                    enqueue_pending_report(commander_host, commander_port, previous_report, serr)
                except OSError as e:
                    logging.error("Failed to queue replay report: %s", e, extra=extras)
            return
        if claim.status == "running":
            logging.info("Duplicate already running; ignoring", extra=extras)
            send_dispatch_response(
                conn,
                {
                    "ok": True,
                    "status": "running",
                    "task_ref": full_ref,
                    "execution_deadline": str(previous_state.get("execution_deadline") or "") if isinstance(previous_state, dict) else "",
                },
            )
            return

        execution_deadline = ""
        if isinstance(previous_state, dict):
            execution_deadline = str(previous_state.get("execution_deadline") or "")
        if not send_dispatch_response(
            conn,
            {
                "ok": True,
                "status": "accepted",
                "task_ref": full_ref,
                "execution_deadline": execution_deadline,
                "execution_timeout_seconds": timeout_sec,
            },
        ):
            logging.error("Claimed but acknowledgment failed; not executing", extra=extras)
            if claim.handle is not None:
                claim.handle.abort()
            return

        argv = ["opencode", "run", "--auto", prompt]
        try:
            argv = build_opencode_argv(prompt)
        except FileNotFoundError as exc:
            out, err_out, exit_code, status, msg = "", str(exc), -1, "failed", str(exc)
        else:
            out, err_out, exit_code, status, msg = execute_command(
                argv,
                timeout_sec,
                task_ref=full_ref,
                env=opencode_run_env(),
            )

        report = {
            "task_ref": full_ref,
            "status": status,
            "exit_code": exit_code,
            "stdout": out,
            "stderr": err_out,
        }
        if msg is not None:
            report["message"] = msg

        try:
            if claim.handle is not None:
                claim.handle.record["argv"] = list(argv)
                claim.handle.record["task"] = prompt
                claim.handle.complete(
                    report,
                    status=status,
                    exit_code=exit_code,
                    stdout_text=out,
                    stderr_text=err_out,
                    message=msg,
                )
            else:
                append_task_execution_log(
                    task_id=task_id,
                    task_ref=full_ref,
                    date_str=date_str,
                    received_at=received_at,
                    command=prompt,
                    status=status,
                    exit_code=exit_code,
                    stdout_text=out,
                    stderr_text=err_out,
                    message=msg,
                    argv=argv,
                    task=prompt,
                )
        except OSError as e:
            logging.error("Failed to write task log: %s", e, extra=extras)

        logging.info(
            "Finished — %s — exit_code=%s",
            status,
            exit_code,
            extra=extras,
        )
        if out:
            logging.debug("stdout — %s", out[:500], extra=extras)
        if err_out:
            logging.debug("stderr — %s", err_out[:500], extra=extras)
        _, serr = send_report(commander_host, commander_port, report)
        if serr:
            logging.error("Failed to report to commander: %s", serr, extra=extras)
            try:
                enqueue_pending_report(commander_host, commander_port, report, serr)
                logging.info("Queued for commander report retry", extra=extras)
            except OSError as e:
                logging.error("Failed to queue report retry: %s", e, extra=extras)
            return
        logging.debug("Reported successfully to commander", extra=extras)
    finally:
        if claim is not None and claim.handle is not None:
            claim.handle.close()
        conn.close()


def spawn_sysmon_collector() -> subprocess.Popen | None:
    """Sysmon collection is manual-only; ``soldier`` does not start it."""
    logging.info("Sysmon collector is manual-only; run sysmon-collect as Administrator")
    return None


def maybe_start_sysmon_collector(*, enabled: bool = True) -> subprocess.Popen | None:
    logging.info("Sysmon collector is manual-only; run sysmon-collect as Administrator")
    return None


def run_listen(
    config_path: Path,
    bind: str | None,
    port: int | None,
    commander_host: str | None,
    commander_port: int | None,
    *,
    no_sysmon: bool = False,
) -> None:
    _SHUTTING_DOWN.clear()
    b, lp = resolve_listen(bind, port, config_path)
    sh, sp = resolve_endpoint(commander_host, commander_port, config_path)
    to = load_exec_timeout(config_path)
    timeout_sec = to if to is not None and to > 0 else SUBPROCESS_TIMEOUT_DEFAULT
    worker_threads = load_worker_threads(config_path)
    try:
        script_dir = soldier_data_dir()
    except FileNotFoundError as exc:
        logging.error("%s", exc)
        raise

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((b, lp))
    sock.listen(32)
    sock.settimeout(LOG_DATE_CHECK_INTERVAL_SECONDS)
    logging.info(
        "Listening for tasks on %s:%s; reporting to commander %s:%s; "
        "exec timeout=%ss; worker_threads=%s",
        b,
        lp,
        sh,
        sp,
        timeout_sec,
        worker_threads,
    )
    soldier_logs = get_logs_dir(script_dir)
    current_log_day = date.today()
    start_report_retry_thread(script_dir)
    try:
        executor = ThreadPoolExecutor(max_workers=worker_threads)
        execution_slots = threading.BoundedSemaphore(worker_threads)

        def run_claimed_connection(conn: socket.socket) -> None:
            try:
                handle_dispatch_connection(conn, sh, sp, timeout_sec)
            finally:
                execution_slots.release()

        while True:
            new_log_day = date.today()
            if new_log_day != current_log_day:
                log_file = reattach_soldier_dated_file_handler(
                    soldier_logs, logging.INFO, target_day=new_log_day
                )
                current_log_day = new_log_day
                logging.info("Soldier log switched to %s", log_file)
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            if not execution_slots.acquire(blocking=False):
                logging.warning("Soldier at capacity; rejecting dispatch from %s", addr)
                send_dispatch_response(
                    conn,
                    {
                        "ok": False,
                        "status": "busy",
                        "error": f"Soldier capacity reached ({worker_threads})",
                    },
                )
                conn.close()
                continue
            try:
                executor.submit(run_claimed_connection, conn)
            except Exception:
                execution_slots.release()
                conn.close()
                raise
    finally:
        _SHUTTING_DOWN.set()
        sock.close()
        terminate_all_active_processes("soldier shutdown")
        if "executor" in locals():
            executor.shutdown(wait=True, cancel_futures=True)


def run_report(args: argparse.Namespace, config_path: Path) -> int:
    host, port = resolve_endpoint(args.host, args.port, config_path)
    _, err = parse_task_ref(args.task_ref)
    if err:
        logging.error(err)
        return 1
    payload: dict = {"task_ref": args.task_ref, "status": args.status}
    if args.message is not None:
        payload["message"] = args.message
    if args.exit_code is not None:
        payload["exit_code"] = args.exit_code
    if args.stdout is not None:
        payload["stdout"] = args.stdout
    if args.stderr is not None:
        payload["stderr"] = args.stderr
    resp, serr = send_report(host, port, payload)
    if serr:
        logging.error(serr)
        return 1
    assert resp is not None
    print(json.dumps(resp, ensure_ascii=False))
    return 0 if resp.get("ok") is True else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Soldier: listen for dispatched tasks by default and execute; can use report subcommand to manually report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Default reads {DEFAULT_CONFIG_NAME} in same directory:
  [commander] ip / port — report commander address
  [listen] bind / port — local listen address (default 0.0.0.0:{DEFAULT_LISTEN_PORT})
  [exec] timeout — single command timeout in seconds (default {SUBPROCESS_TIMEOUT_DEFAULT})
""",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"INI path (default same directory as script {DEFAULT_CONFIG_NAME})",
    )
    sub = parser.add_subparsers(dest="cmd", help="subcommand")

    listen_p = sub.add_parser("listen", help="listen for tasks dispatched by commander (default)")
    listen_p.add_argument("--bind", default=None, metavar="ADDR", help="bind address")
    listen_p.add_argument("--listen-port", type=int, default=None, help="listen port")
    listen_p.add_argument("--commander-host", default=None, help="report commander address (override INI)")
    listen_p.add_argument("--commander-port", type=int, default=None, help="report commander port")
    listen_p.add_argument(
        "--no-sysmon",
        action="store_true",
        help="accepted for compatibility; Sysmon collection is manual-only",
    )

    report_p = sub.add_parser("report", help="manually report a receipt to commander")
    report_p.add_argument("--host", default=None, metavar="ADDR", help="commander address")
    report_p.add_argument("--port", type=int, default=None, metavar="N", help="commander port")
    report_p.add_argument("--task-ref", required=True, help="date_role_taskId (YYYY-MM-DD or MM-DD)")
    report_p.add_argument("--status", required=True, choices=["successed", "failed"])
    report_p.add_argument("--message", default=None)
    report_p.add_argument("--exit-code", type=int, default=None, dest="exit_code")
    report_p.add_argument("--stdout", default=None, help="optional output text")
    report_p.add_argument("--stderr", default=None)
    sub.add_parser(
        "replay-failed-reports",
        help="retry records in runtime/failed_reports.jsonl once",
    )
    build_p = sub.add_parser(
        "build",
        help="install this role's OpenCode skills and MCP into ~/.config/opencode",
    )
    build_p.add_argument("role", help="role name (hr, accountancy, manager, programmer, attacker, victim)")

    args = parser.parse_args(argv)
    if args.cmd == "build":
        try:
            from soldier.host_build import run_build
        except ImportError:
            from host_build import run_build

        return run_build(args.role)

    try:
        script_dir = soldier_data_dir()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    soldier_logs = get_logs_dir(script_dir)
    get_runtime_dir(script_dir)
    log_file = configure_soldier_root_logging(soldier_logs, logging.INFO)

    logging.info("Soldier starting, logs: %s", log_file)
    logging.info("Soldier workspace: %s", script_dir)
    logging.info("Runtime state directory: %s", get_runtime_dir(script_dir))

    cfg = args.config if getattr(args, "config", None) is not None else default_config_path()

    if args.cmd is None or args.cmd == "listen":
        run_listen(
            cfg,
            getattr(args, "bind", None),
            getattr(args, "listen_port", None),
            getattr(args, "commander_host", None),
            getattr(args, "commander_port", None),
            no_sysmon=bool(getattr(args, "no_sysmon", False)),
        )
        return 0
    if args.cmd == "report":
        return run_report(args, cfg)
    if args.cmd == "replay-failed-reports":
        delivered, remaining = replay_failed_reports_once(script_dir)
        print(json.dumps({"delivered": delivered, "remaining": remaining}, ensure_ascii=False))
        return 0 if remaining == 0 else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
