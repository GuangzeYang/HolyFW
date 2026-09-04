#!/usr/bin/env python3
"""Reset the current day's commander run: task file and dated logs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import tasks_path

try:
    from runtime_config import (
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.runtime_config import (
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


StatusCallback = Callable[[str], None]


def _emit(message: str) -> None:
    print(message, flush=True)


def _parse_day(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        return date.today().isoformat()
    text = str(raw).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def _remove_path(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return str(path)
    except OSError as exc:
        return f"{path} (failed: {exc})"


def _truncate_or_remove_file(path: Path) -> str | None:
    """Clear a log file. Prefer truncate so a running FileHandler can keep writing."""
    if not path.exists() or not path.is_file():
        return None
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.truncate(0)
        return str(path)
    except OSError:
        return _remove_path(path)


def _task_artifacts(data_dir: Path, day: str) -> list[Path]:
    task_file = tasks_path(data_dir, day)
    stem = task_file.name[: -len(task_file.suffix)] if task_file.suffix else task_file.name
    matches = sorted(data_dir.glob(f"{stem}*"))
    extras = [
        task_file.with_name(task_file.name + ".lock"),
        task_file.with_name(task_file.name + ".tmp"),
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in [*matches, *extras]:
        resolved = path if path.is_absolute() else data_dir / path
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def clear_day_runtime_files(
    *,
    data_dir: Path,
    logs_dir: Path,
    day: str,
) -> dict[str, list[str]]:
    """Delete today's task artifacts and clear dated commander logs."""
    removed_tasks: list[str] = []
    for path in _task_artifacts(data_dir, day):
        result = _remove_path(path)
        if result:
            removed_tasks.append(result)

    cleared_logs: list[str] = []
    log_file = logs_dir / f"commander_{day}.log"
    log_result = _truncate_or_remove_file(log_file)
    if log_result:
        cleared_logs.append(log_result)
    response_dir = logs_dir / f"agent_responses_{day}"
    response_result = _remove_path(response_dir)
    if response_result:
        cleared_logs.append(response_result)
    return {"removed_task_files": removed_tasks, "cleared_logs": cleared_logs}


def reset_day_state(
    *,
    day: str | None = None,
    emit_status: StatusCallback = _emit,
) -> dict[str, Any]:
    """Delete today's task file and dated commander logs. Does not generate tasks."""
    target_day = _parse_day(day)
    runtime = load_runtime_config()
    scanner_config = get_scanner_config(runtime)
    paths_config = get_paths_config(runtime)
    data_dir = resolve_config_relative_path(scanner_config["data_dir"])
    logs_dir = resolve_config_relative_path(paths_config["logs_dir"])

    emit_status(f"Resetting day {target_day}: removing task artifacts")
    files = clear_day_runtime_files(data_dir=data_dir, logs_dir=logs_dir, day=target_day)
    return {
        "ok": True,
        "day": target_day,
        "removed_task_files": files["removed_task_files"],
        "cleared_logs": files["cleared_logs"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reset today's commander run (task file and dated logs)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    reset_parser = sub.add_parser(
        "reset",
        help=(
            "Reset the day's run: delete today's task file and clear commander logs. "
            "Does not generate tasks."
        ),
    )
    reset_parser.add_argument("--date", default=None, help="date YYYY-MM-DD; default today")

    args = parser.parse_args(argv)
    try:
        payload = reset_day_state(day=args.date)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
