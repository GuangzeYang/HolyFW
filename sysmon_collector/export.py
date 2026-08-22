"""Export Sysmon and Security logon/auth events into dated evtx files."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Sequence
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Callable

from sysmon_collector.paths import (
    SECURITY_CHANNEL,
    SECURITY_LOGON_AUTH_EVENT_IDS,
    SYSMON_CHANNEL,
    evtx_path,
    security_evtx_path,
)

RunFn = Callable[..., subprocess.CompletedProcess]
SYSMON_EXPORT_TIMEOUT = 120
SECURITY_EXPORT_TIMEOUT = 300
_EMPTY_EXPORT_MARKERS = (
    "no events were found",
    "no event was found",
    "the specified query is invalid",
)


def wevtutil_path() -> str:
    found = shutil.which("wevtutil")
    return found if found else "wevtutil"


def _run_text(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    if os.name == "nt":
        kwargs.setdefault("encoding", "oem")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(args, **kwargs)


def day_query_window(day: date) -> tuple[str, str]:
    """UTC bounds for the local calendar day *day* (Windows Event Log timestamps)."""
    start_local = datetime.combine(day, dt_time.min).astimezone()
    end_local = datetime.combine(day + timedelta(days=1), dt_time.min).astimezone()
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    return (
        start_local.astimezone(timezone.utc).strftime(fmt),
        end_local.astimezone(timezone.utc).strftime(fmt),
    )


def build_export_query(day: date, event_ids: Sequence[int] | None = None) -> str:
    start, end = day_query_window(day)
    time_pred = f"TimeCreated[@SystemTime>='{start}' and @SystemTime<'{end}']"
    if not event_ids:
        return f"*[System[{time_pred}]]"
    id_pred = " or ".join(f"EventID={int(eid)}" for eid in event_ids)
    return f"*[System[({id_pred}) and {time_pred}]]"


def is_empty_export_result(returncode: int, stdout: str = "", stderr: str = "") -> bool:
    if returncode == 0:
        return False
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in _EMPTY_EXPORT_MARKERS)


def _replace_existing(out_file: Path) -> None:
    if not out_file.exists():
        return
    try:
        out_file.unlink()
    except OSError as exc:
        raise RuntimeError(f"cannot replace existing evtx {out_file}: {exc}") from exc


def export_channel(
    day: date,
    out_dir: Path,
    *,
    channel: str,
    out_file: Path,
    query: str,
    run_fn: RunFn | None = None,
    wevtutil: str | None = None,
    timeout: float = SYSMON_EXPORT_TIMEOUT,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    _replace_existing(out_file)
    tool = wevtutil or wevtutil_path()
    args = [tool, "epl", channel, str(out_file), f"/q:{query}"]
    runner = run_fn or _run_text
    result = runner(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        if is_empty_export_result(result.returncode, stdout, stderr):
            logging.info(
                "No matching %s events for %s; skipped %s",
                channel,
                day.isoformat(),
                out_file,
            )
            return out_file
        detail = (stderr or stdout or "wevtutil epl failed").strip()
        raise RuntimeError(detail)
    logging.info("Wrote %s", out_file)
    return out_file


def export_day(
    day: date,
    out_dir: Path,
    *,
    run_fn: RunFn | None = None,
    wevtutil: str | None = None,
    channel: str = SYSMON_CHANNEL,
    timeout: float = SYSMON_EXPORT_TIMEOUT,
) -> Path:
    out_file = evtx_path(out_dir, day)
    return export_channel(
        day,
        out_dir,
        channel=channel,
        out_file=out_file,
        query=build_export_query(day),
        run_fn=run_fn,
        wevtutil=wevtutil,
        timeout=timeout,
    )


def export_security_logon_day(
    day: date,
    out_dir: Path,
    *,
    run_fn: RunFn | None = None,
    wevtutil: str | None = None,
    channel: str = SECURITY_CHANNEL,
    event_ids: Sequence[int] = SECURITY_LOGON_AUTH_EVENT_IDS,
    timeout: float = SECURITY_EXPORT_TIMEOUT,
) -> Path:
    out_file = security_evtx_path(out_dir, day)
    return export_channel(
        day,
        out_dir,
        channel=channel,
        out_file=out_file,
        query=build_export_query(day, event_ids),
        run_fn=run_fn,
        wevtutil=wevtutil,
        timeout=timeout,
    )


def export_collected_logs(
    day: date,
    out_dir: Path,
    *,
    run_fn: RunFn | None = None,
    wevtutil: str | None = None,
) -> list[Path]:
    """Export Sysmon and Security logon/auth evtx files for *day*."""
    written: list[Path] = []
    errors: list[Exception] = []
    for fn, timeout in (
        (export_day, SYSMON_EXPORT_TIMEOUT),
        (export_security_logon_day, SECURITY_EXPORT_TIMEOUT),
    ):
        try:
            written.append(
                fn(day, out_dir, run_fn=run_fn, wevtutil=wevtutil, timeout=timeout)
            )
        except Exception as exc:
            errors.append(exc)
            logging.error("Failed bundled export: %s", exc)
    if errors and not written:
        raise errors[0]
    return written
