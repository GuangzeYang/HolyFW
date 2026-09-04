#!/usr/bin/env python3
"""Sysmon 5-tuple selection and tshark filter assembly for attacker extract."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from attacker.extract_pcap import (
    ExtractOptions,
    NetworkConnect,
    PacketRow,
    ProcessCreate,
    build_display_filter,
    connection_is_malicious,
    extract_from_sources,
    image_basename,
    lab_nets_from_config,
    match_streams,
    options_from_args,
    parse_sysmon_xml,
    parse_task_window,
    parse_tshark_fields,
    processes_by_guid,
    run_extract,
    select_malicious_connects,
)

GUID = "11111111-1111-1111-1111-111111111111"
UTC = datetime(2026, 9, 4, 4, 0, 0, tzinfo=timezone.utc)

EID1 = f"""<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>1</EventID>
    <TimeCreated SystemTime="2026-09-04T04:00:00.0000000Z"/>
  </System>
  <EventData>
    <Data Name="UtcTime">2026-09-04 04:00:00.000</Data>
    <Data Name="ProcessGuid">{{{GUID}}}</Data>
    <Data Name="Image">C:\\Python314\\python.exe</Data>
    <Data Name="CommandLine">python -m impacket.examples.smbclient corp/user@172.16.24.1</Data>
  </EventData>
</Event>"""

EID3 = f"""<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>3</EventID>
    <TimeCreated SystemTime="2026-09-04T04:00:01.0000000Z"/>
  </System>
  <EventData>
    <Data Name="UtcTime">2026-09-04 04:00:01.000</Data>
    <Data Name="ProcessGuid">{{{GUID}}}</Data>
    <Data Name="Image">C:\\Python314\\python.exe</Data>
    <Data Name="Protocol">tcp</Data>
    <Data Name="Initiated">true</Data>
    <Data Name="SourceIp">172.16.24.10</Data>
    <Data Name="SourcePort">49723</Data>
    <Data Name="DestinationIp">172.16.24.1</Data>
    <Data Name="DestinationPort">445</Data>
  </EventData>
</Event>"""


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


class ParseSysmonXmlTests(unittest.TestCase):
    def test_joins_eid1_and_eid3(self) -> None:
        creates, connects = parse_sysmon_xml(EID1 + EID3)
        self.assertEqual(len(creates), 1)
        self.assertEqual(len(connects), 1)
        self.assertEqual(creates[0].process_guid, GUID)
        self.assertIn("impacket", creates[0].command_line)
        self.assertEqual(connects[0].dest_port, 445)
        self.assertTrue(connects[0].initiated)
        self.assertEqual(image_basename(connects[0].image), "python.exe")


class FilterTests(unittest.TestCase):
    def test_keeps_impacket_python_to_lab(self) -> None:
        options = ExtractOptions()
        keep, cmdline = connection_is_malicious(
            _connect(), processes_by_guid([_create()]), options
        )
        self.assertTrue(keep)
        self.assertIn("impacket", cmdline)

    def test_drops_python_without_attack_cmdline(self) -> None:
        create = _create(command_line="python scripts/state.py read")
        keep, _ = connection_is_malicious(_connect(), processes_by_guid([create]), ExtractOptions())
        self.assertFalse(keep)

    def test_no_require_cmdline_keeps_state_python(self) -> None:
        create = _create(command_line="python scripts/state.py read")
        keep, _ = connection_is_malicious(
            _connect(),
            processes_by_guid([create]),
            ExtractOptions(require_cmdline=False),
        )
        self.assertTrue(keep)

    def test_drops_destination_outside_lab(self) -> None:
        keep, _ = connection_is_malicious(
            _connect(dest_ip="8.8.8.8"),
            processes_by_guid([_create()]),
            ExtractOptions(),
        )
        self.assertFalse(keep)

    def test_drops_inbound_when_initiated_only(self) -> None:
        keep, _ = connection_is_malicious(
            _connect(initiated=False),
            processes_by_guid([_create()]),
            ExtractOptions(),
        )
        self.assertFalse(keep)

    def test_time_window(self) -> None:
        later = datetime(2026, 9, 4, 5, 0, 0, tzinfo=timezone.utc)
        keep, _ = connection_is_malicious(
            _connect(),
            processes_by_guid([_create()]),
            ExtractOptions(since=later),
        )
        self.assertFalse(keep)

    def test_nmap_image_does_not_need_cmdline(self) -> None:
        keep, _ = connection_is_malicious(
            _connect(image=r"C:\Program Files (x86)\Nmap\nmap.exe", process_guid="aaaa"),
            {},
            ExtractOptions(),
        )
        self.assertTrue(keep)

    def test_select_uses_guid_join(self) -> None:
        selected = select_malicious_connects([_create()], [_connect()], ExtractOptions())
        self.assertEqual(len(selected), 1)
        self.assertIn("impacket", selected[0].command_line)


class TaskWindowTests(unittest.TestCase):
    def test_started_and_completed(self) -> None:
        text = (
            "---\n"
            "started_at: 2026-09-04T04:00:00+00:00\n"
            "completed_at: 2026-09-04T04:02:00+00:00\n"
            "---\n"
        )
        start, end = parse_task_window(text, slack_seconds=5)
        self.assertEqual(start.minute, 59)
        self.assertEqual(end.minute, 2)
        self.assertEqual(end.second, 5)

    def test_started_only_uses_default_duration(self) -> None:
        text = "---\nstarted_at: 2026-09-04T04:00:00+00:00\n---\n"
        start, end = parse_task_window(text, slack_seconds=0, default_duration_seconds=60)
        self.assertEqual(start, datetime(2026, 9, 4, 4, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 4, 4, 1, 0, tzinfo=timezone.utc))


class TsharkFilterTests(unittest.TestCase):
    def test_parse_fields_and_match_stream(self) -> None:
        text = (
            "1\t1756958401.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\n"
            "2\t1756958401.2\t172.16.24.1\t172.16.24.10\t445\t49723\t\t\t12\t\t6\t\t\t0\t1\n"
        )
        packets = parse_tshark_fields(text)
        self.assertEqual(len(packets), 2)
        self.assertEqual(packets[0].tcp_stream, "12")
        connect = _connect(utc=datetime.fromtimestamp(1756958401.0, tz=timezone.utc))
        from attacker.extract_pcap import SelectedConnect

        matched = match_streams(
            [SelectedConnect(connect=connect, command_line="x")],
            packets,
            slack_seconds=2,
        )
        self.assertEqual(matched[0].tcp_stream, "12")

    def test_outside_slack_does_not_bind_stream(self) -> None:
        packets = parse_tshark_fields(
            "1\t100.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t99\t\t6\t\t\t1\t0\n"
        )
        connect = _connect(utc=datetime.fromtimestamp(200.0, tz=timezone.utc))
        from attacker.extract_pcap import SelectedConnect

        matched = match_streams(
            [SelectedConnect(connect=connect, command_line="x")],
            packets,
            slack_seconds=2,
        )
        self.assertEqual(matched[0].tcp_stream, "")

    def test_display_filter_streams_and_scan(self) -> None:
        filt = build_display_filter(["12", "3"], ["0"])
        self.assertIn("tcp.stream eq 3", filt)
        self.assertIn("tcp.stream eq 12", filt)
        self.assertIn("udp.stream eq 0", filt)
        scan = build_display_filter(
            [],
            [],
            include_unlogged_scan=True,
            attacker_ip="172.16.24.10",
            scan_since_epoch=1.0,
            scan_until_epoch=2.0,
        )
        self.assertIn("ip.src == 172.16.24.10", scan)
        self.assertIn("icmp", scan)
        self.assertEqual(build_display_filter([], []), "frame.number == 0")

    def test_extract_from_sources_builds_filter(self) -> None:
        packets = parse_tshark_fields(
            "1\t1756958401.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\n"
        )
        connect = _connect(utc=datetime.fromtimestamp(1756958401.0, tz=timezone.utc))
        matched, filt = extract_from_sources(
            creates=[_create()],
            connects=[connect],
            packets=packets,
            options=ExtractOptions(),
        )
        self.assertEqual(matched[0].tcp_stream, "12")
        self.assertEqual(filt, "tcp.stream eq 12")


class ConfigAndCliTests(unittest.TestCase):
    def test_lab_nets_from_config(self) -> None:
        self.assertEqual(lab_nets_from_config({}), ("172.16.24.0/24",))
        self.assertEqual(
            lab_nets_from_config({"extract": {"lab_nets": ["10.0.0.0/8"]}}),
            ("10.0.0.0/8",),
        )

    def test_cli_exposes_extract(self) -> None:
        import attacker.cli as attacker_cli

        parser = attacker_cli.build_parser()
        args = parser.parse_args(
            [
                "extract",
                "--evtx",
                "sysmon.evtx",
                "--pcap",
                "mix.pcapng",
                "--out-dir",
                "out",
            ]
        )
        self.assertEqual(args.cmd, "extract")
        self.assertEqual(str(args.evtx), "sysmon.evtx")

    def test_scan_flag_requires_attacker_ip(self) -> None:
        ns = mock.Mock(
            lab_nets=None,
            since="",
            until="",
            task_md=None,
            include_unlogged_scan=True,
            attacker_ip="",
            no_require_cmdline=False,
        )
        with self.assertRaises(ValueError):
            options_from_args(ns, {})

    def test_run_extract_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml_path = root / "sysmon.xml"
            xml_path.write_text(EID1 + EID3, encoding="utf-8")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            out = root / "out"
            epoch = _connect().utc.timestamp()
            field_line = (
                f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\n"
            )

            def fake_run(args, **_kwargs):
                cmd = [str(part) for part in args]
                if "-T" in cmd and "fields" in cmd:
                    return mock.Mock(returncode=0, stdout=field_line, stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")

            args = mock.Mock(
                evtx=xml_path,
                pcap=pcap,
                out_dir=out,
                tshark="tshark",
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
            self.assertEqual(payload["connects"], 1)
            self.assertEqual(payload["display_filter"], "tcp.stream eq 12")
            records = json.loads((out / "tuples.json").read_text(encoding="utf-8"))
            self.assertEqual(records[0]["dest_port"], 445)
            self.assertIn("impacket", records[0]["command_line"])


if __name__ == "__main__":
    unittest.main()
