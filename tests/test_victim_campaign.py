#!/usr/bin/env python3
"""Tests for on-demand victim campaign state and daily-generation role filtering."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from commander.target_config import load_all_roles, load_daily_generation_roles
from commander.victim_campaign import (
    DEFAULT_RECON_TASK,
    empty_state,
    merge_campaign_into_state,
    parse_campaign_block,
    recommend_next_phase,
    resolve_step_task,
)


class DailyGenerationRoleTests(unittest.TestCase):
    def test_victim_and_attacker_are_excluded_from_daily_generation_but_kept_in_all_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[hr]\nhost = 127.0.0.1\nport = 38472\n\n"
                "[victim]\nhost = 127.0.0.1\nport = 38473\n\n"
                "[attacker]\nhost = 127.0.0.1\nport = 38474\n",
                encoding="utf-8",
            )
            self.assertEqual(load_all_roles(ini), ("hr", "victim", "attacker"))
            self.assertEqual(load_daily_generation_roles(ini), ("hr",))


class CampaignParseTests(unittest.TestCase):
    def test_parse_campaign_block_extracts_json(self) -> None:
        text = (
            "run_id: recon-001\nlast_result: blocked_local_priv\n"
            "---HOLYFW_CAMPAIGN---\n"
            '{"last_result":"blocked_local_priv","next_task":"Use the penetration-test skill"}\n'
            "---END_HOLYFW_CAMPAIGN---\n"
        )
        parsed = parse_campaign_block(text)
        assert parsed is not None
        self.assertEqual(parsed["last_result"], "blocked_local_priv")
        self.assertIn("penetration-test skill", parsed["next_task"])

    def test_recommend_next_phase_routes_privilege_failures(self) -> None:
        self.assertEqual(recommend_next_phase("blocked_local_priv"), "privilege-escalation")
        self.assertEqual(recommend_next_phase("blocked_remote_priv"), "credential-access")
        self.assertEqual(
            recommend_next_phase("blocked_remote_priv", has_cred_ref=True),
            "lateral-movement",
        )
        self.assertIsNone(recommend_next_phase("blocked_missing"))
        self.assertIsNone(recommend_next_phase("success"))

    def test_resolve_step_task_prefers_override_then_next_task(self) -> None:
        state = empty_state()
        with self.assertRaises(ValueError):
            resolve_step_task(state, None)
        state["next_task"] = DEFAULT_RECON_TASK
        self.assertEqual(resolve_step_task(state, None), DEFAULT_RECON_TASK)
        self.assertEqual(resolve_step_task(state, "  custom task  "), "custom task")

    def test_merge_blank_next_task_becomes_none(self) -> None:
        merged = merge_campaign_into_state(empty_state(), {"next_task": "  ", "last_result": "success"})
        self.assertIsNone(merged["next_task"])
        self.assertEqual(merged["last_result"], "success")


if __name__ == "__main__":
    unittest.main()
