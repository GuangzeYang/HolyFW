"""Console entry for the ``commander`` command after ``pip install .``."""

from __future__ import annotations

import argparse
import sys
from typing import Callable


_SUBCOMMANDS = frozenset({"generate", "dispatch", "victim", "schedule", "breaker", "build"})


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in _SUBCOMMANDS:
        raise SystemExit(_run_subcommand(args[0], args[1:]))
    if any(item in {"-h", "--help"} for item in args):
        raise SystemExit(_print_root_help())
    from commander.commander import main as serve_main

    serve_main(args)


def _run_subcommand(name: str, argv: list[str]) -> int:
    runners: dict[str, Callable[[list[str]], int]] = {
        "generate": _run_generate,
        "dispatch": _run_dispatch,
        "victim": _run_victim,
        "schedule": _run_schedule,
        "breaker": _run_breaker,
        "build": _run_build,
    }
    return runners[name](argv)


def _print_root_help() -> int:
    from commander.commander import build_parser

    parser = build_parser()
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = (
        "subcommands:\n"
        "  generate   Generate today's office-role task file\n"
        "  dispatch   Send one task to a soldier\n"
        "  victim     On-demand victim campaign (step/show/dispatch)\n"
        "  schedule   Sample or plot the arrival-time model\n"
        "  breaker    Inspect or reset role circuit breakers\n"
        "  build      Write DeepSeek provider env-key config into ~/.config/opencode\n"
        "             (add --test to verify OpenCode load and the DeepSeek provider)\n"
    )
    parser.print_help()
    return 0


def _run_generate(argv: list[str]) -> int:
    from commander.generate_role_task import main as generate_main

    return int(generate_main(argv))


def _run_dispatch(argv: list[str]) -> int:
    from commander import dispatch as dispatch_mod

    return _run_with_sys_argv(dispatch_mod.main, argv)


def _run_victim(argv: list[str]) -> int:
    from commander.victim_campaign import main as victim_main

    return int(victim_main(argv))


def _run_schedule(argv: list[str]) -> int:
    from common.time_model import main as schedule_main

    return int(schedule_main(argv))


def _run_with_sys_argv(mod_main: Callable[[], int], argv: list[str]) -> int:
    old = sys.argv
    sys.argv = [old[0], *argv]
    try:
        return int(mod_main())
    finally:
        sys.argv = old


def _run_breaker(argv: list[str]) -> int:
    from commander import breaker_control

    return _run_with_sys_argv(breaker_control.main, argv)


def _run_build(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="commander build",
        description="Write DeepSeek provider env-key config into ~/.config/opencode",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="after install, verify OpenCode can load and run a short provider smoke prompt",
    )
    args = parser.parse_args(argv)
    from commander.host_build import run_build

    if args.test:
        return int(run_build(run_test=True))
    return int(run_build())


if __name__ == "__main__":
    main()
