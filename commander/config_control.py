"""Set LLM provider/model and the required API key, then fan them out to soldiers."""

from __future__ import annotations

import argparse
import json
import sys

from common.llm_catalog import resolve_config_selection, save_enabled_selection
from common.user_env import set_user_env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commander config",
        description=(
            "Set the LLM provider API key (required) and optionally the provider/model, "
            "then push the selection to soldiers"
        ),
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="API key for the selected provider (never stored in JSON)",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        help="provider name from llm.json (default: the enable=true entry)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id for OpenCode --model provider/model (default: that provider's models in llm.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    api_key = (args.api_key or "").strip()
    if not api_key:
        print("error: --api-key is empty", file=sys.stderr)
        return 1
    if args.llm_provider is not None and not str(args.llm_provider).strip():
        print("error: --llm-provider is empty", file=sys.stderr)
        return 1
    if args.model is not None and not str(args.model).strip():
        print("error: --model is empty", file=sys.stderr)
        return 1
    try:
        name, record, model, _persist = resolve_config_selection(args.llm_provider, args.model)
        record = save_enabled_selection(name, model)
        set_user_env(record.env, api_key)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"local: set user environment {record.env} for provider {name} model {model}",
        flush=True,
    )

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
        resp = send_llm_config(host, port, name, api_key, model, timeout=timeout)
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
            if _looks_like_old_soldier(resp):
                print(
                    "  soldier is too old for llm_config; update the code and restart soldier listen",
                    flush=True,
                )
    if failed:
        print(f"failed: {failed}/{len(roles)} soldiers", file=sys.stderr)
        return 1
    return 0


def _looks_like_old_soldier(resp: dict) -> bool:
    extra = str(resp.get("error") or "")
    return "missing or invalid task_ref" in extra.lower()


if __name__ == "__main__":
    raise SystemExit(main())
