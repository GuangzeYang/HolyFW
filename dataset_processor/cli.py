"""CLI for offline dataset extract and Windows evtx-to-XML export.

The mixed SPAN pcap is never modified. ``export-evtx`` writes XML beside or under
``--out-dir`` so Linux extract can use ``--evtx *.xml`` without wevtutil.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dataset_processor.extract import run_extract
from dataset_processor.evtx_export import add_export_evtx_arguments, run_export_evtx


def add_extract_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evtx", type=Path, required=True, help="attacker Sysmon .evtx or wevtutil .xml")
    parser.add_argument(
        "--security-evtx",
        type=Path,
        default=None,
        help="optional attacker Security / security_logon .evtx; sliced by task time window",
    )
    parser.add_argument(
        "--dc-security-evtx",
        type=Path,
        default=None,
        help="optional DC Security / security_logon .evtx; sliced by task time window",
    )
    parser.add_argument("--pcap", type=Path, required=True, help="domain SPAN mixed .pcap / .pcapng (read-only)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="directory for new pcapng, evtx slices, and tuples JSON (never the source pcap path)",
    )
    parser.add_argument(
        "--date",
        default="",
        help="YYYY-MM-DD; scan that day's task transcripts and write one pcap/evtx set per technique",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        default=None,
        help="directory of {task_id}.md transcripts (default: --out-dir if it contains md files)",
    )
    parser.add_argument(
        "--lab-net",
        action="append",
        default=None,
        dest="lab_nets",
        help="lab CIDR (repeatable). Default from config extract.lab_nets or 172.16.24.0/24",
    )
    parser.add_argument("--since", default="", help="include EID 3 at or after this ISO timestamp")
    parser.add_argument("--until", default="", help="include EID 3 at or before this ISO timestamp")
    parser.add_argument(
        "--task-md",
        type=Path,
        default=None,
        help="single attacker task transcript; writes malicious.pcapng (use --date for per-technique names)",
    )
    parser.add_argument(
        "--no-require-cmdline",
        action="store_true",
        help="do not require EID 1 CommandLine to contain impacket/bloodhound/nmap/kerbrute",
    )
    parser.add_argument(
        "--include-unlogged-scan",
        action="store_true",
        help="also keep ICMP/ARP/bare SYN from --attacker-ip in the time window",
    )
    parser.add_argument(
        "--attacker-ip",
        default="",
        help="attacker host IPv4 for unlogged scan packets (default: config extract.attacker_ip)",
    )
    parser.add_argument("--tshark", default="tshark", help="tshark executable")
    parser.add_argument("--mergecap", default="mergecap", help="mergecap executable (Wireshark)")
    parser.add_argument("--wevtutil", default="wevtutil", help="wevtutil executable")
    parser.add_argument("--tuples-name", default="tuples.json", help="JSON filename written under --out-dir")


def add_extract_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "extract",
        help="slice malicious and benign flows and per-task Sysmon/Security evtx from a day's captures",
    )
    add_extract_arguments(parser)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Slice malicious and benign flows from a mixed SPAN pcap. "
            "The source pcap is read-only; all outputs are new files under --out-dir. "
            "On Windows, preprocess evtx with: python -m dataset_processor export-evtx"
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="optional JSON with extract.lab_nets / extract.attacker_ip (e.g. attacker/config.json)",
    )
    add_extract_arguments(parser)
    return parser


def build_export_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dataset_processor export-evtx",
        description=(
            "Export file .evtx to UTF-8 XML via wevtutil (Windows). "
            "Linux extract then uses --evtx *.xml and does not need wevtutil. "
            "Does not read or write pcap."
        ),
    )
    add_export_evtx_arguments(parser)
    return parser


def load_optional_config(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"config is not a JSON object: {path}")
    return payload


def _print_payload(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0 if payload.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        if raw and raw[0] == "export-evtx":
            args = build_export_parser().parse_args(raw[1:])
            return _print_payload(run_export_evtx(args))
        if raw and raw[0] == "extract":
            raw = raw[1:]
        parser = build_parser()
        args = parser.parse_args(raw)
        config = load_optional_config(args.config)
        return _print_payload(run_extract(args, config=config))
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 1


def main_export(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    return main(["export-evtx", *raw])
