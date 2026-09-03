#!/usr/bin/env python3
"""Wrap a single atomic attack action with tshark traffic capture.

Usage:
    python capture_traffic.py start --label <technique-id> [--iface IFACE]
    python capture_traffic.py stop
    python capture_traffic.py status

`start` launches tshark in the background writing a pcapng file named
`{task_id}_{label}.pcapng` into HOLYFW_ATTACKER_OUTPUT_DIR when set, otherwise
the configured output directory. `stop` terminates tshark and reports the path
of the finished capture file. `status` reports whether a capture is currently
active.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
STATE_FILE_NAME = ".capture_traffic_state.json"
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import winproc  # noqa: E402

try:
    from attacker.capture_paths import capture_file_stem, dataset_output_dir
except ImportError:  # pragma: no cover - skill copy without the attacker package
    capture_file_stem = None  # type: ignore[assignment]
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


def default_interface() -> str:
    cfg = load_config()
    return cfg.get("traffic", {}).get("interface", "Ethernet0")


def tshark_path() -> str:
    cfg = load_config()
    configured = cfg.get("traffic", {}).get("tshark", "tshark")
    resolved = shutil.which(configured)
    return resolved if resolved else configured


def state_file() -> Path:
    return output_dir() / STATE_FILE_NAME


def _pcap_name(label: str) -> str:
    if capture_file_stem is not None:
        return capture_file_stem(label) + ".pcapng"
    task_id = str(os.environ.get("HOLYFW_ATTACKER_TASK_ID") or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label) or "capture"
    if task_id:
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_id)
        return f"{safe_id}_{cleaned}.pcapng"
    return f"{cleaned}.pcapng"


def _launch_tshark(tshark: str, iface: str, file_path: Path) -> int:
    args = [tshark, "-i", iface, "-w", str(file_path), "-q"]
    if os.name == "nt":
        flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    else:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return proc.pid


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

    iface = args.iface or default_interface()
    tshark = tshark_path()
    if not shutil.which(tshark):
        print(json.dumps({"ok": False, "error": f"tshark not found: {tshark}"}))
        return 1

    file_path = out_dir / _pcap_name(args.label)

    pid = _launch_tshark(tshark, iface, file_path)
    time.sleep(0.5)
    if not winproc.pid_alive(pid):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"tshark pid {pid} exited immediately; "
                        f"check interface '{iface}' and Npcap"
                    ),
                    "iface": iface,
                    "file": str(file_path),
                }
            )
        )
        return 1

    state = {
        "label": args.label,
        "pid": pid,
        "file": str(file_path),
        "iface": iface,
        "started_at": datetime.now().isoformat(),
    }
    state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = {"ok": True, "pid": pid, "file": str(file_path), "iface": iface}
    if reclaimed:
        result["reclaimed"] = reclaimed
    print(json.dumps(result))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    sf = state_file()
    if not sf.exists():
        print(json.dumps({"ok": False, "error": "no active traffic capture"}))
        return 1

    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except ValueError:
        print(json.dumps({"ok": False, "error": "corrupt capture state file"}))
        return 1

    pid = state.get("pid")
    if pid is not None:
        _stop_process(int(pid))

    started = state.get("started_at")
    file_path = state.get("file", "")
    try:
        sf.unlink()
    except OSError:
        pass

    print(
        json.dumps(
            {
                "ok": True,
                "label": state.get("label"),
                "file": file_path,
                "iface": state.get("iface"),
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
        description="Start/stop tshark traffic capture around an attack action"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="start traffic capture")
    p_start.add_argument(
        "--label", required=True, help="technique id used in the pcap file name"
    )
    p_start.add_argument(
        "--iface", default=None, help="capture interface (default from config.json)"
    )

    sub.add_parser("stop", help="stop traffic capture and finalize the pcap")
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
