#!/usr/bin/env python3
"""Tests for commander.ini time-model overlay onto config.json defaults."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from commander.role_task_generation import _role_task_count, _schedule_time_model
from commander.target_config import TIME_MODEL_INI_KEYS, load_role_time_model, load_target_config


DEFAULTS = {
    "tasks_per_role": 39,
    "mu_am_minutes": 630.0,
    "mu_pm_minutes": 900.0,
    "sigma_am_minutes": 50.0,
    "sigma_pm_minutes": 65.0,
    "a_am": 1.0,
    "a_pm": 1.0,
    "phi": 0.85,
    "sigma_eta": 0.18,
    "avoid_five_minutes": True,
}


class RoleTimeModelOverlayTests(unittest.TestCase):
    def test_missing_ini_keeps_json_defaults(self) -> None:
        missing = Path("definitely-not-a-commander-ini.ini")
        self.assertEqual(load_role_time_model(missing, "hr", DEFAULTS), DEFAULTS)

    def test_role_without_time_keys_keeps_json_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text("[hr]\nhost = 127.0.0.1\nport = 38472\n", encoding="utf-8")
            self.assertEqual(load_role_time_model(ini, "hr", DEFAULTS), DEFAULTS)
            self.assertEqual(load_target_config(ini, "hr"), ("127.0.0.1", 38472))

    def test_present_keys_override_and_omitted_keys_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[hr]\n"
                "host = 127.0.0.1\n"
                "port = 38472\n"
                "mu_am_minutes = 600\n"
                "tasks_per_role = 20\n",
                encoding="utf-8",
            )
            merged = load_role_time_model(ini, "hr", DEFAULTS)
            self.assertEqual(merged["mu_am_minutes"], 600.0)
            self.assertEqual(merged["tasks_per_role"], 20)
            self.assertTrue(merged["avoid_five_minutes"])
            self.assertEqual(merged["mu_pm_minutes"], 900.0)
            self.assertEqual(merged["phi"], 0.85)

    def test_ini_avoid_five_minutes_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[hr]\nhost = 127.0.0.1\nport = 38472\navoid_five_minutes = false\n",
                encoding="utf-8",
            )
            merged = load_role_time_model(ini, "hr", DEFAULTS)
            self.assertTrue(merged["avoid_five_minutes"])

    def test_empty_ini_value_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[hr]\nhost = 127.0.0.1\nport = 38472\nphi =\n",
                encoding="utf-8",
            )
            merged = load_role_time_model(ini, "hr", DEFAULTS)
            self.assertEqual(merged["phi"], 0.85)

    def test_invalid_number_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[hr]\nhost = 127.0.0.1\nport = 38472\nphi = not-a-number\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_role_time_model(ini, "hr", DEFAULTS)
            self.assertIn("phi", str(ctx.exception))

    def test_schedule_time_model_uses_ini_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ini = Path(tmp) / "commander.ini"
            ini.write_text(
                "[programmer]\n"
                "host = 127.0.0.1\n"
                "port = 38472\n"
                "sigma_am_minutes = 40\n"
                "tasks_per_role = 12\n",
                encoding="utf-8",
            )
            model = _schedule_time_model("programmer", DEFAULTS, ini)
            self.assertEqual(model.sigma_am_minutes, 40.0)
            self.assertEqual(model.mu_am_minutes, 630.0)
            self.assertEqual(_role_task_count("programmer", DEFAULTS, ini, None), 12)

    def test_production_ini_covers_time_model_keys(self) -> None:
        ini_path = Path(__file__).resolve().parent.parent / "commander" / "commander.ini"
        text = ini_path.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?m)^avoid_five_minutes\s*=", text))
        for role in ("manager", "hr", "accountancy", "programmer"):
            self.assertIn(f"[{role}]", text)
            merged = load_role_time_model(ini_path, role, DEFAULTS)
            for key in TIME_MODEL_INI_KEYS:
                self.assertIn(key, merged)
            self.assertIn("avoid_five_minutes", merged)
            host, port = load_target_config(ini_path, role)
            self.assertTrue(host)
            self.assertGreater(port, 0)


if __name__ == "__main__":
    unittest.main()
