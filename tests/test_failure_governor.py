#!/usr/bin/env python3
"""Tests for persistent role cooldowns, circuit breaking, and alert deduplication."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from commander.failure_governor import RoleFailureGovernor


class FakeAlerter:
    def __init__(self, succeeds: bool = True):
        self.succeeds = succeeds
        self.calls: list[tuple[str, str, dict]] = []

    def send_role_opened(self, role: str, day: str, state: dict):
        self.calls.append((role, day, dict(state)))
        return (True, None) if self.succeeds else (False, "smtp down")


class RoleFailureGovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.state_file = Path(self.temp_dir.name) / "role_failures.json"
        self.alerter = FakeAlerter()
        self.governor = RoleFailureGovernor(
            self.state_file,
            cooldown_seconds=300,
            max_consecutive_failures=3,
            email_alerter=self.alerter,
        )
        self.day = "2026-07-17"

    def test_three_failures_open_persistent_circuit_and_alert_once(self) -> None:
        self.governor.record_failure("hr", self.day, "one", "ref1")
        self.governor.record_failure("hr", self.day, "two", "ref2")
        state = self.governor.record_failure("hr", self.day, "three", "ref3")

        self.assertTrue(state["circuit_open"])
        self.assertEqual(state["consecutive_failures"], 3)
        self.assertEqual(len(self.alerter.calls), 1)
        self.assertFalse(self.governor.can_dispatch("hr", self.day)[0])

        reloaded = RoleFailureGovernor(
            self.state_file,
            cooldown_seconds=300,
            max_consecutive_failures=3,
            email_alerter=self.alerter,
        )
        self.assertFalse(reloaded.can_dispatch("hr", self.day)[0])
        reloaded.record_failure("hr", self.day, "duplicate", "ref4")
        self.assertEqual(len(self.alerter.calls), 1)

    def test_success_clears_failures_but_does_not_bypass_open_circuit(self) -> None:
        for index in range(3):
            self.governor.record_failure("hr", self.day, f"failure {index}")
        state = self.governor.record_success("hr", self.day, "late-success")

        self.assertEqual(state["consecutive_failures"], 0)
        self.assertTrue(state["circuit_open"])
        self.assertFalse(self.governor.can_dispatch("hr", self.day)[0])

    def test_manual_reset_reopens_role(self) -> None:
        for index in range(3):
            self.governor.record_failure("hr", self.day, f"failure {index}")

        self.assertTrue(self.governor.reset("hr", self.day))
        self.assertTrue(self.governor.can_dispatch("hr", self.day)[0])

    def test_reset_day_clears_all_roles(self) -> None:
        for role in ("hr", "manager"):
            for index in range(3):
                self.governor.record_failure(role, self.day, f"{role} {index}")
        self.assertFalse(self.governor.can_dispatch("hr", self.day)[0])
        self.assertFalse(self.governor.can_dispatch("manager", self.day)[0])

        cleared = self.governor.reset_day(self.day)
        self.assertEqual(cleared, ["hr", "manager"])
        self.assertTrue(self.governor.can_dispatch("hr", self.day)[0])
        self.assertTrue(self.governor.can_dispatch("manager", self.day)[0])
        self.assertEqual(self.governor.status(self.day), {})

    def test_duplicate_result_key_is_counted_once(self) -> None:
        self.governor.record_failure(
            "hr",
            self.day,
            "failed",
            "task-ref",
            result_key="task-ref",
        )
        state = self.governor.record_failure(
            "hr",
            self.day,
            "failed replay",
            "task-ref",
            result_key="task-ref",
        )
        self.assertEqual(state["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
