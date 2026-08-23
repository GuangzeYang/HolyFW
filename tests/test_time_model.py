#!/usr/bin/env python3
"""Tests for the thesis 3.4 non-homogeneous Poisson time model."""

from __future__ import annotations

import json
import unittest

from common.time_model import (
    TimeModelConfig,
    bin_times_half_hour,
    generate_schedule,
    half_hour_right_edge,
    sample_schedule_minutes,
    zip_tasks_with_schedule,
)
from common import parse_hhmm_to_minute


class TimeModelScheduleTests(unittest.TestCase):
    def test_strict_increase_and_work_windows(self) -> None:
        times = generate_schedule(38, role="hr", day="2026-08-17", seed=7)
        self.assertGreaterEqual(len(times), 1)
        minutes = [parse_hhmm_to_minute(item) for item in times]
        self.assertTrue(all(value is not None for value in minutes))
        self.assertEqual(minutes, sorted(set(minutes)))

    def test_realized_count_is_not_the_configured_mean(self) -> None:
        lengths = {
            len(generate_schedule(39, role="hr", day="2026-08-17", seed=seed))
            for seed in range(1, 25)
        }
        self.assertGreater(len(lengths), 1)
        self.assertNotIn(0, lengths)

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


class HalfHourBinTests(unittest.TestCase):
    def test_right_edge_maps_to_half_hour_end(self) -> None:
        self.assertEqual(half_hour_right_edge(9 * 60), 9 * 60 + 30)
        self.assertEqual(half_hour_right_edge(9 * 60 + 29), 9 * 60 + 30)
        self.assertEqual(half_hour_right_edge(18 * 60), 18 * 60 + 30)

    def test_nine_am_bin_is_empty_for_workday_starts(self) -> None:
        counts = bin_times_half_hour(["09:00", "09:14", "09:30", "18:00"])
        self.assertEqual(counts["09:00"], 0)
        self.assertEqual(counts["09:30"], 2)
        self.assertEqual(counts["10:00"], 1)
        self.assertEqual(counts["18:30"], 1)


class StatisticFromTasksTests(unittest.TestCase):
    def test_from_persisted_tasks_uses_right_endpoint_bins(self) -> None:
        import tempfile
        from pathlib import Path

        from common.time_model import write_role_schedule_statistics_from_tasks

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            payload = write_role_schedule_statistics_from_tasks(
                {"hr": [{"time": "09:14", "task": "a"}, {"time": "18:00", "task": "b"}]},
                roles=("hr",),
                day="2026-08-20",
                output_dir=out,
            )
            self.assertEqual(payload["roles"]["hr"]["half_hour"]["09:00"], 0)
            self.assertEqual(payload["roles"]["hr"]["half_hour"]["09:30"], 1)
            self.assertEqual(payload["roles"]["hr"]["half_hour"]["18:30"], 1)
            self.assertTrue((out / "role_schedule_30min.png").is_file())


class StatisticCliTests(unittest.TestCase):
    def test_statistic_writes_list_and_chart(self) -> None:
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path

        from common.time_model import main

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--statistic", "--date", "2026-08-20", "--output-dir", str(out)])
            self.assertEqual(code, 0)
            json_path = out / "role_schedule_times.json"
            png_path = out / "role_schedule_30min.png"
            self.assertTrue(json_path.is_file())
            self.assertTrue(png_path.is_file())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["day"], "2026-08-20")
            self.assertIn("manager", payload["roles"])
            manager = payload["roles"]["manager"]
            self.assertGreater(manager["count"], 0)
            self.assertEqual(len(manager["times"]), manager["count"])
            self.assertEqual(manager["half_hour"]["09:00"], 0)
            self.assertEqual(sum(manager["half_hour"].values()), manager["count"])
            self.assertIn("ROLE=manager", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
