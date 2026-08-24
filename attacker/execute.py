"""Local OpenCode execution and per-task result log for attacker."""

from __future__ import annotations

import json
import logging
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
RESULT_PREVIEW_CHARS = 240
logger = logging.getLogger(__name__)


def preview_text(text: str, limit: int = RESULT_PREVIEW_CHARS) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


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
    logger.info("Starting opencode run --auto; timeout=%ss; prompt=%s", timeout_seconds, preview_text(prompt))
    try:
        argv = build_opencode_argv(prompt)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
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
        message = f"opencode timed out after {timeout_seconds} seconds"
        logger.error("%s", message)
        return TIMEOUT_EXIT_CODE, message
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = stdout if not stderr else f"{stdout}\n{stderr}".strip()
    code = int(completed.returncode)
    if code != 0:
        logger.error("opencode exited %s; output=%s", code, preview_text(combined))
    else:
        logger.info("opencode exited %s; output=%s", code, preview_text(combined))
    return code, combined


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
    logger.info("Executing planned_time=%s task=%s", item.get("planned_time"), preview_text(item.get("task") or ""))
    run = runner if runner is not None else run_opencode
    exit_code, result = run(item["task"], timeout_seconds)
    finished = datetime.now().astimezone() if now is None else stamp
    item["completed_at"] = iso_now(finished)
    logger.info(
        "Finished planned_time=%s exit_code=%s started_at=%s completed_at=%s",
        item.get("planned_time"),
        exit_code,
        item.get("started_at"),
        item.get("completed_at"),
    )
    append_execution_log(
        logs_dir,
        planned_time=item["planned_time"],
        task=item["task"],
        result=result,
        exit_code=exit_code,
        day=day,
    )
    return item
