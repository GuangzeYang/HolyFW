#!/usr/bin/env python3
"""Tests for post-generation schedule base_time shifting."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from commander.policies import EarliestPendingSelectionPolicy
from commander.repository import DailyTaskRepository
from commander.scanner_service import TaskScanService
from commander.schedule_shift import (
    ORIGIN_HOUR,
    SCHEDULE_SHIFT_KEY,
    apply_base_time_shift,
    clock_wrap_day_offset,
    file_day_from_tasks_path,
    resolve_active_task_day,
    shift_hhmm,
    shifted_window_still_active,
    stamp_base_time,
    validate_base_time,
)


def _task(time_text: str, body: str = "do work") -> dict:
    return {
        "time": time_text,
        "is_load": False,
        "task": body,
        "task_id": "",
        "status": "planned",
        "issued_at": "",
        "expiry_time": "",
        "completed_at": "",
        "report_message": "",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
    }


class ShiftHhmmTests(unittest.TestCase):
    def test_nine_plus_twelve_hours(self) -> None:
        self.assertEqual(shift_hhmm("09:06", 12), "21:06")

    def test_afternoon_wraps_past_midnight(self) -> None:
        self.assertEqual(shift_hhmm("13:03", 12), "01:03")
        self.assertEqual(shift_hhmm("17:46", 12), "05:46")

    def test_zero_delta_is_unchanged(self) -> None:
        self.assertEqual(shift_hhmm("10:19", 0), "10:19")

    def test_invalid_returns_none(self) -> None:
        self.assertIsNone(shift_hhmm("nope", 12))


class ApplyBaseTimeShiftTests(unittest.TestCase):
    def test_base_time_21_rewrites_and_stamps(self) -> None:
        data = {"manager": [_task("09:06"), _task("13:03"), _task("17:46")]}
        out, changed = apply_base_time_shift(data, 21, file_day="2026-08-21")
        self.assertTrue(changed)
        self.assertEqual(out["manager"][0]["time"], "21:06")
        self.assertEqual(out["manager"][1]["time"], "01:03")
        self.assertEqual(out["manager"][2]["time"], "05:46")
        self.assertEqual(out[SCHEDULE_SHIFT_KEY]["base_time"], 21)
        self.assertEqual(out[SCHEDULE_SHIFT_KEY]["origin_hour"], ORIGIN_HOUR)
        self.assertEqual(out[SCHEDULE_SHIFT_KEY]["file_day"], "2026-08-21")
        self.assertEqual(data["manager"][0]["time"], "09:06")

    def test_base_time_nine_without_stamp_is_noop(self) -> None:
        data = {"hr": [_task("09:14")]}
        out, changed = apply_base_time_shift(data, 9)
        self.assertFalse(changed)
        self.assertIs(out, data)
        self.assertNotIn(SCHEDULE_SHIFT_KEY, out)

    def test_second_apply_with_same_stamp_is_noop(self) -> None:
        data = {"hr": [_task("09:14")]}
        first, _ = apply_base_time_shift(data, 21, file_day="2026-08-21")
        second, changed = apply_base_time_shift(first, 21, file_day="2026-08-21")
        self.assertFalse(changed)
        self.assertEqual(second["hr"][0]["time"], "21:14")

    def test_unshifts_from_21_back_to_9(self) -> None:
        data = {"hr": [_task("09:14"), _task("15:02")]}
        shifted, _ = apply_base_time_shift(data, 21, file_day="2026-08-21")
        restored, changed = apply_base_time_shift(shifted, 9, file_day="2026-08-21")
        self.assertTrue(changed)
        self.assertEqual(restored["hr"][0]["time"], "09:14")
        self.assertEqual(restored["hr"][1]["time"], "15:02")
        self.assertEqual(stamp_base_time(restored, ORIGIN_HOUR), 9)

    def test_leaves_non_role_keys_alone(self) -> None:
        data = {"note": "keep", "hr": [_task("09:00")]}
        out, _ = apply_base_time_shift(data, 21)
        self.assertEqual(out["note"], "keep")


class ClockWrapTests(unittest.TestCase):
    def test_offset_increments_when_clock_goes_backwards(self) -> None:
        tasks = [_task("23:50"), _task("00:10")]
        self.assertEqual(clock_wrap_day_offset(tasks, 0), 0)
        self.assertEqual(clock_wrap_day_offset(tasks, 1), 1)


class ResolveActiveTaskDayTests(unittest.TestCase):
    def test_pins_yesterday_while_wrapped_window_is_open(self) -> None:
        yesterday = date(2026, 8, 21)
        payload = {
            "hr": [_task("21:06"), _task("01:03")],
            SCHEDULE_SHIFT_KEY: {
                "origin_hour": 9,
                "base_time": 21,
                "file_day": yesterday.isoformat(),
            },
        }

        def load_day(date_str: str) -> dict:
            if date_str == yesterday.isoformat():
                return payload
            return {}

        now = datetime(2026, 8, 22, 1, 0, 0)
        self.assertTrue(shifted_window_still_active(payload, yesterday, now))
        self.assertEqual(resolve_active_task_day(load_day, now=now), "2026-08-21")

    def test_rolls_to_today_after_window_ends(self) -> None:
        yesterday = date(2026, 8, 21)
        payload = {
            "hr": [_task("21:06"), _task("01:03")],
            SCHEDULE_SHIFT_KEY: {
                "origin_hour": 9,
                "base_time": 21,
                "file_day": yesterday.isoformat(),
            },
        }

        def load_day(date_str: str) -> dict:
            if date_str == yesterday.isoformat():
                return payload
            return {}

        now = datetime(2026, 8, 22, 6, 0, 0)
        self.assertEqual(resolve_active_task_day(load_day, now=now), "2026-08-22")

    def test_file_day_from_tasks_path(self) -> None:
        path = Path("commander/role_task/tasks_08-21.json")
        self.assertEqual(
            file_day_from_tasks_path(path, today=date(2026, 8, 21)),
            "2026-08-21",
        )


class ScannerClockWrapTests(unittest.TestCase):
    def test_wrapped_next_task_stays_future(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = DailyTaskRepository(Path(tmp.name), lock_timeout=5, max_store_text=1024)
        dispatched: list[str] = []

        def fake_dispatch(role: str, task_text: str, task_time: str | None = None, **_kwargs):
            dispatched.append(task_text)
            return True

        service = TaskScanService(
            repository=repo,
            selection_policy=EarliestPendingSelectionPolicy(),
            dispatch_task=fake_dispatch,
            max_dispatch_lateness_minutes=6,
            debug=False,
        )
        date_str = "2026-08-21"
        repo.save_day(
            date_str,
            {"hr": [_task("23:50", "late evening"), _task("00:10", "after midnight")]},
        )

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 8, 21, 23, 55, 0)

        with mock.patch("commander.scanner_service.datetime", FakeDateTime):
            service.process_roles(
                tasks_by_role=repo.load_day(date_str),
                roles=("hr",),
                role_pointers={},
                date_str=date_str,
            )

        self.assertEqual(dispatched, ["late evening"])
        self.assertEqual(repo.load_day(date_str)["hr"][1]["status"], "planned")


class RuntimeConfigBaseTimeTests(unittest.TestCase):
    def test_rejects_out_of_range_base_time(self) -> None:
        from commander.runtime_config import load_runtime_config

        base_path = Path(__file__).resolve().parent.parent / "commander" / "config.json"
        data = json.loads(base_path.read_text(encoding="utf-8"))
        for bad in (-1, 24):
            data["scanner"]["base_time"] = bad
            with tempfile.TemporaryDirectory() as tmp:
                cfg_path = Path(tmp) / "config.json"
                cfg_path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError) as ctx:
                    load_runtime_config(cfg_path)
            self.assertIn("0..23", str(ctx.exception))

    def test_cli_base_time_overrides_and_rejects_24(self) -> None:
        from commander.commander import build_parser

        parser = build_parser()
        args = parser.parse_args(["--base-time", "21"])
        self.assertEqual(args.base_time, 21)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--base-time", "24"])


class ValidateBaseTimeTests(unittest.TestCase):
    def test_bounds(self) -> None:
        self.assertEqual(validate_base_time(0), 0)
        self.assertEqual(validate_base_time(23), 23)
        with self.assertRaises(ValueError):
            validate_base_time(24)


if __name__ == "__main__":
    unittest.main()
