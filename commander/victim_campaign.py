#!/usr/bin/env python3
"""On-demand victim campaign helper: one technique per step.

Office roles still use commander daily generation. The victim role is excluded
from that quota. This script runs a single `opencode run` (local) or one
`dispatch.py` send (commander), then stores last_result / next_task.

State file default: %USERPROFILE%/.holyfw/campaign_state.json
Override with HOLYFW_VICTIM_CAMPAIGN_STATE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CAMPAIGN_BEGIN = "---HOLYFW_CAMPAIGN---"
CAMPAIGN_END = "---END_HOLYFW_CAMPAIGN---"
LAST_RESULTS = frozenset(
    {
        "success",
        "blocked_local_priv",
        "blocked_remote_priv",
        "blocked_missing",
        "failed",
        "stopped",
        "cleaned",
    }
)

DEFAULT_RECON_TASK = (
    "Use the penetration-test skill on the victim host, run observe for the reconnaissance phase, "
    "{run_id: recon-001, approved target: <DC_IP>, technique: domain users and trusts, "
    "traffic objective: LDAP queries to the approved DC, "
    "success criteria: sanitized user and trust counts saved, cleanup: not applicable}"
)


def default_state_path() -> Path:
    raw = (os.environ.get("HOLYFW_VICTIM_CAMPAIGN_STATE") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".holyfw" / "campaign_state.json"


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "access_level": "domain-user",
        "current_host": "victim",
        "phase": "reconnaissance",
        "last_result": "",
        "last_run_id": "",
        "next_task": None,
        "blocked_reason": "",
        "approved_targets": [],
        "inventory": {
            "hosts": [{"name": "victim", "ip": "", "access": "domain-user"}],
            "cred_refs": [],
        },
    }


def parse_campaign_block(text: str) -> dict[str, Any] | None:
    """Extract the campaign JSON fence from OpenCode / soldier stdout."""
    if not text:
        return None
    pattern = re.compile(
        re.escape(CAMPAIGN_BEGIN) + r"\s*(.*?)\s*" + re.escape(CAMPAIGN_END),
        re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def recommend_next_phase(
    last_result: str,
    *,
    has_cred_ref: bool = False,
) -> str | None:
    """Map a privilege/missing failure to the next single phase. None means stop."""
    if last_result == "blocked_local_priv":
        return "privilege-escalation"
    if last_result == "blocked_remote_priv":
        return "lateral-movement" if has_cred_ref else "credential-access"
    return None


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    merged = empty_state()
    merged.update(data)
    return merged


def save_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_campaign_into_state(state: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    merged.update(campaign)
    merged["schema_version"] = 1
    last_result = merged.get("last_result")
    if last_result and last_result not in LAST_RESULTS:
        merged["blocked_reason"] = (
            f"{merged.get('blocked_reason') or ''} invalid last_result={last_result!r}"
        ).strip()
    next_task = merged.get("next_task")
    if isinstance(next_task, str):
        next_task = next_task.strip()
        merged["next_task"] = next_task or None
    elif next_task is not None:
        merged["next_task"] = None
    return merged


def resolve_step_task(state: dict[str, Any], task_override: str | None) -> str:
    if task_override and task_override.strip():
        return task_override.strip()
    next_task = state.get("next_task")
    if isinstance(next_task, str) and next_task.strip():
        return next_task.strip()
    raise ValueError(
        "No task to run. Pass --task for the first step, or wait until the previous "
        "run stored next_task in the campaign state file."
    )


def _run_opencode(task_text: str, timeout_seconds: int) -> tuple[int, str]:
    completed = subprocess.run(
        ["opencode", "run", task_text],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    combined = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return completed.returncode, combined


def _run_dispatch(task_text: str, timeout_seconds: int) -> tuple[int, str]:
    dispatch_script = Path(__file__).resolve().parent / "dispatch.py"
    command = f"opencode run {json.dumps(task_text, ensure_ascii=False)}"
    completed = subprocess.run(
        [
            sys.executable,
            str(dispatch_script),
            "--target",
            "victim",
            "--command",
            command,
            "--task",
            task_text,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    combined = (completed.stdout or "") + ("\n" + completed.stderr if completed.stderr else "")
    return completed.returncode, combined


def cmd_show(state_path: Path) -> int:
    state = load_state(state_path)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print(f"# state file: {state_path}", flush=True)
    return 0


def cmd_step(
    *,
    state_path: Path,
    task_override: str | None,
    mode: str,
    timeout_seconds: int,
) -> int:
    state = load_state(state_path)
    try:
        task_text = resolve_step_task(state, task_override)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        print("Example first task:", file=sys.stderr)
        print(DEFAULT_RECON_TASK, file=sys.stderr)
        return 2

    print(f"Running one victim step ({mode}):", flush=True)
    print(task_text, flush=True)
    try:
        if mode == "dispatch":
            code, output = _run_dispatch(task_text, timeout_seconds)
        else:
            code, output = _run_opencode(task_text, timeout_seconds)
    except FileNotFoundError as exc:
        print(f"Failed to start helper: {exc}", file=sys.stderr)
        return 127
    except subprocess.TimeoutExpired:
        print(f"Step timed out after {timeout_seconds} seconds", file=sys.stderr)
        return 124

    print(output, end="" if output.endswith("\n") else "\n", flush=True)
    campaign = parse_campaign_block(output)
    if campaign is not None:
        state = merge_campaign_into_state(state, campaign)
        save_state(state_path, state)
        updated = state
    else:
        refreshed = load_state(state_path)
        if refreshed.get("last_result"):
            updated = refreshed
            print("# using campaign_state.json written by the skill (no stdout fence)", flush=True)
        else:
            if mode == "dispatch":
                print(
                    "# dispatch acknowledged the send only. After soldier/opencode finishes, "
                    "read campaign_state.json on the victim (or soldier stdout) for last_result/next_task.",
                    flush=True,
                )
            else:
                print(
                    "# no ---HOLYFW_CAMPAIGN--- block parsed; state file left unchanged",
                    flush=True,
                )
            return 0 if code == 0 else code

    print(f"# updated campaign state: {state_path}", flush=True)
    print(f"# last_result: {updated.get('last_result')!r}", flush=True)
    print(f"# next_task: {updated.get('next_task')!r}", flush=True)
    phase = recommend_next_phase(
        str(updated.get("last_result") or ""),
        has_cred_ref=bool((updated.get("inventory") or {}).get("cred_refs")),
    )
    if phase:
        print(f"# suggested next phase if next_task is empty: {phase}", flush=True)
    return 0 if code == 0 else code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one on-demand victim penetration-test step (not daily generation)."
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Campaign JSON path (default: ~/.holyfw/campaign_state.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="Print the current campaign state file")

    step = sub.add_parser("step", help="Run one local opencode task and update campaign state")
    step.add_argument("--task", default=None, help="Task text; default is state.next_task")
    step.add_argument("--timeout", type=int, default=900, help="Seconds to wait for opencode")

    dispatch = sub.add_parser(
        "dispatch",
        help="Send one task to the victim soldier via commander/dispatch.py",
    )
    dispatch.add_argument("--task", default=None, help="Task text; default is state.next_task")
    dispatch.add_argument("--timeout", type=int, default=125, help="Seconds to wait for dispatch.py")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    state_path = Path(args.state_file).expanduser() if args.state_file else default_state_path()
    if args.command == "show":
        return cmd_show(state_path)
    if args.command == "step":
        return cmd_step(
            state_path=state_path,
            task_override=args.task,
            mode="local",
            timeout_seconds=args.timeout,
        )
    if args.command == "dispatch":
        return cmd_step(
            state_path=state_path,
            task_override=args.task,
            mode="dispatch",
            timeout_seconds=args.timeout,
        )
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
