"""Soldier-style Markdown transcripts for attacker OpenCode runs."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import IO

from common.task_markdown import render_task_markdown as render_shared_task_markdown
from common.task_markdown import write_task_markdown as write_shared_task_markdown

_META_KEYS = (
    "task_id",
    "planned_time",
    "date",
    "started_at",
)


def write_task_markdown(handle: IO[bytes], record: dict) -> None:
    write_shared_task_markdown(handle, record, meta_keys=_META_KEYS)


def render_task_markdown(record: dict) -> str:
    return render_shared_task_markdown(record, meta_keys=_META_KEYS)


def task_record_path(logs_dir: Path, day: date, task_id: str) -> Path:
    return logs_dir / day.isoformat() / f"{task_id}.md"


def write_attacker_task_record(path: Path, record: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        write_task_markdown(handle, record)
    return path
