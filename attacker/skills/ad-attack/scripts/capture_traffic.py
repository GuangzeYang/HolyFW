#!/usr/bin/env python3
"""Protocol-compatible traffic-capture stub.

Usage:
    python capture_traffic.py start --label <technique-id> [--iface IFACE]
    python capture_traffic.py stop
    python capture_traffic.py status

Live tshark is disabled. Malicious flows are sliced later with
``attacker extract`` from a domain SPAN pcap. ``start`` / ``stop`` / ``status``
still succeed so the skill protocol can call them. A leftover tshark from an
older skill copy is stopped if its PID is still alive.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
STATE_FILE_NAME = ".capture_traffic_state.json"
SKIP_REASON = "live pcap disabled"
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import winproc  # noqa: E402

try:
    from attacker.capture_paths import dataset_output_dir
except ImportError:  # pragma: no cover - skill copy without the attacker package
    dataset_output_dir = None  # type: ignore[assignment]


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


def state_file() -> Path:
    return output_dir() / STATE_FILE_NAME


def _stop_process(pid: int) -> None:
    if os.name == "nt":
        winproc.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            timeout=30,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    time.sleep(2)


def _reclaim_stale_capture() -> dict | None:
    """Stop a live leftover tshark, or drop a lock whose PID is already dead."""
    sf = state_file()
    if not sf.exists():
        return None
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        try:
            sf.unlink()
        except OSError:
            pass
        return {"reclaimed": "corrupt_state"}
    pid = state.get("pid")
    alive = pid is not None and winproc.pid_alive(int(pid))
    if alive:
        _stop_process(int(pid))
        action = "stopped_live"
    else:
        action = "dropped_dead"
    try:
        sf.unlink()
    except OSError:
        pass
    return {
        "reclaimed": action,
        "previous_pid": pid,
        "previous_label": state.get("label"),
        "previous_file": state.get("file"),
    }


def cmd_start(args: argparse.Namespace) -> int:
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    reclaimed = _reclaim_stale_capture()
    state = {
        "label": args.label,
        "skipped": SKIP_REASON,
        "started_at": datetime.now().isoformat(),
    }
    state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = {"ok": True, "skipped": SKIP_REASON, "label": args.label}
    if reclaimed:
        result["reclaimed"] = reclaimed
    print(json.dumps(result))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    sf = state_file()
    label = ""
    started = ""
    if sf.exists():
        try:
            state = json.loads(sf.read_text(encoding="utf-8"))
        except ValueError:
            state = {}
        pid = state.get("pid")
        if pid is not None:
            _stop_process(int(pid))
        label = str(state.get("label") or "")
        started = str(state.get("started_at") or "")
        try:
            sf.unlink()
        except OSError:
            pass
    print(
        json.dumps(
            {
                "ok": True,
                "skipped": SKIP_REASON,
                "label": label,
                "started_at": started,
                "stopped_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sf = state_file()
    if not sf.exists():
        print(json.dumps({"active": False, "skipped": SKIP_REASON}))
        return 0
    print(
        json.dumps(
            {
                "active": False,
                "skipped": SKIP_REASON,
                "state": json.loads(sf.read_text(encoding="utf-8")),
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Protocol stub for traffic capture (live tshark disabled)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="record the technique label (no pcap)")
    p_start.add_argument(
        "--label", required=True, help="technique id (kept for protocol compatibility)"
    )
    p_start.add_argument(
        "--iface", default=None, help="ignored; live capture is disabled"
    )

    sub.add_parser("stop", help="end the capture stub")
    sub.add_parser("status", help="report that live capture is disabled")

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
