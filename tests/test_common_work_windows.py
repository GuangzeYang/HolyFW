#!/usr/bin/env python3
"""Tests for closed work windows, time-violation index collection, and remediation merge rules."""

from __future__ import annotations

import unittest

from common import (
    _in_work_window,
    build_role_task_time_remediation_prompt,
    collect_task_indices_outside_work_windows,
    verify_time_remediation_payload,
)


class WorkWindowClosedIntervalTests(unittest.TestCase):
    def test_morning_and_afternoon_include_endpoints(self) -> None:
        self.assertTrue(_in_work_window(9 * 60))
        self.assertTrue(_in_work_window(12 * 60))
        self.assertTrue(_in_work_window(13 * 60 + 30))
        self.assertTrue(_in_work_window(18 * 60))

    def test_lunch_gap_excludes_interior(self) -> None:
        self.assertFalse(_in_work_window(12 * 60 + 1))
        self.assertFalse(_in_work_window(13 * 60 + 29))
        self.assertFalse(_in_work_window(13 * 60 + 15))

    def test_outside_day_segments(self) -> None:
        self.assertFalse(_in_work_window(8 * 60 + 59))
        self.assertFalse(_in_work_window(18 * 60 + 1))


class CollectOutsideWindowTests(unittest.TestCase):
    def test_collects_invalid_parse_and_out_of_window(self) -> None:
        tasks = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "12:05", "is_load": False, "task": "b"},
            {"time": "bad", "is_load": False, "task": "c"},
            {"time": "14:00", "is_load": True, "task": "d"},
            {"time": "19:00", "is_load": False, "task": "e"},
        ]
        bad = collect_task_indices_outside_work_windows(tasks)
        self.assertEqual(bad, [1, 2, 4])

    def test_non_dict_rows_are_bad(self) -> None:
        tasks: list = [{"time": "10:01", "is_load": False, "task": "ok"}, "x"]
        self.assertEqual(collect_task_indices_outside_work_windows(tasks), [1])


class VerifyRemediationPayloadTests(unittest.TestCase):
    def test_allows_time_change_only_on_bad_index(self) -> None:
        old = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "12:05", "is_load": False, "task": "b"},
            {"time": "14:00", "is_load": False, "task": "c"},
        ]
        new = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "13:35", "is_load": False, "task": "b"},
            {"time": "14:00", "is_load": False, "task": "c"},
        ]
        ok, err = verify_time_remediation_payload(old, new, [1])
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_allows_delete_bad_row(self) -> None:
        old = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "12:05", "is_load": False, "task": "b"},
            {"time": "14:00", "is_load": False, "task": "c"},
        ]
        new = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "14:00", "is_load": False, "task": "c"},
        ]
        ok, err = verify_time_remediation_payload(old, new, [1])
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_rejects_change_to_non_bad_row(self) -> None:
        old = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "12:05", "is_load": False, "task": "b"},
        ]
        new = [
            {"time": "09:02", "is_load": False, "task": "a"},
            {"time": "13:35", "is_load": False, "task": "b"},
        ]
        ok, err = verify_time_remediation_payload(old, new, [1])
        self.assertFalse(ok)
        self.assertIsNotNone(err)

    def test_rejects_extra_rows(self) -> None:
        old = [{"time": "09:01", "is_load": False, "task": "a"}]
        new = [
            {"time": "09:01", "is_load": False, "task": "a"},
            {"time": "10:00", "is_load": False, "task": "x"},
        ]
        ok, err = verify_time_remediation_payload(old, new, [])
        self.assertFalse(ok)
        self.assertIn("extra", (err or "").lower())


class RemediationPromptTests(unittest.TestCase):
    def test_prompt_contains_indices_and_role(self) -> None:
        old = [{"time": "12:05", "is_load": False, "task": "x"}]
        p = build_role_task_time_remediation_prompt(
            role="hr",
            old_tasks=old,
            bad_indices=[0],
            min_tasks_per_role=18,
            max_tasks_per_role=18,
            validation_reason="test reason",
            prior_feedback="fix it",
        )
        self.assertIn("hr", p)
        self.assertIn("[0]", p)
        self.assertIn("test reason", p)
        self.assertIn("fix it", p)
        self.assertIn("_index", p)


if __name__ == "__main__":
    unittest.main()
