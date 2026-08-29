#!/usr/bin/env python3
"""Wrap a single atomic attack action with Windows event log capture.

Usage:
    python capture_logs.py start --label <technique-id>
    python capture_logs.py stop
    python capture_logs.py status

`start` records the current UTC time as the window start. `stop` exports the
events recorded between that start time and now into one .evtx file per
configured channel named `{task_id}_{label}_{channel}.evtx` in
HOLYFW_ATTACKER_OUTPUT_DIR when set, otherwise the configured output directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
STATE_FILE_NAME = ".capture_logs_state.json"
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


def sysmon_log() -> str:
    cfg = load_config()
    return cfg.get("logs", {}).get("sysmon_log", "Microsoft-Windows-Sysmon/Operational")


def security_log() -> str | None:
    cfg = load_config()
    return cfg.get("logs", {}).get("security_log") or None


def capture_channels() -> list[str]:
    """Log channels to export per atomic action (Sysmon first, then Security)."""
    channels = [sysmon_log()]
    sec = security_log()
    if sec and sec not in channels:
        channels.append(sec)
    return channels


def channel_short(log_name: str) -> str:
    """Short label for a log channel used in output file names."""
    low = log_name.lower()
    if "sysmon" in low:
        return "Sysmon"
    if low in ("security",) or low.endswith("security"):
        return "Security"
    if "kerberos" in low:
        return "Kerberos"
    if "ntlm" in low:
        return "NTLM"
    if "powershell" in low:
        return "PowerShell"
    if "terminalservices" in low or "rdp" in low:
        return "RDP"
    if "openssh" in low or low.startswith("openssh"):
        return "SSH"
    if "smbserver" in low or "smb" in low:
        return "SMBServer"
    if "winrm" in low:
        return "WinRM"
    frag = log_name.replace("Microsoft-Windows-", "").split("/")[0]
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in frag)
    return cleaned or "logs"


def wevtutil_path() -> str:
    cfg = load_config()
    configured = cfg.get("logs", {}).get("wevtutil", "wevtutil")
    resolved = shutil.which(configured)
    return resolved if resolved else configured


def _load_apt_state() -> dict:
    path = SKILL_ROOT / "state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_elevate_payload(stdout: str) -> dict:
    text = (stdout or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


def _export_one_channel(wevtutil: str, log_name: str, out_file: Path, query: str) -> str | None:
    """Export one channel. Return an error string, or None on success."""
    args = [wevtutil, "epl", log_name, str(out_file), f"/q:{query}"]
    rc, out, err = winproc.run(args, timeout=120)
    detail = (err or out or "").strip()
    if rc == 0:
        return None
    if not winproc.looks_like_access_denied(rc, detail):
        return f"{log_name}: {detail or 'export failed'}"
    creds = winproc.local_admin_creds(_load_apt_state())
    if not creds:
        return (
            f"{log_name}: {detail or 'access denied'}; "
            "set campaign.local_admin in state.json to retry via elevate.py"
        )
    user, password = creds
    elev = SKILL_ROOT / "scripts" / "elevate.py"
    erc, eout, eerr = winproc.run(
        [
            sys.executable,
            str(elev),
            "--user",
            user,
            "--password",
            password,
            "--timeout",
            "180",
            "--",
            *args,
        ],
        timeout=200,
    )
    payload = _parse_elevate_payload(eout)
    if payload.get("ok") or erc == 0:
        return None
    nested = str(payload.get("output") or payload.get("error") or eerr or eout or detail)
    return f"{log_name}: elevated export failed: {nested.strip()[:240]}"


def _export_open_window() -> dict:
    """Export leftover/current log window and always remove the state file."""
    sf = state_file()
    empty = {
        "ok": False,
        "files": [],
        "errors": ["no active log capture"],
        "label": "",
        "channels": [],
        "started_at": "",
        "stopped_at": _now_utc_query(),
    }
    if not sf.exists():
        return empty
    try:
        state = json.loads(sf.read_text(encoding="utf-8"))
    except ValueError:
        try:
            sf.unlink()
        except OSError:
            pass
        empty["errors"] = ["corrupt log capture state file"]
        return empty

    started_at = state.get("started_at")
    label = state.get("label", "logs")
    channels = state.get("channels") or [state.get("log") or sysmon_log()]
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    query = (
        f"*[System[TimeCreated[@SystemTime>='{started_at}' "
        f"and @SystemTime<='{_now_utc_query()}']]]"
    )
    wevtutil = wevtutil_path()
    files: list[dict] = []
    errors: list[str] = []
    for log_name in channels:
        short = channel_short(log_name)
        out_file = out_dir / _evtx_name(label, short)
        err = _export_one_channel(wevtutil, log_name, out_file, query)
        if err:
            errors.append(err)
            continue
        files.append({"log": log_name, "file": str(out_file)})
    try:
        sf.unlink()
    except OSError:
        pass
    return {
        "ok": bool(files) and not errors,
        "label": label,
        "files": files,
        "channels": channels,
        "started_at": started_at,
        "stopped_at": _now_utc_query(),
        "errors": errors,
    }


def state_file() -> Path:
    return output_dir() / STATE_FILE_NAME


def _safe_label(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label)
    return cleaned or "logs"


def _evtx_name(label: str, short: str) -> str:
    if capture_file_stem is not None:
        return capture_file_stem(label, short) + ".evtx"
    task_id = str(os.environ.get("HOLYFW_ATTACKER_TASK_ID") or "").strip()
    cleaned = _safe_label(label)
    if task_id:
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in task_id)
        return f"{safe_id}_{cleaned}_{short}.evtx"
    return f"{cleaned}_{short}.evtx"


def _now_utc_query() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def cmd_start(args: argparse.Namespace) -> int:
    out_dir = output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    reclaimed = None
    if state_file().exists():
        reclaimed = _export_open_window()

    label = _safe_label(args.label)
    channels = capture_channels()
    state = {
        "label": label,
        "started_at": _now_utc_query(),
        "log": channels[0],
        "channels": channels,
    }
    state_file().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    payload = {
        "ok": True,
        "label": label,
        "started_at": state["started_at"],
        "log": state["log"],
        "channels": channels,
    }
    if reclaimed is not None:
        payload["reclaimed"] = reclaimed
    print(json.dumps(payload))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    result = _export_open_window()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


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
