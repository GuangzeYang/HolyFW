#!/usr/bin/env python3
"""Shared logging bootstrap helpers for commander scripts."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import date, datetime
from pathlib import Path
from typing import Any

try:
    import colorlog
except ImportError:
    colorlog = None

# Root FileHandler for commander main process; swapped on calendar day by periodic hook.
COMMANDER_DATED_FILE_HANDLER_NAME = "commander_dated_file"


def _resolve_log_level(level_name: str) -> int:
    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid logging level: {level_name}")
    return level


def _plain_log_formatter() -> logging.Formatter:
    return logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    plain_formatter = _plain_log_formatter()
    if colorlog is not None:
        color_formatter = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
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
    commander periodic hook, not ``TimedRotatingFileHandler``. Values such as
    ``logging.backup_count`` / ``logging.rotation_interval_days`` in config apply
    only to :func:`configure_daily_logging` (e.g. dispatch), not this path.
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
    file_handler.setFormatter(plain_formatter)
    file_handler.name = COMMANDER_DATED_FILE_HANDLER_NAME
    root.addHandler(file_handler)
    return log_file


def configure_daily_logging(
    logs_dir: Path,
    log_prefix: str,
    level_name: str,
    backup_count: int,
    rotation_interval_days: int,
) -> Path:
    """Configure root logger with console + daily rotating file handlers."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{log_prefix}_{date.today().isoformat()}.log"

    level = _resolve_log_level(level_name)

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
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=rotation_interval_days,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(plain_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return log_file


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


def write_agent_response_log(
    logs_dir: Path,
    source: str,
    attempt: int,
    note: str,
    *,
    prompt_text: str | bytes | None = None,
    provider: str | None = None,
    model: str | None = None,
    status_code: int | None = None,
    role: str | None = None,
    finish_reason: str | None = None,
    response_text: str | bytes | None = None,
    raw_response_text: str | bytes | None = None,
    error_text: str | bytes | None = None,
    request_state: str | None = None,
) -> Path:
    """Write one model response/error payload into its own UTF-8 log file."""
    response_logs_dir = logs_dir / f"agent_responses_{date.today().isoformat()}"
    response_logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%H%M%S_%f")
    safe_source = _safe_log_name(source)
    safe_note = _safe_log_name(note)
    log_file = response_logs_dir / f"{safe_source}_attempt{attempt}_{timestamp}_{safe_note}.log"

    normalized_prompt = _normalize_log_text(prompt_text)
    normalized_response = _normalize_log_text(response_text)
    normalized_raw_response = _normalize_log_text(raw_response_text)
    normalized_error = _normalize_log_text(error_text)
    lines = [
        f"timestamp: {datetime.now().astimezone().isoformat()}",
        f"source: {source}",
        f"attempt: {attempt}",
        f"note: {note}",
        f"provider: {provider or ''}",
        f"model: {model or ''}",
        f"status_code: {'' if status_code is None else status_code}",
        f"role: {role or ''}",
        f"finish_reason: {finish_reason or ''}",
        f"request_state: {request_state or ''}",
        "--- PROMPT_TEXT ---",
        normalized_prompt,
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


def append_agent_output_log(
    logs_dir: Path,
    source: str,
    attempt: int,
    prompt: str,
    note: str,
    *,
    model: str | None = None,
    response_text: str | bytes | None = None,
    error_text: str | bytes | None = None,
    status_code: int | None = None,
    **extra_fields: Any,
) -> Path:
    """Append one model interaction record to the daily agent output log."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"agent_output_{date.today().isoformat()}.log"
    normalized_response = _normalize_log_text(response_text)
    normalized_error = _normalize_log_text(error_text)
    payload = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "source": source,
        "attempt": attempt,
        "model": model,
        "prompt_length": len(prompt),
        "prompt_preview": prompt[:500],
        "note": note,
        "status_code": status_code,
        "response_preview": normalized_response[:500],
        "response_text": normalized_response,
        "error_text": normalized_error,
    }
    payload.update(extra_fields)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return log_file
