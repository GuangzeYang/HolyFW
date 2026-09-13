#!/usr/bin/env python3
"""Tests for ad-attack skill scripts: encoding, capture locks, log elevate."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "attacker" / "skills" / "ad-attack" / "scripts"
TSHARK_D_TEXT = (
    "1. \\Device\\NPF_{ED8FB947-4C8F-40A1-AC29-DDCD187D4410} (本地连接* 8)\n"
    "4. \\Device\\NPF_{AA2AD301-E011-4D38-B7B0-9EC8C22A5B29} (Ethernet1)\n"
)


def _load_script(filename: str):
    path = SCRIPTS / filename
    spec = importlib.util.spec_from_file_location(f"ad_attack_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _winproc():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import winproc

    return winproc


class WinprocDecodeTests(unittest.TestCase):
    def test_utf8_tshark_line_does_not_need_gbk(self) -> None:
        raw = TSHARK_D_TEXT.encode("utf-8")
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("gbk")
        text = _winproc().decode_bytes(raw)
        self.assertIn("Ethernet1", text)
        self.assertIn("本地连接", text)
        self.assertEqual(raw[57], 0xAC)

    def test_local_admin_creds_from_campaign_and_users(self) -> None:
        winproc = _winproc()
        self.assertEqual(
            winproc.local_admin_creds(
                {"campaign": {"local_admin": {"username": "attdemo", "password": "pw"}}}
            ),
            ("attdemo", "pw"),
        )
        self.assertEqual(
            winproc.local_admin_creds(
                {"users": [{"username": "bob", "password": "x", "is_local_admin": True}]}
            ),
            ("bob", "x"),
        )
        self.assertEqual(
            winproc.local_admin_creds(
                {"campaign": {"local_admin_account": {"name": "NDRTEST\\attdemo", "password": "pw"}}}
            ),
            ("NDRTEST\\attdemo", "pw"),
        )
        self.assertEqual(
            winproc.local_admin_creds(
                {
                    "users": [
                        {
                            "username": "attdemo",
                            "password": "pw",
                            "is_local_admin_on_attack_host": True,
                        }
                    ]
                }
            ),
            ("attdemo", "pw"),
        )
        self.assertIsNone(winproc.local_admin_creds({"users": [{"username": "bob"}]}))

    def test_access_denied_markers(self) -> None:
        winproc = _winproc()
        self.assertTrue(winproc.looks_like_access_denied(5, ""))
        self.assertTrue(winproc.looks_like_access_denied(1, "拒绝访问。"))
        self.assertTrue(winproc.looks_like_access_denied(1, "Access is denied."))
        self.assertFalse(winproc.looks_like_access_denied(1, "not found"))


class TsharkInterfaceTests(unittest.TestCase):
    def test_keeps_stdout_even_when_returncode_nonzero(self) -> None:
        env = _load_script("check_environment.py")
        env._run = lambda _args, timeout=30: (1, TSHARK_D_TEXT, "warning")
        probe = env.check_tshark_interfaces("tshark")
        self.assertEqual(probe["returncode"], 1)
        self.assertTrue(any("Ethernet1" in line for line in probe["interfaces"]))
        self.assertIn("warning", probe["stderr"])


class CaptureTrafficLockTests(unittest.TestCase):
    def test_reclaim_drops_dead_pid_lock(self) -> None:
        mod = _load_script("capture_traffic.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            mod.output_dir = lambda: out
            sf = out / ".capture_traffic_state.json"
            sf.write_text(
                json.dumps({"pid": 4242, "label": "old", "file": "x.pcapng"}),
                encoding="utf-8",
            )
            with mock.patch.object(mod.winproc, "pid_alive", return_value=False):
                info = mod._reclaim_stale_capture()
            self.assertEqual(info["reclaimed"], "dropped_dead")
            self.assertFalse(sf.exists())

    def test_reclaim_stops_live_pid(self) -> None:
        mod = _load_script("capture_traffic.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            mod.output_dir = lambda: out
            sf = out / ".capture_traffic_state.json"
            sf.write_text(json.dumps({"pid": 99, "label": "live"}), encoding="utf-8")
            with mock.patch.object(mod.winproc, "pid_alive", return_value=True):
                with mock.patch.object(mod, "_stop_process") as stop:
                    info = mod._reclaim_stale_capture()
            stop.assert_called_once_with(99)
            self.assertEqual(info["reclaimed"], "stopped_live")
            self.assertFalse(sf.exists())

    def test_start_stop_do_not_write_pcap(self) -> None:
        mod = _load_script("capture_traffic.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            mod.output_dir = lambda: out
            start_args = mock.Mock(label="discovery.port-scan", iface=None)
            self.assertEqual(mod.cmd_start(start_args), 0)
            self.assertFalse(list(out.glob("*.pcapng")))
            state = json.loads((out / ".capture_traffic_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["skipped"], "live pcap disabled")
            self.assertEqual(mod.cmd_stop(mock.Mock()), 0)
            self.assertFalse((out / ".capture_traffic_state.json").exists())
            self.assertFalse(list(out.glob("*.pcapng")))


class CaptureLogsElevateTests(unittest.TestCase):
    def _prepare_stop(self, mod, tmp: Path) -> None:
        mod.output_dir = lambda: tmp
        (tmp / ".capture_logs_state.json").write_text(
            json.dumps(
                {
                    "label": "discovery_port-scan",
                    "started_at": "2026-09-10T00:00:00.000Z",
                    "log": "Security",
                    "channels": ["Security"],
                }
            ),
            encoding="utf-8",
        )

    def test_stop_retries_elevate_without_flag(self) -> None:
        mod = _load_script("capture_logs.py")
        denied = mock.Mock(returncode=5, stdout="", stderr="Access is denied.")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._prepare_stop(mod, out)
            with mock.patch.object(mod.subprocess, "run", return_value=denied):
                with mock.patch.object(
                    mod, "_elevation_account", return_value=("NDRTEST\\attdemo", "pw")
                ):
                    with mock.patch.object(mod, "_run_epl_elevated", return_value=True) as elev:
                        rc = mod.cmd_stop(mock.Mock())
            self.assertEqual(rc, 0)
            elev.assert_called_once()
            self.assertFalse((out / ".capture_logs_state.json").exists())

    def test_access_denied_without_creds_explains_local_admin(self) -> None:
        import io

        mod = _load_script("capture_logs.py")
        denied = mock.Mock(returncode=5, stdout="", stderr="拒绝访问。")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self._prepare_stop(mod, out)
            buf = io.StringIO()
            with mock.patch.object(mod.subprocess, "run", return_value=denied):
                with mock.patch.object(mod, "_elevation_account", return_value=None):
                    with mock.patch("sys.stdout", buf):
                        rc = mod.cmd_stop(mock.Mock())
            self.assertEqual(rc, 1)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["errors"])
            self.assertIn("campaign.local_admin_account", payload["errors"][0])


class WriteFilterTests(unittest.TestCase):
    def test_writes_task_id_txt(self) -> None:
        mod = _load_script("write_filter.py")
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HOLYFW_ATTACKER_TASK_ID": "abc123abc123abcd",
                "HOLYFW_ATTACKER_OUTPUT_DIR": tmp,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                rc = mod.main(["--expression", "ip.addr == 172.16.24.10 && kerberos"])
            self.assertEqual(rc, 0)
            dest = Path(tmp) / "abc123abc123abcd.txt"
            self.assertEqual(dest.read_text(encoding="utf-8").strip(), "ip.addr == 172.16.24.10 && kerberos")

    def test_rejects_empty_and_command_or_time(self) -> None:
        mod = _load_script("write_filter.py")
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "HOLYFW_ATTACKER_TASK_ID": "abc123abc123abcd",
                "HOLYFW_ATTACKER_OUTPUT_DIR": tmp,
            }
            with mock.patch.dict(os.environ, env, clear=False):
                self.assertEqual(mod.main(["--expression", "   "]), 1)
                self.assertEqual(
                    mod.main(["--expression", "tshark -r mix.pcapng -Y ip -w out.pcapng"]),
                    1,
                )
                self.assertEqual(
                    mod.main(["--expression", "ip.src == 1.1.1.1 && frame.time_epoch >= 1"]),
                    1,
                )
            self.assertFalse((Path(tmp) / "abc123abc123abcd.txt").exists())
