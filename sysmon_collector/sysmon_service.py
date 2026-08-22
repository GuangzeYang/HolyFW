"""Query the local Sysmon service without stopping or restarting it."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from typing import Callable

SERVICE_NAMES = ("Sysmon64", "Sysmon")
_STATE_RE = re.compile(r"STATE\s*:\s*\d+\s+(\w+)", re.IGNORECASE)
RunFn = Callable[..., subprocess.CompletedProcess]
NowFn = Callable[[], datetime]


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


def observe_sysmon(
    *,
    run_fn: RunFn = _default_run,
    query_fn: Callable[[], tuple[str | None, str | None]] | None = None,
    now_fn: NowFn | None = None,
) -> dict[str, str | bool | datetime | None]:
    """Record whether Sysmon is running. Does not stop, start, or reconfigure it."""
    probe = query_fn or (lambda: query_sysmon_service(run_fn=run_fn))
    stamp_fn = now_fn or datetime.now
    name, state = probe()
    observed_at = stamp_fn()
    return {
        "service": name,
        "state": state,
        "running": state == "RUNNING",
        "observed_at": observed_at,
    }
