#!/usr/bin/env python3
"""Tests for the thesis 3.4 non-homogeneous Poisson time model."""

from __future__ import annotations

import unittest

from commander.time_model import TimeModelConfig, generate_schedule, sample_schedule_minutes, zip_tasks_with_schedule
from common import parse_hhmm_to_minute


class TimeModelScheduleTests(unittest.TestCase):
    def test_length_and_strict_increase(self) -> None:
        times = generate_schedule(38, role="hr", day="2026-08-17", seed=7)
        self.assertEqual(len(times), 38)
        minutes = [parse_hhmm_to_minute(item) for item in times]
        self.assertTrue(all(value is not None for value in minutes))
        self.assertEqual(minutes, sorted(set(minutes)))

    def test_no_lunch_or_offhours(self) -> None:
        minutes = sample_schedule_minutes(40, role="programmer", day="2026-08-18", seed=11)
        for value in minutes:
            self.assertTrue(9 * 60 <= value <= 18 * 60)
            self.assertFalse(12 * 60 < value < 13 * 60)

    def test_bimodal_mass_outside_lunch(self) -> None:
        minutes = sample_schedule_minutes(
            80,
            role="manager",
            day="2026-08-19",
            seed=3,
            config=TimeModelConfig(avoid_five_minutes=False),
        )
        morning = sum(1 for value in minutes if value <= 12 * 60)
        afternoon = sum(1 for value in minutes if value >= 13 * 60)
        lunch = sum(1 for value in minutes if 12 * 60 < value < 13 * 60)
        self.assertEqual(lunch, 0)
        self.assertGreater(morning, 0)
        self.assertGreater(afternoon, 0)

    def test_different_seeds_differ(self) -> None:
        a = generate_schedule(20, role="hr", day="2026-08-17", seed=1)
        b = generate_schedule(20, role="hr", day="2026-08-17", seed=2)
        self.assertNotEqual(a, b)

    def test_role_date_seed_is_stable(self) -> None:
        a = generate_schedule(12, role="accountancy", day="2026-08-17")
        b = generate_schedule(12, role="accountancy", day="2026-08-17")
        self.assertEqual(a, b)

    def test_zip_requires_matching_length(self) -> None:
        with self.assertRaises(ValueError):
            zip_tasks_with_schedule([{"task": "a"}], ["09:01", "09:17"])

    def test_zip_writes_schedule_times(self) -> None:
        rows = zip_tasks_with_schedule(
            [{"is_load": False, "task": "a"}, {"task": "b"}],
            ["09:07", "13:11"],
        )
        self.assertEqual(rows[0]["time"], "09:07")
        self.assertEqual(rows[1]["time"], "13:11")
        self.assertFalse(rows[1]["is_load"])


if __name__ == "__main__":
    unittest.main()
