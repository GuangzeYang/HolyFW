#!/usr/bin/env python3
"""Tests for soldier long-running logging and dispatch response behavior."""

from __future__ import annotations

import json
import logging
import logging.handlers
import shutil
import sys
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
            mock.patch("soldier.soldier.execute_command", return_value=("ok", "", 0, "successed", None)),
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
            mock.patch("soldier.soldier.execute_command", return_value=("ok", "", 0, "successed", None)),
            mock.patch("soldier.soldier.save_task_record"),
            mock.patch("soldier.soldier.save_command_output", return_value=Path("out.txt")),
            mock.patch("soldier.soldier.send_report", return_value=(None, "boom")),
            mock.patch("soldier.soldier.enqueue_pending_report"),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(conn.sent, [])
        self.assertTrue(conn.closed)

    def test_execute_command_truncates_large_output(self) -> None:
        command = f'"{sys.executable}" -c "print(\'x\' * 40)"'

        out, err, exit_code, status, msg = soldier.execute_command(
            command,
            timeout_sec=10,
            max_output_bytes=10,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(status, "successed")
        self.assertIn("...[truncated]", out)
        self.assertEqual(err, "")
        self.assertEqual(msg, "output truncated")

    def test_pending_report_retries_three_times_then_moves_to_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            payload = {
                "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                "status": "successed",
            }
            soldier.enqueue_pending_report("127.0.0.1", 38471, payload, "initial", base_dir=base_dir)

            with mock.patch("soldier.soldier.send_report", return_value=(None, "still down")):
                soldier.process_pending_reports_once(base_dir)
                soldier.process_pending_reports_once(base_dir)
                soldier.process_pending_reports_once(base_dir)

            pending = soldier.pending_reports_path(base_dir).read_text(encoding="utf-8")
            failed_lines = soldier.failed_reports_path(base_dir).read_text(encoding="utf-8").splitlines()
            self.assertEqual(pending, "")
            self.assertEqual(len(failed_lines), 1)
            failed = json.loads(failed_lines[0])
            self.assertEqual(failed["attempts"], 3)
            self.assertEqual(failed["last_error"], "still down")


if __name__ == "__main__":
    unittest.main()
