#!/usr/bin/env python3
"""TCP completeness, benign copy filters, and read-only source pcap."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from dataset_processor.extract import (
    ExtractOptions,
    NetworkConnect,
    PacketRow,
    ProcessCreate,
    assert_output_not_source,
    extract_from_sources,
    parse_tshark_fields,
    run_extract,
    write_filtered_pcap,
    write_pcap_from_filters,
)
from dataset_processor.tcp_streams import (
    benign_filter_parts,
    classify_tcp_streams,
    collect_scan_tcp_streams,
    complete_tcp_stream_ids,
)

GUID = "11111111-1111-1111-1111-111111111111"
UTC = datetime(2026, 9, 4, 4, 0, 0, tzinfo=timezone.utc)


def _connect(**overrides: object) -> NetworkConnect:
    values = dict(
        utc=UTC,
        process_guid=GUID,
        image=r"C:\Python314\python.exe",
        initiated=True,
        protocol="tcp",
        source_ip="172.16.24.10",
        source_port=49723,
        dest_ip="172.16.24.1",
        dest_port=445,
    )
    values.update(overrides)
    return NetworkConnect(**values)  # type: ignore[arg-type]


def _create(**overrides: object) -> ProcessCreate:
    values = dict(
        utc=UTC,
        process_guid=GUID,
        image=r"C:\Python314\python.exe",
        command_line="python -m impacket.examples.smbclient x",
    )
    values.update(overrides)
    return ProcessCreate(**values)  # type: ignore[arg-type]


def _row(**overrides: object) -> PacketRow:
    values = dict(
        number="1",
        time_epoch=1.0,
        ip_src="172.16.24.10",
        ip_dst="172.16.24.1",
        tcp_sport=49723,
        tcp_dport=445,
        tcp_stream="12",
        syn=True,
        ack=False,
        fin=False,
        rst=False,
    )
    values.update(overrides)
    return PacketRow(**values)  # type: ignore[arg-type]


class TcpCompleteTests(unittest.TestCase):
    def test_syn_and_fin_is_complete(self) -> None:
        packets = [
            _row(syn=True, ack=False, fin=False, rst=False),
            _row(number="2", syn=False, ack=True, fin=True, rst=False),
        ]
        shapes = classify_tcp_streams(packets)
        self.assertTrue(shapes["12"].complete)
        self.assertEqual(complete_tcp_stream_ids(packets), {"12"})

    def test_syn_and_rst_is_complete(self) -> None:
        packets = [
            _row(syn=True, ack=False),
            _row(number="2", syn=False, ack=False, fin=False, rst=True),
        ]
        self.assertEqual(complete_tcp_stream_ids(packets), {"12"})

    def test_syn_only_is_incomplete(self) -> None:
        packets = [_row(syn=True, ack=False, fin=False, rst=False)]
        self.assertEqual(complete_tcp_stream_ids(packets), set())

    def test_fin_without_syn_is_incomplete(self) -> None:
        packets = [_row(syn=False, ack=True, fin=True, rst=False)]
        self.assertEqual(complete_tcp_stream_ids(packets), set())

    def test_parse_fin_rst_columns(self) -> None:
        packets = parse_tshark_fields(
            "1\t1.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t7\t\t6\t\t\t1\t0\t0\t1\n"
        )
        self.assertTrue(packets[0].syn)
        self.assertFalse(packets[0].fin)
        self.assertTrue(packets[0].rst)
        self.assertEqual(complete_tcp_stream_ids(packets), {"7"})


class MaliciousCompleteFilterTests(unittest.TestCase):
    def test_incomplete_tcp_not_in_malicious_filter(self) -> None:
        epoch = UTC.timestamp()
        packets = parse_tshark_fields(
            f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t0\t0\n"
        )
        matched, filt = extract_from_sources(
            creates=[_create()],
            connects=[_connect(utc=datetime.fromtimestamp(epoch, tz=timezone.utc))],
            packets=packets,
            options=ExtractOptions(),
        )
        self.assertEqual(matched[0].tcp_stream, "12")
        self.assertEqual(filt, "frame.number == 0")

    def test_complete_tcp_stays_in_malicious_filter(self) -> None:
        epoch = UTC.timestamp()
        packets = parse_tshark_fields(
            f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"
        )
        matched, filt = extract_from_sources(
            creates=[_create()],
            connects=[_connect(utc=datetime.fromtimestamp(epoch, tz=timezone.utc))],
            packets=packets,
            options=ExtractOptions(),
        )
        self.assertEqual(matched[0].tcp_stream, "12")
        self.assertEqual(filt, "tcp.stream eq 12")

    def test_scan_bare_syn_stays_without_fin(self) -> None:
        matched, filt = extract_from_sources(
            creates=[],
            connects=[],
            packets=[],
            options=ExtractOptions(
                include_unlogged_scan=True,
                attacker_ip="172.16.24.10",
                since=datetime.fromtimestamp(1.0, tz=timezone.utc),
                until=datetime.fromtimestamp(2.0, tz=timezone.utc),
            ),
        )
        self.assertEqual(matched, [])
        self.assertIn("tcp.flags.syn == 1 && tcp.flags.ack == 0", filt)
        self.assertIn("icmp", filt)


class BenignFilterTests(unittest.TestCase):
    def test_benign_keeps_complete_non_malicious_tcp(self) -> None:
        parts = benign_filter_parts(
            malicious_filter="tcp.stream eq 12",
            complete_benign_tcp=["7"],
        )
        self.assertEqual(len(parts), 1)
        self.assertIn("not (tcp.stream eq 12)", parts[0])
        self.assertIn("tcp.stream eq 7", parts[0])
        self.assertIn("not tcp", parts[0])
        self.assertNotIn("tcp.stream eq 99", parts[0])

    def test_incomplete_only_source_yields_non_tcp_benign(self) -> None:
        parts = benign_filter_parts(malicious_filter="frame.number == 0", complete_benign_tcp=[])
        self.assertEqual(parts, ["not (frame.number == 0) && not tcp"])

    def test_long_list_is_chunked(self) -> None:
        streams = [str(i) for i in range(5)]
        parts = benign_filter_parts(
            malicious_filter="tcp.stream eq 99",
            complete_benign_tcp=streams,
            chunk_size=2,
            max_chars=40,
        )
        self.assertGreater(len(parts), 1)
        self.assertTrue(any(item.startswith("not tcp") for item in parts))
        joined = " ".join(parts)
        self.assertIn("tcp.stream eq 0", joined)
        self.assertIn("tcp.stream eq 4", joined)

    def test_scan_syn_rst_stream_excluded_from_benign_ids(self) -> None:
        packets = [
            _row(tcp_stream="80", syn=True, ack=False, time_epoch=1.5, rst=True),
        ]
        scan_ids = collect_scan_tcp_streams(
            packets,
            attacker_ip="172.16.24.10",
            scan_since_epoch=1.0,
            scan_until_epoch=2.0,
        )
        self.assertEqual(scan_ids, {"80"})
        complete = complete_tcp_stream_ids(packets)
        benign_tcp = complete - scan_ids
        self.assertNotIn("80", benign_tcp)


class SourcePcapReadOnlyTests(unittest.TestCase):
    def test_refuses_write_onto_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pcap = Path(tmp) / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            with self.assertRaises(ValueError):
                assert_output_not_source(pcap, pcap)
            with self.assertRaises(ValueError):
                write_filtered_pcap(pcap, "tcp", pcap, run_fn=lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr=""))

    def test_extract_leaves_source_bytes_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "sysmon.xml"
            xml_path.write_text(
                """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><EventID>1</EventID></System>
  <EventData>
    <Data Name="UtcTime">2026-09-04 04:00:00.000</Data>
    <Data Name="ProcessGuid">{11111111-1111-1111-1111-111111111111}</Data>
    <Data Name="Image">C:\\Python314\\python.exe</Data>
    <Data Name="CommandLine">python -m impacket.examples.smbclient x</Data>
  </EventData>
</Event>
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System><EventID>3</EventID></System>
  <EventData>
    <Data Name="UtcTime">2026-09-04 04:00:00.000</Data>
    <Data Name="ProcessGuid">{11111111-1111-1111-1111-111111111111}</Data>
    <Data Name="Image">C:\\Python314\\python.exe</Data>
    <Data Name="Protocol">tcp</Data>
    <Data Name="Initiated">true</Data>
    <Data Name="SourceIp">172.16.24.10</Data>
    <Data Name="SourcePort">49723</Data>
    <Data Name="DestinationIp">172.16.24.1</Data>
    <Data Name="DestinationPort">445</Data>
  </EventData>
</Event>""",
                encoding="utf-8",
            )
            pcap = root / "mix.pcapng"
            original = b"original-mixed-pcap"
            pcap.write_bytes(original)
            out = root / "out"
            epoch = UTC.timestamp()
            field_line = (
                f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"
            )

            def fake_run(args, **_kwargs):
                cmd = [str(part) for part in args]
                if "-T" in cmd and "fields" in cmd:
                    return mock.Mock(returncode=0, stdout=field_line, stderr="")
                if "-w" in cmd:
                    dest = Path(cmd[cmd.index("-w") + 1])
                    self.assertNotEqual(dest.resolve(), pcap.resolve())
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"copy")
                return mock.Mock(returncode=0, stdout="", stderr="")

            args = mock.Mock(
                evtx=xml_path,
                pcap=pcap,
                out_dir=out,
                date="",
                tshark="tshark",
                mergecap="mergecap",
                wevtutil="wevtutil",
                tuples_name="tuples.json",
                lab_nets=["172.16.24.0/24"],
                since="",
                until="",
                task_md=None,
                include_unlogged_scan=False,
                attacker_ip="",
                no_require_cmdline=False,
            )
            payload = run_extract(args, config={}, run_fn=fake_run)
            self.assertTrue(payload["ok"])
            self.assertEqual(pcap.read_bytes(), original)
            self.assertTrue((out / "malicious.pcapng").is_file())
            self.assertTrue((out / "benign.pcapng").is_file())

    def test_chunked_write_merges_into_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"src")
            dest = root / "benign.pcapng"
            seen: list[list[str]] = []

            def fake_run(args, **_kwargs):
                cmd = [str(part) for part in args]
                seen.append(cmd)
                if "-w" in cmd:
                    out = Path(cmd[cmd.index("-w") + 1])
                    self.assertNotEqual(out.resolve(), pcap.resolve())
                    out.write_bytes(b"x")
                return mock.Mock(returncode=0, stdout="", stderr="")

            write_pcap_from_filters(
                pcap,
                ["tcp.stream eq 1", "tcp.stream eq 2", "not tcp"],
                dest,
                tshark="tshark",
                mergecap="mergecap",
                run_fn=fake_run,
            )
            self.assertTrue(dest.is_file())
            self.assertFalse(list(root.glob(".benign.part*")))
            merge = next(cmd for cmd in seen if cmd and "mergecap" in cmd[0])
            self.assertEqual(merge[1], "-w")
            self.assertEqual(Path(merge[2]), dest)
            self.assertEqual(pcap.read_bytes(), b"src")


class CliTests(unittest.TestCase):
    def test_module_parser_has_transcripts_dir(self) -> None:
        from dataset_processor.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(
            [
                "--evtx",
                "sysmon.evtx",
                "--pcap",
                "mix.pcap",
                "--out-dir",
                "out",
                "--transcripts-dir",
                "md",
                "--date",
                "2026-09-11",
            ]
        )
        self.assertEqual(str(args.pcap), "mix.pcap")
        self.assertEqual(str(args.transcripts_dir), "md")
        self.assertEqual(args.date, "2026-09-11")
        self.assertEqual(args.mergecap, "mergecap")

    def test_export_evtx_parser(self) -> None:
        from dataset_processor.cli import build_export_parser, main

        parser = build_export_parser()
        args = parser.parse_args(
            ["--evtx", "sysmon.evtx", "--out-dir", "xml", "--security-evtx", "sec.evtx"]
        )
        self.assertEqual(str(args.evtx), "sysmon.evtx")
        self.assertEqual(str(args.out_dir), "xml")
        self.assertIn("EventID=1", args.sysmon_query)
        with mock.patch("dataset_processor.cli.run_export_evtx", return_value={"ok": True, "files": []}):
            code = main(["export-evtx", "--evtx", "sysmon.evtx"])
        self.assertEqual(code, 0)


class ExportEvtxTests(unittest.TestCase):
    def test_sysmon_qe_argv_and_streaming_write(self) -> None:
        from dataset_processor.evtx_export import EVENT_QUERY, export_evtx_to_xml

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sysmon_2026-09-11.evtx"
            dest = Path(tmp) / "out" / "sysmon_2026-09-11.xml"
            src.write_bytes(b"evtx")
            seen: list[list[str]] = []

            def fake_run(args, **kwargs):
                cmd = [str(part) for part in args]
                seen.append(cmd)
                stdout = kwargs["stdout"]
                stdout.write("<Event><System><EventID>3</EventID></System></Event>\n")
                return mock.Mock(returncode=0, stderr="")

            written = export_evtx_to_xml(src, dest, run_fn=fake_run)
            self.assertEqual(written, dest)
            self.assertIn("qe", seen[0])
            self.assertIn("/lf:true", seen[0])
            self.assertIn("/f:xml", seen[0])
            self.assertIn(f"/q:{EVENT_QUERY}", seen[0])
            self.assertIn("EventID>3", dest.read_text(encoding="utf-8"))

    def test_security_export_has_no_sysmon_query(self) -> None:
        from dataset_processor.evtx_export import run_export_evtx

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sysmon = root / "sysmon.evtx"
            security = root / "security.evtx"
            sysmon.write_bytes(b"s")
            security.write_bytes(b"c")
            out = root / "xml"
            queries: list[str | None] = []

            def fake_run(args, **kwargs):
                cmd = [str(part) for part in args]
                q = next((part[3:] for part in cmd if part.startswith("/q:")), None)
                queries.append(q)
                kwargs["stdout"].write("<Event/>\n")
                return mock.Mock(returncode=0, stderr="")

            args = mock.Mock(
                evtx=sysmon,
                security_evtx=security,
                dc_security_evtx=None,
                out_dir=out,
                wevtutil="wevtutil",
                sysmon_query="",
            )
            payload = run_export_evtx(args, run_fn=fake_run)
            self.assertTrue(payload["ok"])
            self.assertEqual(len(payload["files"]), 2)
            prefix = sysmon.parent.name
            self.assertTrue((out / f"{prefix}_sysmon.xml").is_file())
            self.assertTrue((out / f"{prefix}_security.xml").is_file())
            self.assertIsNotNone(queries[0])
            self.assertIsNone(queries[1])

    def test_same_stem_in_out_dir_uses_parent_prefix(self) -> None:
        from dataset_processor.evtx_export import xml_path_for

        attacker = Path("20260911-20260912/attacker/security_logon_2026-09-11.evtx")
        dc = Path("20260911-20260912/DC/security_logon_2026-09-11.evtx")
        out = Path("xml")
        self.assertNotEqual(xml_path_for(attacker, out).name, xml_path_for(dc, out).name)
        self.assertEqual(xml_path_for(attacker, out).name, "attacker_security_logon_2026-09-11.xml")
        self.assertEqual(xml_path_for(dc, out).name, "DC_security_logon_2026-09-11.xml")


class SkipEmptyFilterTests(unittest.TestCase):
    def test_empty_filter_does_not_invoke_tshark_write(self) -> None:
        from dataset_processor.extract import maybe_write_filtered_pcap

        with tempfile.TemporaryDirectory() as tmp:
            pcap = Path(tmp) / "mix.pcap"
            dest = Path(tmp) / "task.pcapng"
            pcap.write_bytes(b"pcap")
            dest.write_bytes(b"stale")
            calls: list[list[str]] = []

            def fake_run(args, **_kwargs):
                calls.append([str(part) for part in args])
                return mock.Mock(returncode=0, stdout="", stderr="")

            written = maybe_write_filtered_pcap(
                pcap, "frame.number == 0", dest, tshark="tshark", run_fn=fake_run
            )
            self.assertIsNone(written)
            self.assertFalse(dest.exists())
            self.assertEqual(calls, [])



if __name__ == "__main__":
    unittest.main()
