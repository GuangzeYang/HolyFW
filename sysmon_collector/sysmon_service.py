"""Query, stop, and start the local Sysmon service."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

SERVICE_NAMES = ("Sysmon64", "Sysmon")
_STATE_RE = re.compile(r"STATE\s*:\s*\d+\s+(\w+)", re.IGNORECASE)
RunFn = Callable[..., subprocess.CompletedProcess]


def _default_run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    if os.name == "nt":
        kwargs.setdefault("encoding", "oem")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(args, **kwargs)


def parse_sc_query(stdout: str, returncode: int) -> str | None:
    """Return RUNNING/STOPPED/UNKNOWN, or None when the service does not exist."""
    if returncode != 0:
        return None
    match = _STATE_RE.search(stdout or "")
    if not match:
        return "UNKNOWN"
    return match.group(1).upper()


def query_service_state(name: str, *, run_fn: RunFn = _default_run) -> str | None:
    result = run_fn(["sc", "query", name], timeout=30)
    return parse_sc_query(result.stdout or "", result.returncode)


def query_sysmon_service(*, run_fn: RunFn = _default_run) -> tuple[str | None, str | None]:
    for name in SERVICE_NAMES:
        state = query_service_state(name, run_fn=run_fn)
        if state is not None:
            return name, state
    return None, None


def _sc_output(result: subprocess.CompletedProcess) -> str:
    return f"{result.stdout or ''}\n{result.stderr or ''}"


def stop_sysmon_service(
    name: str,
    *,
    run_fn: RunFn = _default_run,
    query_fn: Callable[[], str | None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    timeout: float = 30.0,
) -> None:
    result = run_fn(["sc", "stop", name], timeout=60)
    text = _sc_output(result)
    # 1062: service has not been started
    if result.returncode != 0 and "1062" not in text:
        lowered = text.lower()
        if "access is denied" in lowered or "拒绝访问" in text or "denied" in lowered:
            raise RuntimeError(f"Access denied stopping {name}: {text.strip()}")
    probe = query_fn or (lambda: query_service_state(name, run_fn=run_fn))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = probe()
        if state in {"STOPPED", None}:
            return
        sleep_fn(0.2)
    raise TimeoutError(f"Timed out waiting for {name} to stop")


def apply_sysmon_config(exe: Path, config: Path, *, run_fn: RunFn = _default_run) -> None:
    result = run_fn(
        [str(exe), "-accepteula", "-c", str(config)],
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "sysmon -c failed").strip()
        raise RuntimeError(detail)


def install_sysmon(exe: Path, config: Path, *, run_fn: RunFn = _default_run) -> None:
    result = run_fn(
        [str(exe), "-accepteula", "-i", str(config)],
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "sysmon -i failed").strip()
        raise RuntimeError(detail)


def start_sysmon_service(name: str, *, run_fn: RunFn = _default_run) -> None:
    result = run_fn(["sc", "start", name], timeout=60)
    if result.returncode != 0:
        text = _sc_output(result)
        # 1056: already running
        if "1056" in text:
            return
        detail = text.strip() or "sc start failed"
        raise RuntimeError(detail)


def restart_sysmon(
    exe: Path,
    config: Path,
    *,
    run_fn: RunFn = _default_run,
    query_fn: Callable[[], tuple[str | None, str | None]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    timeout: float = 30.0,
) -> dict[str, str | bool | None]:
    """Stop Sysmon if running, start it again, then apply config while it is up.

    If config apply fails after a stop, still attempt ``sc start`` so the host
    is not left with Sysmon down.
    """
    probe = query_fn or (lambda: query_sysmon_service(run_fn=run_fn))
    name, state = probe()
    was_running = state == "RUNNING"
    action = "config+start"

    try:
        if was_running:
            assert name is not None
            logging.info("Sysmon service %s is running; stopping it", name)
            stop_sysmon_service(
                name,
                run_fn=run_fn,
                query_fn=lambda: probe()[1],
                sleep_fn=sleep_fn,
                timeout=timeout,
            )

        if name is None:
            logging.info("Sysmon service not installed; installing with %s", config)
            install_sysmon(exe, config, run_fn=run_fn)
            action = "install"
            name, _ = probe()
        else:
            logging.info("Starting Sysmon service %s", name)
            start_sysmon_service(name, run_fn=run_fn)
            logging.info("Applying Sysmon config %s", config)
            apply_sysmon_config(exe, config, run_fn=run_fn)
    except Exception:
        if name is not None:
            try:
                logging.error("Sysmon restart failed; ensuring service %s is started", name)
                start_sysmon_service(name, run_fn=run_fn)
            except Exception as start_exc:
                logging.error("Failed to start Sysmon after error: %s", start_exc)
        raise

    return {"service": name, "was_running": was_running, "action": action}
