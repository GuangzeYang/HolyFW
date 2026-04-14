#!/usr/bin/env python3
"""Regression tests for commander refactor modules."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from commander.domain import STATUS_PLANNED, STATUS_WAITING
from commander.policies import EarliestPendingSelectionPolicy, task_needs_dispatch
from commander.repository import DailyTaskRepository


class PolicyTests(unittest.TestCase):
    def test_loaded_without_task_id_is_pending(self) -> None:
        task = {
            "is_load": True,
            "task_id": "",
            "status": "planned",
        }
        self.assertTrue(task_needs_dispatch(task))

    def test_policy_finds_earliest_pending_index(self) -> None:
        policy = EarliestPendingSelectionPolicy()
        tasks = [
            {"is_load": True, "task_id": "abc", "status": "waiting"},
            {"is_load": True, "task_id": "", "status": "planned"},
            {"is_load": False, "task_id": "", "status": "planned"},
        ]
        self.assertEqual(policy.find_next_pending_index(tasks), 1)
        self.assertEqual(policy.find_next_pending_index(tasks, start_index=2), 2)


class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.repo = DailyTaskRepository(self.data_dir)
        self.today = date.today().isoformat()
        self.path = self.repo.day_path(self.today)

        initial = {
            "hr": [
                {
                    "time": "09:01",
                    "is_load": True,
                    "task": "t1",
                    "task_id": "",
                    "status": STATUS_PLANNED,
                    "issued_at": "",
                    "expiry_time": "",
                    "completed_at": "",
                    "report_message": "",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            ]
        }
        self.repo.save_day(self.today, initial)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_bind_dispatched_task_updates_waiting_state(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )
        data = self.repo.load_day(self.today)
        item = data["hr"][0]
        self.assertEqual(item["task_id"], "abc12345")
        self.assertEqual(item["status"], STATUS_WAITING)
        self.assertTrue(item["issued_at"])

    def test_report_requires_waiting_transition(self) -> None:
        result = self.repo.update_task_report(
            task_ref=f"{self.today}_hr_abc12345",
            status="successed",
            message="",
            exit_code=0,
            stdout="ok",
            stderr="",
        )
        self.assertFalse(result["ok"])

    def test_waiting_visibility_respects_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=future,
            planned_time="09:01",
        )
        self.assertTrue(self.repo.has_active_waiting_task("hr", self.today))


if __name__ == "__main__":
    unittest.main()
