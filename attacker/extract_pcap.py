"""Slice malicious flows from a domain SPAN capture using attacker Sysmon events.

Load the attacker-only Sysmon config on the attack host by hand::

    Sysmon64.exe -c <path>\\attacker\\sysmonconfig.xml

Then, offline::

    attacker extract --evtx sysmon.evtx --pcap mixed.pcapng --out-dir out
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

DEFAULT_LAB_NETS = ("172.16.24.0/24",)
DEFAULT_CMDLINE_NEEDLES = ("impacket", "bloodhound", "nmap", "kerbrute")
PYTHON_IMAGES = frozenset({"python.exe", "pythonw.exe", "python3.exe", "py.exe"})
DEDICATED_IMAGES = frozenset({"nmap.exe", "kerbrute.exe", "winrs.exe", "schtasks.exe"})
ATTACK_IMAGES = PYTHON_IMAGES | DEDICATED_IMAGES
PCAP_TIME_SLACK_SECONDS = 2.0
TASK_WINDOW_SLACK_SECONDS = 5.0
DEFAULT_TASK_DURATION_SECONDS = 900.0
WEVTUTIL_TIMEOUT = 120
TSHARK_TIMEOUT = 600
EVENT_QUERY = "*[System[(EventID=1 or EventID=3)]]"
TSHARK_FIELDS = (
    "frame.number",
    "frame.time_epoch",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "udp.srcport",
    "udp.dstport",
    "tcp.stream",
    "udp.stream",
    "ip.proto",
    "icmp.type",
    "arp.src.proto_ipv4",
    "tcp.flags.syn",
    "tcp.flags.ack",
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
RunFn = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class ProcessCreate:
    utc: datetime
    process_guid: str
    image: str
    command_line: str


@dataclass(frozen=True)
class NetworkConnect:
    utc: datetime
    process_guid: str
    image: str
    initiated: bool
    protocol: str
    source_ip: str
    source_port: int
    dest_ip: str
    dest_port: int


@dataclass
class ExtractOptions:
    lab_nets: tuple[str, ...] = DEFAULT_LAB_NETS
    cmdline_needles: tuple[str, ...] = DEFAULT_CMDLINE_NEEDLES
    require_cmdline: bool = True
    initiated_only: bool = True
    since: datetime | None = None
    until: datetime | None = None
    time_slack_seconds: float = PCAP_TIME_SLACK_SECONDS
    include_unlogged_scan: bool = False
    attacker_ip: str = ""


@dataclass
class PacketRow:
    number: str = ""
    time_epoch: float = 0.0
    ip_src: str = ""
    ip_dst: str = ""
    tcp_sport: int | None = None
    tcp_dport: int | None = None
    udp_sport: int | None = None
    udp_dport: int | None = None
    tcp_stream: str = ""
    udp_stream: str = ""
    ip_proto: str = ""
    icmp_type: str = ""
    arp_src: str = ""
    syn: bool = False
    ack: bool = False


@dataclass
class SelectedConnect:
    connect: NetworkConnect
    command_line: str
    tcp_stream: str = ""
    udp_stream: str = ""


def image_basename(image: str) -> str:
    return Path(str(image).replace("\\", "/")).name.lower()


def normalize_guid(value: str) -> str:
    return str(value or "").strip().strip("{}").lower()


def parse_sysmon_utc(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if " " in text and "T" not in text[:12]:
        text = text.replace(" ", "T", 1)
    if "+" not in text[10:] and not text.endswith("Z"):
        text += "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    for child in list(element):
        if _local_tag(child.tag) == name:
            return child
    return None


def _event_data_map(event: ET.Element) -> dict[str, str]:
    block = _child(event, "EventData")
    if block is None:
        return {}
    values: dict[str, str] = {}
    for data in list(block):
        if _local_tag(data.tag) != "Data":
            continue
        key = str(data.attrib.get("Name") or "").strip()
        if key:
            values[key] = "" if data.text is None else str(data.text)
    return values


def _event_id(event: ET.Element) -> int | None:
    system = _child(event, "System")
    if system is None:
        return None
    node = _child(system, "EventID")
    if node is None or not (node.text or "").strip():
        return None
    try:
        return int(str(node.text).strip())
    except ValueError:
        return None


def _time_created(event: ET.Element) -> datetime | None:
    system = _child(event, "System")
    if system is None:
        return None
    node = _child(system, "TimeCreated")
    if node is None:
        return None
    return parse_sysmon_utc(node.attrib.get("SystemTime") or "")


def _parse_port(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_events_root(text: str) -> list[ET.Element]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    if stripped.startswith("\ufeff"):
        stripped = stripped.lstrip("\ufeff")
    try:
        root = ET.fromstring(stripped)
    except ET.ParseError:
        root = ET.fromstring("<Events>" + stripped + "</Events>")
    if _local_tag(root.tag) == "Event":
        return [root]
    return [child for child in list(root) if _local_tag(child.tag) == "Event"]


def parse_process_create(event: ET.Element) -> ProcessCreate | None:
    data = _event_data_map(event)
    utc = parse_sysmon_utc(data.get("UtcTime") or "") or _time_created(event)
    guid = normalize_guid(data.get("ProcessGuid") or "")
    image = str(data.get("Image") or "").strip()
    if utc is None or not guid:
        return None
    return ProcessCreate(
        utc=utc,
        process_guid=guid,
        image=image,
        command_line=str(data.get("CommandLine") or ""),
    )


def parse_network_connect(event: ET.Element) -> NetworkConnect | None:
    data = _event_data_map(event)
    utc = parse_sysmon_utc(data.get("UtcTime") or "") or _time_created(event)
    guid = normalize_guid(data.get("ProcessGuid") or "")
    source_ip = str(data.get("SourceIp") or "").strip()
    dest_ip = str(data.get("DestinationIp") or "").strip()
    source_port = _parse_port(data.get("SourcePort") or "")
    dest_port = _parse_port(data.get("DestinationPort") or "")
    protocol = str(data.get("Protocol") or "").strip().lower()
    if utc is None or not guid or not source_ip or not dest_ip:
        return None
    if source_port is None or dest_port is None:
        return None
    if protocol not in {"tcp", "udp"}:
        protocol = protocol or "tcp"
    initiated = str(data.get("Initiated") or "true").strip().lower() == "true"
    return NetworkConnect(
        utc=utc,
        process_guid=guid,
        image=str(data.get("Image") or "").strip(),
        initiated=initiated,
        protocol=protocol,
        source_ip=source_ip,
        source_port=source_port,
        dest_ip=dest_ip,
        dest_port=dest_port,
    )


def parse_sysmon_xml(text: str) -> tuple[list[ProcessCreate], list[NetworkConnect]]:
    creates: list[ProcessCreate] = []
    connects: list[NetworkConnect] = []
    for event in _parse_events_root(text):
        eid = _event_id(event)
        if eid == 1:
            parsed = parse_process_create(event)
            if parsed is not None:
                creates.append(parsed)
        elif eid == 3:
            parsed = parse_network_connect(event)
            if parsed is not None:
                connects.append(parsed)
    return creates, connects


def load_sysmon_events(
    path: Path,
    *,
    run_fn: RunFn = subprocess.run,
    wevtutil: str = "wevtutil",
) -> tuple[list[ProcessCreate], list[NetworkConnect]]:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return parse_sysmon_xml(path.read_text(encoding="utf-8"))
    resolved = shutil.which(wevtutil) or wevtutil
    completed = run_fn(
        [resolved, "qe", str(path), "/lf:true", "/f:xml", f"/q:{EVENT_QUERY}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=WEVTUTIL_TIMEOUT,
        check=False,
    )
    if int(completed.returncode) != 0:
        err = (completed.stderr or completed.stdout or "wevtutil failed").strip()
        raise RuntimeError(f"wevtutil failed on {path}: {err}")
    return parse_sysmon_xml(completed.stdout or "")


def parse_networks(raw: Sequence[str]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in raw:
        text = str(item).strip()
        if text:
            nets.append(ipaddress.ip_network(text, strict=False))
    return tuple(nets)


def ip_in_nets(ip: str, nets: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    text = str(ip or "").strip()
    if not text or text == "-" or not nets:
        return False
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def command_line_matches(command_line: str, needles: Sequence[str]) -> bool:
    text = str(command_line or "").lower()
    if not text:
        return False
    return any(str(needle).lower() in text for needle in needles if needle)


def lab_nets_from_config(config: dict[str, Any] | None) -> tuple[str, ...]:
    block = config.get("extract") if isinstance(config, dict) else None
    raw = block.get("lab_nets") if isinstance(block, dict) else None
    if isinstance(raw, list) and raw:
        return tuple(str(item) for item in raw if str(item).strip())
    return DEFAULT_LAB_NETS


def processes_by_guid(creates: Sequence[ProcessCreate]) -> dict[str, ProcessCreate]:
    by_guid: dict[str, ProcessCreate] = {}
    for event in creates:
        key = normalize_guid(event.process_guid)
        current = by_guid.get(key)
        if current is None or len(event.command_line) > len(current.command_line):
            by_guid[key] = event
    return by_guid


def _cmdline_allowed(
    image: str,
    command_line: str,
    *,
    require_cmdline: bool,
    needles: Sequence[str],
) -> bool:
    if not require_cmdline:
        return True
    if image_basename(image) in DEDICATED_IMAGES:
        return True
    return command_line_matches(command_line, needles)


def in_time_window(stamp: datetime, since: datetime | None, until: datetime | None) -> bool:
    if since is not None and stamp < since:
        return False
    if until is not None and stamp > until:
        return False
    return True


def connection_is_malicious(
    connect: NetworkConnect,
    creates_by_guid: dict[str, ProcessCreate],
    options: ExtractOptions,
) -> tuple[bool, str]:
    if options.initiated_only and not connect.initiated:
        return False, ""
    if not in_time_window(connect.utc, options.since, options.until):
        return False, ""
    nets = parse_networks(options.lab_nets)
    if nets and not ip_in_nets(connect.dest_ip, nets):
        return False, ""
    proc = creates_by_guid.get(normalize_guid(connect.process_guid))
    command_line = proc.command_line if proc is not None else ""
    image_ok = image_basename(connect.image) in ATTACK_IMAGES
    create_ok = False
    if proc is not None:
        create_ok = image_basename(proc.image) in ATTACK_IMAGES or command_line_matches(
            proc.command_line, options.cmdline_needles
        )
    if not (image_ok or create_ok):
        return False, command_line
    image_for_cmd = connect.image or (proc.image if proc is not None else "")
    if not _cmdline_allowed(
        image_for_cmd,
        command_line,
        require_cmdline=options.require_cmdline,
        needles=options.cmdline_needles,
    ):
        return False, command_line
    return True, command_line


def select_malicious_connects(
    creates: Sequence[ProcessCreate],
    connects: Sequence[NetworkConnect],
    options: ExtractOptions,
) -> list[SelectedConnect]:
    by_guid = processes_by_guid(creates)
    selected: list[SelectedConnect] = []
    for connect in connects:
        keep, command_line = connection_is_malicious(connect, by_guid, options)
        if keep:
            selected.append(SelectedConnect(connect=connect, command_line=command_line))
    return selected


def parse_task_window(
    text: str,
    *,
    slack_seconds: float = TASK_WINDOW_SLACK_SECONDS,
    default_duration_seconds: float = DEFAULT_TASK_DURATION_SECONDS,
) -> tuple[datetime, datetime]:
    match = _FRONTMATTER_RE.search(text or "")
    block = match.group(1) if match else (text or "")
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip().strip("'\"")
    started = parse_sysmon_utc(fields.get("started_at") or "")
    if started is None:
        raise ValueError("task transcript has no started_at")
    completed = parse_sysmon_utc(fields.get("completed_at") or "")
    slack = timedelta(seconds=float(slack_seconds))
    start = started - slack
    if completed is None:
        end = started + timedelta(seconds=float(default_duration_seconds)) + slack
    else:
        end = completed + slack
    return start, end


def _as_int(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_bool_flag(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    return text in {"1", "true", "set"}


def parse_tshark_fields(text: str) -> list[PacketRow]:
    rows: list[PacketRow] = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        padded = cols + [""] * (len(TSHARK_FIELDS) - len(cols))
        try:
            epoch = float(padded[1] or 0)
        except ValueError:
            epoch = 0.0
        rows.append(
            PacketRow(
                number=padded[0],
                time_epoch=epoch,
                ip_src=padded[2],
                ip_dst=padded[3],
                tcp_sport=_as_int(padded[4]),
                tcp_dport=_as_int(padded[5]),
                udp_sport=_as_int(padded[6]),
                udp_dport=_as_int(padded[7]),
                tcp_stream=padded[8].strip(),
                udp_stream=padded[9].strip(),
                ip_proto=padded[10].strip(),
                icmp_type=padded[11].strip(),
                arp_src=padded[12].strip(),
                syn=_as_bool_flag(padded[13]),
                ack=_as_bool_flag(padded[14]),
            )
        )
    return rows


def _tuple_match(
    connect: NetworkConnect,
    src: str,
    dst: str,
    sport: int | None,
    dport: int | None,
) -> bool:
    if sport is None or dport is None:
        return False
    forward = (
        src == connect.source_ip
        and dst == connect.dest_ip
        and sport == connect.source_port
        and dport == connect.dest_port
    )
    reverse = (
        src == connect.dest_ip
        and dst == connect.source_ip
        and sport == connect.dest_port
        and dport == connect.source_port
    )
    return forward or reverse


def match_streams(
    selected: Sequence[SelectedConnect],
    packets: Sequence[PacketRow],
    *,
    slack_seconds: float = PCAP_TIME_SLACK_SECONDS,
) -> list[SelectedConnect]:
    slack = float(slack_seconds)
    matched: list[SelectedConnect] = []
    for item in selected:
        connect = item.connect
        tcp_stream = ""
        udp_stream = ""
        for packet in packets:
            if abs(packet.time_epoch - connect.utc.timestamp()) > slack:
                continue
            if connect.protocol == "tcp" and _tuple_match(
                connect, packet.ip_src, packet.ip_dst, packet.tcp_sport, packet.tcp_dport
            ):
                tcp_stream = packet.tcp_stream
                break
            if connect.protocol == "udp" and _tuple_match(
                connect, packet.ip_src, packet.ip_dst, packet.udp_sport, packet.udp_dport
            ):
                udp_stream = packet.udp_stream
                break
        matched.append(
            SelectedConnect(
                connect=connect,
                command_line=item.command_line,
                tcp_stream=tcp_stream,
                udp_stream=udp_stream,
            )
        )
    return matched


def _stream_sort_key(value: str) -> tuple[int, str]:
    try:
        return (0, str(int(value)))
    except ValueError:
        return (1, value)


def build_display_filter(
    tcp_streams: Iterable[str],
    udp_streams: Iterable[str],
    *,
    include_unlogged_scan: bool = False,
    attacker_ip: str = "",
    scan_since_epoch: float | None = None,
    scan_until_epoch: float | None = None,
) -> str:
    parts: list[str] = []
    for stream in sorted({item for item in tcp_streams if item != ""}, key=_stream_sort_key):
        parts.append(f"tcp.stream eq {stream}")
    for stream in sorted({item for item in udp_streams if item != ""}, key=_stream_sort_key):
        parts.append(f"udp.stream eq {stream}")
    if (
        include_unlogged_scan
        and attacker_ip
        and scan_since_epoch is not None
        and scan_until_epoch is not None
    ):
        time_term = (
            f"frame.time_epoch >= {scan_since_epoch:.6f} && "
            f"frame.time_epoch <= {scan_until_epoch:.6f}"
        )
        scan_term = (
            f"({time_term}) && ip.src == {attacker_ip} && "
            f"(icmp || arp || (tcp.flags.syn == 1 && tcp.flags.ack == 0))"
        )
        parts.append(scan_term)
    if not parts:
        return "frame.number == 0"
    return " || ".join(f"({part})" if " && " in part else part for part in parts)


def selected_to_records(selected: Sequence[SelectedConnect]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in selected:
        connect = item.connect
        records.append(
            {
                "utc_time": connect.utc.isoformat(),
                "protocol": connect.protocol,
                "source_ip": connect.source_ip,
                "source_port": connect.source_port,
                "dest_ip": connect.dest_ip,
                "dest_port": connect.dest_port,
                "process_guid": connect.process_guid,
                "image": connect.image,
                "command_line": item.command_line,
                "initiated": connect.initiated,
                "tcp_stream": item.tcp_stream,
                "udp_stream": item.udp_stream,
            }
        )
    return records


def dump_tshark_fields(
    pcap: Path,
    *,
    tshark: str = "tshark",
    run_fn: RunFn = subprocess.run,
) -> str:
    resolved = shutil.which(tshark) or tshark
    args = [resolved, "-r", str(pcap), "-T", "fields", "-E", "separator=\t", "-E", "occurrence=f"]
    for name in TSHARK_FIELDS:
        args.extend(["-e", name])
    completed = run_fn(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TSHARK_TIMEOUT,
        check=False,
    )
    if int(completed.returncode) != 0:
        err = (completed.stderr or completed.stdout or "tshark failed").strip()
        raise RuntimeError(f"tshark field dump failed: {err}")
    return completed.stdout or ""


def write_filtered_pcap(
    pcap: Path,
    display_filter: str,
    dest: Path,
    *,
    tshark: str = "tshark",
    run_fn: RunFn = subprocess.run,
) -> None:
    resolved = shutil.which(tshark) or tshark
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = run_fn(
        [resolved, "-r", str(pcap), "-Y", display_filter, "-w", str(dest)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TSHARK_TIMEOUT,
        check=False,
    )
    if int(completed.returncode) != 0:
        err = (completed.stderr or completed.stdout or "tshark failed").strip()
        raise RuntimeError(f"tshark write {dest.name} failed: {err}")


def extract_from_sources(
    *,
    creates: Sequence[ProcessCreate],
    connects: Sequence[NetworkConnect],
    packets: Sequence[PacketRow],
    options: ExtractOptions,
) -> tuple[list[SelectedConnect], str]:
    selected = select_malicious_connects(creates, connects, options)
    matched = match_streams(selected, packets, slack_seconds=options.time_slack_seconds)
    scan_since = options.since.timestamp() if options.since is not None else None
    scan_until = options.until.timestamp() if options.until is not None else None
    display_filter = build_display_filter(
        (item.tcp_stream for item in matched),
        (item.udp_stream for item in matched),
        include_unlogged_scan=options.include_unlogged_scan,
        attacker_ip=options.attacker_ip,
        scan_since_epoch=scan_since,
        scan_until_epoch=scan_until,
    )
    return matched, display_filter


def add_extract_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "extract",
        help="slice malicious flows from a SPAN pcap using attacker Sysmon evtx",
    )
    parser.add_argument("--evtx", type=Path, required=True, help="attacker Sysmon .evtx or wevtutil .xml")
    parser.add_argument("--pcap", type=Path, required=True, help="domain SPAN .pcap / .pcapng")
    parser.add_argument("--out-dir", type=Path, required=True, help="directory for malicious/benign pcapng and tuples JSON")
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
        help="attacker task transcript; uses started_at/completed_at plus slack as the time window",
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
    parser.add_argument("--attacker-ip", default="", help="attacker host IPv4 for --include-unlogged-scan")
    parser.add_argument("--tshark", default="tshark", help="tshark executable")
    parser.add_argument("--wevtutil", default="wevtutil", help="wevtutil executable")
    parser.add_argument("--tuples-name", default="tuples.json", help="JSON filename written under --out-dir")
    return parser


def options_from_args(args: argparse.Namespace, config: dict[str, Any] | None) -> ExtractOptions:
    since = parse_sysmon_utc(str(getattr(args, "since", "") or ""))
    until = parse_sysmon_utc(str(getattr(args, "until", "") or ""))
    task_md = getattr(args, "task_md", None)
    if task_md is not None:
        start, end = parse_task_window(Path(task_md).read_text(encoding="utf-8"))
        since = start if since is None else since
        until = end if until is None else until
    lab = tuple(args.lab_nets) if getattr(args, "lab_nets", None) else lab_nets_from_config(config)
    include_scan = bool(getattr(args, "include_unlogged_scan", False))
    attacker_ip = str(getattr(args, "attacker_ip", "") or "").strip()
    if include_scan and not attacker_ip:
        raise ValueError("--include-unlogged-scan requires --attacker-ip")
    return ExtractOptions(
        lab_nets=lab,
        require_cmdline=not bool(getattr(args, "no_require_cmdline", False)),
        since=since,
        until=until,
        include_unlogged_scan=include_scan,
        attacker_ip=attacker_ip,
    )


def run_extract(
    args: argparse.Namespace,
    *,
    config: dict[str, Any] | None = None,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    options = options_from_args(args, config)
    creates, connects = load_sysmon_events(
        Path(args.evtx), run_fn=run_fn, wevtutil=str(getattr(args, "wevtutil", "wevtutil"))
    )
    field_text = dump_tshark_fields(Path(args.pcap), tshark=str(args.tshark), run_fn=run_fn)
    packets = parse_tshark_fields(field_text)
    matched, display_filter = extract_from_sources(
        creates=creates, connects=connects, packets=packets, options=options
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    malicious_path = out_dir / "malicious.pcapng"
    benign_path = out_dir / "benign.pcapng"
    tuples_path = out_dir / str(getattr(args, "tuples_name", "tuples.json") or "tuples.json")
    tshark = str(args.tshark)
    write_filtered_pcap(Path(args.pcap), display_filter, malicious_path, tshark=tshark, run_fn=run_fn)
    benign_filter = f"not ({display_filter})"
    write_filtered_pcap(Path(args.pcap), benign_filter, benign_path, tshark=tshark, run_fn=run_fn)
    records = selected_to_records(matched)
    tuples_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "connects": len(records),
        "display_filter": display_filter,
        "malicious": str(malicious_path),
        "benign": str(benign_path),
        "tuples": str(tuples_path),
    }
