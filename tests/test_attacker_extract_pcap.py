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
    ExcludeFlow,
    NetworkConnect,
    PacketRow,
    ProcessCreate,
    auto_unlogged_scan,
    build_display_filter,
    build_window_query,
    connection_is_malicious,
    exclude_flows_from_config,
    export_evtx_window,
    extract_from_sources,
    flow_is_excluded,
    image_basename,
    infer_attacker_ip,
    lab_nets_from_config,
    match_streams,
    options_from_args,
    parse_sysmon_xml,
    parse_task_window,
    parse_technique_id,
    parse_tshark_fields,
    processes_by_guid,
    run_extract,
    select_malicious_connects,
    technique_pcap_stem,
    utc_query_bound,
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

    def test_drops_avp_to_kaspersky_peer(self) -> None:
        keep, _ = connection_is_malicious(
            _connect(image=r"C:\Program Files (x86)\Kaspersky Lab\avp.exe", dest_ip="172.16.24.42"),
            {},
            ExtractOptions(require_cmdline=False),
        )
        self.assertFalse(keep)
        self.assertTrue(
            flow_is_excluded(
                _connect(image=r"C:\Program Files (x86)\Kaspersky Lab\avp.exe", dest_ip="172.16.24.42"),
                ExtractOptions().exclude_flows,
            )
        )

    def test_keeps_impacket_python_to_kaspersky_peer(self) -> None:
        keep, cmdline = connection_is_malicious(
            _connect(dest_ip="172.16.24.42"),
            processes_by_guid([_create()]),
            ExtractOptions(),
        )
        self.assertTrue(keep)
        self.assertIn("impacket", cmdline)
        self.assertFalse(flow_is_excluded(_connect(dest_ip="172.16.24.42"), ExtractOptions().exclude_flows))

    def test_avp_to_other_lab_ip_is_not_exclude_hit(self) -> None:
        connect = _connect(image=r"C:\Program Files (x86)\Kaspersky Lab\avp.exe", dest_ip="172.16.24.1")
        self.assertFalse(flow_is_excluded(connect, ExtractOptions().exclude_flows))

    def test_exclude_flows_from_config(self) -> None:
        self.assertEqual(
            exclude_flows_from_config({}),
            (ExcludeFlow(peer_ip="172.16.24.42", image_contains="avp"),),
        )
        custom = exclude_flows_from_config(
            {"extract": {"exclude_flows": [{"peer_ip": "10.0.0.9", "image_contains": "klnagent"}]}}
        )
        self.assertEqual(custom, (ExcludeFlow(peer_ip="10.0.0.9", image_contains="klnagent"),))


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

    def test_window_query_uses_utc_bounds(self) -> None:
        text = (
            "---\n"
            "started_at: 2026-09-04T04:00:00+00:00\n"
            "completed_at: 2026-09-04T04:02:00+00:00\n"
            "---\n"
        )
        start, end = parse_task_window(text, slack_seconds=5)
        query = build_window_query(start, end)
        self.assertIn("TimeCreated[@SystemTime>='2026-09-04T03:59:55.000Z'", query)
        self.assertIn("@SystemTime<='2026-09-04T04:02:05.000Z']", query)
        self.assertTrue(query.startswith("*[System["))
        self.assertEqual(utc_query_bound(start), "2026-09-04T03:59:55.000Z")


class TsharkFilterTests(unittest.TestCase):
    def test_parse_fields_and_match_stream(self) -> None:
        text = (
            "1\t1756958401.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t0\t0\n"
            "2\t1756958401.2\t172.16.24.1\t172.16.24.10\t445\t49723\t\t\t12\t\t6\t\t\t0\t1\t1\t0\n"
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
            "1\t100.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t99\t\t6\t\t\t1\t0\t0\t1\n"
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
        self.assertNotIn("ip.addr != 172.16.24.42", scan)
        dropped = build_display_filter(
            [],
            [],
            include_unlogged_scan=True,
            attacker_ip="172.16.24.10",
            scan_since_epoch=1.0,
            scan_until_epoch=2.0,
            exclude_peer_ips=["172.16.24.42"],
        )
        self.assertIn("ip.addr != 172.16.24.42", dropped)
        self.assertEqual(build_display_filter([], []), "frame.number == 0")

    def test_extract_from_sources_builds_filter(self) -> None:
        packets = parse_tshark_fields(
            "1\t1756958401.0\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"
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

    def test_extract_from_sources_scan_excludes_kaspersky_peer(self) -> None:
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
        self.assertIn("ip.addr != 172.16.24.42", filt)


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
        self.assertIsNone(args.security_evtx)
        dated = parser.parse_args(
            [
                "extract",
                "--date",
                "2026-09-06",
                "--evtx",
                "sysmon.evtx",
                "--security-evtx",
                "security.evtx",
                "--dc-security-evtx",
                "dc.evtx",
                "--pcap",
                "mix.pcapng",
            ]
        )
        self.assertEqual(dated.date, "2026-09-06")
        self.assertIsNone(dated.out_dir)
        self.assertEqual(str(dated.security_evtx), "security.evtx")
        self.assertEqual(str(dated.dc_security_evtx), "dc.evtx")

    def test_scan_flag_without_attacker_ip_is_deferred(self) -> None:
        ns = mock.Mock(
            lab_nets=None,
            since="",
            until="",
            date="",
            task_md=None,
            include_unlogged_scan=True,
            attacker_ip="",
            no_require_cmdline=False,
        )
        options = options_from_args(ns, {})
        self.assertTrue(options.include_unlogged_scan)
        self.assertEqual(options.attacker_ip, "")

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
                f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"
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
                date="",
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
            self.assertTrue(records[0]["tcp_complete"])

    def test_technique_id_and_stem(self) -> None:
        self.assertEqual(
            parse_technique_id(
                "Use the ad-attack skill: execute discovery.port-scan against host 172.16.24.11."
            ),
            "discovery.port-scan",
        )
        self.assertEqual(
            technique_pcap_stem("ed6d1a8d95fa463c", "discovery.port-scan"),
            "ed6d1a8d95fa463c_discovery_port-scan",
        )
        self.assertTrue(auto_unlogged_scan("discovery.port-scan", "172.16.24.10", False))
        self.assertFalse(auto_unlogged_scan("credential.brute-user", "172.16.24.10", False))

    def test_batch_writes_per_technique_pcaps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "2026-09-04"
            logs.mkdir()
            (logs / "aaa111aaa111aaaa.md").write_text(
                "---\n"
                "task_id: aaa111aaa111aaaa\n"
                "started_at: 2026-09-04T04:00:00+00:00\n"
                "completed_at: 2026-09-04T04:02:00+00:00\n"
                "task: Use the ad-attack skill: execute credential.brute-user against domain.\n"
                "---\n",
                encoding="utf-8",
            )
            (logs / "bbb222bbb222bbbb.md").write_text(
                "---\n"
                "task_id: bbb222bbb222bbbb\n"
                "started_at: 2026-09-04T04:00:00+00:00\n"
                "completed_at: 2026-09-04T04:02:00+00:00\n"
                "task: Use the ad-attack skill: execute discovery.port-scan against host 172.16.24.11.\n"
                "---\n",
                encoding="utf-8",
            )
            (logs / "skip.md").write_text("---\nplanned_time: 10:00\n---\n", encoding="utf-8")
            xml_path = root / "sysmon.xml"
            xml_path.write_text(EID1 + EID3, encoding="utf-8")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            epoch = _connect().utc.timestamp()
            field_line = (
                f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"
            )

            def fake_run(args, **_kwargs):
                cmd = [str(part) for part in args]
                if "-T" in cmd and "fields" in cmd:
                    return mock.Mock(returncode=0, stdout=field_line, stderr="")
                if "-w" in cmd:
                    dest = Path(cmd[cmd.index("-w") + 1])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"pcap")
                return mock.Mock(returncode=0, stdout="", stderr="")

            args = mock.Mock(
                evtx=xml_path,
                pcap=pcap,
                out_dir=None,
                date="2026-09-04",
                tshark="tshark",
                wevtutil="wevtutil",
                tuples_name="tuples.json",
                lab_nets=["172.16.24.0/24"],
                since="",
                until="",
                task_md=None,
                include_unlogged_scan=False,
                attacker_ip="172.16.24.10",
                no_require_cmdline=False,
            )
            with mock.patch(
                "dataset_processor.extract.resolve_day_logs_dir",
                return_value=logs,
            ):
                payload = run_extract(args, config={}, run_fn=fake_run)
            self.assertTrue(payload["ok"])
            names = {Path(item["pcap"]).name for item in payload["tasks"]}
            self.assertEqual(
                names,
                {
                    "aaa111aaa111aaaa_credential_brute-user.pcapng",
                    "bbb222bbb222bbbb_discovery_port-scan.pcapng",
                },
            )
            scan = next(item for item in payload["tasks"] if item["technique"] == "discovery.port-scan")
            self.assertIn("icmp", scan["display_filter"])
            self.assertEqual(len(payload["skipped"]), 1)
            self.assertIn("no started_at", payload["skipped"][0]["reason"])
            records = json.loads((logs / "tuples.json").read_text(encoding="utf-8"))
            self.assertTrue(all("task_id" in row and "technique" in row for row in records))
            self.assertTrue((logs / "benign.pcapng").is_file())
            self.assertFalse((logs / "malicious.pcapng").is_file())


def _write_day_transcripts(logs: Path) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "aaa111aaa111aaaa.md").write_text(
        "---\n"
        "task_id: aaa111aaa111aaaa\n"
        "started_at: 2026-09-04T04:00:00+00:00\n"
        "completed_at: 2026-09-04T04:02:00+00:00\n"
        "task: Use the ad-attack skill: execute credential.brute-user against domain.\n"
        "---\n",
        encoding="utf-8",
    )
    (logs / "bbb222bbb222bbbb.md").write_text(
        "---\n"
        "task_id: bbb222bbb222bbbb\n"
        "started_at: 2026-09-04T04:00:00+00:00\n"
        "completed_at: 2026-09-04T04:02:00+00:00\n"
        "task: Use the ad-attack skill: execute discovery.port-scan against host 172.16.24.11.\n"
        "---\n",
        encoding="utf-8",
    )
    (logs / "skip.md").write_text("---\nplanned_time: 10:00\n---\n", encoding="utf-8")


def _extract_fake_run(field_line: str, *, empty_epl: bool = False, epl_calls: list | None = None):
    def fake_run(args, **_kwargs):
        cmd = [str(part) for part in args]
        if "qe" in cmd:
            return mock.Mock(returncode=0, stdout=EID1 + EID3, stderr="")
        if "epl" in cmd:
            if epl_calls is not None:
                epl_calls.append(cmd)
            dest = Path(cmd[3])
            if empty_epl:
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="No events were found that match the specified selection criteria.",
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"evtx")
            return mock.Mock(returncode=0, stdout="", stderr="")
        if "-T" in cmd and "fields" in cmd:
            return mock.Mock(returncode=0, stdout=field_line, stderr="")
        if "-w" in cmd:
            dest = Path(cmd[cmd.index("-w") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"pcap")
        return mock.Mock(returncode=0, stdout="", stderr="")

    return fake_run


def _epoch_field_line() -> str:
    epoch = _connect().utc.timestamp()
    return f"1\t{epoch:.1f}\t172.16.24.10\t172.16.24.1\t49723\t445\t\t\t12\t\t6\t\t\t1\t0\t1\t0\n"


class EvtxWindowExportTests(unittest.TestCase):
    def test_epl_argv_uses_file_log_and_time_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "day.evtx"
            dest = Path(tmp) / "slice.evtx"
            src.write_bytes(b"src")
            seen: list[list[str]] = []

            def fake_run(args, **_kwargs):
                cmd = [str(part) for part in args]
                seen.append(cmd)
                Path(cmd[3]).write_bytes(b"out")
                return mock.Mock(returncode=0, stdout="", stderr="")

            since = datetime(2026, 9, 4, 3, 59, 55, tzinfo=timezone.utc)
            until = datetime(2026, 9, 4, 4, 2, 5, tzinfo=timezone.utc)
            written = export_evtx_window(src, dest, since, until, run_fn=fake_run)
            self.assertEqual(written, dest)
            self.assertEqual(seen[0][1], "epl")
            self.assertEqual(seen[0][2], str(src))
            self.assertEqual(seen[0][3], str(dest))
            self.assertIn("/lf:true", seen[0])
            query = next(part[3:] for part in seen[0] if part.startswith("/q:"))
            self.assertIn("TimeCreated[@SystemTime>='2026-09-04T03:59:55.000Z'", query)
            self.assertIn("@SystemTime<='2026-09-04T04:02:05.000Z']", query)

    def test_empty_window_is_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "day.evtx"
            dest = Path(tmp) / "slice.evtx"
            src.write_bytes(b"src")

            def fake_run(args, **_kwargs):
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="The specified query did not return any events. No events were found.",
                )

            written = export_evtx_window(
                src,
                dest,
                datetime(2026, 9, 4, 4, 0, tzinfo=timezone.utc),
                datetime(2026, 9, 4, 4, 2, tzinfo=timezone.utc),
                run_fn=fake_run,
            )
            self.assertIsNone(written)
            self.assertFalse(dest.exists())


class InferAttackerIpTests(unittest.TestCase):
    def test_majority_initiated_attack_image_in_lab(self) -> None:
        ip = infer_attacker_ip(
            [
                _connect(source_ip="172.16.24.10"),
                _connect(source_ip="172.16.24.10"),
                _connect(source_ip="8.8.8.8"),
                _connect(initiated=False, source_ip="172.16.24.99"),
                _connect(image=r"C:\Windows\System32\svchost.exe", source_ip="172.16.24.11"),
            ],
            ("172.16.24.0/24",),
        )
        self.assertEqual(ip, "172.16.24.10")

    def test_empty_when_no_attack_connects(self) -> None:
        self.assertEqual(
            infer_attacker_ip(
                [_connect(image=r"C:\Windows\System32\svchost.exe")],
                ("172.16.24.0/24",),
            ),
            "",
        )


class BatchEvtxAndScanTests(unittest.TestCase):
    def test_batch_writes_evtx_and_scan_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "2026-09-04"
            _write_day_transcripts(logs)
            sysmon = root / "sysmon.evtx"
            security = root / "security.evtx"
            dc = root / "dc.evtx"
            for path in (sysmon, security, dc):
                path.write_bytes(b"evtx")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            epl_calls: list[list[str]] = []
            args = mock.Mock(
                evtx=sysmon,
                security_evtx=security,
                dc_security_evtx=dc,
                pcap=pcap,
                out_dir=None,
                date="2026-09-04",
                tshark="tshark",
                wevtutil="wevtutil",
                tuples_name="tuples.json",
                lab_nets=["172.16.24.0/24"],
                since="",
                until="",
                task_md=None,
                include_unlogged_scan=False,
                attacker_ip="172.16.24.10",
                no_require_cmdline=False,
            )
            with mock.patch(
                "dataset_processor.extract.resolve_day_logs_dir",
                return_value=logs,
            ):
                payload = run_extract(
                    args,
                    config={},
                    run_fn=_extract_fake_run(_epoch_field_line(), epl_calls=epl_calls),
                )
            self.assertTrue(payload["ok"])
            brute = next(item for item in payload["tasks"] if item["technique"] == "credential.brute-user")
            scan = next(item for item in payload["tasks"] if item["technique"] == "discovery.port-scan")
            self.assertTrue(str(brute["sysmon"]).endswith("aaa111aaa111aaaa_credential_brute-user_Sysmon.evtx"))
            self.assertTrue(str(brute["security"]).endswith("aaa111aaa111aaaa_credential_brute-user_Security.evtx"))
            self.assertTrue(str(brute["dc_security"]).endswith("aaa111aaa111aaaa_credential_brute-user_DC_Security.evtx"))
            self.assertTrue(Path(brute["sysmon"]).is_file())
            self.assertIn("icmp", scan["display_filter"])
            self.assertTrue(any("/lf:true" in " ".join(cmd) for cmd in epl_calls))
            self.assertEqual(len(payload["skipped"]), 1)

    def test_infers_attacker_ip_for_port_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "2026-09-04"
            _write_day_transcripts(logs)
            sysmon = root / "sysmon.evtx"
            sysmon.write_bytes(b"evtx")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            args = mock.Mock(
                evtx=sysmon,
                security_evtx=None,
                dc_security_evtx=None,
                pcap=pcap,
                out_dir=None,
                date="2026-09-04",
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
            with mock.patch(
                "dataset_processor.extract.resolve_day_logs_dir",
                return_value=logs,
            ):
                payload = run_extract(
                    args,
                    config={},
                    run_fn=_extract_fake_run(_epoch_field_line()),
                )
            self.assertEqual(payload["attacker_ip"], "172.16.24.10")
            scan = next(item for item in payload["tasks"] if item["technique"] == "discovery.port-scan")
            self.assertIn("icmp", scan["display_filter"])
            self.assertNotIn("warnings", scan)

    def test_scan_without_ip_still_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "2026-09-04"
            _write_day_transcripts(logs)
            sysmon = root / "sysmon.evtx"
            sysmon.write_bytes(b"evtx")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")

            def no_connects(args, **_kwargs):
                cmd = [str(part) for part in args]
                if "qe" in cmd:
                    return mock.Mock(returncode=0, stdout=EID1, stderr="")
                if "epl" in cmd:
                    dest = Path(cmd[3])
                    dest.write_bytes(b"evtx")
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if "-T" in cmd:
                    return mock.Mock(returncode=0, stdout="", stderr="")
                if "-w" in cmd:
                    dest = Path(cmd[cmd.index("-w") + 1])
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"pcap")
                return mock.Mock(returncode=0, stdout="", stderr="")

            args = mock.Mock(
                evtx=sysmon,
                security_evtx=None,
                dc_security_evtx=None,
                pcap=pcap,
                out_dir=None,
                date="2026-09-04",
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
            with mock.patch(
                "dataset_processor.extract.resolve_day_logs_dir",
                return_value=logs,
            ):
                payload = run_extract(args, config={}, run_fn=no_connects)
            scan = next(item for item in payload["tasks"] if item["technique"] == "discovery.port-scan")
            self.assertIsNone(scan["pcap"])
            self.assertTrue(Path(scan["sysmon"]).is_file())
            self.assertIn("unlogged scan omitted", scan["warnings"])
            self.assertIn("empty pcap filter skipped", scan["warnings"])
            skipped_reasons = [item["reason"] for item in payload["skipped"]]
            self.assertTrue(all("attacker-ip" not in reason for reason in skipped_reasons))

    def test_empty_evtx_window_does_not_fail_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "2026-09-04"
            _write_day_transcripts(logs)
            sysmon = root / "sysmon.evtx"
            sysmon.write_bytes(b"evtx")
            pcap = root / "mix.pcapng"
            pcap.write_bytes(b"pcap")
            args = mock.Mock(
                evtx=sysmon,
                security_evtx=None,
                dc_security_evtx=None,
                pcap=pcap,
                out_dir=None,
                date="2026-09-04",
                tshark="tshark",
                wevtutil="wevtutil",
                tuples_name="tuples.json",
                lab_nets=["172.16.24.0/24"],
                since="",
                until="",
                task_md=None,
                include_unlogged_scan=False,
                attacker_ip="172.16.24.10",
                no_require_cmdline=False,
            )
            with mock.patch(
                "dataset_processor.extract.resolve_day_logs_dir",
                return_value=logs,
            ):
                payload = run_extract(
                    args,
                    config={},
                    run_fn=_extract_fake_run(_epoch_field_line(), empty_epl=True),
                )
            self.assertTrue(payload["ok"])
            brute = next(item for item in payload["tasks"] if item["technique"] == "credential.brute-user")
            self.assertIsNone(brute["sysmon"])
            self.assertIn("sysmon: no events were found", brute["warnings"])


if __name__ == "__main__":
    unittest.main()
