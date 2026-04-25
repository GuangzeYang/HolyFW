#!/usr/bin/env python3
"""Tests for cross-role dependency validation messages."""

from __future__ import annotations

import unittest

from commander.role_dependency_provider import validate_dependency_order


class ValidateDependencyOrderMessageTests(unittest.TestCase):
    def test_violation_reason_names_both_roles_times_and_indices(self) -> None:
        task_data = {
            "manager": [
                {
                    "time": "14:13",
                    "is_load": True,
                    "task": "使用 exchange-use skill，发送邮件，{收件人：programmer@edrtest.local}",
                }
            ]
        }
        candidate_tasks = [
            {
                "time": "09:09",
                "is_load": False,
                "task": "使用 exchange-use skill，查看 manager@edrtest.local 的邮件",
            }
        ]
        ok, reason = validate_dependency_order(task_data, "programmer", candidate_tasks)
        self.assertFalse(ok)
        assert reason is not None
        self.assertIn("跨角色时序不满足", reason)
        self.assertIn("manager", reason)
        self.assertIn("programmer", reason)
        self.assertIn("14:13", reason)
        self.assertIn("09:09", reason)
        self.assertIn("数组下标 0", reason)
        self.assertIn("严格晚于", reason)


if __name__ == "__main__":
    unittest.main()
