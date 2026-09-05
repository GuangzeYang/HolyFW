#!/usr/bin/env python3
"""Smoke-test the enabled llm.json provider the same way commander generates tasks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.agent_request_abc import AgentRequestError, AgentTimeoutError
from common.deepseek_client import DeepSeekAgentClient, DeepSeekConfig, _normalize_endpoint
from common.llm_catalog import enabled_provider, is_proxy_provider, llm_json_path
from common.user_env import get_user_env

SMOKE_PROMPT = "Reply with exactly: ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="POST a short prompt to the enable=true llm.json entry. Never prints the API key."
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="override the catalog env key for this run only (never stored)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=32,
        help="max_tokens for the smoke prompt (default: 32)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalog = llm_json_path()
    name, record = enabled_provider(catalog)
    api_key = (args.api_key or "").strip() or get_user_env(record.env)
    if not api_key:
        print(
            f"FAIL: {record.env} is empty. Pass --api-key or run commander config --api-key first.",
            file=sys.stderr,
        )
        return 1
    endpoint = _normalize_endpoint(record.base_url, provider_name=name)
    print(f"catalog:  {catalog}")
    print(f"provider: {name}  proxy={is_proxy_provider(name)}")
    print(f"model:    {record.models}")
    print(f"base_url: {record.base_url}")
    print(f"endpoint: {endpoint}")
    print(f"env:      {record.env}  (set, not printed)")
    client = DeepSeekAgentClient(
        DeepSeekConfig(
            api_base_url=record.base_url,
            api_key=api_key,
            model=record.models,
            request_timeout_seconds=args.timeout,
            max_tokens=args.max_tokens,
            provider_name=name,
        )
    )
    try:
        response = client.request_completion(SMOKE_PROMPT)
    except AgentTimeoutError as exc:
        print(f"FAIL: timeout/connect {exc}", file=sys.stderr)
        return 1
    except AgentRequestError as exc:
        detail = (exc.response_text or "").strip().replace("\n", " ")
        if len(detail) > 300:
            detail = detail[:300] + "..."
        print(f"FAIL: HTTP {exc.status_code} {exc}", file=sys.stderr)
        if detail:
            print(f"body: {detail}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    text = (response.response_text or "").strip().replace("\n", " ")
    if len(text) > 200:
        text = text[:200] + "..."
    print(f"status:   {response.status_code}")
    print(f"elapsed:  {response.elapsed_seconds:.2f}s")
    print(f"reply:    {text or '(empty)'}")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
