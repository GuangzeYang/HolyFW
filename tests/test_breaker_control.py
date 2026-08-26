#!/usr/bin/env python3
"""Tests for commander breaker reset day-state clearing."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from commander.breaker_control import clear_day_runtime_files, main, reset_day_state
from commander.failure_governor import RoleFailureGovernor


class FakeAlerter:
    def send_role_opened(self, role: str, day: str, state: dict):
        return True, None


class ClearDayRuntimeFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "role_task"
        self.logs_dir = self.root / "logs"
        self.data_dir.mkdir()
        self.logs_dir.mkdir()
        self.day = date.today().isoformat()
        month_day = self.day[5:]
        self.task_file = self.data_dir / f"tasks_{month_day}.json"
        self.task_file.write_text('{"hr": []}', encoding="utf-8")
        (self.data_dir / f"tasks_{month_day}.json.lock").write_text("lock", encoding="utf-8")
        (self.data_dir / f"tasks_{month_day}.candidate.json").write_text("{}", encoding="utf-8")
        self.log_file = self.logs_dir / f"commander_{self.day}.log"
        self.log_file.write_text("old log\n", encoding="utf-8")
        self.responses = self.logs_dir / f"agent_responses_{self.day}"
        self.responses.mkdir()
        (self.responses / "hr_attempt1_interactive.log").write_text("old", encoding="utf-8")

    def test_removes_task_artifacts_and_clears_logs(self) -> None:
        result = clear_day_runtime_files(
            data_dir=self.data_dir,
            logs_dir=self.logs_dir,
            day=self.day,
        )
        self.assertFalse(self.task_file.exists())
        self.assertFalse((self.data_dir / f"tasks_{self.day[5:]}.candidate.json").exists())
        self.assertTrue(self.log_file.exists())
        self.assertEqual(self.log_file.read_text(encoding="utf-8"), "")
        self.assertFalse(self.responses.exists())
        self.assertTrue(any("tasks_" in item for item in result["removed_task_files"]))
        self.assertTrue(any("commander_" in item for item in result["cleared_logs"]))


class ResetDayStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "role_task"
        self.logs_dir = self.root / "logs"
        self.data_dir.mkdir()
        self.logs_dir.mkdir()
        self.day = date.today().isoformat()
        self.task_file = self.data_dir / f"tasks_{self.day[5:]}.json"
        self.task_file.write_text('{"hr": [{"time": "11:00"}]}', encoding="utf-8")
        (self.logs_dir / f"commander_{self.day}.log").write_text("old\n", encoding="utf-8")
        self.state_file = self.root / "role_failures.json"
        self.governor = RoleFailureGovernor(
            self.state_file,
            cooldown_seconds=300,
            max_consecutive_failures=3,
            email_alerter=FakeAlerter(),
        )
        for index in range(3):
            self.governor.record_failure("hr", self.day, f"fail {index}")
        self.assertFalse(self.governor.can_dispatch("hr", self.day)[0])

        self.scanner = {"data_dir": str(self.data_dir), "base_time": 11}
        self.paths = {"logs_dir": str(self.logs_dir)}

    def _patches(self):
        return (
            mock.patch("commander.breaker_control.load_runtime_config", return_value={}),
            mock.patch("commander.breaker_control.get_scanner_config", return_value=self.scanner),
            mock.patch("commander.breaker_control.get_paths_config", return_value=self.paths),
            mock.patch(
                "commander.breaker_control.resolve_config_relative_path",
                side_effect=lambda value: Path(value),
            ),
        )

    def test_clears_breaker_tasks_and_logs_without_generating(self) -> None:
        for patcher in self._patches():
            patcher.start()
            self.addCleanup(patcher.stop)

        payload = reset_day_state(
            day=self.day,
            emit_status=lambda _message: None,
            governor=self.governor,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cleared_breaker_roles"], ["hr"])
        self.assertTrue(self.governor.can_dispatch("hr", self.day)[0])
        self.assertFalse(self.task_file.exists())
        self.assertNotIn("generation", payload)


class BreakerCliTests(unittest.TestCase):
    def test_circuit_only_role_uses_legacy_reset(self) -> None:
        with mock.patch("commander.breaker_control._build_governor") as build:
            governor = mock.Mock()
            governor.reset.return_value = True
            build.return_value = governor
            code = main(["reset", "--circuit-only", "--role", "hr"])
        self.assertEqual(code, 0)
        governor.reset.assert_called_once_with("hr", mock.ANY)
        governor.reset_day.assert_not_called()


if __name__ == "__main__":
    unittest.main()
