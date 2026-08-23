"""Console entry for the ``attacker`` command after ``pip install .``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from attacker.runtime import load_config, resolve_workspace, run_loop
from attacker.task_file import load_attacker_tasks, tasks_file_path


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
    sub = parser.add_subparsers(dest="cmd", help="subcommand")

    run_p = sub.add_parser("run", help="start the attacker scheduler (default)")
    run_p.add_argument("--date", default="", help="YYYY-MM-DD used for the task file (default: today)")
    run_p.add_argument("--seed", type=int, default=None, help="Override the NHPP seed")

    show_p = sub.add_parser("show", help="print today's attacker task JSON")
    show_p.add_argument("--date", default="", help="YYYY-MM-DD (default: today)")
    return parser


def _parse_day(raw: str) -> date:
    text = (raw or "").strip()
    if not text:
        return date.today()
    return date.fromisoformat(text)


def cmd_show(*, config_path: Path | None, day: date) -> int:
    config = load_config(config_path)
    workspace = resolve_workspace()
    data_dir_raw = str((config.get("paths") or {}).get("data_dir") or "role_task")
    data_dir = Path(data_dir_raw)
    if not data_dir.is_absolute():
        data_dir = workspace / data_dir
    path = tasks_file_path(data_dir, day)
    if not path.is_file():
        print(f"# no task file: {path}", file=sys.stderr)
        return 1
    tasks = load_attacker_tasks(path)
    print(json.dumps(tasks, ensure_ascii=False, indent=2))
    print(f"# task file: {path}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None or args.cmd == "run":
        day = _parse_day(getattr(args, "date", "") or "")
        return run_loop(
            config_path=args.config,
            day=day,
            seed=getattr(args, "seed", None),
        )
    if args.cmd == "show":
        return cmd_show(config_path=args.config, day=_parse_day(args.date))
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
