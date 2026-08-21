#!/usr/bin/env python3
"""Wrap a single atomic attack action with Sysmon event log capture.

Usage:
    python capture_logs.py start --label <technique-id>
    python capture_logs.py stop
    python capture_logs.py status

`start` records the current UTC time as the window start. `stop` exports all
Sysmon events recorded between that start time and now into an .evtx file in
the configured output directory using `wevtutil epl` with a time-based XPath
query.

The Sysmon log name and output directory are read from config.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
STATE_FILE_NAME = ".capture_logs_state.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def output_dir() -> Path:
    cfg = load_config()
    raw = cfg.get("output_dir", "output")
    p = Path(raw)
    if not p.is_absolute():
        p = SKILL_ROOT / p
    return p


def sysmon_log() -> str:
    cfg = load_config()
    return cfg.get("logs", {}).get("sysmon_log", "Microsoft-Windows-Sysmon/Operational")


def wevtutil_path() -> str:
    cfg = load_config()
    configured = cfg.get("logs", {}).get("wevtutil", "wevtutil")
    resolved = shutil.which(configured)
    return resolved if resolved else configured


def state_file() -> Path:
    return output_dir() / STATE_FILE_NAME


def _safe_label(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
    return cleaned or "logs"


def _now_utc_query() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def cmd_start(args: argparse.Namespace) -> int:
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    if state_file().exists():
        print(json.dumps({"ok": False, "error": "a log capture is already active"}))
        return 1

    label = _safe_label(args.label)
    state = {
        "label": label,
        "started_at": _now_utc_query(),
        "log": sysmon_log(),
    }
    state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "ok": True,
                "label": label,
                "started_at": state["started_at"],
                "log": state["log"],
            }
        )
    )
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    sf = state_file()
    if not sf.exists():
        print(json.dumps({"ok": False, "error": "no active log capture"}))
        return 1

    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except ValueError:
        print(json.dumps({"ok": False, "error": "corrupt log capture state file"}))
        return 1

    started_at = state.get("started_at")
    label = state.get("label", "logs")
    log_name = state.get("log", sysmon_log())

    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{label}_{_now_stamp()}.evtx"

    query = (
        f"*[System[TimeCreated[@SystemTime>='{started_at}' "
        f"and @SystemTime<='{_now_utc_query()}']]]"
    )

    wevtutil = wevtutil_path()
    wevtutil_args = [wevtutil, "epl", log_name, str(out_file), f"/q:{query}"]
    try:
        proc = subprocess.run(
            wevtutil_args, capture_output=True, text=True, timeout=120
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": f"wevtutil failed: {exc}"}))
        return 1

    if proc.returncode != 0:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        proc.stderr or proc.stdout or "wevtutil export failed"
                    ).strip(),
                }
            )
        )
        return 1

    try:
        sf.unlink()
    except OSError:
        pass

    print(
        json.dumps(
            {
                "ok": True,
                "label": label,
                "file": str(out_file),
                "log": log_name,
                "started_at": started_at,
                "stopped_at": _now_utc_query(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sf = state_file()
    if not sf.exists():
        print(json.dumps({"active": False}))
        return 0
    print(
        json.dumps(
            {"active": True, "state": json.loads(sf.read_text(encoding="utf-8"))},
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start/stop Sysmon log capture around an attack action"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="start log capture")
    p_start.add_argument(
        "--label", required=True, help="technique id used in the evtx file name"
    )

    sub.add_parser("stop", help="stop log capture and export the evtx")
    sub.add_parser("status", help="report whether a capture is active")

    args = parser.parse_args()
    if args.command == "start":
        return cmd_start(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "status":
        return cmd_status(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
