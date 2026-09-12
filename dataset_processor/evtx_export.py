"""Windows-only: export file evtx to XML so Linux extract can skip wevtutil."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dataset_processor.extract import (
    EVENT_QUERY,
    RunFn,
    _tool_name,
    is_empty_evtx_export,
    is_evtx_source,
    same_pcap_path,
)

EXPORT_EVTX_TIMEOUT = 1800


def xml_path_for(source: Path, out_dir: Path | None) -> Path:
    """Name XML files uniquely when several evtx share a stem (e.g. security_logon)."""
    if out_dir is None:
        return source.with_suffix(".xml")
    parent = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in source.parent.name)
    parent = parent or "evtx"
    return Path(out_dir) / f"{parent}_{source.stem}.xml"


def export_evtx_to_xml(
    source: Path,
    dest: Path,
    *,
    query: str | None = EVENT_QUERY,
    run_fn: RunFn = subprocess.run,
    wevtutil: str = "wevtutil",
    timeout: float = EXPORT_EVTX_TIMEOUT,
) -> Path:
    """Stream ``wevtutil qe /lf:true /f:xml`` into ``dest``. Does not load XML in memory."""
    if not source.is_file():
        raise FileNotFoundError(f"evtx not found: {source}")
    if not is_evtx_source(source):
        raise ValueError(f"not an evtx file: {source}")
    if same_pcap_path(source, dest):
        raise ValueError(f"refusing to overwrite source evtx: {source}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    resolved = shutil.which(wevtutil) or wevtutil
    args = [resolved, "qe", str(source), "/lf:true", "/f:xml"]
    if query:
        args.append(f"/q:{query}")
    with dest.open("w", encoding="utf-8", errors="replace", newline="\n") as out:
        completed = run_fn(
            args,
            stdout=out,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    code = int(completed.returncode)
    stderr = completed.stderr or ""
    if code != 0:
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink()
        if is_empty_evtx_export(code, "", stderr):
            raise RuntimeError(f"wevtutil qe found no events in {source}")
        detail = stderr.strip() or "wevtutil qe failed"
        raise RuntimeError(f"wevtutil qe failed on {source}: {detail}")
    if not dest.exists():
        raise RuntimeError(f"wevtutil qe wrote no file: {dest}")
    return dest


def _export_one(
    source: Path | None,
    *,
    out_dir: Path | None,
    query: str | None,
    wevtutil: str,
    run_fn: RunFn,
    label: str,
) -> dict[str, Any] | None:
    if source is None:
        return None
    dest = xml_path_for(source, out_dir)
    print(f"exporting {label} -> {dest.name}", flush=True)
    written = export_evtx_to_xml(
        source,
        dest,
        query=query,
        run_fn=run_fn,
        wevtutil=wevtutil,
    )
    return {"source": str(source), "xml": str(written), "query": query or ""}


def run_export_evtx(
    args: argparse.Namespace,
    *,
    run_fn: RunFn = subprocess.run,
) -> dict[str, Any]:
    wevtutil = _tool_name(args, "wevtutil", "wevtutil")
    out_raw = getattr(args, "out_dir", None)
    out_dir: Path | None
    if isinstance(out_raw, Path):
        out_dir = out_raw
    elif isinstance(out_raw, str) and out_raw.strip():
        out_dir = Path(out_raw)
    else:
        out_dir = None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
    sysmon_query = str(getattr(args, "sysmon_query", "") or "").strip() or EVENT_QUERY
    files: list[dict[str, Any]] = []
    sysmon = _export_one(
        Path(args.evtx),
        out_dir=out_dir,
        query=sysmon_query,
        wevtutil=wevtutil,
        run_fn=run_fn,
        label="sysmon",
    )
    if sysmon:
        files.append(sysmon)
    for name, label in (("security_evtx", "security"), ("dc_security_evtx", "dc_security")):
        raw = getattr(args, name, None)
        path: Path | None = None
        if isinstance(raw, Path):
            path = raw
        elif isinstance(raw, str) and raw.strip():
            path = Path(raw)
        row = _export_one(
            path,
            out_dir=out_dir,
            query=None,
            wevtutil=wevtutil,
            run_fn=run_fn,
            label=label,
        )
        if row:
            files.append(row)
    return {"ok": True, "files": files}


def add_export_evtx_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evtx", type=Path, required=True, help="attacker Sysmon .evtx (EID 1/3 exported)")
    parser.add_argument(
        "--security-evtx",
        type=Path,
        default=None,
        help="optional attacker Security / security_logon .evtx; exported in full",
    )
    parser.add_argument(
        "--dc-security-evtx",
        type=Path,
        default=None,
        help="optional DC Security / security_logon .evtx; exported in full",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="directory for new .xml files (default: same directory as each source, {stem}.xml)",
    )
    parser.add_argument(
        "--sysmon-query",
        default=EVENT_QUERY,
        help="XPath for Sysmon export (default: EventID 1 or 3)",
    )
    parser.add_argument("--wevtutil", default="wevtutil", help="wevtutil executable (Windows)")


def add_export_evtx_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        "export-evtx",
        help="on Windows, export file evtx to XML for Linux extract (no pcap, no wevtutil on Linux)",
    )
    add_export_evtx_arguments(parser)
    return parser
