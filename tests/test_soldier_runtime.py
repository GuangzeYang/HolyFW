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

    def test_append_task_execution_log_writes_unified_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = soldier.append_task_execution_log(
                task_id="c01b883dfefd4c85",
                task_ref="2026-04-29_accountancy_c01b883dfefd4c85",
                date_str="2026-04-29",
                received_at="2026-04-29T09:08:09+08:00",
                command='opencode run "Check email"',
                status="successed",
                exit_code=0,
                stdout_text="done",
                stderr_text="",
                base_dir=base,
            )
            self.assertEqual(path, base / "logs" / "tasks_2026-04-29.jsonl")
            payload = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["received_at"], "2026-04-29T09:08:09+08:00")
            self.assertEqual(payload["command"], "Check email")
            self.assertEqual(payload["task"], "Check email")
            self.assertEqual(payload["status"], "successed")
            self.assertEqual(payload["exit_code"], 0)
            self.assertEqual(payload["stdout"], "done")
            self.assertEqual(payload["task_id"], "c01b883dfefd4c85")

    def test_pending_and_task_state_live_under_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            pending = soldier.pending_reports_path(base)
            failed = soldier.failed_reports_path(base)
            state = soldier.task_state_path("2026-04-29", base)
            self.assertEqual(pending, base / "runtime" / "pending_reports.jsonl")
            self.assertEqual(failed, base / "runtime" / "failed_reports.jsonl")
            self.assertEqual(state, base / "runtime" / "task_state_04-29.jsonl")

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
            mock.patch("soldier.soldier.claim_task_execution", return_value=("claimed", {})),
            mock.patch("soldier.soldier.execute_command", return_value=("ok", "", 0, "successed", None)),
            mock.patch("soldier.soldier.append_task_execution_log", return_value=Path("logs/tasks_2026-04-29.jsonl")),
            mock.patch("soldier.soldier.mark_task_completed"),
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
            mock.patch("soldier.soldier.claim_task_execution", return_value=("claimed", {})),
            mock.patch("soldier.soldier.execute_command", return_value=("ok", "", 0, "successed", None)),
            mock.patch("soldier.soldier.append_task_execution_log", return_value=Path("logs/tasks_2026-04-29.jsonl")),
            mock.patch("soldier.soldier.mark_task_completed"),
            mock.patch("soldier.soldier.send_report", return_value=(None, "boom")),
            mock.patch("soldier.soldier.enqueue_pending_report"),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        self.assertEqual(len(conn.sent), 1)
        acknowledgment = json.loads(conn.sent[0].decode("utf-8"))
        self.assertEqual(acknowledgment["status"], "accepted")
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

    def test_claim_task_execution_blocks_running_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            first, _ = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                999,
                base_dir=base_dir,
            )
            second, state = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:01+00:00",
                999,
                base_dir=base_dir,
            )

            self.assertEqual(first, "claimed")
            self.assertEqual(second, "running")
            self.assertEqual(state["status"], "running")

    def test_claim_task_execution_allows_stale_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp)
            path = soldier.task_state_path("2026-04-29", base_dir)
            stale = {
                "task_id": "c01b883dfefd4c85",
                "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                "status": "running",
                "updated_at": "2000-01-01T00:00:00+00:00",
                "command": "echo old",
            }
            soldier._append_jsonl(path, stale)

            status, _ = soldier.claim_task_execution(
                "2026-04-29",
                "c01b883dfefd4c85",
                "2026-04-29_accountancy_c01b883dfefd4c85",
                "echo ok",
                "2026-04-29T01:00:00+00:00",
                1,
                base_dir=base_dir,
            )

            self.assertEqual(status, "claimed")

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

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=("completed", {"report": report})),
            mock.patch("soldier.soldier.execute_command") as execute_mock,
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)) as send_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_not_called()
        send_mock.assert_called_once_with("127.0.0.1", 38471, report)
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
            mock.patch("soldier.soldier.claim_task_execution", return_value=("running", {"status": "running"})),
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

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=("completed", {"report": report})),
            mock.patch("soldier.soldier.send_report", return_value=(None, "down")),
            mock.patch("soldier.soldier.enqueue_pending_report") as enqueue_mock,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        enqueue_mock.assert_called_once_with("127.0.0.1", 38471, report, "down")

    def test_handle_dispatch_runs_opencode_argv_with_prompt_only(self) -> None:
        conn = FakeDispatchConnection()
        prompt = "Use the exchange-use skill, open the Exchange mailbox, view email"
        payload = {
            "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
            "task_date": "2026-04-29",
            "command": f'opencode run "{prompt}"',
            "task": prompt,
        }
        execute_mock = mock.Mock(return_value=("ok", "", 0, "successed", None))

        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch("soldier.soldier.claim_task_execution", return_value=("claimed", {})),
            mock.patch("soldier.soldier.execute_command", execute_mock),
            mock.patch("soldier.soldier.resolve_opencode_executable", return_value="opencode"),
            mock.patch(
                "soldier.soldier.append_task_execution_log",
                return_value=Path("logs/tasks_2026-04-29.jsonl"),
            ),
            mock.patch("soldier.soldier.mark_task_completed"),
            mock.patch("soldier.soldier.send_report", return_value=({"ok": True}, None)),
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)

        execute_mock.assert_called_once()
        argv = execute_mock.call_args[0][0]
        self.assertEqual(list(argv), ["opencode", "run", "--auto", prompt])
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
        ):
            soldier.terminate_process_tree(proc, "test")

        run_mock.assert_called_once()
        args = run_mock.call_args.args[0]
        self.assertEqual(args, ["taskkill", "/PID", "4321", "/T", "/F"])

    @unittest.skipUnless(os.name == "nt", "Windows process-tree integration test")
    def test_execute_timeout_removes_spawned_windows_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "spawn_child.py"
            script.write_text(
                "import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                "print(child.pid, flush=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            command = f'"{sys.executable}" "{script}"'

            stdout, _, exit_code, status, _ = soldier.execute_command(command, timeout_sec=1)

            self.assertEqual(exit_code, -1)
            self.assertEqual(status, "failed")
            child_pid = int(stdout.strip())
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
