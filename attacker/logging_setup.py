"""Console and dated-file logging for the attacker scheduler."""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

try:
    import colorlog
except ImportError:
    colorlog = None

ATTACKER_LOGGER_NAME = "attacker"
ATTACKER_DATED_FILE_HANDLER_NAME = "attacker_dated_file"
ATTACKER_LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"


def _plain_formatter() -> logging.Formatter:
    return logging.Formatter(ATTACKER_LOG_FORMAT)


def _build_console_handler(level: int) -> logging.StreamHandler:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    if colorlog is not None:
        console_handler.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s" + ATTACKER_LOG_FORMAT,
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    else:
        console_handler.setFormatter(_plain_formatter())
    return console_handler


def configure_attacker_logging(logs_dir: Path, level: int = logging.INFO) -> Path:
    """Configure the attacker logger for console plus a dated log file."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"attacker_{date.today().isoformat()}.log"
    logger = logging.getLogger(ATTACKER_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    logger.addHandler(_build_console_handler(level))
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(_plain_formatter())
    file_handler.name = ATTACKER_DATED_FILE_HANDLER_NAME
    logger.addHandler(file_handler)
    return log_file


def reattach_attacker_dated_file_handler(
    logs_dir: Path,
    *,
    target_day: date,
    level: int | None = None,
    logger: logging.Logger | None = None,
) -> Path:
    """Replace the attacker dated FileHandler for *target_day*.

    Removes only the handler named :data:`ATTACKER_DATED_FILE_HANDLER_NAME`
    from the ``attacker`` logger (or an explicit *logger*), keeping other
    handlers (e.g. console) unchanged. The new handler inherits the removed
    handler's level (falling back to *level* or INFO) and the plain format.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    target = logger or logging.getLogger(ATTACKER_LOGGER_NAME)
    previous_level: int | None = None
    previous_formatter: logging.Formatter | None = None
    for handler in list(target.handlers):
        if getattr(handler, "name", None) == ATTACKER_DATED_FILE_HANDLER_NAME:
            previous_level = handler.level
            previous_formatter = handler.formatter
            target.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    resolved_level = previous_level if previous_level not in (None, logging.NOTSET) else level
    if resolved_level is None:
        resolved_level = logging.INFO
    log_file = logs_dir / f"attacker_{target_day.isoformat()}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(previous_formatter or _plain_formatter())
    file_handler.name = ATTACKER_DATED_FILE_HANDLER_NAME
    target.addHandler(file_handler)
    return log_file


REQUEST_TASK_LLM_DIR_PREFIX = "request_task_LLM"


def _normalize_log_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _format_request_messages(messages: Sequence[dict[str, str]] | None) -> str:
    if not messages:
        return ""
    parts: list[str] = []
    for item in messages:
        role = str(item.get("role") or "").strip() or "unknown"
        content = item.get("content") or ""
        parts.append(f"role: {role}")
        parts.append(_normalize_log_text(content))
        parts.append("")
    return "\n".join(parts).rstrip()


def request_task_llm_dir(logs_dir: Path, day: date) -> Path:
    return logs_dir / f"{REQUEST_TASK_LLM_DIR_PREFIX}_{day.isoformat()}"


def _unique_request_md_path(directory: Path, stamp: str, batch: int) -> Path:
    candidate = directory / f"{stamp}_{batch}.md"
    if not candidate.exists():
        return candidate
    suffix = 2
    while True:
        candidate = directory / f"{stamp}_{batch}_{suffix}.md"
        if not candidate.exists():
            return candidate
        suffix += 1


def write_request_task_md(
    logs_dir: Path,
    *,
    day: date,
    batch: int,
    attempt: int,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    status_code: int | None = None,
    finish_reason: str | None = None,
    messages: Sequence[dict[str, str]] | None = None,
    response_text: str | bytes | None = None,
    raw_response_text: str | bytes | None = None,
    error_text: str | bytes | None = None,
    request_state: str | None = None,
    caller: str | None = None,
    now: datetime | None = None,
) -> Path:
    """Write one attacker LLM fill interaction as a markdown file.

    Directory is ``request_task_LLM_{YYYY-MM-DD}``. Filename is
    ``{HHMMSS}_{batch}.md``; a ``_2`` suffix is added on same-second collisions.
    """
    resolved_day = day
    clock = now if now is not None else datetime.now().astimezone()
    response_logs_dir = request_task_llm_dir(logs_dir, resolved_day)
    response_logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.strftime("%H%M%S")
    log_file = _unique_request_md_path(response_logs_dir, stamp, int(batch))

    lines = [
        f"timestamp: {clock.isoformat()}",
        f"batch: {int(batch)}",
        f"attempt: {attempt}",
        f"note: interactive",
        f"caller: {caller or 'request_task_batch'}",
        f"provider: {provider or ''}",
        f"model: {model or ''}",
        f"base_url: {base_url or ''}",
        f"status_code: {'' if status_code is None else status_code}",
        f"finish_reason: {finish_reason or ''}",
        f"request_state: {request_state or ''}",
        "--- REQUEST ---",
        _format_request_messages(messages),
        "--- RAW_RESPONSE ---",
        _normalize_log_text(raw_response_text),
        "--- RESPONSE_TEXT ---",
        _normalize_log_text(response_text),
        "--- ERROR_TEXT ---",
        _normalize_log_text(error_text),
        "",
    ]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    return log_file
