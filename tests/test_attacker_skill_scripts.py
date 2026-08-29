#!/usr/bin/env python3
"""Tests for ad-attack skill scripts: encoding, capture locks, log elevate."""

from __future__ import annotations

import importlib.util
import json
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


class CaptureLogsElevateTests(unittest.TestCase):
    def test_access_denied_retries_via_elevate(self) -> None:
        mod = _load_script("capture_logs.py")
        elev_json = json.dumps({"ok": True, "exit_code": 0, "output": ""})
        with mock.patch.object(
            mod.winproc,
            "run",
            side_effect=[(5, "", "Access is denied."), (0, elev_json, "")],
        ) as run:
            with mock.patch.object(
                mod,
                "_load_apt_state",
                return_value={
                    "campaign": {"local_admin": {"username": "attdemo", "password": "pw"}}
                },
            ):
                err = mod._export_one_channel(
                    "wevtutil", "Security", Path("out.evtx"), "*"
                )
        self.assertIsNone(err)
        self.assertEqual(run.call_count, 2)
        elev_argv = run.call_args_list[1][0][0]
        self.assertTrue(any(str(part).endswith("elevate.py") for part in elev_argv))
        self.assertIn("attdemo", elev_argv)

    def test_access_denied_without_creds_explains_local_admin(self) -> None:
        mod = _load_script("capture_logs.py")
        with mock.patch.object(mod.winproc, "run", return_value=(5, "", "拒绝访问。")):
            with mock.patch.object(mod, "_load_apt_state", return_value={}):
                err = mod._export_one_channel(
                    "wevtutil", "Security", Path("out.evtx"), "*"
                )
        self.assertIsNotNone(err)
        self.assertIn("campaign.local_admin", err or "")
