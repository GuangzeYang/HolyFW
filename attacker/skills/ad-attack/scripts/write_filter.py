#!/usr/bin/env python3
"""Write a per-task Wireshark display filter next to the task transcript.

Usage:
    python write_filter.py --expression "<display filter>"

The file is `{task_id}.txt` in HOLYFW_ATTACKER_OUTPUT_DIR when set, otherwise
the configured output directory. It contains only the display filter expression
(no tshark command, no time window).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"

try:
    from attacker.capture_paths import dataset_output_dir
except ImportError:  # pragma: no cover - skill copy without the attacker package
    dataset_output_dir = None  # type: ignore[assignment]

_FORBIDDEN = re.compile(
    r"tshark|\b-r\b|\b-Y\b|\b-w\b|frame\.time_epoch|frame\.time\b",
    re.IGNORECASE,
)
_WRAPPED_QUOTES = re.compile(r"^(['\"`])(.*)\1$", re.DOTALL)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def output_dir() -> Path:
    cfg = load_config()
    raw = str(cfg.get("output_dir") or "output")
    if dataset_output_dir is not None:
        return dataset_output_dir(config_output_dir=raw, skill_root=SKILL_ROOT)
    env_raw = str(os.environ.get("HOLYFW_ATTACKER_OUTPUT_DIR") or "").strip()
    if env_raw:
        return Path(env_raw)
    path = Path(raw)
    return path if path.is_absolute() else SKILL_ROOT / path


def task_id() -> str:
    value = str(os.environ.get("HOLYFW_ATTACKER_TASK_ID") or "").strip()
    if not value:
        raise SystemExit("HOLYFW_ATTACKER_TASK_ID is not set")
    return value


def normalize_expression(raw: str) -> str:
    text = str(raw or "").strip()
    match = _WRAPPED_QUOTES.match(text)
    if match:
        text = match.group(2).strip()
    text = " ".join(text.split())
    if not text:
        raise SystemExit("display filter expression is empty")
    if _FORBIDDEN.search(text):
        raise SystemExit(
            "expression must be a display filter only "
            "(no tshark command, no frame.time / frame.time_epoch)"
        )
    return text


def write_expression(expression: str) -> Path:
    dest = output_dir() / f"{task_id()}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(expression + "\n", encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the task Wireshark display filter to {task_id}.txt"
    )
    parser.add_argument(
        "--expression",
        required=True,
        help="Wireshark display filter (expression only)",
    )
    args = parser.parse_args(argv)
    try:
        expression = normalize_expression(args.expression)
        dest = write_expression(expression)
    except SystemExit as exc:
        message = str(exc)
        if message:
            print(message, file=sys.stderr)
        return 1
    print(str(dest), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
