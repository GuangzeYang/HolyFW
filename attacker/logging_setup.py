"""Console and dated-file logging for the attacker scheduler."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

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
