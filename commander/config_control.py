"""Set the enabled LLM provider's user-level API key and fan it out to soldiers."""

from __future__ import annotations

import argparse
import json
import sys

from common.llm_catalog import enabled_provider
from common.user_env import set_user_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commander config",
        description="Set the enabled LLM provider's user-level API key and push it to soldiers",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="API key for the currently enabled provider in llm.json (never stored in JSON)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = (args.api_key or "").strip()
    if not api_key:
        print("error: --api-key is empty", file=sys.stderr)
        return 1
    try:
        name, record = enabled_provider()
        set_user_env(record.env, api_key)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"local: set user environment {record.env} for provider {name}", flush=True)

    from commander.dispatch import send_llm_config
    from commander.runtime_config import (
        get_dispatch_config,
        get_paths_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
    from commander.target_config import load_all_roles, load_target_config

    try:
        runtime_config = load_runtime_config()
        dispatch_config = get_dispatch_config(runtime_config)
        paths_config = get_paths_config(runtime_config)
        target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
        roles = load_all_roles(target_ini_path)
        timeout = float(dispatch_config["soldier_timeout_seconds"])
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failed = 0
    for role in roles:
        try:
            host, port = load_target_config(target_ini_path, role)
        except (FileNotFoundError, ValueError) as exc:
            print(f"{role}: fail {exc}", flush=True)
            failed += 1
            continue
        resp = send_llm_config(host, port, name, api_key, timeout=timeout)
        ok = bool(resp.get("ok"))
        status = str(resp.get("status") or ("ok" if ok else "fail"))
        extra = resp.get("error")
        if ok:
            print(f"{role} ({host}:{port}): {status}", flush=True)
        else:
            failed += 1
            detail = f" {extra}" if extra else ""
            print(f"{role} ({host}:{port}): {status}{detail}", flush=True)
            print(json.dumps({k: v for k, v in resp.items() if k != "api_key"}, ensure_ascii=False), flush=True)
    if failed:
        print(f"failed: {failed}/{len(roles)} soldiers", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
