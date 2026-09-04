"""Set LLM provider/model and the required API key from llm.json (no soldier fan-out)."""

from __future__ import annotations

import argparse
import sys

from common.llm_config_cli import (
    add_llm_config_arguments,
    apply_local_llm_config,
    format_local_config_line,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attacker config",
        description=(
            "Set the LLM provider API key (required) and optionally the provider/model "
            "in workspace llm.json"
        ),
    )
    add_llm_config_arguments(parser)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
