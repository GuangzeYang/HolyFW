#!/usr/bin/env python3
"""Tests for TaskScanService dispatch lateness window."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from commander.policies import EarliestPendingSelectionPolicy
from commander.repository import DailyTaskRepository
from commander.scanner_service import TaskScanService


def _pending_task(time_str: str, task_text: str = "do work") -> dict:
    return {
        "time": time_str,
        "is_load": False,
        "task": task_text,
        "task_id": "",
        "status": "planned",
        "issued_at": "",
        "expiry_time": "",
        "completed_at": "",
        "report_message": "",
        "exit_code": None,
    }


class DispatchLatenessWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = DailyTaskRepository(Path(self.tmp.name), lock_timeout=5, max_store_text=1024)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.dispatched: list[tuple[str, str, str | None]] = []

        def fake_dispatch(
            role: str,
            task_text: str,
            task_time: str | None = None,
            **_kwargs,
        ):
            self.dispatched.append((role, task_text, task_time))
            return True

        self.service = TaskScanService(
            repository=self.repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=fake_dispatch,
            max_dispatch_lateness_minutes=6,
            debug=True,
        )

    def test_expires_task_older_than_six_minutes_and_dispatches_recent(self) -> None:
        now = datetime.now()
        old_time = (now - timedelta(minutes=10)).strftime("%H:%M")
        recent_time = (now - timedelta(minutes=3)).strftime("%H:%M")
        future_time = (now + timedelta(minutes=20)).strftime("%H:%M")

        self.repo.save_day(
            self.today,
            {
                "hr": [
                    _pending_task(old_time, "old task"),
                    _pending_task(recent_time, "recent task"),
                    _pending_task(future_time, "future task"),
                ]
            },
        )

        self.service.process_roles(
            tasks_by_role=self.repo.load_day(self.today),
            roles=("hr",),
            role_pointers={},
            date_str=self.today,
        )

        data = self.repo.load_day(self.today)
        self.assertEqual(data["hr"][0]["status"], "failed")
        self.assertIn("Missed dispatch window", data["hr"][0]["report_message"])
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.dispatched[0][1], "recent task")
        self.assertEqual(self.dispatched[0][2], recent_time)
        self.assertEqual(data["hr"][2]["status"], "planned")

    def test_non_debug_dispatches_task_older_than_six_minutes(self) -> None:
        old_time = (datetime.now() - timedelta(minutes=10)).strftime("%H:%M")
        self.repo.save_day(self.today, {"hr": [_pending_task(old_time, "old task")]})
        service = TaskScanService(
            repository=self.repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=self.service.dispatch_task,
            max_dispatch_lateness_minutes=6,
            debug=False,
        )

        service.process_roles(
            tasks_by_role=self.repo.load_day(self.today),
            roles=("hr",),
            role_pointers={},
            date_str=self.today,
        )

        data = self.repo.load_day(self.today)
        self.assertEqual(len(self.dispatched), 1)
        self.assertEqual(self.dispatched[0][1], "old task")
        self.assertNotEqual(data["hr"][0]["status"], "failed")
        self.assertNotIn("Missed dispatch window", data["hr"][0]["report_message"])

    def test_non_debug_still_waits_for_future_task(self) -> None:
        future_time = (datetime.now() + timedelta(minutes=20)).strftime("%H:%M")
        self.repo.save_day(self.today, {"hr": [_pending_task(future_time, "future task")]})
        service = TaskScanService(
            repository=self.repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=self.service.dispatch_task,
            max_dispatch_lateness_minutes=6,
            debug=False,
        )

        service.process_roles(
            tasks_by_role=self.repo.load_day(self.today),
            roles=("hr",),
            role_pointers={},
            date_str=self.today,
        )

        self.assertEqual(self.dispatched, [])
        self.assertEqual(self.repo.load_day(self.today)["hr"][0]["status"], "planned")

    def test_non_debug_still_rejects_invalid_task_time(self) -> None:
        self.repo.save_day(self.today, {"hr": [_pending_task("invalid", "invalid task")]})
        service = TaskScanService(
            repository=self.repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=self.service.dispatch_task,
            max_dispatch_lateness_minutes=6,
            debug=False,
        )

        service.process_roles(
            tasks_by_role=self.repo.load_day(self.today),
            roles=("hr",),
            role_pointers={},
            date_str=self.today,
        )

        self.assertEqual(self.dispatched, [])
        self.assertTrue(self.repo.load_day(self.today)["hr"][0]["is_load"])


class ClosedCircuitGovernor:
    def can_dispatch(self, role: str, day: str) -> tuple[bool, str | None]:
        return False, "role circuit is open: Command exit code 1"

    def record_failure(self, *args, **kwargs):
        return {}


class CircuitBreakerLogTests(unittest.TestCase):
    def test_open_circuit_logs_warning_and_skips_dispatch(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = DailyTaskRepository(Path(tmp.name), lock_timeout=5, max_store_text=1024)
        today = datetime.now().strftime("%Y-%m-%d")
        dispatched: list[str] = []

        def fake_dispatch(role: str, task_text: str, task_time: str | None = None, **_kwargs):
            dispatched.append(task_text)
            return True

        repo.save_day(today, {"hr": [_pending_task("00:01", "blocked task")]})
        service = TaskScanService(
            repository=repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=fake_dispatch,
            failure_governor=ClosedCircuitGovernor(),
            max_dispatch_lateness_minutes=6,
            debug=False,
        )
        with self.assertLogs(level="WARNING") as captured:
            service.process_roles(
                tasks_by_role=repo.load_day(today),
                roles=("hr",),
                role_pointers={},
                date_str=today,
            )
        self.assertTrue(any("Skipping dispatch" in line and "role circuit is open" in line for line in captured.output))
        self.assertEqual(dispatched, [])


if __name__ == "__main__":
    unittest.main()
