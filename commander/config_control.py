"""Set LLM provider/model and the required API key on this host.

Pass ``--sync`` to also fan the catalog and key out to soldiers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common.llm_config_cli import (
    add_llm_config_arguments,
    apply_local_llm_config,
    format_local_config_line,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commander config",
        description=(
            "Set the LLM provider API key (required) and optionally the provider/model "
            "on this host. Pass --sync to also push workspace llm.json and the API key "
            "to soldiers"
        ),
    )
    add_llm_config_arguments(parser)
    parser.add_argument(
        "--sync",
        action="store_true",
        default=False,
        help="also push workspace llm.json and the API key to every soldier",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        name, record, model, catalog = apply_local_llm_config(
            api_key=args.api_key,
            llm_provider=args.llm_provider,
            model=args.model,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_local_config_line(catalog, name, model, record.env), flush=True)
    if not args.sync:
        return 0
    return _sync_soldiers(catalog, name, args.api_key.strip(), model)


def _sync_soldiers(catalog: Path, name: str, api_key: str, model: str) -> int:
    try:
        llm_json_text = catalog.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not llm_json_text.strip():
        print(f"error: {catalog} is empty", file=sys.stderr)
        return 1

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
        resp = send_llm_config(
            host,
            port,
            name,
            api_key,
            model,
            timeout=timeout,
            llm_json=llm_json_text,
        )
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
