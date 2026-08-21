#!/usr/bin/env python3
"""Tests for pyproject console scripts and commander CLI routing."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import commander.cli as commander_cli

REPO_ROOT = Path(__file__).resolve().parents[1]


class PyprojectEntrypointTests(unittest.TestCase):
    def test_console_scripts_point_at_commander_and_soldier(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('commander = "commander.cli:main"', text)
        self.assertIn('soldier = "soldier.soldier:main"', text)
        self.assertIn("holyfw_assets", text)
        self.assertIn('"skills" = "holyfw_assets/skills"', text)
        self.assertIn('"mcp" = "holyfw_assets/mcp"', text)


class CommanderCliRouteTests(unittest.TestCase):
    def test_generate_subcommand_forwards_argv(self) -> None:
        with mock.patch("commander.generate_role_task.main", return_value=0) as generate:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["generate", "--statistic"])
        self.assertEqual(ctx.exception.code, 0)
        generate.assert_called_once_with(["--statistic"])

    def test_schedule_subcommand_forwards_argv(self) -> None:
        with mock.patch("commander.time_model.main", return_value=0) as schedule:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["schedule", "--statistic"])
        self.assertEqual(ctx.exception.code, 0)
        schedule.assert_called_once_with(["--statistic"])

    def test_breaker_subcommand_rewrites_sys_argv(self) -> None:
        with mock.patch("commander.breaker_control.main", return_value=0) as breaker:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["breaker", "status"])
        self.assertEqual(ctx.exception.code, 0)
        breaker.assert_called_once_with()

    def test_root_help_lists_subcommands(self) -> None:
        import io

        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        text = buf.getvalue()
        self.assertIn("generate", text)
        self.assertIn("breaker", text)

    def test_plain_flags_go_to_server_main(self) -> None:
        import commander.commander as commander_serve

        with mock.patch.object(commander_serve, "main") as serve:
            commander_cli.main(["--statistic"])
        serve.assert_called_once_with(["--statistic"])


class BundledAssetTests(unittest.TestCase):
    def test_repo_skills_and_mcp_are_visible(self) -> None:
        from holyfw_assets import mcp_config_path, skills_root

        self.assertTrue((skills_root() / "hr-skills").is_dir())
        self.assertTrue(mcp_config_path().is_file())

    def test_config_relative_path_falls_back_to_bundled_basename(self) -> None:
        from commander.runtime_config import resolve_config_relative_path

        resolved = resolve_config_relative_path("missing/domain_resource.md")
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, "domain_resource.md")


if __name__ == "__main__":
    unittest.main()
