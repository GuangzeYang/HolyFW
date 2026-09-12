"""TCP stream completeness and benign display-filter assembly.

The mixed SPAN pcap is never modified. Filters only copy packets into new files.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

STREAM_CHUNK_SIZE = 150
MAX_FILTER_CHARS = 12000


@dataclass
class TcpStreamShape:
    stream: str
    has_syn: bool = False
    has_fin: bool = False
    has_rst: bool = False

    @property
    def complete(self) -> bool:
        return self.has_syn and (self.has_fin or self.has_rst)


def stream_sort_key(value: str) -> tuple[int, str]:
    try:
        return (0, str(int(value)))
    except ValueError:
        return (1, value)


def classify_tcp_streams(packets: Sequence[Any]) -> dict[str, TcpStreamShape]:
    shapes: dict[str, TcpStreamShape] = {}
    for packet in packets:
        stream = str(getattr(packet, "tcp_stream", "") or "").strip()
        if not stream:
            continue
        shape = shapes.get(stream)
        if shape is None:
            shape = TcpStreamShape(stream=stream)
            shapes[stream] = shape
        if bool(getattr(packet, "syn", False)):
            shape.has_syn = True
        if bool(getattr(packet, "fin", False)):
            shape.has_fin = True
        if bool(getattr(packet, "rst", False)):
            shape.has_rst = True
    return shapes


def complete_tcp_stream_ids(packets: Sequence[Any]) -> set[str]:
    return {stream for stream, shape in classify_tcp_streams(packets).items() if shape.complete}


def unique_sorted_streams(streams: Iterable[str]) -> list[str]:
    return sorted({item for item in streams if str(item).strip()}, key=stream_sort_key)


def or_stream_terms(kind: str, streams: Iterable[str]) -> str:
    parts = [f"{kind}.stream eq {stream}" for stream in unique_sorted_streams(streams)]
    return " || ".join(parts)


def collect_scan_tcp_streams(
    packets: Sequence[Any],
    *,
    attacker_ip: str,
    scan_since_epoch: float | None,
    scan_until_epoch: float | None,
    exclude_peer_ips: Iterable[str] = (),
) -> set[str]:
    """tcp.stream ids of attacker-sourced bare SYN in the scan window (including SYN+RST)."""
    ip = str(attacker_ip or "").strip()
    if not ip or scan_since_epoch is None or scan_until_epoch is None:
        return set()
    excluded = {str(item).strip() for item in exclude_peer_ips if str(item).strip()}
    found: set[str] = set()
    for packet in packets:
        stream = str(getattr(packet, "tcp_stream", "") or "").strip()
        if not stream:
            continue
        if not bool(getattr(packet, "syn", False)):
            continue
        if bool(getattr(packet, "ack", False)):
            continue
        if str(getattr(packet, "ip_src", "") or "").strip() != ip:
            continue
        epoch = float(getattr(packet, "time_epoch", 0.0) or 0.0)
        if epoch < float(scan_since_epoch) or epoch > float(scan_until_epoch):
            continue
        peer = str(getattr(packet, "ip_dst", "") or "").strip()
        if peer and peer in excluded:
            continue
        found.add(stream)
    return found


def benign_filter_parts(
    *,
    malicious_filter: str,
    complete_benign_tcp: Iterable[str],
    chunk_size: int = STREAM_CHUNK_SIZE,
    max_chars: int = MAX_FILTER_CHARS,
) -> list[str]:
    """Display filters that copy benign packets out of the source pcap.

    Short form matches ``not (malicious) && (not tcp || complete benign TCP)``.
    Long stream lists are split so tshark argv stays under the Windows limit.
    """
    mal = str(malicious_filter or "").strip() or "frame.number == 0"
    tcp_ids = unique_sorted_streams(complete_benign_tcp)
    if not tcp_ids:
        return [f"not ({mal}) && not tcp"]
    tcp_or = or_stream_terms("tcp", tcp_ids)
    combined = f"not ({mal}) && (not tcp || {tcp_or})"
    if len(combined) <= max_chars:
        return [combined]
    parts: list[str] = [f"not tcp && not ({mal})"]
    size = max(int(chunk_size), 1)
    for index in range(0, len(tcp_ids), size):
        chunk = tcp_ids[index : index + size]
        chunk_or = or_stream_terms("tcp", chunk)
        piece = f"not ({mal}) && ({chunk_or})"
        if len(piece) > max_chars:
            parts.append(chunk_or)
        else:
            parts.append(piece)
    return parts
