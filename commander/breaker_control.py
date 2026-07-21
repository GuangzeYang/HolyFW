#!/usr/bin/env python3
"""Inspect or manually reset persistent per-role circuit breakers."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from failure_governor import EmailAlerter, RoleFailureGovernor
    from runtime_config import (
        get_email_alert_config,
        get_failure_policy_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
except ImportError:
    from commander.failure_governor import EmailAlerter, RoleFailureGovernor
    from commander.runtime_config import (
        get_email_alert_config,
        get_failure_policy_config,
        load_runtime_config,
        resolve_config_relative_path,
    )


def _build_governor() -> RoleFailureGovernor:
    runtime = load_runtime_config()
    policy = get_failure_policy_config(runtime)
    alerter = EmailAlerter(get_email_alert_config(runtime))
    return RoleFailureGovernor(
        resolve_config_relative_path(policy["state_file"]),
        cooldown_seconds=policy["cooldown_seconds"],
        max_consecutive_failures=policy["max_consecutive_failures"],
        email_alerter=alerter,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or reset HolyFW role circuit breakers")
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="show breaker state")
    status_parser.add_argument("--date", default=None, help="date YYYY-MM-DD; default today")

    reset_parser = sub.add_parser("reset", help="manually reset one role")
    reset_parser.add_argument("--role", required=True)
    reset_parser.add_argument("--date", default=None, help="date YYYY-MM-DD; default today")

    args = parser.parse_args()
    governor = _build_governor()
    if args.command == "status":
        print(json.dumps(governor.status(args.date), ensure_ascii=False, indent=2))
        return 0
    reset = governor.reset(args.role.lower(), args.date)
    print(json.dumps({"ok": reset, "role": args.role.lower()}, ensure_ascii=False))
    return 0 if reset else 1


if __name__ == "__main__":
    raise SystemExit(main())
