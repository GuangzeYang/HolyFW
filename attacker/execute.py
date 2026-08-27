"""Local OpenCode execution and per-task Markdown transcripts for attacker."""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from common import assign_task_id, strip_opencode_run_prefix
from common.task_markdown import OPENCODE_RUN_FLAGS, format_opencode_session

from attacker.capture_paths import OUTPUT_DIR_ENV, TASK_ID_ENV
from attacker.task_record import task_record_path, write_attacker_task_record

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
    return [resolve_opencode_executable(), "run", *OPENCODE_RUN_FLAGS, prompt]


def format_opencode_command(prompt: str, argv: list[str] | None = None) -> str:
    if argv:
        return " ".join(shlex.quote(str(part)) for part in argv)
    if prompt:
        return " ".join(shlex.quote(part) for part in ("opencode", "run", *OPENCODE_RUN_FLAGS, prompt))
    return ""


def opencode_run_env(
    base: Mapping[str, str] | None = None,
    *,
    task_id: str = "",
    output_dir: Path | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["OPENCODE_PERMISSION"] = json.dumps(OPENCODE_PERMISSION_ALLOW, separators=(",", ":"))
    if task_id:
        env[TASK_ID_ENV] = task_id
    if output_dir is not None:
        env[OUTPUT_DIR_ENV] = str(output_dir)
    return env


def iso_now(now: datetime | None = None) -> str:
    stamp = now if now is not None else datetime.now().astimezone()
    if stamp.tzinfo is None:
        stamp = stamp.astimezone()
    return stamp.isoformat(timespec="seconds")


def unpack_opencode_result(packed: Any) -> tuple[int, str, str]:
    """Accept ``(code, stdout)`` or ``(code, stdout, stderr)`` from a runner."""
    if isinstance(packed, tuple):
        if len(packed) >= 3:
            return int(packed[0]), str(packed[1] or ""), str(packed[2] or "")
        if len(packed) == 2:
            return int(packed[0]), str(packed[1] or ""), ""
        if len(packed) == 1:
            return int(packed[0]), "", ""
    return 0, str(packed or ""), ""


def run_opencode(
    task: str,
    timeout_seconds: int,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run one OpenCode prompt locally. Returns (exit_code, stdout, stderr)."""
    prompt = strip_opencode_run_prefix(task)
    logger.info("Starting opencode run --auto; timeout=%ss; prompt=%s", timeout_seconds, preview_text(prompt))
    try:
        argv = build_opencode_argv(prompt)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return MISSING_EXIT_CODE, str(exc), ""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            env=dict(env) if env is not None else opencode_run_env(),
        )
    except subprocess.TimeoutExpired:
        message = f"opencode timed out after {timeout_seconds} seconds"
        logger.error("%s", message)
        return TIMEOUT_EXIT_CODE, message, ""
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = format_opencode_session(stdout, stderr) or stdout or stderr
    code = int(completed.returncode)
    if code != 0:
        logger.error("opencode exited %s; output=%s", code, preview_text(combined))
    else:
        logger.info("opencode exited %s; output=%s", code, preview_text(combined))
    return code, stdout, stderr


def execute_task(
    item: dict[str, str],
    *,
    logs_dir: Path,
    timeout_seconds: int,
    now: datetime | None = None,
    runner: Any | None = None,
    day: date | None = None,
) -> dict[str, str]:
    """Fill started_at/completed_at, run the agent, and write one Markdown transcript."""
    assign_task_id(item)
    stamp = now if now is not None else datetime.now().astimezone()
    item["started_at"] = iso_now(stamp)
    target_day = day or date.today()
    output_dir = logs_dir / target_day.isoformat()
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Executing planned_time=%s task_id=%s task=%s",
        item.get("planned_time"),
        item.get("task_id"),
        preview_text(item.get("task") or ""),
    )
    prompt = strip_opencode_run_prefix(item["task"])
    env = opencode_run_env(task_id=item["task_id"], output_dir=output_dir)
    run = runner if runner is not None else run_opencode
    if runner is None:
        packed = run(item["task"], timeout_seconds, env=env)
    else:
        packed = run(item["task"], timeout_seconds)
    exit_code, stdout, stderr = unpack_opencode_result(packed)
    finished = datetime.now().astimezone() if now is None else stamp
    item["completed_at"] = iso_now(finished)
    logger.info(
        "Finished planned_time=%s task_id=%s exit_code=%s started_at=%s completed_at=%s",
        item.get("planned_time"),
        item.get("task_id"),
        exit_code,
        item.get("started_at"),
        item.get("completed_at"),
    )
    record_path = task_record_path(logs_dir, target_day, item["task_id"])
    write_attacker_task_record(
        record_path,
        {
            "task_id": item["task_id"],
            "planned_time": item.get("planned_time") or "",
            "date": target_day.isoformat(),
            "started_at": item["started_at"],
            "command": format_opencode_command(prompt),
            "stdout": stdout,
            "stderr": stderr,
        },
    )
    logger.info("Wrote task record %s", record_path)
    return item
