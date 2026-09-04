"""Console entry for the ``attacker`` command after ``pip install .``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from attacker.extract_pcap import add_extract_parser
from attacker.logging_setup import configure_attacker_logging
from attacker.runtime import load_config, resolve_logs_dir, resolve_workspace, run_loop
from attacker.task_file import load_attacker_tasks, tasks_file_path


def _parse_base_time_arg(value: str) -> int:
    try:
        hour = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("base_time must be an integer 0..23") from exc
    if not 0 <= hour <= 23:
        raise argparse.ArgumentTypeError("base_time must be an integer 0..23")
    return hour


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="HolyFW attacker: generate a day's time nodes, fill tasks in batches of 5, execute locally.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="attacker config.json path (default: attacker/config.json in the workspace)",
    )
    parser.add_argument(
        "--base-time",
        type=_parse_base_time_arg,
        default=None,
        help="Hour (0-23) when the generated 09:00 workday should start. Default from config (9).",
    )
    sub = parser.add_subparsers(dest="cmd", help="subcommand")

    sub.add_parser(
        "config",
        help="set LLM API key (required); optional --llm-provider / --model",
    )

    run_p = sub.add_parser("run", help="start the attacker scheduler (default; runs continuously across days)")
    run_p.add_argument("--date", default="", help="YYYY-MM-DD used for the task file (default: today, or yesterday if that shifted window is still open)")
    run_p.add_argument("--seed", type=int, default=None, help="Override the NHPP seed")
    run_p.add_argument(
        "--base-time",
        type=_parse_base_time_arg,
        default=argparse.SUPPRESS,
        help="Hour (0-23) when the generated 09:00 workday should start. Default from config (9).",
    )
    run_p.add_argument(
        "--forever",
        action="store_true",
        help="keep running across days and roll to the next active task day automatically (default)",
    )
    run_p.add_argument(
        "--once",
        action="store_true",
        help="run a single day and exit when that day's tasks are complete",
    )

    show_p = sub.add_parser("show", help="print today's attacker task JSON")
    show_p.add_argument("--date", default="", help="YYYY-MM-DD (default: today)")
    build_p = sub.add_parser("build", help="install attacker OpenCode skills into ~/.config/opencode")
    build_p.add_argument(
        "--test",
        action="store_true",
        help="after install, verify OpenCode load and run a representative prompt per skill and MCP",
    )
    breaker_p = sub.add_parser("breaker", help="reset today's attacker task file and/or APT state")
    breaker_sub = breaker_p.add_subparsers(dest="breaker_cmd", required=True)
    reset_p = breaker_sub.add_parser(
        "reset",
        help="delete today's task file; default --all also resets state.json and changes.json",
    )
    reset_p.add_argument(
        "--all",
        action="store_true",
        help="clear today's task file and reset state.json plus changes.json (default)",
    )
    reset_p.add_argument(
        "--task",
        action="store_true",
        help="only delete today's task file; leave state.json and changes.json",
    )
    reset_p.add_argument("--date", default="", help="YYYY-MM-DD (default: today)")
    add_extract_parser(sub)
    return parser


def _parse_day(raw: str) -> date | None:
    text = (raw or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def cmd_show(*, config_path: Path | None, day: date) -> int:
    config = load_config(config_path)
    workspace = resolve_workspace()
    data_dir_raw = str((config.get("paths") or {}).get("data_dir") or "role_task")
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    path = tasks_file_path(data_dir, day or date.today())
    if not path.is_file():
        print(f"# no task file: {path}", file=sys.stderr)
        return 1
    tasks = load_attacker_tasks(path)
    print(json.dumps(tasks, ensure_ascii=False, indent=2))
    print(f"# task file: {path}", flush=True)
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    from attacker.extract_pcap import run_extract

    try:
        config = load_config(args.config)
        payload = run_extract(args, config=config)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload.get("ok") else 1


def cmd_breaker(args: argparse.Namespace) -> int:
    if getattr(args, "breaker_cmd", None) != "reset":
        print("unknown breaker command", file=sys.stderr)
        return 2
    if bool(getattr(args, "all", False)) and bool(getattr(args, "task", False)):
        print("use only one of --all or --task", file=sys.stderr)
        return 2
    from attacker.breaker import MODE_ALL, MODE_TASK, reset_attacker

    mode = MODE_TASK if getattr(args, "task", False) else MODE_ALL
    try:
        payload = reset_attacker(
            mode=mode,
            day=_parse_day(getattr(args, "date", "") or "") or date.today(),
            config_path=args.config,
        )
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "config":
        from attacker.config_control import main as config_main

        return config_main(raw[1:])
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.cmd == "build":
        from attacker.host_build import run_build

        if getattr(args, "test", False):
            return run_build(run_test=True)
        return run_build()
    if args.cmd == "show":
        return cmd_show(config_path=args.config, day=_parse_day(args.date) or date.today())
    if args.cmd == "breaker":
        return cmd_breaker(args)
    if args.cmd == "extract":
        return cmd_extract(args)
    if args.cmd is None or args.cmd == "run":
        forever_flag = bool(getattr(args, "forever", False))
        once_flag = bool(getattr(args, "once", False))
        day = _parse_day(getattr(args, "date", "") or "")
        if forever_flag and once_flag:
            parser.error("use only one of --forever or --once")
        if forever_flag and day is not None:
            parser.error("--date cannot be combined with --forever")
        run_forever = not (once_flag or day is not None)
        try:
            loaded = load_config(args.config)
            workspace = resolve_workspace()
            logs_dir = resolve_logs_dir(loaded, workspace)
            log_file = configure_attacker_logging(logs_dir)
        except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
            print(exc, file=sys.stderr)
            return 1
        logging.getLogger("attacker").info("Attacker starting, logs: %s", log_file)
        logging.getLogger("attacker").info("Attacker workspace: %s", workspace)
        if run_forever:
            logging.getLogger("attacker").info("Continuous mode: rolling to the next active task day")
        return run_loop(
            config_path=args.config,
            day=day,
            seed=getattr(args, "seed", None),
            base_time=getattr(args, "base_time", None),
            run_forever=run_forever,
        )
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
