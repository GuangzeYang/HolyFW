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
import shlex
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
from typing import IO, Callable, Mapping, Sequence
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
from common.task_markdown import (
    OPENCODE_RUN_FLAGS,
    format_opencode_session,
    report_from_task_record,
    parse_task_markdown as parse_shared_task_markdown,
    render_task_markdown as render_shared_task_markdown,
    write_task_markdown as write_shared_task_markdown,
)
from common.llm_catalog import (
    enabled_provider,
    opencode_model_spec,
    overwrite_workspace_llm_json,
)
from common.user_env import set_user_env
from common.opencode_install import bind_opencode_provider_api_key_env

DEFAULT_PORT = 38471
DEFAULT_LISTEN_PORT = 38472
DEFAULT_CONFIG_NAME = "soldier.ini"
SUBPROCESS_TIMEOUT_DEFAULT = 900
DEFAULT_WORKER_THREADS = 3
MAX_LINE_BYTES = 65536
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
OUTPUT_LOG_PREVIEW_CHARS = 500
SOLDIER_DATED_FILE_HANDLER_NAME = "soldier_dated_file"
SOLDIER_CONSOLE_HANDLER_NAME = "soldier_console"
SOLDIER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(task)s - %(message)s"
LOG_DATE_CHECK_INTERVAL_SECONDS = 1.0
_PENDING_REPORTS_LOCK = threading.Lock()
_ACTIVE_PROCESSES_LOCK = threading.Lock()
_ACTIVE_PROCESSES: dict[int, subprocess.Popen] = {}
_SHUTTING_DOWN = threading.Event()
_TASK_RECORD_META_KEYS = (
    "task_id",
    "task_ref",
    "date",
    "status",
    "outcome",
    "result_status",
    "received_at",
    "started_at",
    "finished_at",
    "reported_at",
    "argv",
)


class _TaskDefaultFilter(logging.Filter):
    """Ensure every record has a ``task`` attribute for the soldier formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task") or not record.task:
            record.task = "system"
        return True


class _ConsoleVisibilityFilter(logging.Filter):
    """Console: system lines plus per-task receive/result (``to_console``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        task = getattr(record, "task", "system") or "system"
        if task == "system":
            return True
        return bool(getattr(record, "to_console", False))


def task_extra(task_id: str | None = None, *, to_console: bool = False) -> dict[str, object]:
    extra: dict[str, object] = {"task": (task_id or "system").strip() or "system"}
    extra["to_console"] = to_console
    return extra


def log_task(
    level: int,
    msg: str,
    *args: object,
    task_id: str | None = None,
    to_console: bool = False,
) -> None:
    logging.log(level, msg, *args, extra=task_extra(task_id, to_console=to_console))


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _plain_log_formatter() -> logging.Formatter:
    return logging.Formatter(SOLDIER_LOG_FORMAT)


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.addFilter(_TaskDefaultFilter())
    console_handler.addFilter(_ConsoleVisibilityFilter())
    console_handler.name = SOLDIER_CONSOLE_HANDLER_NAME
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


def task_record_path(task_id: str, date_str: str, *, base_dir: Path | None = None) -> Path:
    return get_task_records_dir(base_dir) / date_str / f"{task_id}.md"


def legacy_task_json_path(task_id: str, *, base_dir: Path | None = None) -> Path:
    return get_task_records_dir(base_dir) / f"{task_id}.json"


def find_existing_task_record(task_id: str, *, base_dir: Path | None = None) -> Path | None:
    root = get_task_records_dir(base_dir)
    matches = sorted(path for path in root.glob(f"**/{task_id}.md") if path.is_file())
    if matches:
        return matches[0]
    legacy = legacy_task_json_path(task_id, base_dir=base_dir)
    if legacy.is_file():
        return legacy
    return None


def resolve_task_record_path(
    task_id: str,
    date_str: str,
    *,
    base_dir: Path | None = None,
) -> Path:
    existing = find_existing_task_record(task_id, base_dir=base_dir)
    return existing if existing is not None else task_record_path(task_id, date_str, base_dir=base_dir)


def _other_task_record_path(
    path: Path,
    task_id: str,
    date_str: str,
    *,
    base_dir: Path | None = None,
) -> Path:
    if path.suffix.lower() == ".json":
        return task_record_path(task_id, date_str, base_dir=base_dir)
    return legacy_task_json_path(task_id, base_dir=base_dir)


def _close_record_lock(handle: IO[bytes] | None, lock: FileLock | None) -> None:
    if handle is not None and not handle.closed:
        try:
            handle.close()
        except OSError:
            pass
    if lock is not None and lock.is_locked:
        try:
            lock.release()
        except OSError:
            pass


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _clean_old_task_records(base_dir: Path | None = None, days: int = 20) -> None:
    root = get_task_records_dir(base_dir)
    clean_old_files(root, "**/*.md", days=days)
    clean_old_files(root, "*.json", days=days)
    cutoff_time = time.time() - days * 86400
    try:
        children = list(root.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime >= cutoff_time:
                continue
            next(child.iterdir())
        except StopIteration:
            try:
                child.rmdir()
            except OSError:
                pass
        except OSError:
            pass


def write_task_markdown(handle: IO[bytes], record: dict) -> None:
    write_shared_task_markdown(handle, record, meta_keys=_TASK_RECORD_META_KEYS)


def parse_task_markdown(text: str) -> dict:
    """Parse soldier task Markdown (JSON-valued frontmatter) or leftover JSON."""
    return parse_shared_task_markdown(text)


def render_task_markdown(record: dict) -> str:
    return render_shared_task_markdown(record, meta_keys=_TASK_RECORD_META_KEYS)


def _read_task_record_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return parse_task_markdown(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


class TaskRecordFile:
    """Exclusive per-task Markdown file. The handle stays open until complete/abort/close."""

    def __init__(self, path: Path, lock: FileLock, handle: IO[bytes], record: dict) -> None:
        self.path = path
        self.record = record
        self._lock: FileLock | None = lock
        self._handle: IO[bytes] | None = handle

    def persist(self) -> None:
        if self._handle is None or self._handle.closed:
            raise OSError(f"Task record file is closed: {self.path}")
        self.record["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._handle.seek(0)
        write_task_markdown(self._handle, self.record)
        self._handle.truncate()
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def complete(
        self,
        report: dict,
        *,
        status: str,
        exit_code: int,
        message: str | None,
        outcome: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        command: str | None = None,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.record["status"] = "completed"
        self.record["completed_at"] = now
        self.record["finished_at"] = finished_at or _now_iso()
        if started_at:
            self.record["started_at"] = started_at
        if command:
            self.record["command"] = command
        self.record["report"] = report
        self.record["exit_code"] = exit_code
        self.record["message"] = message or ""
        self.record["result_status"] = status
        self.record["outcome"] = outcome or ("Success" if status == "successed" else "Fail")
        self.record["stdout"] = stdout or ""
        self.record["stderr"] = stderr or ""
        self.persist()

    def abort(self) -> None:
        self.close()
        _unlink_quietly(self.path)

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


def _read_open_task_record(handle: IO[bytes]) -> dict:
    handle.seek(0)
    raw_bytes = handle.read()
    raw = raw_bytes.decode("utf-8") if raw_bytes else ""
    return parse_task_markdown(raw) if raw.strip() else {}


def _migrate_legacy_json_claim(
    *,
    json_path: Path,
    json_handle: IO[bytes],
    json_lock: FileLock,
    task_id: str,
    date_str: str,
    base_dir: Path | None,
) -> tuple[Path, FileLock, IO[bytes], dict]:
    md_path = task_record_path(task_id, date_str, base_dir=base_dir)
    md_lock = FileLock(str(md_path) + ".lock", timeout=0)
    md_lock.acquire()
    md_handle: IO[bytes] | None = None
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        if not md_path.exists():
            md_path.touch()
        md_handle = md_path.open("r+b")
    except Exception:
        _close_record_lock(md_handle, md_lock)
        raise
    _close_record_lock(json_handle, json_lock)
    _unlink_quietly(json_path)
    return md_path, md_lock, md_handle, _read_open_task_record(md_handle)


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
    """Open ``runtime/tasks/{date}/{task_id}.md`` exclusively for the life of the task."""
    path = resolve_task_record_path(task_id, date_str, base_dir=base_dir)
    lock = FileLock(str(path) + ".lock", timeout=0)
    try:
        lock.acquire()
    except FileLockTimeout:
        existing = _read_task_record_file(path)
        if not existing:
            other = _other_task_record_path(path, task_id, date_str, base_dir=base_dir)
            existing = _read_task_record_file(other)
        return ClaimResult("running", existing or {"status": "running", "task_id": task_id})

    handle: IO[bytes] | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
        handle = path.open("r+b")
        existing = _read_open_task_record(handle)
        if not existing and path.suffix.lower() != ".json":
            legacy = legacy_task_json_path(task_id, base_dir=base_dir)
            if legacy.is_file():
                try:
                    existing = parse_task_markdown(legacy.read_text(encoding="utf-8"))
                except OSError:
                    existing = {}
                if existing.get("status") == "completed":
                    _close_record_lock(handle, lock)
                    return ClaimResult("completed", existing)
                _unlink_quietly(legacy)

        if existing.get("status") == "completed":
            _close_record_lock(handle, lock)
            return ClaimResult("completed", existing)

        if path.suffix.lower() == ".json":
            try:
                path, lock, handle, md_existing = _migrate_legacy_json_claim(
                    json_path=path,
                    json_handle=handle,
                    json_lock=lock,
                    task_id=task_id,
                    date_str=date_str,
                    base_dir=base_dir,
                )
            except FileLockTimeout:
                _close_record_lock(handle, lock)
                md_path = task_record_path(task_id, date_str, base_dir=base_dir)
                other = _read_task_record_file(md_path) or existing
                return ClaimResult("running", other or {"status": "running", "task_id": task_id})
            if md_existing.get("status") == "completed":
                _close_record_lock(handle, lock)
                return ClaimResult("completed", md_existing)
            existing = md_existing or existing

        if existing.get("status") == "running":
            log_task(
                logging.WARNING,
                "Task %s has leftover running state; allowing re-execution",
                task_ref,
                task_id=task_id,
            )

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
        _clean_old_task_records(base_dir)
        return ClaimResult("claimed", record, owned)
    except Exception:
        _close_record_lock(handle, lock)
        raise


def format_opencode_command(argv: Sequence[str] | None = None, prompt: str = "") -> str:
    if argv:
        return " ".join(shlex.quote(str(part)) for part in argv)
    if prompt:
        return " ".join(shlex.quote(part) for part in ("opencode", "run", *OPENCODE_RUN_FLAGS, prompt))
    return ""


def outcome_to_report_status(outcome: str) -> str:
    return "successed" if outcome == "Success" else "failed"


def commander_report_payload(
    task_ref: str,
    status: str,
    exit_code: int,
    message: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_ref": task_ref,
        "status": status,
        "exit_code": exit_code,
    }
    if message is not None:
        payload["message"] = message
    return payload


def strip_report_streams(payload: dict) -> dict:
    """Drop stdout/stderr from stored or queued reports before they go on the wire."""
    cleaned = dict(payload)
    cleaned.pop("stdout", None)
    cleaned.pop("stderr", None)
    return cleaned


def _process_output_preview(result: CommandResult) -> str:
    text = format_opencode_session(result.stdout or "", result.stderr or "").strip()
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= OUTPUT_LOG_PREVIEW_CHARS:
        return compact
    return compact[:OUTPUT_LOG_PREVIEW_CHARS] + "..."


def _log_console_outcome(result: CommandResult, task_id: str) -> None:
    preview = _process_output_preview(result)
    if result.outcome == "Success":
        log_task(logging.INFO, "Success", task_id=task_id, to_console=True)
        return
    if result.outcome == "Fail":
        reason = result.message or f"exit_code={result.exit_code}"
        if preview:
            log_task(logging.WARNING, "Fail %s; output %s", reason, preview, task_id=task_id, to_console=True)
        else:
            log_task(logging.WARNING, "Fail %s", reason, task_id=task_id, to_console=True)
        return
    reason = result.message or "unknown error"
    if preview:
        log_task(logging.ERROR, "Error %s; output %s", reason, preview, task_id=task_id, to_console=True)
    else:
        log_task(logging.ERROR, "Error %s", reason, task_id=task_id, to_console=True)


def append_task_execution_log(
    *,
    task_id: str,
    task_ref: str,
    date_str: str,
    received_at: str,
    command: str,
    status: str,
    exit_code: int,
    message: str | None = None,
    base_dir: Path | None = None,
    argv: list[str] | None = None,
    task: str | None = None,
    outcome: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> Path:
    """Write a completed task Markdown record keyed by task_id."""
    prompt = strip_opencode_run_prefix(task if task is not None else command)
    if argv:
        command_line = format_opencode_command(argv, prompt)
    elif command.strip().startswith("opencode"):
        command_line = command
    else:
        command_line = format_opencode_command(prompt=prompt or command)
    resolved_outcome = outcome or (
        "Success" if status == "successed" else "Fail" if status == "failed" else "Error"
    )
    result_status = status if status in {"successed", "failed"} else outcome_to_report_status(resolved_outcome)
    path = resolve_task_record_path(task_id, date_str, base_dir=base_dir)
    record = {
        "received_at": received_at,
        "started_at": started_at or received_at,
        "finished_at": finished_at or _now_iso(),
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "task_id": task_id,
        "task_ref": task_ref,
        "date": date_str,
        "command": command_line,
        "argv": list(argv) if argv is not None else [],
        "status": "completed",
        "outcome": resolved_outcome,
        "result_status": result_status,
        "exit_code": exit_code,
        "message": message or "",
        "stdout": stdout or "",
        "stderr": stderr or "",
        "report": commander_report_payload(
            task_ref,
            result_status,
            exit_code,
            message,
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        write_task_markdown(handle, record)
    return path


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
        "payload": strip_report_streams(payload),
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


def _register_active_process(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES[proc.pid] = proc


def _unregister_active_process(proc: subprocess.Popen) -> None:
    with _ACTIVE_PROCESSES_LOCK:
        _ACTIVE_PROCESSES.pop(proc.pid, None)


def terminate_process_tree(
    proc: subprocess.Popen,
    reason: str,
    *,
    task_id: str | None = None,
) -> None:
    """Terminate the shell and all descendants, with Windows-specific tree killing."""
    if proc.poll() is not None:
        return
    log_task(
        logging.WARNING,
        "Terminating process tree pid=%s reason=%s",
        proc.pid,
        reason,
        task_id=task_id,
        to_console=False,
    )
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=PROCESS_TREE_KILL_TIMEOUT_SECONDS,
            )
            if result.returncode != 0 and proc.poll() is None:
                log_task(
                    logging.ERROR,
                    "taskkill failed for pid=%s code=%s stderr=%s",
                    proc.pid,
                    result.returncode,
                    (result.stderr or "").strip(),
                    task_id=task_id,
                    to_console=False,
                )
                proc.kill()
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_task(
                logging.ERROR,
                "taskkill exception for pid=%s: %s",
                proc.pid,
                exc,
                task_id=task_id,
                to_console=False,
            )
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
    return [
        resolve_opencode_executable(),
        "run",
        *OPENCODE_RUN_FLAGS,
        "--model",
        runtime_opencode_model_spec(),
        prompt,
    ]


def runtime_opencode_model_spec() -> str:
    name, record = enabled_provider()
    return opencode_model_spec(name, record)


def _payload_llm_json_text(raw: object) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw
    if isinstance(raw, dict) and raw:
        return json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    raise ValueError("Missing or invalid llm_json")


def apply_llm_config(payload: dict) -> dict[str, object]:
    api_key = payload.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("Missing or invalid api_key")
    dest = overwrite_workspace_llm_json(_payload_llm_json_text(payload.get("llm_json")))
    name, record = enabled_provider(dest)
    set_user_env(record.env, api_key.strip())
    bind_opencode_provider_api_key_env(name, record.env)
    logging.info(
        "LLM config applied for provider %s model %s json_written=True",
        name,
        record.models,
    )
    return {
        "ok": True,
        "status": "configured",
        "provider": name,
        "env": record.env,
        "model": record.models,
        "json_written": True,
    }


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


@dataclass
class CommandResult:
    exit_code: int
    outcome: str
    message: str | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def report_status(self) -> str:
        return outcome_to_report_status(self.outcome)


def _read_captured_stream(handle: IO[bytes]) -> str:
    try:
        handle.flush()
        handle.seek(0)
        return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def execute_command(
    command: str | Sequence[str],
    timeout_sec: int,
    *,
    task_ref: str = "",
    task_id: str | None = None,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a command in its own process group and clean the whole tree on timeout."""
    if _SHUTTING_DOWN.is_set():
        return CommandResult(-1, "Error", "Execution cancelled during shutdown")
    stdout_handle = tempfile.TemporaryFile()
    stderr_handle = tempfile.TemporaryFile()
    try:
        popen_options: dict = {
            "stdout": stdout_handle,
            "stderr": stderr_handle,
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

        try:
            proc = subprocess.Popen(popen_target, **popen_options)
        except OSError as exc:
            return CommandResult(-1, "Error", f"Execution failed: {exc}")

        _register_active_process(proc)
        try:
            if _SHUTTING_DOWN.is_set():
                terminate_process_tree(proc, "soldier shutdown race", task_id=task_id)
                return CommandResult(
                    -1,
                    "Error",
                    "Execution cancelled during shutdown",
                    stdout=_read_captured_stream(stdout_handle),
                    stderr=_read_captured_stream(stderr_handle),
                )
            try:
                exit_code = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                terminate_process_tree(
                    proc,
                    f"task timeout: {task_ref or _command_label(command)[:80]}",
                    task_id=task_id,
                )
                return CommandResult(
                    -1,
                    "Fail",
                    f"Command timeout (>{timeout_sec}s); process tree terminated",
                    stdout=_read_captured_stream(stdout_handle),
                    stderr=_read_captured_stream(stderr_handle),
                )
            stdout_text = _read_captured_stream(stdout_handle)
            stderr_text = _read_captured_stream(stderr_handle)
            if exit_code == 0:
                return CommandResult(exit_code, "Success", None, stdout=stdout_text, stderr=stderr_text)
            return CommandResult(
                exit_code,
                "Fail",
                f"Command exit code {exit_code}",
                stdout=stdout_text,
                stderr=stderr_text,
            )
        finally:
            _unregister_active_process(proc)
    finally:
        stdout_handle.close()
        stderr_handle.close()


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
    payload = strip_report_streams(payload)
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


def decode_dispatch_payload(raw: bytes) -> tuple[dict | None, str | None]:
    if not raw.strip():
        return None, "Empty request"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "JSON parsing failed"
    if not isinstance(payload, dict):
        return None, "Request body must be a JSON object"
    return payload, None


def read_dispatch_payload(conn: socket.socket) -> tuple[dict | None, str | None]:
    try:
        raw = recv_one_line(conn)
    except ValueError as e:
        return None, str(e)
    return decode_dispatch_payload(raw)


def handle_dispatch_connection(
    conn: socket.socket,
    commander_host: str,
    commander_port: int,
    timeout_sec: int,
    payload: dict | None = None,
) -> None:
    logging.debug("Dispatch connection accepted")
    claim: ClaimResult | None = None
    try:
        conn.settimeout(timeout_sec + RUNNING_STALE_GRACE_SECONDS)
        if payload is None:
            incoming, error = read_dispatch_payload(conn)
            if error is not None or incoming is None:
                try:
                    conn.sendall(
                        (json.dumps({"ok": False, "error": error or "Invalid request"}, ensure_ascii=False) + "\n").encode(
                            "utf-8"
                        )
                    )
                except OSError as os_err:
                    logging.warning(f"Failed to send error response to dispatch connection: {os_err}")
                return
            payload = incoming
        if payload.get("type") == "llm_config":
            try:
                ack = apply_llm_config(payload)
            except (ValueError, FileNotFoundError, OSError) as exc:
                send_dispatch_response(conn, {"ok": False, "error": str(exc)})
                return
            send_dispatch_response(conn, ack)
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
        received_at = _now_iso()
        log_task(logging.INFO, "Received", task_id=task_id, to_console=True)
        log_task(logging.INFO, "Received at %s", received_at, task_id=task_id)
        claim = claim_task_execution(
            date_str,
            task_id,
            full_ref,
            format_opencode_command(prompt=prompt),
            received_at,
            timeout_sec + RUNNING_STALE_GRACE_SECONDS,
        )
        previous_state = claim.record
        if claim.status == "completed":
            previous_report = report_from_task_record(previous_state)
            if not isinstance(previous_report, dict):
                log_task(
                    logging.WARNING,
                    "Fail completed state has no saved report",
                    task_id=task_id,
                    to_console=True,
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
            prev_status = str(previous_report.get("status") or "")
            if prev_status == "successed":
                log_task(logging.INFO, "Success", task_id=task_id, to_console=True)
            else:
                log_task(
                    logging.WARNING,
                    "Fail replay previous report",
                    task_id=task_id,
                    to_console=True,
                )
            reported_at = _now_iso()
            log_task(logging.INFO, "Reported at %s", reported_at, task_id=task_id)
            _, serr = send_report(commander_host, commander_port, previous_report)
            if serr:
                try:
                    enqueue_pending_report(commander_host, commander_port, previous_report, serr)
                    log_task(logging.INFO, "Report: queued: %s", serr, task_id=task_id)
                except OSError as e:
                    log_task(logging.ERROR, "Error %s", e, task_id=task_id, to_console=True)
                    log_task(logging.INFO, "Report: send failed: %s", e, task_id=task_id)
            else:
                log_task(logging.INFO, "Report: ok", task_id=task_id)
            return
        if claim.status == "running":
            log_task(logging.INFO, "Duplicate already running; ignoring", task_id=task_id)
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
            log_task(
                logging.ERROR,
                "Error Claimed but acknowledgment failed; not executing",
                task_id=task_id,
                to_console=True,
            )
            if claim.handle is not None:
                claim.handle.abort()
            return

        argv = ["opencode", "run", *OPENCODE_RUN_FLAGS, prompt]
        started_at = _now_iso()
        command_line = format_opencode_command(argv, prompt)
        try:
            argv = build_opencode_argv(prompt)
            command_line = format_opencode_command(argv, prompt)
        except (FileNotFoundError, ValueError) as exc:
            log_task(logging.INFO, "Command: %s", command_line, task_id=task_id)
            log_task(logging.INFO, "Started at %s", started_at, task_id=task_id)
            result = CommandResult(-1, "Error", str(exc))
        else:
            log_task(logging.INFO, "Command: %s", command_line, task_id=task_id)
            log_task(logging.INFO, "Started at %s", started_at, task_id=task_id)
            if claim.handle is not None:
                claim.handle.record["argv"] = list(argv)
                claim.handle.record["task"] = prompt
                claim.handle.record["command"] = command_line
                try:
                    claim.handle.persist()
                except OSError:
                    pass
            result = execute_command(
                argv,
                timeout_sec,
                task_ref=full_ref,
                task_id=task_id,
                env=opencode_run_env(),
            )
        finished_at = _now_iso()
        log_task(logging.INFO, "Finished at %s", finished_at, task_id=task_id)
        log_task(logging.INFO, "Outcome: %s", result.outcome, task_id=task_id)
        _log_console_outcome(result, task_id)

        report = commander_report_payload(
            full_ref,
            result.report_status,
            result.exit_code,
            result.message,
        )

        try:
            if claim.handle is not None:
                claim.handle.record["argv"] = list(argv)
                claim.handle.record["task"] = prompt
                claim.handle.record["command"] = command_line
                claim.handle.complete(
                    report,
                    status=result.report_status,
                    exit_code=result.exit_code,
                    message=result.message,
                    outcome=result.outcome,
                    started_at=started_at,
                    finished_at=finished_at,
                    command=command_line,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
            else:
                append_task_execution_log(
                    task_id=task_id,
                    task_ref=full_ref,
                    date_str=date_str,
                    received_at=received_at,
                    command=command_line,
                    status=result.report_status,
                    exit_code=result.exit_code,
                    message=result.message,
                    argv=argv,
                    task=prompt,
                    outcome=result.outcome,
                    started_at=started_at,
                    finished_at=finished_at,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        except OSError as e:
            log_task(logging.ERROR, "Error %s", e, task_id=task_id, to_console=True)

        reported_at = _now_iso()
        log_task(logging.INFO, "Reported at %s", reported_at, task_id=task_id)
        _, serr = send_report(commander_host, commander_port, report)
        if claim.handle is not None:
            claim.handle.record["reported_at"] = reported_at
            try:
                claim.handle.persist()
            except OSError:
                pass
        if serr:
            try:
                enqueue_pending_report(commander_host, commander_port, report, serr)
                log_task(logging.INFO, "Report: queued: %s", serr, task_id=task_id)
            except OSError as e:
                log_task(logging.ERROR, "Error %s", e, task_id=task_id, to_console=True)
                log_task(logging.INFO, "Report: send failed: %s", e, task_id=task_id)
            return
        log_task(logging.INFO, "Report: ok", task_id=task_id)
    finally:
        if claim is not None and claim.handle is not None:
            claim.handle.close()
        conn.close()


def process_accepted_connection(
    conn: socket.socket,
    addr: tuple,
    commander_host: str,
    commander_port: int,
    timeout_sec: int,
    *,
    execution_slots: threading.BoundedSemaphore,
    executor: ThreadPoolExecutor,
    worker_threads: int,
    run_claimed: Callable[[socket.socket, dict], None],
) -> None:
    """Handle one accepted TCP connection. LLM config bypasses the execution slot."""
    conn.settimeout(timeout_sec + RUNNING_STALE_GRACE_SECONDS)
    payload, error = read_dispatch_payload(conn)
    if error is not None or payload is None:
        send_dispatch_response(conn, {"ok": False, "error": error or "Invalid request"})
        conn.close()
        return
    if payload.get("type") == "llm_config":
        handle_dispatch_connection(
            conn, commander_host, commander_port, timeout_sec, payload=payload
        )
        return
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
        return
    try:
        executor.submit(run_claimed, conn, payload)
    except Exception:
        execution_slots.release()
        conn.close()
        raise


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

        def run_claimed_connection(conn: socket.socket, payload: dict) -> None:
            try:
                handle_dispatch_connection(conn, sh, sp, timeout_sec, payload=payload)
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
            process_accepted_connection(
                conn,
                addr,
                sh,
                sp,
                timeout_sec,
                execution_slots=execution_slots,
                executor=executor,
                worker_threads=worker_threads,
                run_claimed=run_claimed_connection,
            )
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
    sub.add_parser(
        "replay-failed-reports",
        help="retry records in runtime/failed_reports.jsonl once",
    )
    build_p = sub.add_parser(
        "build",
        help="install this role's OpenCode skills and MCP into ~/.config/opencode",
    )
    build_p.add_argument("role", help="role name (hr, accountancy, manager, programmer, victim)")
    build_p.add_argument(
        "--test",
        action="store_true",
        help="after install, verify OpenCode load and run a representative prompt per skill and MCP",
    )

    args = parser.parse_args(argv)
    if args.cmd == "build":
        try:
            from soldier.host_build import run_build
        except ImportError:
            from host_build import run_build

        if args.test:
            return run_build(args.role, run_test=True)
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
