"""Long-running Sysmon collector: restart the service and export daily evtx files."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Callable

from sysmon_collector.elevate import is_elevated, start_collector_privileged
from sysmon_collector.export import export_day, export_security_logon_day
from sysmon_collector.paths import (
    collector_log_path,
    collector_logs_dir,
    evtx_dir,
    pid_file,
    resolve_sysmon_config,
    resolve_sysmon_exe,
)
from sysmon_collector.sysmon_service import restart_sysmon

COLLECTOR_HANDLER_NAME = "sysmon_collector_dated_file"
COLLECTOR_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
DATE_CHECK_INTERVAL_SECONDS = 1.0
INSTANCE_KILL_TIMEOUT_SECONDS = 30


def close_collector_logging(logger: logging.Logger | None = None) -> None:
    root = logger or logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == COLLECTOR_HANDLER_NAME:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass


def configure_collector_logging(
    logs_dir: Path,
    *,
    target_day: date | None = None,
    logger: logging.Logger | None = None,
) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = target_day or date.today()
    log_file = collector_log_path(logs_dir, day)
    root = logger or logging.getLogger()
    root.setLevel(logging.INFO)

    has_console = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in root.handlers
    )
    if not has_console:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(COLLECTOR_LOG_FORMAT))
        root.addHandler(console)

    for handler in list(root.handlers):
        if getattr(handler, "name", None) == COLLECTOR_HANDLER_NAME:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(COLLECTOR_LOG_FORMAT))
    file_handler.name = COLLECTOR_HANDLER_NAME
    root.addHandler(file_handler)
    return log_file


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
        pid = int(text)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def pid_is_running(pid: int, *, kill_fn: Callable[[int, int], None] | None = None) -> bool:
    if pid <= 0:
        return False
    probe = kill_fn or os.kill
    try:
        probe(pid, 0)
    except OSError:
        return False
    return True


def terminate_pid(
    pid: int,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    kill_fn: Callable[[int, int], None] = os.kill,
) -> None:
    if os.name == "nt":
        run_fn(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=INSTANCE_KILL_TIMEOUT_SECONDS,
        )
        return
    try:
        kill_fn(pid, signal.SIGTERM)
    except OSError:
        pass


def read_process_command_line(
    pid: int,
    *,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        result = run_fn(
            [
                "wmic",
                "process",
                "where",
                f"ProcessId={pid}",
                "get",
                "CommandLine",
                "/VALUE",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="oem",
            errors="replace",
        )
        for line in (result.stdout or "").splitlines():
            if line.lower().startswith("commandline="):
                return line.split("=", 1)[1].strip()
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def pid_looks_like_collector(
    pid: int,
    *,
    cmdline_fn: Callable[[int], str | None] | None = None,
) -> bool:
    reader = cmdline_fn or read_process_command_line
    cmdline = reader(pid) or ""
    return "sysmon_collector" in cmdline.lower()


def acquire_instance(
    pid_path: Path,
    current_pid: int,
    *,
    running_fn: Callable[[int], bool] | None = None,
    terminate_fn: Callable[[int], None] | None = None,
    identity_fn: Callable[[int], bool] | None = None,
) -> int | None:
    """Replace a live previous collector, then write *current_pid*. Return the old pid if killed."""
    old = read_pid(pid_path)
    replaced: int | None = None
    is_running = running_fn or pid_is_running
    killer = terminate_fn or terminate_pid
    is_ours = identity_fn or pid_looks_like_collector
    if old is not None and old != current_pid and is_running(old):
        if not is_ours(old):
            logging.warning(
                "pid file %s is live but is not a sysmon collector; leaving it alone",
                old,
            )
        else:
            logging.info("Replacing existing sysmon collector pid=%s", old)
            killer(old)
            replaced = old
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(current_pid), encoding="utf-8")
    return replaced


def release_instance(pid_path: Path, current_pid: int) -> None:
    if read_pid(pid_path) == current_pid:
        try:
            pid_path.unlink()
        except OSError:
            pass


def run_collection_loop(
    *,
    export_day_fn: Callable[[date], None],
    today_fn: Callable[[], date] = date.today,
    shutdown_event: threading.Event,
    interval: float = DATE_CHECK_INTERVAL_SECONDS,
    wait_fn: Callable[[float], bool] | None = None,
    on_day_change: Callable[[date], None] | None = None,
) -> None:
    """Export yesterday when the calendar day changes; export today on shutdown."""
    waiter = wait_fn or shutdown_event.wait
    current_day = today_fn()
    while not waiter(interval):
        new_day = today_fn()
        if new_day != current_day:
            export_day_fn(current_day)
            if on_day_change is not None:
                on_day_change(new_day)
            current_day = new_day
    export_day_fn(today_fn())


def _safe_export(
    day: date,
    out_dir: Path,
    export_fn: Callable[..., Path],
    *,
    label: str,
) -> None:
    try:
        path = export_fn(day, out_dir)
        logging.info("Exported %s events for %s to %s", label, day.isoformat(), path)
    except Exception as exc:
        logging.error("Failed to export %s events for %s: %s", label, day.isoformat(), exc)


def _safe_export_day(
    day: date,
    out_dir: Path,
    export_fn: Callable[..., Path] = export_day,
    security_export_fn: Callable[..., Path] = export_security_logon_day,
) -> None:
    _safe_export(day, out_dir, export_fn, label="Sysmon")
    _safe_export(day, out_dir, security_export_fn, label="Security logon/auth")


def _install_signal_handlers(event: threading.Event) -> None:
    def _stop(signum: int, frame: object) -> None:
        event.set()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)
    if os.name == "nt" and hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)


def bootstrap_and_run(
    *,
    base_dir: Path | None = None,
    current_pid: int | None = None,
    restart_fn: Callable[..., object] = restart_sysmon,
    export_fn: Callable[..., Path] = export_day,
    security_export_fn: Callable[..., Path] = export_security_logon_day,
    loop_fn: Callable[..., None] = run_collection_loop,
    shutdown_event: threading.Event | None = None,
    interval: float = DATE_CHECK_INTERVAL_SECONDS,
    install_signals: bool = True,
) -> int:
    try:
        logs_dir = collector_logs_dir(base_dir)
        out_dir = evtx_dir(base_dir)
    except FileNotFoundError:
        raise
    log_file = configure_collector_logging(logs_dir)
    logging.info("Sysmon collector starting, logs: %s", log_file)
    logging.info("EVTX output directory: %s", out_dir)

    pid_path = pid_file(base_dir)
    pid = current_pid if current_pid is not None else os.getpid()
    acquire_instance(pid_path, pid)
    event = shutdown_event or threading.Event()
    if install_signals:
        _install_signal_handlers(event)

    def do_export(day: date) -> None:
        _safe_export_day(day, out_dir, export_fn, security_export_fn)

    def switch_log(new_day: date) -> None:
        switched = configure_collector_logging(logs_dir, target_day=new_day)
        logging.info("Collector log switched to %s", switched)

    try:
        try:
            exe = resolve_sysmon_exe()
            config = resolve_sysmon_config()
            logging.info("Sysmon exe: %s", exe)
            logging.info("Sysmon config: %s", config)
            restart_fn(exe, config)
        except Exception as exc:
            logging.error(
                "Sysmon setup/restart failed; continuing Security/Sysmon export: %s",
                exc,
            )
        loop_fn(
            export_day_fn=do_export,
            shutdown_event=event,
            interval=interval,
            on_day_change=switch_log,
        )
    finally:
        release_instance(pid_path, pid)
        close_collector_logging()
    return 0


def _launch_privileged_collector() -> int:
    """Register a Highest-privilege scheduled task and exit. No UAC prompt."""
    cwd: str | None
    try:
        from common import locate_holyfw_root

        cwd = str(locate_holyfw_root(package_hint=Path(__file__).resolve().parent))
    except FileNotFoundError:
        cwd = os.getcwd() or None
    wrapper = pid_file().parent / "sysmon_collector.cmd"
    log_path = collector_logs_dir() / "sysmon_collector_spawn.log"
    identity = start_collector_privileged(
        python=sys.executable,
        env=os.environ.copy(),
        cwd=cwd,
        wrapper_path=wrapper,
        log_path=log_path,
    )
    logging.info("Started Sysmon collector as %s via scheduled task; exiting unelevated process", identity)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect local Sysmon and Security logon/auth logs into daily evtx files",
    )
    parser.add_argument(
        "--skip-elevate",
        action="store_true",
        help="do not start a privileged scheduled task (used by tests)",
    )
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("Sysmon collector requires Windows", file=sys.stderr)
        return 1
    if not args.skip_elevate and not is_elevated():
        try:
            return _launch_privileged_collector()
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            print(exc, file=sys.stderr)
            return 1
    try:
        return bootstrap_and_run()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    except Exception as exc:
        logging.exception("Sysmon collector failed: %s", exc)
        return 1
