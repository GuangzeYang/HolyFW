#!/usr/bin/env python3
"""Tests for soldier long-running logging and dispatch response behavior."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import soldier.soldier as soldier


def _ok_result() -> soldier.CommandResult:
    return soldier.CommandResult(0, "Success", None)


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
        logging.info("Received — echo ok", extra={"task": "abc123"})
        content = path.read_text(encoding="utf-8")
        self.assertRegex(content, r"\d{4}-\d{2}-\d{2} .* - INFO - abc123 - Received — echo ok")

        dated_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "name", None) == soldier.SOLDIER_DATED_FILE_HANDLER_NAME
        ]
        self.assertEqual(len(dated_handlers), 1)
        self.assertIsInstance(dated_handlers[0], logging.FileHandler)
        self.assertNotIsInstance(dated_handlers[0], logging.handlers.TimedRotatingFileHandler)
        console_handlers = [
            handler
            for handler in logging.getLogger().handlers
            if getattr(handler, "name", None) == soldier.SOLDIER_CONSOLE_HANDLER_NAME
        ]
        self.assertEqual(len(console_handlers), 1)
        self.assertTrue(
            any(isinstance(item, soldier._ConsoleVisibilityFilter) for item in console_handlers[0].filters)
        )

    def test_console_filter_allows_system_and_to_console_task_lines(self) -> None:
        filt = soldier._ConsoleVisibilityFilter()
        system = logging.LogRecord("root", logging.INFO, __file__, 0, "boot", (), None)
        system.task = "system"
        hidden = logging.LogRecord("root", logging.INFO, __file__, 0, "Started at t", (), None)
        hidden.task = "abc123"
        hidden.to_console = False
        shown = logging.LogRecord("root", logging.INFO, __file__, 0, "Success", (), None)
        shown.task = "abc123"
        shown.to_console = True
        self.assertTrue(filt.filter(system))
        self.assertFalse(filt.filter(hidden))
        self.assertTrue(filt.filter(shown))

    def test_append_task_execution_log_writes_task_id_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = soldier.append_task_execution_log(
                task_id="c01b883dfefd4c85",
                task_ref="2026-04-29_accountancy_c01b883dfefd4c85",
                date_str="2026-04-29",
                received_at="2026-04-29T09:08:09+08:00",
                command='opencode run --auto "Check email"',
                status="successed",
                exit_code=0,
                argv=["opencode", "run", "--auto", "Check email"],
                base_dir=base,
            )
            self.assertEqual(
                path,
                base / "runtime" / "tasks" / "2026-04-29" / "c01b883dfefd4c85.md",
            )
            text = path.read_text(encoding="utf-8")
            yaml_block = text.split("---", 2)[1]
            for key in (
                "updated_at",
                "completed_at",
                "execution_deadline",
                "exit_code",
                "message",
                "report",
                "command",
            ):
                self.assertNotIn(f"{key}:", yaml_block)
            self.assertIn("## Command", text)
            self.assertNotIn("## Output", text)
            self.assertNotIn("## stdout", text)
            self.assertNotIn("## stderr", text)
            payload = soldier.parse_task_markdown(text)
            self.assertEqual(payload["received_at"], "2026-04-29T09:08:09+08:00")
            self.assertIn("opencode", payload["command"])
            self.assertIn("run", payload["command"])
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["result_status"], "successed")
            self.assertEqual(payload["outcome"], "Success")
            self.assertNotIn("exit_code", payload)
            self.assertNotIn("report", payload)
            report = soldier.report_from_task_record(payload)
            assert report is not None
            self.assertNotIn("stdout", report)
            self.assertNotIn("stderr", report)
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(payload["task_id"], "c01b883dfefd4c85")

    def test_pending_and_task_records_live_under_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pending = soldier.pending_reports_path(base)
            failed = soldier.failed_reports_path(base)
            record = soldier.task_record_path("c01b883dfefd4c85", "2026-04-29", base_dir=base)
            self.assertEqual(pending, base / "runtime" / "pending_reports.jsonl")
            self.assertEqual(failed, base / "runtime" / "failed_reports.jsonl")
            self.assertEqual(
                record,
                base / "runtime" / "tasks" / "2026-04-29" / "c01b883dfefd4c85.md",
            )

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

    def test_successful_task_acknowledges_before_execution(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("claimed", {})),
            mock.patch("soldier.soldier.execute_command", return_value=_ok_result()),
            mock.patch("soldier.soldier.append_task_execution_log", return_value=Path("runtime/tasks/2026-04-29/c01b883dfefd4c85.md")),
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(len(conn.sent), 1)
        acknowledgment = json.loads(conn.sent[0].decode("utf-8"))
        self.assertTrue(acknowledgment["ok"])
        self.assertEqual(acknowledgment["status"], "accepted")
        self.assertTrue(conn.closed)

    def test_failed_report_still_has_single_acceptance_acknowledgment(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("claimed", {})),
            mock.patch("soldier.soldier.execute_command", return_value=_ok_result()),
            mock.patch("soldier.soldier.append_task_execution_log", return_value=Path("runtime/tasks/2026-04-29/c01b883dfefd4c85.md")),
            mock.patch("soldier.soldier.send_report", return_value=(None, "boom")),
            mock.patch("soldier.soldier.enqueue_pending_report"),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(len(conn.sent), 1)
        acknowledgment = json.loads(conn.sent[0].decode("utf-8"))
        self.assertEqual(acknowledgment["status"], "accepted")
        self.assertTrue(conn.closed)

    def test_execute_command_success_discards_process_output(self) -> None:
        command = f'"{sys.executable}" -c "print(\'x\' * 40)"'
        result = soldier.execute_command(command, timeout_sec=10)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.outcome, "Success")
        self.assertEqual(result.report_status, "successed")
        self.assertIsNone(result.message)
        self.assertFalse(hasattr(result, "stdout"))

    def test_execute_command_nonzero_exit_is_fail_not_error(self) -> None:
        command = f'"{sys.executable}" -c "raise SystemExit(7)"'
        result = soldier.execute_command(command, timeout_sec=10)
        self.assertEqual(result.outcome, "Fail")
        self.assertEqual(result.report_status, "failed")
        self.assertEqual(result.exit_code, 7)

    def test_execute_command_missing_binary_is_error(self) -> None:
        result = soldier.execute_command(
            ["holyfw-missing-opencode-binary"],
            timeout_sec=5,
        )
        self.assertEqual(result.outcome, "Error")
        self.assertEqual(result.report_status, "failed")

    def test_task_record_glob_finds_existing_date_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            first = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "opencode run --auto 'echo ok'",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            try:
                self.assertEqual(first.status, "claimed")
                found = soldier.find_existing_task_record("c01b883dfefd4c85", base_dir=base_dir)
                self.assertEqual(
                    found,
                    base_dir / "runtime" / "tasks" / "2026-04-29" / "c01b883dfefd4c85.md",
                )
                reused = soldier.resolve_task_record_path(
                    "c01b883dfefd4c85",
                    "2026-04-30",
                    base_dir=base_dir,
                )
                self.assertEqual(reused, found)
            finally:
                if first.handle is not None:
                    first.handle.close()

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

    def test_enqueue_pending_report_drops_legacy_streams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            payload = {
                "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                "status": "successed",
                "exit_code": 0,
                "stdout": "x" * 100,
                "stderr": "err",
            }
            soldier.enqueue_pending_report("127.0.0.1", 38471, payload, "initial", base_dir=base_dir)
            stored = json.loads(soldier.pending_reports_path(base_dir).read_text(encoding="utf-8"))
            self.assertNotIn("stdout", stored["payload"])
            self.assertNotIn("stderr", stored["payload"])
            self.assertEqual(stored["payload"]["status"], "successed")

    def test_claim_task_execution_blocks_running_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            first = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            try:
                second = soldier.claim_task_execution(
                    "2026-04-29",
                    "c01b883dfefd4c85",
                    "2026-04-29_accountancy_c01b883dfefd4c85",
                    "echo ok",
                    "2026-04-29T01:00:01+00:00",
                    999,
                    base_dir=base_dir,
                )
                self.assertEqual(first.status, "claimed")
                self.assertIsNotNone(first.handle)
                self.assertEqual(second.status, "running")
                self.assertEqual((second.record or {})["status"], "running")
                payload = soldier.parse_task_markdown(
                    soldier.task_record_path(
                        "c01b883dfefd4c85", "2026-04-29", base_dir=base_dir
                    ).read_text(encoding="utf-8")
                )
                self.assertEqual(payload["task_id"], "c01b883dfefd4c85")
                self.assertEqual(payload["status"], "running")
                yaml_block = (
                    soldier.task_record_path(
                        "c01b883dfefd4c85", "2026-04-29", base_dir=base_dir
                    )
                    .read_text(encoding="utf-8")
                    .split("---", 2)[1]
                )
                for key in ("updated_at", "execution_deadline", "command"):
                    self.assertNotIn(f"{key}:", yaml_block)
            finally:
                if first.handle is not None:
                    first.handle.close()

    def test_claim_task_execution_reclaims_orphan_running_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            path = soldier.task_record_path("c01b883dfefd4c85", "2026-04-29", base_dir=base_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                soldier.render_task_markdown(
                    {
                        "task_id": "c01b883dfefd4c85",
                        "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                        "date": "2026-04-29",
                        "status": "running",
                        "updated_at": "2000-01-01T00:00:00+00:00",
                        "command": "opencode run --auto 'echo old'",
                    }
                ),
                encoding="utf-8",
            )

            claimed = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                1,
                base_dir=base_dir,
            )
            try:
                self.assertEqual(claimed.status, "claimed")
                self.assertIsNotNone(claimed.handle)
            finally:
                if claimed.handle is not None:
                    claimed.handle.close()

    def test_task_record_complete_writes_result_then_releases_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            claimed = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            assert claimed.handle is not None
            claimed.handle.complete(
                {"task_ref": "2026-04-29_accountancy_c01b883dfefd4c85", "status": "successed", "exit_code": 0},
                status="successed",
                exit_code=0,
                message=None,
            )
            claimed.handle.close()
            path = soldier.task_record_path("c01b883dfefd4c85", "2026-04-29", base_dir=base_dir)
            payload = soldier.parse_task_markdown(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "completed")
            self.assertNotIn("exit_code", payload)
            self.assertNotIn("report", payload)
            self.assertNotIn("## Output", path.read_text(encoding="utf-8"))
            report = soldier.report_from_task_record(payload)
            assert report is not None
            self.assertEqual(report["status"], "successed")
            self.assertNotIn("stdout", report)
            replay = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:01+00:00",
                999,
                base_dir=base_dir,
            )
            self.assertEqual(replay.status, "completed")
            self.assertIsNone(replay.handle)

    def test_claim_honors_legacy_json_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            legacy = soldier.legacy_task_json_path("c01b883dfefd4c85", base_dir=base_dir)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            report = {
                "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                "status": "successed",
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }
            legacy.write_text(
                json.dumps(
                    {
                        "task_id": "c01b883dfefd4c85",
                        "status": "completed",
                        "result_status": "successed",
                        "report": report,
                    }
                ),
                encoding="utf-8",
            )
            claimed = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            self.assertEqual(claimed.status, "completed")
            self.assertIsNone(claimed.handle)
            self.assertEqual((claimed.record or {}).get("report"), report)
            self.assertTrue(legacy.is_file())

    def test_claim_migrates_legacy_json_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            legacy = soldier.legacy_task_json_path("c01b883dfefd4c85", base_dir=base_dir)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(
                json.dumps(
                    {
                        "task_id": "c01b883dfefd4c85",
                        "status": "running",
                        "command": "opencode run --auto 'echo old'",
                    }
                ),
                encoding="utf-8",
            )
            claimed = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            try:
                self.assertEqual(claimed.status, "claimed")
                self.assertIsNotNone(claimed.handle)
                md_path = soldier.task_record_path(
                    "c01b883dfefd4c85", "2026-04-29", base_dir=base_dir
                )
                self.assertEqual(claimed.handle.path, md_path)
                self.assertTrue(md_path.is_file())
                self.assertFalse(legacy.exists())
            finally:
                if claimed.handle is not None:
                    claimed.handle.close()

    def test_complete_does_not_write_output_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            claimed = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            assert claimed.handle is not None
            claimed.handle.complete(
                {
                    "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                    "status": "successed",
                    "exit_code": 0,
                },
                status="successed",
                exit_code=0,
                message=None,
            )
            path = claimed.handle.path
            claimed.handle.close()
            text = path.read_text(encoding="utf-8")
            self.assertIn("## Command", text)
            self.assertNotIn("## Output", text)
            self.assertNotIn("## stdout", text)

    def test_clean_old_task_records_keeps_fresh_empty_date_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            fresh = soldier.get_task_records_dir(base_dir) / "2026-08-25"
            fresh.mkdir(parents=True)
            soldier._clean_old_task_records(base_dir, days=20)
            self.assertTrue(fresh.is_dir())

    def test_clean_old_task_records_removes_stale_empty_date_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            stale = soldier.get_task_records_dir(base_dir) / "2026-01-01"
            stale.mkdir(parents=True)
            old = time.time() - 21 * 86400
            os.utime(stale, (old, old))
            soldier._clean_old_task_records(base_dir, days=20)
            self.assertFalse(stale.exists())

    def test_handle_dispatch_logs_command_before_execute(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "task": "Check email",
        }
        order: list[str] = []

        def fake_log(level: int, msg: str, *args: object, **kwargs: object) -> None:
            rendered = msg % args if args else msg
            order.append(str(rendered))

        def fake_execute(*args: object, **kwargs: object):
            order.append("execute")
            return _ok_result()

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("claimed", {})),
            mock.patch("soldier.soldier.execute_command", side_effect=fake_execute),
            mock.patch("soldier.soldier.resolve_opencode_executable", return_value="opencode"),
            mock.patch("soldier.soldier.log_task", side_effect=fake_log),
            mock.patch(
                "soldier.soldier.append_task_execution_log",
                return_value=Path("runtime/tasks/2026-04-29/c01b883dfefd4c85.md"),
            ),
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        command_idx = next(i for i, item in enumerate(order) if item.startswith("Command:"))
        self.assertLess(command_idx, order.index("execute"))
        started_idx = next(i for i, item in enumerate(order) if item.startswith("Started at"))
        self.assertLess(started_idx, order.index("execute"))

    def test_completed_task_replays_report_without_executing(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }
        report = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "successed",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
        expected = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "successed",
            "exit_code": 0,
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("completed", {"report": report})),
            mock.patch("soldier.soldier.execute_command") as execute_mock,
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)) as send_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_not_called()
        send_mock.assert_called_once_with("127.0.0.1", 38471, expected)
        self.assertTrue(conn.closed)

    def test_completed_task_replays_reconstructed_report_without_saved_blob(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }
        record = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "completed",
            "result_status": "successed",
            "output": "session\n\nUpload complete.\n",
        }
        expected = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "successed",
            "exit_code": 0,
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch(
                "soldier.soldier.claim_task_execution",
                return_value=soldier.ClaimResult("completed", record),
            ),
            mock.patch("soldier.soldier.execute_command") as execute_mock,
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)) as send_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_not_called()
        send_mock.assert_called_once_with("127.0.0.1", 38471, expected)
        self.assertTrue(conn.closed)

    def test_running_duplicate_is_ignored_without_executing_or_reporting(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("running", {"status": "running"})),
            mock.patch("soldier.soldier.execute_command") as execute_mock,
            mock.patch("soldier.soldier.send_report") as send_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_not_called()
        send_mock.assert_not_called()
        self.assertTrue(conn.closed)

    def test_completed_replay_failure_queues_pending_report(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": "echo ok",
        }
        report = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "successed",
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
        expected = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "status": "successed",
            "exit_code": 0,
        }

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("completed", {"report": report})),
            mock.patch("soldier.soldier.send_report", return_value=(None, "down")),
            mock.patch("soldier.soldier.enqueue_pending_report") as enqueue_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        enqueue_mock.assert_called_once_with("127.0.0.1", 38471, expected, "down")

    def test_handle_dispatch_runs_opencode_argv_with_prompt_only(self) -> None:
        conn = FakeDispatchConnection()
        prompt = "Use the exchange-use skill, open the Exchange mailbox, view email"
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": f'opencode run "{prompt}"',
            "task": prompt,
        }
        execute_mock = mock.Mock(return_value=_ok_result())

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=soldier.ClaimResult("claimed", {})),
            mock.patch("soldier.soldier.execute_command", execute_mock),
            mock.patch("soldier.soldier.resolve_opencode_executable", return_value="opencode"),
            mock.patch(
                "soldier.soldier.append_task_execution_log",
                return_value=Path("runtime/tasks/2026-04-29/c01b883dfefd4c85.md"),
            ),
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_called_once()
        argv = execute_mock.call_args[0][0]
        self.assertEqual(list(argv), ["opencode", "run", "--auto", "--thinking", "--format", "json", prompt])
        self.assertNotIn("opencode run", argv[3])
        env = execute_mock.call_args.kwargs.get("env")
        self.assertIsNotNone(env)
        permission = json.loads(env["OPENCODE_PERMISSION"])
        self.assertEqual(permission["*"], "allow")
        self.assertEqual(permission["doom_loop"], "allow")
        self.assertEqual(permission["external_directory"], {"*": "allow"})

    def test_opencode_run_env_sets_permission_without_mutating_base(self) -> None:
        base = {"PATH": "/bin", "KEEP": "yes"}
        env = soldier.opencode_run_env(base)
        self.assertEqual(env["KEEP"], "yes")
        self.assertNotIn("OPENCODE_PERMISSION", base)
        permission = json.loads(env["OPENCODE_PERMISSION"])
        self.assertEqual(permission, soldier.OPENCODE_PERMISSION_ALLOW)

    def test_windows_tree_termination_uses_taskkill_t_and_f(self) -> None:
        proc = mock.Mock()
        proc.pid = 4321
        proc.poll.return_value = None
        proc.wait.return_value = 0
        completed = mock.Mock(returncode=0, stderr="")

        with (
            mock.patch("soldier.soldier.os.name", "nt"),
            mock.patch("soldier.soldier.subprocess.run", return_value=completed) as run_mock,
            mock.patch("soldier.soldier.log_task") as log_mock,
        ):
            soldier.terminate_process_tree(proc, "test", task_id="c01b883dfefd4c85")

        run_mock.assert_called_once()
        args = run_mock.call_args.args[0]
        self.assertEqual(args, ["taskkill", "/PID", "4321", "/T", "/F"])
        log_mock.assert_any_call(
            logging.WARNING,
            "Terminating process tree pid=%s reason=%s",
            4321,
            "test",
            task_id="c01b883dfefd4c85",
            to_console=False,
        )

    @unittest.skipUnless(os.name == "nt", "Windows process-tree integration test")
    def test_execute_timeout_removes_spawned_windows_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "spawn_child.py"
            script.write_text(
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            pid_file = Path(tmp) / "child.pid"
            command = f'"{sys.executable}" "{script}" "{pid_file}"'

            result = soldier.execute_command(command, timeout_sec=1)

            self.assertEqual(result.exit_code, -1)
            self.assertEqual(result.report_status, "failed")
            child_pid = int(pid_file.read_text(encoding="utf-8").strip())
            listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {child_pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotIn(f'"{child_pid}"', listing.stdout)

    def test_failed_reports_can_be_replayed_manually(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            record = {
                "commander_host": "127.0.0.1",
                "commander_port": 38471,
                "payload": {"task_ref": "2026-04-29_hr_c01b883dfefd4c85", "status": "failed"},
                "attempts": 3,
                "last_error": "down",
            }
            soldier._append_jsonl(soldier.failed_reports_path(base_dir), record)

            with mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)):
                delivered, remaining = soldier.replay_failed_reports_once(base_dir)

            self.assertEqual((delivered, remaining), (1, 0))
            self.assertEqual(
                soldier.failed_reports_path(base_dir).read_text(encoding="utf-8"),
                "",
            )


if __name__ == "__main__":
    unittest.main()
