#!/usr/bin/env python3
"""Run a command elevated on the local Windows host via a one-shot scheduled task.

Why this exists
---------------
Several attack-side actions need a *full* (unfiltered) administrator token:
installing/updating Sysmon (`sysmon64 -c/-i`), running `auditpol`, exporting the
Sysmon/Security logs (`wevtutil epl`), and so on. `runas /user:<account>` cannot
be driven from an unattended shell — it reads the password from the console and
is blocked by UAC token filtering. `schtasks /ru <account> /rp <password>`
launches the command with the target account's **full token** because the Task
Scheduler service runs as SYSTEM, bypassing UAC filtering. That is what this
script wraps.

Usage
-----
    python scripts/elevate.py --user <account> --password <pw> -- <command...>

Examples
--------
    # whoami under the elevated account (proves the full token)
    python scripts/elevate.py --user ATYdemo --password 123456 -- whoami /groups

    # Update the Sysmon config as a domain admin (already a local admin)
    python scripts/elevate.py --user NDRTEST\\da --password 'P@ss' -- \
        C:\\tools\\Sysmon\\Sysmon64.exe -c C:\\sysmon-modular\\sysmonconfig.xml

    # export the Security log window (full token required for wevtutil epl)
    python scripts/elevate.py --user ATYdemo --password 123456 -- \
        wevtutil epl Security C:\\out\\sec.evtx /q:"*[System[TimeCreated[@SystemTime>='2026-01-01T00:00:00.000Z']]]"

Notes
-----
- The scheduled-task process does NOT inherit the calling user's environment
  (notably the `PYTHONPATH` that makes impacket importable). Pass any required
  environment overrides with `--env KEY=VALUE` (repeatable); a helper sets them
  inside the task's batch wrapper before running the command.
- The command runs with the target account's profile/working environment, not
  the current user's. If it depends on user-site packages (e.g. `readline` from
  an AppData site-packages), add them via `--env PYTHONPATH=...`.
- stdout/stderr of the elevated command are captured and printed; the exit code
  is returned. A temp batch wrapper + output file are cleaned up automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_ST = "23:59"


def _run(args: list[str], timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=timeout, shell=False
        )
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"


def _safe_task_name(tag: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)
    return (cleaned or "elevate_task")[:80]


def _cmd_quote(token: str) -> str:
    """Quote a command token for a batch file when it needs quoting."""
    if token == "":
        return '""'
    specials = any(c in token for c in " &|<>^()%\";,")
    if specials or " " in token:
        return '"' + token.replace('"', '""') + '"'
    return token


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a command elevated via a one-shot scheduled task"
    )
    parser.add_argument("--user", required=True, help="account to run as (e.g. ATYdemo, NDRTEST\\da)")
    parser.add_argument("--password", required=True, help="password for that account")
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        help="environment override for the elevated process, KEY=VALUE (repeatable)",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="scheduled-task name (default: auto-generated)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="seconds to wait for the task output (default 120)",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="working directory for the elevated command (default: the scheduled task's "
        "default, which is System32 — pass this for skill-relative paths)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run elevated (everything after --)",
    )
    args = parser.parse_args()

    # Strip a leading "--" that may leak into the REMAINDER (PowerShell/cmd pass
    # the separator through as a token).
    command = list(args.command)
    while command and command[0] == "--":
        command.pop(0)
    args.command = command

    if not args.command:
        print(json.dumps({"ok": False, "error": "no command given after --"}))
        return 2

    tmp = Path(tempfile.gettempdir())
    task_name = args.task or _safe_task_name("_".join(args.command[:1]))
    out_file = tmp / f"{task_name}_elev_out.txt"
    rc_file = tmp / f"{task_name}_elev_rc.txt"
    bat_file = tmp / f"{task_name}_elev.bat"

    for f in (out_file, rc_file, bat_file):
        try:
            f.unlink()
        except OSError:
            pass

    command_line = " ".join(_cmd_quote(tok) for tok in args.command)
    lines = ["@echo off"]
    if args.cwd:
        lines.append(f"cd /d {_cmd_quote(args.cwd)}")
    for kv in args.env:
        lines.append(f"set {kv}")
    lines.append(f"{command_line} > \"{out_file}\" 2>&1")
    lines.append(f"echo %ERRORLEVEL% > \"{rc_file}\"")
    bat_file.write_text("\r\n".join(lines), encoding="ascii", errors="replace")

    create_rc, create_out, create_err = _run(
        [
            "schtasks", "/create", "/tn", task_name, "/tr", str(bat_file),
            "/sc", "once", "/st", DEFAULT_ST,
            "/ru", args.user, "/rp", args.password, "/f",
        ],
        timeout=60,
    )
    if create_rc != 0:
        print(json.dumps({"ok": False, "error": f"schtasks /create failed: {create_err or create_out}"}))
        return 1

    run_rc, run_out, run_err = _run(["schtasks", "/run", "/tn", task_name], timeout=60)
    if run_rc != 0:
        print(json.dumps({"ok": False, "error": f"schtasks /run failed: {run_err or run_out}"}))
        _run(["schtasks", "/delete", "/tn", task_name, "/f"], timeout=30)
        return 1

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if rc_file.exists():
            break
        time.sleep(1)

    # cleanup the scheduled task regardless of outcome
    _run(["schtasks", "/delete", "/tn", task_name, "/f"], timeout=30)

    if not rc_file.exists():
        print(json.dumps({"ok": False, "error": "elevated command did not complete in time"}))
        return 1

    try:
        elev_rc = int(rc_file.read_text(encoding="ascii").strip() or "0")
    except ValueError:
        elev_rc = 0
    out_text = ""
    if out_file.exists():
        raw = out_file.read_bytes()
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                out_text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if not out_text:
            out_text = raw.decode("utf-8", errors="replace")

    result = {
        "ok": elev_rc == 0,
        "exit_code": elev_rc,
        "account": args.user,
        "command": command_line,
        "output": out_text,
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    try:
        print(payload)
    except UnicodeEncodeError:  # stdout codepage cannot represent some chars
        print(payload.encode("utf-8", errors="replace").decode("ascii", errors="replace"))

    for f in (out_file, rc_file, bat_file):
        try:
            f.unlink()
        except OSError:
            pass

    return 0 if elev_rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
