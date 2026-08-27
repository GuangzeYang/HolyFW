"""Dataset output directory and capture filenames for attacker pcap/evtx files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

TASK_ID_ENV = "HOLYFW_ATTACKER_TASK_ID"
OUTPUT_DIR_ENV = "HOLYFW_ATTACKER_OUTPUT_DIR"


def _safe_token(value: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)
    return cleaned or fallback


def dataset_output_dir(
    *,
    env: Mapping[str, str] | None = None,
    config_output_dir: str = "output",
    skill_root: Path | None = None,
) -> Path:
    mapping = os.environ if env is None else env
    raw = str(mapping.get(OUTPUT_DIR_ENV) or "").strip()
    if raw:
        return Path(raw)
    path = Path(config_output_dir or "output")
    if path.is_absolute():
        return path
    root = skill_root if skill_root is not None else Path.cwd()
    return root / path


def capture_file_stem(
    label: str,
    *parts: str,
    env: Mapping[str, str] | None = None,
) -> str:
    mapping = os.environ if env is None else env
    tokens: list[str] = []
    task_id = str(mapping.get(TASK_ID_ENV) or "").strip()
    if task_id:
        tokens.append(_safe_token(task_id, "task"))
    tokens.append(_safe_token(label, "capture"))
    for part in parts:
        text = str(part or "").strip()
        if text:
            tokens.append(_safe_token(text, "part"))
    return "_".join(tokens)
