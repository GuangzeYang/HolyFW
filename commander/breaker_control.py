#!/usr/bin/env python3
"""Inspect or reset HolyFW circuit breakers and the current day's run state."""

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
    from failure_governor import EmailAlerter, RoleFailureGovernor
    from runtime_config import (
        get_email_alert_config,
        get_failure_policy_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.failure_governor import EmailAlerter, RoleFailureGovernor
    from commander.runtime_config import (
        get_email_alert_config,
        get_failure_policy_config,
        get_paths_config,
        get_scanner_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


StatusCallback = Callable[[str], None]


def _emit(message: str) -> None:
    print(message, flush=True)


def _build_governor() -> RoleFailureGovernor:
    runtime = load_runtime_config()
    policy = get_failure_policy_config(runtime)
    alerter = EmailAlerter(get_email_alert_config(runtime))
    return RoleFailureGovernor(
        resolve_config_relative_path(policy["state_file"]),
        cooldown_seconds=policy["cooldown_seconds"],
        max_consecutive_failures=policy["max_consecutive_failures"],
        email_alerter=alerter,
    )


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
    governor: RoleFailureGovernor | None = None,
) -> dict[str, Any]:
    """Clear breaker state, today's task file, and dated commander logs. Does not generate tasks."""
    target_day = _parse_day(day)
    runtime = load_runtime_config()
    scanner_config = get_scanner_config(runtime)
    paths_config = get_paths_config(runtime)
    data_dir = resolve_config_relative_path(scanner_config["data_dir"])
    logs_dir = resolve_config_relative_path(paths_config["logs_dir"])
    active_governor = governor or _build_governor()

    emit_status(f"Resetting day {target_day}: removing task artifacts")
    files = clear_day_runtime_files(data_dir=data_dir, logs_dir=logs_dir, day=target_day)
    cleared_roles = active_governor.reset_day(target_day)
    emit_status(
        f"Cleared breaker state for {len(cleared_roles)} role(s): {cleared_roles or 'none'}"
    )
    return {
        "ok": True,
        "day": target_day,
        "cleared_breaker_roles": cleared_roles,
        "removed_task_files": files["removed_task_files"],
        "cleared_logs": files["cleared_logs"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or reset HolyFW role circuit breakers")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="show breaker state")
    status_parser.add_argument("--date", default=None, help="date YYYY-MM-DD; default today")

    reset_parser = sub.add_parser(
        "reset",
        help=(
            "Reset the day's run: clear all role breakers, delete today's task file, "
            "and clear commander logs. Does not generate tasks."
        ),
    )
    reset_parser.add_argument(
        "--role",
        default=None,
        help="with --circuit-only, lift one role; ignored for a full day reset",
    )
    reset_parser.add_argument("--date", default=None, help="date YYYY-MM-DD; default today")
    reset_parser.add_argument(
        "--circuit-only",
        action="store_true",
        help="only clear breaker state (optionally one --role); do not delete tasks or logs",
    )

    args = parser.parse_args(argv)
    governor = _build_governor()
    if args.command == "status":
        print(json.dumps(governor.status(args.date), ensure_ascii=False, indent=2))
        return 0

    if args.circuit_only:
        try:
            target_day = _parse_day(args.date)
        except ValueError as exc:
            parser.error(str(exc))
        if args.role:
            reset = governor.reset(str(args.role).lower(), target_day)
            print(
                json.dumps(
                    {"ok": reset, "role": str(args.role).lower(), "day": target_day},
                    ensure_ascii=False,
                )
            )
            return 0 if reset else 1
        roles = governor.reset_day(target_day)
        print(
            json.dumps(
                {"ok": True, "day": target_day, "cleared_breaker_roles": roles},
                ensure_ascii=False,
            )
        )
        return 0

    if args.role:
        _emit(
            f"--role {args.role} is ignored for a full day reset; "
            "use --circuit-only --role to lift a single breaker"
        )
    try:
        payload = reset_day_state(day=args.date, governor=governor)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
