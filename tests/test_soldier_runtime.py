#!/usr/bin/env python3
"""Tests for soldier long-running logging and dispatch response behavior."""

from __future__ import annotations

import json
import logging
import logging.handlers
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import soldier.soldier as soldier


def _clear_root_handlers() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass


class FakeDispatchConnection:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.timeout: int | float | None = None
        self.closed = False

    def settimeout(self, timeout: int | float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class SoldierRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        _clear_root_handlers()

    def test_configure_uses_dated_file_handler_without_timed_rotation(self) -> None:
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        self.addCleanup(_clear_root_handlers)
        logs = Path(td)

        path = soldier.configure_soldier_root_logging(logs)

        expected = logs / f"soldier_{date.today().isoformat()}.log"
        self.assertEqual(path.resolve(), expected.resolve())
        self.assertTrue(path.is_file())

        dated_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "name", None) == soldier.SOLDIER_DATED_FILE_HANDLER_NAME
        ]
        self.assertEqual(len(dated_handlers), 1)
        self.assertIsInstance(dated_handlers[0], logging.FileHandler)
        self.assertNotIsInstance(dated_handlers[0], logging.handlers.TimedRotatingFileHandler)

    def test_reattach_replaces_only_soldier_dated_file_handler(self) -> None:
        td = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(td, ignore_errors=True))
        self.addCleanup(_clear_root_handlers)
        logs = Path(td)
        soldier.configure_soldier_root_logging(logs)

        other_day = date.today() + timedelta(days=1)
        new_path = soldier.reattach_soldier_dated_file_handler(logs, target_day=other_day)

        expected = logs / f"soldier_{other_day.isoformat()}.log"
        self.assertEqual(new_path.resolve(), expected.resolve())
        dated_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "name", None) == soldier.SOLDIER_DATED_FILE_HANDLER_NAME
        ]
        self.assertEqual(len(dated_handlers), 1)
        self.assertEqual(Path(dated_handlers[0].baseFilename).resolve(), expected.resolve())

        console_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if not isinstance(handler, logging.FileHandler)
        ]
        self.assertGreaterEqual(len(console_handlers), 1)

    def test_successful_report_does_not_write_to_dispatch_socket(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch(
                "soldier.soldier.subprocess.run",
                return_value=mock.Mock(stdout=b"ok", stderr=b"", returncode=0),
            ),
            mock.patch("soldier.soldier.save_task_record"),
            mock.patch("soldier.soldier.save_command_output", return_value=Path("out.txt")),
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(conn.sent, [])
        self.assertTrue(conn.closed)

    def test_failed_report_does_not_write_to_dispatch_socket(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch(
                "soldier.soldier.subprocess.run",
                return_value=mock.Mock(stdout=b"ok", stderr=b"", returncode=0),
            ),
            mock.patch("soldier.soldier.save_task_record"),
            mock.patch("soldier.soldier.save_command_output", return_value=Path("out.txt")),
            mock.patch("soldier.soldier.send_report", return_value=(None, "boom")),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(conn.sent, [])
        self.assertTrue(conn.closed)


if __name__ == "__main__":
    unittest.main()
