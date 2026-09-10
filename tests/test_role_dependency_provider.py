#!/usr/bin/env python3
"""Tests for cross-role dependency validation messages."""

from __future__ import annotations

import unittest

from commander.role_dependency_provider import (
    build_backward_items,
    build_dependency_context,
    compact_backward_for_prompt,
    validate_dependency_order,
)


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
        self.assertIn("Failure reason:", reason)
        self.assertIn("Required change:", reason)
        self.assertIn("later schedule time", reason)
        self.assertNotIn("forbidden_slot_indices", reason)
        self.assertNotIn("allowed_slot_indices", reason)

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
        items = build_backward_items(task_data, "manager")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["from"], ["hr"])
        self.assertEqual(items[0]["to"], ["manager"])
        self.assertEqual(items[0]["time"], "10:01")
        self.assertIn('"from": ["hr"]', context)
        self.assertIn('"to": ["manager"]', context)
        self.assertIn("10:01", context)

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

                self.assertIn("14:13", build_dependency_context(task_data, "programmer"))
                items = build_backward_items(task_data, "programmer")
                self.assertEqual(items[0]["to"], ["programmer"])

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

        self.assertIn("10:01", build_dependency_context(task_data, "manager"))
        self.assertEqual(build_backward_items(task_data, "manager")[0]["from"], ["hr"])

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

    def test_non_send_email_actions_do_not_create_send_dependencies(self) -> None:
        tasks = (
            "Use exchange-use skill to review email, {to: programmer@ndrtest.local}",
            "Use exchange-use skill to send an email, {to: programmer@ndrtest.local}",
        )
        for task_text in tasks:
            with self.subTest(task_text=task_text):
                task_data = {"manager": [{"time": "14:13", "task": task_text}]}

                self.assertEqual(build_dependency_context(task_data, "programmer"), "")

    def test_independent_send_email_is_not_a_response_to_inbound_mail(self) -> None:
        task_data = {
            "manager": [
                {
                    "time": "11:29",
                    "task": (
                        "Use the exchange-use skill, open the Exchange mailbox, send email, "
                        "{recipient: hr, subject: Staffing check}"
                    ),
                }
            ]
        }
        candidate_tasks = [
            {
                "time": "10:22",
                "task": (
                    "Use the exchange-use skill, open the Exchange mailbox, send email, "
                    "{recipient: manager, subject: Weekly headcount}"
                ),
            }
        ]

        ok, reason = validate_dependency_order(task_data, "hr", candidate_tasks)
        self.assertTrue(ok, reason)

    def test_odoo_job_alias_is_not_a_named_role_dependency(self) -> None:
        task_data = {
            "manager": [
                {
                    "time": "14:59",
                    "task": (
                        "Use the odoo-use skill, log in to the Odoo system, use the Recruitment "
                        "module, create job posting, {job position: Quality Assurance Analyst, "
                        "email address: hr@ndrtest.local}"
                    ),
                }
            ]
        }

        self.assertEqual(build_backward_items(task_data, "hr"), [])

    def test_schedule_slots_forbid_equal_and_earlier_times(self) -> None:
        task_data = {
            "manager": [
                {
                    "time": "14:13",
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{recipient: programmer@ndrtest.local, subject: Release status}"
                    ),
                }
            ]
        }
        schedule = ["09:09", "14:13", "14:41", "15:02"]

        items = build_backward_items(task_data, "programmer", schedule)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["relation"], "email")
        self.assertEqual(
            items[0]["response_actions"],
            ["reply", "reply all", "view email", "review email", "forward"],
        )
        self.assertEqual(items[0]["forbidden_slot_indices"], [0, 1])
        self.assertEqual(items[0]["forbidden_times"], ["09:09", "14:13"])
        self.assertEqual(items[0]["allowed_slot_indices"], [2, 3])
        self.assertEqual(items[0]["allowed_times"], ["14:41", "15:02"])

    def test_schedule_slots_are_empty_when_no_later_time_exists(self) -> None:
        task_data = {
            "hr": [
                {
                    "time": "16:40",
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{to: manager@ndrtest.local, subject: Onboarding}"
                    ),
                }
            ]
        }

        items = build_backward_items(task_data, "manager", ["09:10", "14:02", "16:40"])

        self.assertEqual(items[0]["forbidden_slot_indices"], [0, 1, 2])
        self.assertEqual(items[0]["allowed_slot_indices"], [])
        self.assertEqual(items[0]["allowed_times"], [])

    def test_slot_fields_are_omitted_without_a_schedule(self) -> None:
        task_data = {
            "hr": [
                {
                    "time": "10:01",
                    "task": (
                        "Use exchange-use skill, send email, "
                        "{to: manager@ndrtest.local, subject: Onboarding}"
                    ),
                }
            ]
        }

        items = build_backward_items(task_data, "manager")

        self.assertEqual(items[0]["from"], ["hr"])
        self.assertNotIn("forbidden_slot_indices", items[0])
        self.assertNotIn("allowed_slot_indices", items[0])


class CompactBackwardForPromptTests(unittest.TestCase):
    def test_compacts_full_events_to_time_keyed_objects(self) -> None:
        items = [
            {
                "from": ["hr"],
                "to": ["manager"],
                "time": "10:01",
                "task": "send mail",
                "forbidden_slot_indices": [0],
                "allowed_slot_indices": [1],
            }
        ]
        self.assertEqual(compact_backward_for_prompt(items), [{"10:01": "send mail"}])

    def test_keeps_already_time_keyed_objects_and_sorts(self) -> None:
        items = [{"11:03": "later"}, {"09:17": "earlier"}]
        self.assertEqual(
            compact_backward_for_prompt(items),
            [{"09:17": "earlier"}, {"11:03": "later"}],
        )

    def test_duplicate_times_stay_as_separate_list_items(self) -> None:
        items = [
            {"time": "10:01", "task": "from hr"},
            {"time": "10:01", "task": "from manager"},
        ]
        self.assertEqual(
            compact_backward_for_prompt(items),
            [{"10:01": "from hr"}, {"10:01": "from manager"}],
        )


if __name__ == "__main__":
    unittest.main()
