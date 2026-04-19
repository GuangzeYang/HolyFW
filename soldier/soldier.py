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
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
import logging
import logging.handlers

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
MAX_LINE_BYTES = 65536
OUTPUT_DIR_NAME = "output"
LOG_BACKUP_COUNT = 7










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
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=timeout_sec,
            )
            out = ""
            if proc.stdout:
                if isinstance(proc.stdout, bytes):
                    out = proc.stdout.decode('utf-8', errors='replace')
                else:
                    out = str(proc.stdout)
            err_out = ""
            if proc.stderr:
                if isinstance(proc.stderr, bytes):
                    err_out = proc.stderr.decode('utf-8', errors='replace')
                else:
                    err_out = str(proc.stderr)
            exit_code = proc.returncode
            ok_run = exit_code == 0
            status = "successed" if ok_run else "failed"
            msg = None if ok_run else f"Command exit code {exit_code}"
        except subprocess.TimeoutExpired as e:
            out = ""
            if e.stdout:
                if isinstance(e.stdout, bytes):
                    out = e.stdout.decode('utf-8', errors='replace')
                else:
                    out = str(e.stdout)
            err_out = "timeout"
            if e.stderr:
                if isinstance(e.stderr, bytes):
                    err_out = e.stderr.decode('utf-8', errors='replace')
                else:
                    err_out = str(e.stderr)
            exit_code = -1
            status = "failed"
            msg = f"Command timeout (>{timeout_sec}s)"
        except OSError as e:
            out = ""
            err_out = str(e)
            exit_code = -1
            status = "failed"
            msg = f"Execution failed: {e}"

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

        logging.info(f"Task {full_ref} executed, status={status}, exit_code={exit_code}")
        sresp, serr = send_report(commander_host, commander_port, report)
        if serr:
            logging.error(f"Failed to report task {full_ref} to commander: {serr}")
            try:
                conn.sendall(
                    (
                        json.dumps(
                            {"ok": False, "error": serr, "local": "Executed but failed to report to client"},
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
            except OSError as e:
                logging.warning(f"Failed to send error response to dispatch connection: {e}")
            return
        try:
            conn.sendall(
                (
                    json.dumps({"ok": True, "client": sresp}, ensure_ascii=False) + "\n"
                ).encode("utf-8")
            )
        except OSError as e:
            logging.warning(f"Failed to send success response to dispatch connection: {e}")
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

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((b, lp))
    sock.listen(32)
    logging.info(f"Listening for tasks on {b}:{lp}; reporting to commander {sh}:{sp}; exec timeout={timeout_sec}s")
    try:
        while True:
            conn, addr = sock.accept()
            t = threading.Thread(
                target=handle_dispatch_connection,
                args=(conn, sh, sp, timeout_sec),
                daemon=True,
            )
            t.start()
    finally:
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
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_dir = script_dir / OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"soldier_{date.today().isoformat()}.log"
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file, when='midnight', interval=1, backupCount=LOG_BACKUP_COUNT, encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    
    plain_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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
    file_handler.setFormatter(plain_formatter)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
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
