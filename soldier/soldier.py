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
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
import logging

try:
    import colorlog
except ImportError:
    colorlog = None

from common import (
    clean_old_files,
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
SUBPROCESS_TIMEOUT_DEFAULT = 3600
DEFAULT_WORKER_THREADS = 6
MAX_LINE_BYTES = 65536
MAX_COMMAND_OUTPUT_BYTES = 65536
OUTPUT_DIR_NAME = "output"
TASK_STATE_FILE_PREFIX = "task_state"
PENDING_REPORTS_FILE_NAME = "pending_reports.jsonl"
FAILED_REPORTS_FILE_NAME = "failed_reports.jsonl"
REPORT_RETRY_LIMIT = 3
REPORT_RETRY_INTERVAL_SECONDS = 60
SOLDIER_DATED_FILE_HANDLER_NAME = "soldier_dated_file"
LOG_DATE_CHECK_INTERVAL_SECONDS = 1.0
_PENDING_REPORTS_LOCK = threading.Lock()
_TASK_STATE_LOCK = threading.Lock()


def _plain_log_formatter() -> logging.Formatter:
    return logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    plain_formatter = _plain_log_formatter()
    if colorlog is not None:
        color_formatter = colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red',
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








def save_task_record(
    task_id: str,
    date_str: str,
    content: dict,
    received_at: str,
    stdout: str,
    stderr: str,
) -> None:
    """Append received task details to daily JSONL under soldier script directory."""
    script_dir = Path(__file__).resolve().parent
    
    month_day = date_str[5:] if len(date_str) >= 10 else date_str
    file_name = f"received_task_{month_day}.jsonl"
    file_path = script_dir / file_name

    record = {
        "task_id": task_id,
        "received_at": received_at,
        "content": content,
        "stdout": stdout,
        "stderr": stderr,
    }
    
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    
    clean_old_files(script_dir, "received_task_*.jsonl", days=20)


def _safe_received_at_for_filename(received_at: str) -> str:
    try:
        dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%S")


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
    script_dir = Path(__file__).resolve().parent
    output_dir = script_dir / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{_safe_received_at_for_filename(received_at)}_{task_id}.txt"
    file_path = output_dir / file_name

    content = (
        f"task_ref: {task_ref}\n"
        f"task_id: {task_id}\n"
        f"received_at: {received_at}\n"
        f"status: {status}\n"
        f"exit_code: {exit_code}\n"
        f"command: {command}\n"
        "\n"
        "===== STDOUT =====\n"
        f"{stdout_text}\n"
        "\n"
        "===== STDERR =====\n"
        f"{stderr_text}\n"
    )

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    clean_old_files(output_dir, "*.txt", days=20)
    return file_path


def task_state_path(date_str: str, base_dir: Path | None = None) -> Path:
    root = base_dir or Path(__file__).resolve().parent
    month_day = date_str[5:] if len(date_str) >= 10 else date_str
    return root / f"{TASK_STATE_FILE_PREFIX}_{month_day}.jsonl"


def _load_task_state_records(path: Path) -> list[dict]:
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
                logging.warning(f"Skipping invalid task state line in {path}")
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def _latest_task_state(records: list[dict], task_id: str) -> dict | None:
    for record in reversed(records):
        if record.get("task_id") == task_id:
            return record
    return None


def _is_running_state_stale(record: dict, stale_after_seconds: int) -> bool:
    updated_at = record.get("updated_at") or record.get("received_at")
    if not isinstance(updated_at, str):
        return False
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() > stale_after_seconds


def claim_task_execution(
    date_str: str,
    task_id: str,
    task_ref: str,
    command: str,
    received_at: str,
    stale_after_seconds: int,
    *,
    base_dir: Path | None = None,
) -> tuple[str, dict | None]:
    """Atomically decide whether a task should execute, replay, or be ignored."""
    path = task_state_path(date_str, base_dir)
    now = datetime.now(timezone.utc).isoformat()
    with _TASK_STATE_LOCK:
        latest = _latest_task_state(_load_task_state_records(path), task_id)
        if latest is not None:
            status = latest.get("status")
            if status == "completed":
                return "completed", latest
            if status == "running" and not _is_running_state_stale(latest, stale_after_seconds):
                return "running", latest
            if status == "running":
                logging.warning(f"Task {task_ref} has stale running state; allowing re-execution")

        record = {
            "task_id": task_id,
            "task_ref": task_ref,
            "status": "running",
            "received_at": received_at,
            "updated_at": now,
            "command": command,
        }
        _append_jsonl(path, record)
        return "claimed", record


def mark_task_completed(
    date_str: str,
    task_id: str,
    task_ref: str,
    command: str,
    received_at: str,
    report: dict,
    output_file: Path | None,
    *,
    base_dir: Path | None = None,
) -> None:
    record = {
        "task_id": task_id,
        "task_ref": task_ref,
        "status": "completed",
        "received_at": received_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "report": report,
        "output_file": str(output_file) if output_file is not None else "",
    }
    with _TASK_STATE_LOCK:
        _append_jsonl(task_state_path(date_str, base_dir), record)


def pending_reports_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path(__file__).resolve().parent
    output_dir = root / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / PENDING_REPORTS_FILE_NAME


def failed_reports_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path(__file__).resolve().parent
    output_dir = root / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / FAILED_REPORTS_FILE_NAME


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


def execute_command(
    command: str,
    timeout_sec: int,
    max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES,
) -> tuple[str, str, int, str, str | None]:
    """Execute a shell command while spooling stdout/stderr to temp files."""
    with tempfile.TemporaryFile() as stdout_tmp, tempfile.TemporaryFile() as stderr_tmp:
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=stdout_tmp,
                stderr=stderr_tmp,
            )
            try:
                exit_code = proc.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                out, out_truncated = _read_temp_output_limited(stdout_tmp, max_output_bytes)
                err_out, err_truncated = _read_temp_output_limited(stderr_tmp, max_output_bytes)
                if not err_out:
                    err_out = "timeout"
                msg = f"Command timeout (>{timeout_sec}s)"
                if out_truncated or err_truncated:
                    msg += "; output truncated"
                return out, err_out, -1, "failed", msg
        except OSError as e:
            return "", str(e), -1, "failed", f"Execution failed: {e}"

        out, out_truncated = _read_temp_output_limited(stdout_tmp, max_output_bytes)
        err_out, err_truncated = _read_temp_output_limited(stderr_tmp, max_output_bytes)
        ok_run = exit_code == 0
        status = "successed" if ok_run else "failed"
        msg = None if ok_run else f"Command exit code {exit_code}"
        if out_truncated or err_truncated:
            msg = f"{msg}; output truncated" if msg else "output truncated"
        return out, err_out, exit_code, status, msg








def task_ref_full(date_str: str, role: str, task_id: str) -> str:
    return f"{date_str}_{role}_{task_id}"


def default_config_path() -> Path:
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
        with socket.create_connection((commander_host, commander_port), timeout=60) as sock:
            sock.settimeout(60)
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


def handle_dispatch_connection(
    conn: socket.socket,
    commander_host: str,
    commander_port: int,
    timeout_sec: int,
) -> None:
    logging.info("Dispatch connection accepted")
    try:
        conn.settimeout(timeout_sec + 120)
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
        command = payload.get("command")
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
        if not isinstance(command, str) or not command.strip():
            try:
                conn.sendall(
                    (
                        json.dumps({"ok": False, "error": "Missing or invalid command"}, ensure_ascii=False)
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
        received_at = datetime.now(timezone.utc).isoformat()
        received_content = {
            "task_ref": task_ref,
            "task_date": date_str,
            "command": command,
            "payload": payload,
        }
        claim_status, previous_state = claim_task_execution(
            date_str,
            task_id,
            full_ref,
            command,
            received_at,
            timeout_sec + 120,
        )
        if claim_status == "completed":
            previous_report = previous_state.get("report") if isinstance(previous_state, dict) else None
            if not isinstance(previous_report, dict):
                logging.warning(f"Duplicate task {full_ref} has completed state without report; ignoring")
                return
            logging.info(f"Duplicate task {full_ref} already completed; replaying saved report")
            _, serr = send_report(commander_host, commander_port, previous_report)
            if serr:
                logging.error(f"Failed to replay completed task {full_ref} to commander: {serr}")
                try:
                    enqueue_pending_report(commander_host, commander_port, previous_report, serr)
                except OSError as e:
                    logging.error(f"Failed to queue replay report for task {full_ref}: {e}")
            return
        if claim_status == "running":
            logging.info(f"Duplicate task {full_ref} is already running; ignoring")
            return

        out, err_out, exit_code, status, msg = execute_command(command, timeout_sec)

        try:
            save_task_record(
                task_id,
                date_str,
                received_content,
                received_at,
                out,
                err_out,
            )
        except OSError as e:
            logging.error(f"Failed to save received task record for task {full_ref}: {e}")

        report = {
            "task_ref": full_ref,
            "status": status,
            "exit_code": exit_code,
            "stdout": out,
            "stderr": err_out,
        }
        if msg is not None:
            report["message"] = msg

        output_file: Path | None = None
        try:
            output_file = save_command_output(
                task_id=task_id,
                received_at=received_at,
                task_ref=full_ref,
                command=command,
                status=status,
                exit_code=exit_code,
                stdout_text=out,
                stderr_text=err_out,
            )
            logging.info(f"Command output saved to {output_file}")
        except OSError as e:
            logging.error(f"Failed to save command output for task {full_ref}: {e}")

        mark_task_completed(
            date_str,
            task_id,
            full_ref,
            command,
            received_at,
            report,
            output_file,
        )
        logging.info(f"Task {full_ref} executed, status={status}, exit_code={exit_code}")
        _, serr = send_report(commander_host, commander_port, report)
        if serr:
            logging.error(f"Failed to report task {full_ref} to commander: {serr}")
            try:
                enqueue_pending_report(commander_host, commander_port, report, serr)
                logging.info(f"Task {full_ref} queued for commander report retry")
            except OSError as e:
                logging.error(f"Failed to queue report retry for task {full_ref}: {e}")
            return
        logging.info(f"Task {full_ref} reported successfully to commander")
    finally:
        conn.close()


def run_listen(
    config_path: Path,
    bind: str | None,
    port: int | None,
    commander_host: str | None,
    commander_port: int | None,
) -> None:
    b, lp = resolve_listen(bind, port, config_path)
    sh, sp = resolve_endpoint(commander_host, commander_port, config_path)
    to = load_exec_timeout(config_path)
    timeout_sec = to if to is not None and to > 0 else SUBPROCESS_TIMEOUT_DEFAULT
    worker_threads = load_worker_threads(config_path)
    script_dir = Path(__file__).resolve().parent

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((b, lp))
    sock.listen(32)
    sock.settimeout(LOG_DATE_CHECK_INTERVAL_SECONDS)
    logging.info(
        f"Listening for tasks on {b}:{lp}; reporting to commander {sh}:{sp}; "
        f"exec timeout={timeout_sec}s; worker_threads={worker_threads}"
    )
    logs_dir = script_dir / "logs"
    current_log_day = date.today()
    start_report_retry_thread(script_dir)
    try:
        executor = ThreadPoolExecutor(max_workers=worker_threads)
        while True:
            new_log_day = date.today()
            if new_log_day != current_log_day:
                log_file = reattach_soldier_dated_file_handler(logs_dir, logging.INFO, target_day=new_log_day)
                current_log_day = new_log_day
                logging.info(f"Soldier log switched to {log_file}")
            try:
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            executor.submit(handle_dispatch_connection, conn, sh, sp, timeout_sec)
    finally:
        if "executor" in locals():
            executor.shutdown(wait=False, cancel_futures=True)
        sock.close()


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


def main() -> int:
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

    report_p = sub.add_parser("report", help="manually report a receipt to commander")
    report_p.add_argument("--host", default=None, metavar="ADDR", help="commander address")
    report_p.add_argument("--port", type=int, default=None, metavar="N", help="commander port")
    report_p.add_argument("--task-ref", required=True, help="date_role_taskId (YYYY-MM-DD or MM-DD)")
    report_p.add_argument("--status", required=True, choices=["successed", "failed"])
    report_p.add_argument("--message", default=None)
    report_p.add_argument("--exit-code", type=int, default=None, dest="exit_code")
    report_p.add_argument("--stdout", default=None, help="optional output text")
    report_p.add_argument("--stderr", default=None)

    args = parser.parse_args()
    
    script_dir = Path(__file__).resolve().parent
    logs_dir = Path(__file__).resolve().parent / "logs"
    output_dir = script_dir / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = configure_soldier_root_logging(logs_dir, logging.INFO)
    
    logging.info(f"Soldier starting, logs: {log_file}")
    logging.info(f"Command output directory: {output_dir}")
    
    cfg = args.config if getattr(args, "config", None) is not None else default_config_path()

    if args.cmd is None or args.cmd == "listen":
        run_listen(
            cfg,
            getattr(args, "bind", None),
            getattr(args, "listen_port", None),
            getattr(args, "commander_host", None),
            getattr(args, "commander_port", None),
        )
        return 0
    if args.cmd == "report":
        return run_report(args, cfg)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
