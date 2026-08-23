"""Local OpenCode execution and per-task result log for attacker."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from common import strip_opencode_run_prefix

OPENCODE_PERMISSION_ALLOW: dict[str, object] = {
    "*": "allow",
    "doom_loop": "allow",
    "external_directory": {"*": "allow"},
}

TIMEOUT_EXIT_CODE = 124
MISSING_EXIT_CODE = 127


def resolve_opencode_executable() -> str:
    found = shutil.which("opencode")
    if not found:
        raise FileNotFoundError("opencode executable not found on PATH")
    return found


def build_opencode_argv(prompt: str) -> list[str]:
    return [resolve_opencode_executable(), "run", "--auto", prompt]


def opencode_run_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["OPENCODE_PERMISSION"] = json.dumps(OPENCODE_PERMISSION_ALLOW, separators=(",", ":"))
    return env


def execution_log_path(logs_dir: Path, day: date | None = None) -> Path:
    target = day or date.today()
    return logs_dir / f"tasks_{target.isoformat()}.jsonl"


def append_execution_log(
    logs_dir: Path,
    *,
    planned_time: str,
    task: str,
    result: str,
    exit_code: int,
    day: date | None = None,
) -> Path:
    path = execution_log_path(logs_dir, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "planned_time": planned_time,
        "task": strip_opencode_run_prefix(task),
        "result": result if isinstance(result, str) else str(result),
        "exit_code": int(exit_code),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def run_opencode(task: str, timeout_seconds: int) -> tuple[int, str]:
    """Run one OpenCode prompt locally. Returns (exit_code, combined output)."""
    prompt = strip_opencode_run_prefix(task)
    try:
        argv = build_opencode_argv(prompt)
    except FileNotFoundError as exc:
        return MISSING_EXIT_CODE, str(exc)
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=opencode_run_env(),
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT_CODE, f"opencode timed out after {timeout_seconds} seconds"
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout if not stderr else f"{stdout}\n{stderr}".strip()
    return int(completed.returncode), combined


def iso_now(now: datetime | None = None) -> str:
    stamp = now if now is not None else datetime.now().astimezone()
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.isoformat(timespec="seconds")


def execute_task(
    item: dict[str, str],
    *,
    logs_dir: Path,
    timeout_seconds: int,
    now: datetime | None = None,
    runner: Any | None = None,
    day: date | None = None,
) -> dict[str, str]:
    """Fill started_at/completed_at, run the agent, and append one result log line."""
    stamp = now if now is not None else datetime.now().astimezone()
    item["started_at"] = iso_now(stamp)
    run = runner if runner is not None else run_opencode
    exit_code, result = run(item["task"], timeout_seconds)
    finished = datetime.now().astimezone() if now is None else stamp
    item["completed_at"] = iso_now(finished)
    append_execution_log(
        logs_dir,
        planned_time=item["planned_time"],
        task=item["task"],
        result=result,
        exit_code=exit_code,
        day=day,
    )
    return item
