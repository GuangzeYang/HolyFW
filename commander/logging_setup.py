#!/usr/bin/env python3
"""Shared logging bootstrap helpers for commander scripts."""

from __future__ import annotations

import logging
import logging.handlers
from datetime import date
from pathlib import Path

try:
    import colorlog
except ImportError:
    colorlog = None


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

    level = getattr(logging, level_name.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid logging level: {level_name}")

    logger = logging.getLogger()
    logger.setLevel(level)

    # Reset handlers to avoid duplicates when configuration is invoked multiple times.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=rotation_interval_days,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)

    plain_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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
    file_handler.setFormatter(plain_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return log_file
