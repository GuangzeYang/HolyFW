#!/usr/bin/env python3
"""Tests for cross-role dependency validation messages."""

from __future__ import annotations

import unittest

from commander.role_dependency_provider import build_dependency_context, validate_dependency_order


class ValidateDependencyOrderMessageTests(unittest.TestCase):
    def test_violation_reason_names_both_roles_times_and_indices(self) -> None:
        task_data = {
            "manager": [
                {
                    "time": "14:13",
                    "is_load": True,
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{recipient: programmer@ndrtest.local, subject: Release status}"
                    ),
                }
            ]
        }
        candidate_tasks = [
            {
                "time": "09:09",
                "is_load": False,
                "task": "Use exchange-use skill to review email from manager@ndrtest.local",
            }
        ]

        ok, reason = validate_dependency_order(task_data, "programmer", candidate_tasks)

        self.assertFalse(ok)
        assert reason is not None
        self.assertIn("Cross-role dependency order is invalid", reason)
        self.assertIn("manager", reason)
        self.assertIn("programmer", reason)
        self.assertIn("14:13", reason)
        self.assertIn("09:09", reason)
        self.assertIn("array index 0", reason)
        self.assertIn("strictly later than", reason)

    def test_context_uses_english_dependency_facts(self) -> None:
        task_data = {
            "hr": [
                {
                    "time": "10:01",
                    "is_load": True,
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{to: manager@ndrtest.local, subject: Onboarding}"
                    ),
                }
            ]
        }

        context = build_dependency_context(task_data, "manager")

        self.assertEqual(
            context,
            "Related dependency facts (for inferring implicit relationships and ordering only): "
            '{"hr": ["10:01 sent an email to manager"]}',
        )

    def test_recipient_fields_are_case_insensitive_and_accept_regular_commas(self) -> None:
        templates = (
            "{RECIPIENT: programmer@ndrtest.local, subject: Status}",
            "{To: programmer@ndrtest.local, subject: Status}",
            "{CC: PROGRAMMER@NDRTEST.LOCAL, subject: Status}",
        )
        for fields in templates:
            with self.subTest(fields=fields):
                task_data = {
                    "manager": [
                        {
                            "time": "14:13",
                            "task": f"Use exchange-use skill, SEND EMAIL, {fields}",
                        }
                    ]
                }

                self.assertIn(
                    "14:13 sent an email to programmer",
                    build_dependency_context(task_data, "programmer"),
                )

    def test_dependency_aliases_use_the_ndrtest_internal_email_domain(self) -> None:
        task_data = {
            "hr": [
                {
                    "time": "10:01",
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{recipient: manager@ndrtest.local, subject: Onboarding}"
                    ),
                }
            ]
        }

        self.assertIn(
            "10:01 sent an email to manager",
            build_dependency_context(task_data, "manager"),
        )

    def test_edrtest_email_domain_is_not_an_internal_role_alias(self) -> None:
        task_data = {
            "hr": [
                {
                    "time": "10:01",
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{recipient: manager@edrtest.local, subject: Onboarding}"
                    ),
                }
            ]
        }

        self.assertEqual(build_dependency_context(task_data, "manager"), "")

    def test_non_send_email_actions_do_not_create_dependencies(self) -> None:
        tasks = (
            "Use exchange-use skill to review email, {to: programmer@ndrtest.local}",
            "Use exchange-use skill to reply to email, {to: programmer@ndrtest.local}",
            "Use exchange-use skill to send an email, {to: programmer@ndrtest.local}",
        )
        for task_text in tasks:
            with self.subTest(task_text=task_text):
                task_data = {"manager": [{"time": "14:13", "task": task_text}]}

                self.assertEqual(build_dependency_context(task_data, "programmer"), "")


if __name__ == "__main__":
    unittest.main()
