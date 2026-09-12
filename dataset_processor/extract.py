"""Slice malicious and benign flows and per-task evtx windows from a day's captures.

The mixed SPAN pcap given as ``--pcap`` is read-only. Outputs are new files under
``--out-dir``.

Offline::

    python -m dataset_processor --evtx sysmon.evtx --pcap mixed.pcapng --out-dir out
    python -m dataset_processor --date 2026-09-06 --evtx sysmon.evtx --pcap mixed.pcapng
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from dataset_processor.paths import list_task_transcripts, technique_pcap_stem
from dataset_processor.tcp_streams import (
    benign_filter_parts,
    collect_scan_tcp_streams,
    complete_tcp_stream_ids,
    stream_sort_key,
)

DEFAULT_LAB_NETS = ("172.16.24.0/24",)
DEFAULT_CMDLINE_NEEDLES = ("impacket", "bloodhound", "nmap", "kerbrute")
PYTHON_IMAGES = frozenset({"python.exe", "pythonw.exe", "python3.exe", "py.exe"})
DEDICATED_IMAGES = frozenset({"nmap.exe", "kerbrute.exe", "winrs.exe", "schtasks.exe"})
ATTACK_IMAGES = PYTHON_IMAGES | DEDICATED_IMAGES
PCAP_TIME_SLACK_SECONDS = 2.0
TASK_WINDOW_SLACK_SECONDS = 5.0
DEFAULT_TASK_DURATION_SECONDS = 900.0
WEVTUTIL_TIMEOUT = 600
EVTX_SLICE_TIMEOUT = 600
TSHARK_TIMEOUT = 7200
MERGECAP_TIMEOUT = 3600
EVENT_QUERY = "*[System[(EventID=1 or EventID=3)]]"
EMPTY_DISPLAY_FILTER = "frame.number == 0"
_EMPTY_EXPORT_MARKERS = (
    "no events were found",
    "no event was found",
    "the specified query is invalid",
)
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
    "tcp.flags.fin",
    "tcp.flags.reset",
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.DOTALL)
_TECHNIQUE_RE = re.compile(
    r"execute\s+([a-z][a-z0-9]*(?:\.[a-z0-9-]+)+)",
    re.IGNORECASE,
)
SCAN_TECHNIQUES = frozenset({"discovery.host-scan", "discovery.port-scan"})
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


@dataclass(frozen=True)
class ExcludeFlow:
    peer_ip: str
    image_contains: str


DEFAULT_EXCLUDE_FLOWS = (ExcludeFlow(peer_ip="172.16.24.42", image_contains="avp"),)


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
    exclude_flows: tuple[ExcludeFlow, ...] = DEFAULT_EXCLUDE_FLOWS


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
    fin: bool = False
    rst: bool = False


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


def attacker_ip_from_config(config: dict[str, Any] | None) -> str:
    block = config.get("extract") if isinstance(config, dict) else None
    raw = block.get("attacker_ip") if isinstance(block, dict) else None
    return str(raw or "").strip()


def _parse_exclude_flow(item: Any) -> ExcludeFlow | None:
    if not isinstance(item, dict):
        return None
    peer_ip = str(item.get("peer_ip") or "").strip()
    needle = str(item.get("image_contains") or "").strip()
    if not peer_ip or not needle:
        return None
    return ExcludeFlow(peer_ip=peer_ip, image_contains=needle)


def exclude_flows_from_config(config: dict[str, Any] | None) -> tuple[ExcludeFlow, ...]:
    block = config.get("extract") if isinstance(config, dict) else None
    raw = block.get("exclude_flows") if isinstance(block, dict) else None
    if not isinstance(raw, list) or not raw:
        return DEFAULT_EXCLUDE_FLOWS
    flows = tuple(parsed for parsed in (_parse_exclude_flow(item) for item in raw) if parsed is not None)
    return flows if flows else DEFAULT_EXCLUDE_FLOWS


def peer_ips_from_exclude_flows(rules: Sequence[ExcludeFlow]) -> tuple[str, ...]:
    seen: list[str] = []
    for rule in rules:
        peer = str(rule.peer_ip or "").strip()
        if peer and peer not in seen:
            seen.append(peer)
    return tuple(seen)


def _image_has_needle(image: str, needle: str) -> bool:
    text = str(needle or "").strip().lower()
    if not text:
        return False
    return text in image_basename(image)


def flow_is_excluded(
    connect: NetworkConnect,
    rules: Sequence[ExcludeFlow],
    *,
    extra_image: str = "",
) -> bool:
    images = [connect.image]
    if extra_image:
        images.append(extra_image)
    for rule in rules:
        peer = str(rule.peer_ip or "").strip()
        if not peer:
            continue
        if connect.source_ip != peer and connect.dest_ip != peer:
            continue
        if any(_image_has_needle(image, rule.image_contains) for image in images):
            return True
    return False


def parse_technique_id(text: str) -> str:
    match = _TECHNIQUE_RE.search(str(text or ""))
    return match.group(1).lower() if match else ""


def parse_frontmatter_fields(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.search(text or "")
    block = match.group(1) if match else (text or "")
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip().strip("'\"")
    return fields


def resolve_day_logs_dir(config: dict[str, Any] | None, day: date) -> Path:
    from attacker.runtime import resolve_logs_dir, resolve_workspace

    return resolve_logs_dir(config or {}, resolve_workspace()) / day.isoformat()


def auto_unlogged_scan(technique: str, attacker_ip: str, explicit: bool) -> bool:
    if explicit:
        return True
    return bool(attacker_ip) and technique in SCAN_TECHNIQUES


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
    extra_image = proc.image if proc is not None else ""
    if flow_is_excluded(connect, options.exclude_flows, extra_image=extra_image):
        return False, command_line
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
    fields = parse_frontmatter_fields(text)
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


def utc_query_bound(stamp: datetime) -> str:
    aware = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_window_query(since: datetime, until: datetime) -> str:
    start = utc_query_bound(since)
    end = utc_query_bound(until)
    return f"*[System[TimeCreated[@SystemTime>='{start}' and @SystemTime<='{end}']]]"


def is_evtx_source(path: Path | None) -> bool:
    if path is None:
        return False
    return path.suffix.lower() in {".evtx", ".evt"}


def is_empty_evtx_export(returncode: int, stdout: str = "", stderr: str = "") -> bool:
    if returncode == 0:
        return False
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in _EMPTY_EXPORT_MARKERS)


def infer_attacker_ip(
    connects: Sequence[NetworkConnect],
    lab_nets: Sequence[str],
) -> str:
    nets = parse_networks(lab_nets)
    counts: Counter[str] = Counter()
    for connect in connects:
        if not connect.initiated:
            continue
        if image_basename(connect.image) not in ATTACK_IMAGES:
            continue
        if nets and not ip_in_nets(connect.source_ip, nets):
            continue
        ip = str(connect.source_ip or "").strip()
        if ip:
            counts[ip] += 1
    if not counts:
        return ""
    return counts.most_common(1)[0][0]


def export_evtx_window(
    source: Path,
    dest: Path,
    since: datetime,
    until: datetime,
    *,
    run_fn: RunFn = subprocess.run,
    wevtutil: str = "wevtutil",
    timeout: float = EVTX_SLICE_TIMEOUT,
) -> Path | None:
    """Copy events in ``[since, until]`` from a file evtx. None if the window is empty."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    resolved = shutil.which(wevtutil) or wevtutil
    query = build_window_query(since, until)
    completed = run_fn(
        [resolved, "epl", str(source), str(dest), "/lf:true", f"/q:{query}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    code = int(completed.returncode)
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if code != 0:
        if is_empty_evtx_export(code, stdout, stderr):
            if dest.exists():
                dest.unlink()
            return None
        detail = (stderr or stdout or "wevtutil epl failed").strip()
        raise RuntimeError(f"wevtutil epl failed on {source} -> {dest.name}: {detail}")
    if not dest.exists():
        return None
    return dest


def export_task_evtx_files(
    *,
    stem: str,
    out_dir: Path,
    since: datetime,
    until: datetime,
    sysmon_src: Path | None,
    security_src: Path | None,
    dc_security_src: Path | None,
    run_fn: RunFn,
    wevtutil: str,
) -> tuple[dict[str, str | None], list[str]]:
    paths: dict[str, str | None] = {"sysmon": None, "security": None, "dc_security": None}
    warnings: list[str] = []

    def _slice(src: Path | None, suffix: str, key: str) -> None:
        if src is None:
            return
        if not is_evtx_source(src):
            warnings.append(f"{key}: source is not evtx")
            return
        dest = out_dir / f"{stem}_{suffix}.evtx"
        written = export_evtx_window(
            src,
            dest,
            since,
            until,
            run_fn=run_fn,
            wevtutil=wevtutil,
        )
        if written is None:
            warnings.append(f"{key}: no events were found")
            return
        paths[key] = str(written)

    _slice(sysmon_src, "Sysmon", "sysmon")
    _slice(security_src, "Security", "security")
    _slice(dc_security_src, "DC_Security", "dc_security")
    return paths, warnings


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
                fin=_as_bool_flag(padded[15]),
                rst=_as_bool_flag(padded[16]),
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


def same_pcap_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def assert_output_not_source(source: Path, dest: Path) -> None:
    if same_pcap_path(source, dest):
        raise ValueError(f"refusing to overwrite source pcap: {source}")


def build_display_filter(
    tcp_streams: Iterable[str],
    udp_streams: Iterable[str],
    *,
    include_unlogged_scan: bool = False,
    attacker_ip: str = "",
    scan_since_epoch: float | None = None,
    scan_until_epoch: float | None = None,
    exclude_peer_ips: Iterable[str] = (),
) -> str:
    parts: list[str] = []
    for stream in sorted({item for item in tcp_streams if item != ""}, key=stream_sort_key):
        parts.append(f"tcp.stream eq {stream}")
    for stream in sorted({item for item in udp_streams if item != ""}, key=stream_sort_key):
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
        dropped = " && ".join(
            f"ip.addr != {ip}" for ip in sorted({str(item).strip() for item in exclude_peer_ips if str(item).strip()})
        )
        if dropped:
            scan_term = f"{scan_term} && {dropped}"
        parts.append(scan_term)
    if not parts:
        return EMPTY_DISPLAY_FILTER
    return " || ".join(f"({part})" if " && " in part else part for part in parts)


def is_empty_display_filter(display_filter: str) -> bool:
    return str(display_filter or "").strip() == EMPTY_DISPLAY_FILTER


def maybe_write_filtered_pcap(
    pcap: Path,
    display_filter: str,
    dest: Path,
    *,
    tshark: str,
    run_fn: RunFn,
) -> Path | None:
    """Copy matching packets to dest. Skip tshark when the filter matches nothing."""
    if is_empty_display_filter(display_filter):
        if dest.exists() and not same_pcap_path(pcap, dest):
            dest.unlink()
        return None
    write_filtered_pcap(pcap, display_filter, dest, tshark=tshark, run_fn=run_fn)
    return dest


def selected_to_records(
    selected: Sequence[SelectedConnect],
    *,
    task_id: str = "",
    technique: str = "",
    complete_tcp: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    complete = {item for item in (complete_tcp or ()) if item}
    records: list[dict[str, Any]] = []
    for item in selected:
        connect = item.connect
        if connect.protocol == "tcp":
            tcp_complete = bool(item.tcp_stream) and item.tcp_stream in complete
        else:
            tcp_complete = True
        row: dict[str, Any] = {
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
            "tcp_complete": tcp_complete,
        }
        if task_id:
            row["task_id"] = task_id
        if technique:
            row["technique"] = technique
        records.append(row)
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
    assert_output_not_source(pcap, dest)
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


def merge_pcaps(
    inputs: Sequence[Path],
    dest: Path,
    *,
    source_pcap: Path,
    mergecap: str = "mergecap",
    run_fn: RunFn = subprocess.run,
) -> None:
    assert_output_not_source(source_pcap, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    resolved = shutil.which(mergecap) or mergecap
    args = [resolved, "-w", str(dest), *[str(path) for path in inputs]]
    completed = run_fn(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=MERGECAP_TIMEOUT,
        check=False,
    )
    if int(completed.returncode) != 0:
        err = (completed.stderr or completed.stdout or "mergecap failed").strip()
        raise RuntimeError(f"mergecap write {dest.name} failed: {err}")


def write_pcap_from_filters(
    pcap: Path,
    filters: Sequence[str],
    dest: Path,
    *,
    tshark: str = "tshark",
    mergecap: str = "mergecap",
    run_fn: RunFn = subprocess.run,
) -> None:
    assert_output_not_source(pcap, dest)
    cleaned = [str(item).strip() for item in filters if str(item).strip()]
    if not cleaned:
        cleaned = ["frame.number == 0"]
    if len(cleaned) == 1:
        write_filtered_pcap(pcap, cleaned[0], dest, tshark=tshark, run_fn=run_fn)
        return
    chunks: list[Path] = []
    try:
        for index, filt in enumerate(cleaned):
            chunk = dest.parent / f".{dest.stem}.part{index:04d}{dest.suffix}"
            assert_output_not_source(pcap, chunk)
            write_filtered_pcap(pcap, filt, chunk, tshark=tshark, run_fn=run_fn)
            chunks.append(chunk)
        merge_pcaps(chunks, dest, source_pcap=pcap, mergecap=mergecap, run_fn=run_fn)
    finally:
        for chunk in chunks:
            if chunk.exists() and not same_pcap_path(chunk, pcap) and not same_pcap_path(chunk, dest):
                chunk.unlink()


def extract_from_sources(
    *,
    creates: Sequence[ProcessCreate],
    connects: Sequence[NetworkConnect],
    packets: Sequence[PacketRow],
    options: ExtractOptions,
) -> tuple[list[SelectedConnect], str]:
    selected = select_malicious_connects(creates, connects, options)
    matched = match_streams(selected, packets, slack_seconds=options.time_slack_seconds)
    complete_ids = complete_tcp_stream_ids(packets)
    tcp_for_filter = [
        item.tcp_stream
        for item in matched
        if item.tcp_stream and item.tcp_stream in complete_ids
    ]
    scan_since = options.since.timestamp() if options.since is not None else None
    scan_until = options.until.timestamp() if options.until is not None else None
    display_filter = build_display_filter(
        tcp_for_filter,
        (item.udp_stream for item in matched),
        include_unlogged_scan=options.include_unlogged_scan,
        attacker_ip=options.attacker_ip,
        scan_since_epoch=scan_since,
        scan_until_epoch=scan_until,
        exclude_peer_ips=peer_ips_from_exclude_flows(options.exclude_flows),
    )
    return matched, display_filter


def resolve_extract_out_dir(
    args: argparse.Namespace,
    config: dict[str, Any] | None,
    day: date | None,
) -> Path:
    raw = getattr(args, "out_dir", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    if day is not None:
        return resolve_day_logs_dir(config, day)
    raise ValueError("--out-dir is required unless --date is set")


def resolve_transcripts_dir(
    args: argparse.Namespace,
    config: dict[str, Any] | None,
    day: date,
    out_dir: Path,
) -> Path:
    explicit = _optional_path_arg(args, "transcripts_dir")
    if explicit is not None:
        return explicit
    if list_task_transcripts(out_dir):
        return out_dir
    return resolve_day_logs_dir(config, day)


def options_from_args(args: argparse.Namespace, config: dict[str, Any] | None) -> ExtractOptions:
    since = parse_sysmon_utc(str(getattr(args, "since", "") or ""))
    until = parse_sysmon_utc(str(getattr(args, "until", "") or ""))
    task_md = getattr(args, "task_md", None)
    if isinstance(task_md, (str, Path)) and not str(getattr(args, "date", "") or "").strip():
        start, end = parse_task_window(Path(task_md).read_text(encoding="utf-8"))
        since = start if since is None else since
        until = end if until is None else until
    lab = tuple(args.lab_nets) if getattr(args, "lab_nets", None) else lab_nets_from_config(config)
    include_scan = bool(getattr(args, "include_unlogged_scan", False))
    attacker_ip = str(getattr(args, "attacker_ip", "") or "").strip() or attacker_ip_from_config(config)
    return ExtractOptions(
        lab_nets=lab,
        require_cmdline=not bool(getattr(args, "no_require_cmdline", False)),
        since=since,
        until=until,
        include_unlogged_scan=include_scan,
        attacker_ip=attacker_ip,
        exclude_flows=exclude_flows_from_config(config),
    )


def resolve_attacker_ip(
    args: argparse.Namespace,
    config: dict[str, Any] | None,
    connects: Sequence[NetworkConnect],
) -> str:
    explicit = str(getattr(args, "attacker_ip", "") or "").strip() or attacker_ip_from_config(config)
    if explicit:
        return explicit
    lab = tuple(args.lab_nets) if getattr(args, "lab_nets", None) else lab_nets_from_config(config)
    return infer_attacker_ip(connects, lab)


def _optional_path_arg(args: argparse.Namespace, name: str) -> Path | None:
    raw = getattr(args, name, None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return None


def _sysmon_slice_source(args: argparse.Namespace) -> Path | None:
    path = Path(args.evtx)
    return path if is_evtx_source(path) else None


def _parse_extract_day(raw: str) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _tool_name(args: argparse.Namespace, name: str, default: str) -> str:
    raw = getattr(args, name, default)
    if not isinstance(raw, str):
        return default
    text = raw.strip()
    return text or default


def _dropped_incomplete_rows(
    matched: Sequence[SelectedConnect],
    complete_ids: set[str],
    *,
    task_id: str = "",
    technique: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in matched:
        if not item.tcp_stream or item.tcp_stream in complete_ids:
            continue
        row = {"tcp_stream": item.tcp_stream}
        if task_id:
            row["task_id"] = task_id
        if technique:
            row["technique"] = technique
        rows.append(row)
    return rows


def _write_benign_and_tuples(
    *,
    pcap: Path,
    out_dir: Path,
    display_filter: str,
    complete_benign_tcp: Iterable[str],
    records: list[dict[str, Any]],
    tuples_name: str,
    tshark: str,
    mergecap: str,
    run_fn: RunFn,
) -> tuple[Path, Path, list[str]]:
    benign_path = out_dir / "benign.pcapng"
    tuples_path = out_dir / (tuples_name or "tuples.json")
    filters = benign_filter_parts(
        malicious_filter=display_filter,
        complete_benign_tcp=complete_benign_tcp,
    )
    write_pcap_from_filters(
        pcap,
        filters,
        benign_path,
        tshark=tshark,
        mergecap=mergecap,
        run_fn=run_fn,
    )
    tuples_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return benign_path, tuples_path, filters


def run_extract_batch(
    args: argparse.Namespace,
    *,
    day: date,
    config: dict[str, Any] | None,
    creates: Sequence[ProcessCreate],
    connects: Sequence[NetworkConnect],
    packets: Sequence[PacketRow],
    run_fn: RunFn,
) -> dict[str, Any]:
    out_dir = resolve_extract_out_dir(args, config, day)
    out_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir = resolve_transcripts_dir(args, config, day, out_dir)
    base = options_from_args(args, config)
    attacker_ip = resolve_attacker_ip(args, config, connects)
    explicit_scan = bool(getattr(args, "include_unlogged_scan", False))
    tshark = _tool_name(args, "tshark", "tshark")
    mergecap = _tool_name(args, "mergecap", "mergecap")
    wevtutil = _tool_name(args, "wevtutil", "wevtutil")
    pcap = Path(args.pcap)
    sysmon_src = _sysmon_slice_source(args)
    security_src = _optional_path_arg(args, "security_evtx")
    dc_security_src = _optional_path_arg(args, "dc_security_evtx")
    complete_ids = complete_tcp_stream_ids(packets)
    all_tcp: list[str] = []
    all_udp: list[str] = []
    all_records: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    dropped_incomplete: list[dict[str, str]] = []
    union_scan = False
    scan_since: float | None = None
    scan_until: float | None = None
    for md_path in list_task_transcripts(transcripts_dir):
        text = md_path.read_text(encoding="utf-8")
        fields = parse_frontmatter_fields(text)
        task_id = str(fields.get("task_id") or md_path.stem).strip()
        technique = parse_technique_id(fields.get("task") or "")
        try:
            since, until = parse_task_window(text)
        except ValueError as exc:
            skipped.append({"path": str(md_path), "reason": str(exc)})
            continue
        if not technique:
            skipped.append({"path": str(md_path), "reason": "no technique id in task text"})
            continue
        include_scan = auto_unlogged_scan(technique, attacker_ip, explicit_scan)
        warnings: list[str] = []
        if include_scan and not attacker_ip:
            include_scan = False
            warnings.append("unlogged scan omitted")
        elif technique in SCAN_TECHNIQUES and not attacker_ip:
            warnings.append("unlogged scan omitted")
        options = ExtractOptions(
            lab_nets=base.lab_nets,
            cmdline_needles=base.cmdline_needles,
            require_cmdline=base.require_cmdline,
            initiated_only=base.initiated_only,
            since=since,
            until=until,
            time_slack_seconds=base.time_slack_seconds,
            include_unlogged_scan=include_scan,
            attacker_ip=attacker_ip,
            exclude_flows=base.exclude_flows,
        )
        matched, display_filter = extract_from_sources(
            creates=creates, connects=connects, packets=packets, options=options
        )
        dropped = _dropped_incomplete_rows(
            matched, complete_ids, task_id=task_id, technique=technique
        )
        if dropped:
            warnings.append("incomplete tcp dropped")
            dropped_incomplete.extend(dropped)
        stem = technique_pcap_stem(task_id, technique)
        dest = out_dir / f"{stem}.pcapng"
        if is_empty_display_filter(display_filter):
            print(f"skipping {dest.name} (empty filter)", flush=True)
            warnings.append("empty pcap filter skipped")
        else:
            print(f"extracting {dest.name}", flush=True)
        written = maybe_write_filtered_pcap(
            pcap, display_filter, dest, tshark=tshark, run_fn=run_fn
        )
        evtx_paths, evtx_warnings = export_task_evtx_files(
            stem=stem,
            out_dir=out_dir,
            since=since,
            until=until,
            sysmon_src=sysmon_src,
            security_src=security_src,
            dc_security_src=dc_security_src,
            run_fn=run_fn,
            wevtutil=wevtutil,
        )
        warnings.extend(evtx_warnings)
        records = selected_to_records(
            matched,
            task_id=task_id,
            technique=technique,
            complete_tcp=complete_ids,
        )
        all_records.extend(records)
        all_tcp.extend(
            item.tcp_stream
            for item in matched
            if item.tcp_stream and item.tcp_stream in complete_ids
        )
        all_udp.extend(item.udp_stream for item in matched if item.udp_stream)
        if include_scan:
            union_scan = True
            start_ts = since.timestamp()
            end_ts = until.timestamp()
            scan_since = start_ts if scan_since is None else min(scan_since, start_ts)
            scan_until = end_ts if scan_until is None else max(scan_until, end_ts)
        task_row: dict[str, Any] = {
            "task_id": task_id,
            "technique": technique,
            "pcap": str(written) if written is not None else None,
            "sysmon": evtx_paths["sysmon"],
            "security": evtx_paths["security"],
            "dc_security": evtx_paths["dc_security"],
            "connects": len(records),
            "display_filter": display_filter,
        }
        if warnings:
            task_row["warnings"] = warnings
        tasks.append(task_row)
    exclude_peers = peer_ips_from_exclude_flows(base.exclude_flows)
    union_filter = build_display_filter(
        all_tcp,
        all_udp,
        include_unlogged_scan=union_scan,
        attacker_ip=attacker_ip,
        scan_since_epoch=scan_since,
        scan_until_epoch=scan_until,
        exclude_peer_ips=exclude_peers,
    )
    scan_tcp = (
        collect_scan_tcp_streams(
            packets,
            attacker_ip=attacker_ip,
            scan_since_epoch=scan_since,
            scan_until_epoch=scan_until,
            exclude_peer_ips=exclude_peers,
        )
        if union_scan
        else set()
    )
    benign_tcp = complete_ids - set(all_tcp) - scan_tcp
    benign_path, tuples_path, benign_filters = _write_benign_and_tuples(
        pcap=pcap,
        out_dir=out_dir,
        display_filter=union_filter,
        complete_benign_tcp=benign_tcp,
        records=all_records,
        tuples_name=str(getattr(args, "tuples_name", "tuples.json") or "tuples.json"),
        tshark=tshark,
        mergecap=mergecap,
        run_fn=run_fn,
    )
    payload: dict[str, Any] = {
        "ok": True,
        "connects": len(all_records),
        "attacker_ip": attacker_ip,
        "display_filter": union_filter,
        "benign": str(benign_path),
        "benign_filter": benign_filters[0] if len(benign_filters) == 1 else benign_filters,
        "tuples": str(tuples_path),
        "tasks": tasks,
        "skipped": skipped,
    }
    if dropped_incomplete:
        payload["dropped_incomplete"] = dropped_incomplete
    return payload


def run_extract(
    args: argparse.Namespace,
    *,
    config: dict[str, Any] | None = None,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    day = _parse_extract_day(str(getattr(args, "date", "") or ""))
    creates, connects = load_sysmon_events(
        Path(args.evtx), run_fn=run_fn, wevtutil=_tool_name(args, "wevtutil", "wevtutil")
    )
    field_text = dump_tshark_fields(Path(args.pcap), tshark=_tool_name(args, "tshark", "tshark"), run_fn=run_fn)
    packets = parse_tshark_fields(field_text)
    if day is not None:
        return run_extract_batch(
            args,
            day=day,
            config=config,
            creates=creates,
            connects=connects,
            packets=packets,
            run_fn=run_fn,
        )
    options = options_from_args(args, config)
    attacker_ip = resolve_attacker_ip(args, config, connects)
    options.attacker_ip = attacker_ip
    if options.include_unlogged_scan and not attacker_ip:
        options.include_unlogged_scan = False
    matched, display_filter = extract_from_sources(
        creates=creates, connects=connects, packets=packets, options=options
    )
    out_dir = resolve_extract_out_dir(args, config, None)
    out_dir.mkdir(parents=True, exist_ok=True)
    malicious_path = out_dir / "malicious.pcapng"
    tshark = _tool_name(args, "tshark", "tshark")
    mergecap = _tool_name(args, "mergecap", "mergecap")
    complete_ids = complete_tcp_stream_ids(packets)
    dropped_incomplete = _dropped_incomplete_rows(matched, complete_ids)
    written_malicious = maybe_write_filtered_pcap(
        Path(args.pcap), display_filter, malicious_path, tshark=tshark, run_fn=run_fn
    )
    records = selected_to_records(matched, complete_tcp=complete_ids)
    exclude_peers = peer_ips_from_exclude_flows(options.exclude_flows)
    scan_since = options.since.timestamp() if options.since is not None else None
    scan_until = options.until.timestamp() if options.until is not None else None
    scan_tcp = (
        collect_scan_tcp_streams(
            packets,
            attacker_ip=attacker_ip,
            scan_since_epoch=scan_since,
            scan_until_epoch=scan_until,
            exclude_peer_ips=exclude_peers,
        )
        if options.include_unlogged_scan
        else set()
    )
    malicious_tcp = {
        item.tcp_stream for item in matched if item.tcp_stream and item.tcp_stream in complete_ids
    } | scan_tcp
    benign_tcp = complete_ids - malicious_tcp
    benign_path, tuples_path, benign_filters = _write_benign_and_tuples(
        pcap=Path(args.pcap),
        out_dir=out_dir,
        display_filter=display_filter,
        complete_benign_tcp=benign_tcp,
        records=records,
        tuples_name=str(getattr(args, "tuples_name", "tuples.json") or "tuples.json"),
        tshark=tshark,
        mergecap=mergecap,
        run_fn=run_fn,
    )
    evtx_paths: dict[str, str | None] = {"sysmon": None, "security": None, "dc_security": None}
    evtx_warnings: list[str] = []
    stem = "malicious"
    window = (options.since, options.until)
    task_md = getattr(args, "task_md", None)
    if isinstance(task_md, (str, Path)):
        text = Path(task_md).read_text(encoding="utf-8")
        fields = parse_frontmatter_fields(text)
        task_id = str(fields.get("task_id") or Path(task_md).stem).strip()
        technique = parse_technique_id(fields.get("task") or "")
        if task_id and technique:
            stem = technique_pcap_stem(task_id, technique)
        if window[0] is None or window[1] is None:
            try:
                window = parse_task_window(text)
            except ValueError:
                window = (options.since, options.until)
    if window[0] is not None and window[1] is not None:
        evtx_paths, evtx_warnings = export_task_evtx_files(
            stem=stem,
            out_dir=out_dir,
            since=window[0],
            until=window[1],
            sysmon_src=_sysmon_slice_source(args),
            security_src=_optional_path_arg(args, "security_evtx"),
            dc_security_src=_optional_path_arg(args, "dc_security_evtx"),
            run_fn=run_fn,
            wevtutil=_tool_name(args, "wevtutil", "wevtutil"),
        )
    payload: dict[str, Any] = {
        "ok": True,
        "connects": len(records),
        "attacker_ip": attacker_ip,
        "display_filter": display_filter,
        "malicious": str(written_malicious) if written_malicious is not None else None,
        "benign": str(benign_path),
        "benign_filter": benign_filters[0] if len(benign_filters) == 1 else benign_filters,
        "tuples": str(tuples_path),
        "sysmon": evtx_paths["sysmon"],
        "security": evtx_paths["security"],
        "dc_security": evtx_paths["dc_security"],
    }
    if evtx_warnings:
        payload["warnings"] = evtx_warnings
    if written_malicious is None:
        extra = list(payload.get("warnings") or [])
        extra.append("empty pcap filter skipped")
        payload["warnings"] = extra
    if dropped_incomplete:
        payload["dropped_incomplete"] = dropped_incomplete
        extra = list(payload.get("warnings") or [])
        extra.append("incomplete tcp dropped")
        payload["warnings"] = extra
    return payload
