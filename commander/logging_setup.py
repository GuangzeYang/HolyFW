#!/usr/bin/env python3
"""Shared logging bootstrap helpers for commander scripts."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import colorlog
except ImportError:
    colorlog = None

# Root FileHandler for commander main process; swapped on calendar day by periodic hook.
COMMANDER_DATED_FILE_HANDLER_NAME = "commander_dated_file"
COMMANDER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(role)s - %(message)s"


class _RoleDefaultFilter(logging.Filter):
    """Ensure every record has a ``role`` attribute for the commander formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "role") or not record.role:
            record.role = "system"
        return True


def role_label(role: str, index: int | None = None) -> str:
    """Build ``accountancy[1]``-style role label used in commander logs."""
    name = (role or "system").strip() or "system"
    if index is None:
        return name
    return f"{name}[{index}]"


def log_extra(role: str, index: int | None = None) -> dict[str, str]:
    """``extra`` dict for logging calls that need a role label."""
    return {"role": role_label(role, index)}


def _resolve_log_level(level_name: str) -> int:
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid logging level: {level_name}")
    return level


def _plain_log_formatter() -> logging.Formatter:
    return logging.Formatter(COMMANDER_LOG_FORMAT)


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.addFilter(_RoleDefaultFilter())
    plain_formatter = _plain_log_formatter()
    if colorlog is not None:
        color_formatter = colorlog.ColoredFormatter(
            "%(log_color)s" + COMMANDER_LOG_FORMAT,
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


def configure_commander_root_logging(logs_dir: Path, level_name: str) -> Path:
    """Configure root logger for commander: console plus one dated file (today only).

    Calendar rollover uses :func:`reattach_commander_dated_file_handler` from the
    commander periodic hook, not ``TimedRotatingFileHandler``.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    level = _resolve_log_level(level_name)
    log_file = logs_dir / f"commander_{date.today().isoformat()}.log"

    logger = logging.getLogger()
    logger.setLevel(level)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    plain_formatter = _plain_log_formatter()
    console_handler = _build_console_handler(level)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.addFilter(_RoleDefaultFilter())
    file_handler.setFormatter(plain_formatter)
    file_handler.name = COMMANDER_DATED_FILE_HANDLER_NAME

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return log_file


def reattach_commander_dated_file_handler(
    logs_dir: Path,
    level_name: str,
    *,
    target_day: date | None = None,
    logger: logging.Logger | None = None,
    encoding: str = "utf-8",
) -> Path:
    """Replace the commander dated FileHandler on *logger* (default: root) for *target_day*.

    Removes only the handler named :data:`COMMANDER_DATED_FILE_HANDLER_NAME`;
    leaves other handlers (e.g. console) unchanged.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    day = target_day or date.today()
    level = _resolve_log_level(level_name)
    log_file = logs_dir / f"commander_{day.isoformat()}.log"

    root = logger or logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "name", None) == COMMANDER_DATED_FILE_HANDLER_NAME:
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    plain_formatter = _plain_log_formatter()
    file_handler = logging.FileHandler(log_file, encoding=encoding)
    file_handler.setLevel(level)
    file_handler.addFilter(_RoleDefaultFilter())
    file_handler.setFormatter(plain_formatter)
    file_handler.name = COMMANDER_DATED_FILE_HANDLER_NAME
    root.addHandler(file_handler)
    return log_file


def configure_subprocess_logging(level_name: str = "WARNING") -> None:
    """Minimal stderr logging for one-shot CLI helpers (e.g. dispatch.py).

    Does not create ``dispatch_*.log`` files; commander owns the dated file log.
    """
    level = _resolve_log_level(level_name)
    logger = logging.getLogger()
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    console_handler = _build_console_handler(level)
    logger.addHandler(console_handler)


def _normalize_log_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _safe_log_name(value: str) -> str:
    cleaned = []
    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            cleaned.append(char)
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "log"


def write_interactive_log(
    logs_dir: Path,
    role: str,
    attempt: int,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    status_code: int | None = None,
    finish_reason: str | None = None,
    response_text: str | bytes | None = None,
    raw_response_text: str | bytes | None = None,
    error_text: str | bytes | None = None,
    request_state: str | None = None,
    caller: str | None = None,
) -> Path:
    """Write one finished AI interaction for a single role into an interactive log file.

    Filename prefix is the role name (e.g. ``hr_attempt1_..._interactive.log``).
    One interaction produces exactly one file — no separate request-started logs.
    """
    response_logs_dir = logs_dir / f"agent_responses_{date.today().isoformat()}"
    response_logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%H%M%S_%f")
    safe_role = _safe_log_name(role)
    log_file = response_logs_dir / f"{safe_role}_attempt{attempt}_{timestamp}_interactive.log"

    normalized_response = _normalize_log_text(response_text)
    normalized_raw_response = _normalize_log_text(raw_response_text)
    normalized_error = _normalize_log_text(error_text)
    lines = [
        f"timestamp: {datetime.now().astimezone().isoformat()}",
        f"role: {role}",
        f"attempt: {attempt}",
        f"note: interactive",
        f"caller: {caller or ''}",
        f"provider: {provider or ''}",
        f"model: {model or ''}",
        f"base_url: {base_url or ''}",
        f"status_code: {'' if status_code is None else status_code}",
        f"finish_reason: {finish_reason or ''}",
        f"request_state: {request_state or ''}",
        "--- RAW_RESPONSE ---",
        normalized_raw_response,
        "--- RESPONSE_TEXT ---",
        normalized_response,
        "--- ERROR_TEXT ---",
        normalized_error,
        "",
    ]
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return log_file


# Backward-compatible alias used by older imports/tests during transition.
def write_agent_response_log(
    logs_dir: Path,
    source: str,
    attempt: int,
    note: str = "interactive",
    **kwargs: Any,
) -> Path:
    """Deprecated wrapper around :func:`write_interactive_log`."""
    role = kwargs.pop("role", None) or source
    kwargs.pop("prompt_text", None)
    kwargs["caller"] = kwargs.get("caller") or source
    # Ignore legacy note names such as api_response / request_started.
    _ = note
    return write_interactive_log(logs_dir, role=role, attempt=attempt, **kwargs)
