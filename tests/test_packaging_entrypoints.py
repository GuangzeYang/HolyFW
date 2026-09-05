#!/usr/bin/env python3
"""Tests for pyproject console scripts and commander CLI routing."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

import commander.cli as commander_cli

REPO_ROOT = Path(__file__).resolve().parents[1]


class PyprojectEntrypointTests(unittest.TestCase):
    def test_console_scripts_point_at_commander_soldier_and_attacker(self) -> None:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('commander = "commander.cli:main"', text)
        self.assertIn('soldier = "soldier.soldier:main"', text)
        self.assertIn('attacker = "attacker.cli:main"', text)
        self.assertIn('sysmon-collect = "sysmon_collector.collector:main"', text)
        self.assertIn("sysmon_collector", text)
        self.assertIn('"sysmonconfig.xml" = "sysmon_collector/sysmonconfig.xml"', text)
        self.assertNotIn("sysmon_collector/config.json", text)
        self.assertIn("holyfw_assets", text)
        self.assertIn('"role_profiles" = "holyfw_assets/role_profiles"', text)
        self.assertNotIn('"skills" = "holyfw_assets/skills"', text)
        self.assertNotIn('"mcp" = "holyfw_assets/mcp"', text)
        self.assertNotIn('"common.py" = "common.py"', text)
        self.assertIn("common", text)
        self.assertIn("attacker", text)
        self.assertIn("attacker/config.json", text)
        self.assertIn("attacker/sysmonconfig.xml", text)
        self.assertIn('"llm.json" = "holyfw_assets/llm.json"', text)
        self.assertIn("/llm.json", text)
        self.assertIn("attacker/AGENTS.md", text)
        self.assertIn("attacker/skills/**", text)
        self.assertNotIn(
            '"commander/prompt_resources" = "commander/prompt_resources"',
            text,
        )
        self.assertIn('"commander/prompt_resources/**"', text)
        self.assertIn("commander/opencode.json", text)


class CommanderCliRouteTests(unittest.TestCase):
    def test_generate_subcommand_forwards_argv(self) -> None:
        with mock.patch("commander.generate_role_task.main", return_value=0) as generate:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["generate", "--statistic"])
        self.assertEqual(ctx.exception.code, 0)
        generate.assert_called_once_with(["--statistic"])

    def test_schedule_subcommand_forwards_argv(self) -> None:
        with mock.patch("common.time_model.main", return_value=0) as schedule:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["schedule", "--statistic"])
        self.assertEqual(ctx.exception.code, 0)
        schedule.assert_called_once_with(["--statistic"])

    def test_breaker_subcommand_rewrites_sys_argv(self) -> None:
        with mock.patch("commander.breaker_control.main", return_value=0) as breaker:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["breaker", "reset"])
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
        self.assertIn("build", text)
        self.assertIn("config", text)

    def test_config_subcommand_does_not_start_server(self) -> None:
        with (
            mock.patch("commander.config_control.main", return_value=0) as config,
            mock.patch("commander.commander.main") as serve,
        ):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["config", "--api-key", "sk-test"])
        self.assertEqual(ctx.exception.code, 0)
        config.assert_called_once_with(["--api-key", "sk-test"])
        serve.assert_not_called()

    def test_config_subcommand_forwards_provider_and_model(self) -> None:
        with (
            mock.patch("commander.config_control.main", return_value=0) as config,
            mock.patch("commander.commander.main") as serve,
        ):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(
                    [
                        "config",
                        "--llm-provider",
                        "zhipu",
                        "--api-key",
                        "sk-test",
                        "--model",
                        "GLM-4.7-Flash",
                    ]
                )
        self.assertEqual(ctx.exception.code, 0)
        config.assert_called_once_with(
            ["--llm-provider", "zhipu", "--api-key", "sk-test", "--model", "GLM-4.7-Flash"]
        )
        serve.assert_not_called()

    def test_config_subcommand_forwards_sync(self) -> None:
        with (
            mock.patch("commander.config_control.main", return_value=0) as config,
            mock.patch("commander.commander.main") as serve,
        ):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["config", "--api-key", "sk-test", "--sync"])
        self.assertEqual(ctx.exception.code, 0)
        config.assert_called_once_with(["--api-key", "sk-test", "--sync"])
        serve.assert_not_called()

    def test_build_subcommand_does_not_start_server(self) -> None:
        with (
            mock.patch("commander.host_build.run_build", return_value=0) as build,
            mock.patch("commander.commander.main") as serve,
        ):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["build"])
        self.assertEqual(ctx.exception.code, 0)
        build.assert_called_once_with()
        serve.assert_not_called()

    def test_plain_flags_go_to_server_main(self) -> None:
        import commander.commander as commander_serve

        with mock.patch.object(commander_serve, "main") as serve:
            commander_cli.main(["--statistic"])
        serve.assert_called_once_with(["--statistic"])


class AttackerCliRouteTests(unittest.TestCase):
    def test_no_subcommand_runs_attacker_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.cli.run_loop", return_value=0) as run,
            mock.patch("attacker.cli.configure_attacker_logging", return_value=Path("attacker.log")),
        ):
            code = attacker_cli.main([])
        self.assertEqual(code, 0)
        run.assert_called_once()

    def test_run_subcommand_forwards_to_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.cli.run_loop", return_value=0) as run,
            mock.patch("attacker.cli.configure_attacker_logging", return_value=Path("attacker.log")),
        ):
            code = attacker_cli.main(["run", "--date", "2026-08-23"])
        self.assertEqual(code, 0)
        kwargs = run.call_args.kwargs
        self.assertEqual(str(kwargs["day"]), "2026-08-23")

    def test_base_time_flag_forwards_to_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.cli.run_loop", return_value=0) as run,
            mock.patch("attacker.cli.configure_attacker_logging", return_value=Path("attacker.log")),
        ):
            code = attacker_cli.main(["--base-time", "21"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["base_time"], 21)

        with (
            mock.patch("attacker.cli.run_loop", return_value=0) as run,
            mock.patch("attacker.cli.configure_attacker_logging", return_value=Path("attacker.log")),
        ):
            code = attacker_cli.main(["run", "--base-time", "21"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["base_time"], 21)

    def test_build_does_not_start_run_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.host_build.run_build", return_value=0) as build,
            mock.patch("attacker.cli.run_loop") as run,
            mock.patch("attacker.cli.configure_attacker_logging") as logs,
        ):
            code = attacker_cli.main(["build"])
        self.assertEqual(code, 0)
        build.assert_called_once_with()
        run.assert_not_called()
        logs.assert_not_called()

    def test_config_does_not_start_run_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.config_control.main", return_value=0) as config,
            mock.patch("attacker.cli.run_loop") as run,
            mock.patch("attacker.cli.configure_attacker_logging") as logs,
        ):
            code = attacker_cli.main(["config", "--api-key", "sk-test"])
        self.assertEqual(code, 0)
        config.assert_called_once_with(["--api-key", "sk-test"])
        run.assert_not_called()
        logs.assert_not_called()

    def test_attacker_build_installs_from_package_dir(self) -> None:
        from attacker.host_build import run_build

        with (
            mock.patch("attacker.host_build.copy_skills", return_value=["ad-attack"]) as copy,
            mock.patch("attacker.host_build.write_host_opencode_configs") as write_cfg,
            mock.patch("attacker.host_build.install_agents_md") as agents,
            mock.patch("attacker.host_build.clear_opencode_cache", return_value=False),
            mock.patch("attacker.host_build.opencode_legacy_skill_dir") as legacy,
        ):
            legacy.return_value.is_dir.return_value = False
            self.assertEqual(run_build(), 0)
        copy.assert_called_once()
        self.assertEqual(copy.call_args.args[0].name, "skills")
        write_cfg.assert_called_once()
        self.assertEqual(write_cfg.call_args.args[0].name, "opencode.json")
        self.assertEqual(write_cfg.call_args.kwargs["keys"], ("permission",))
        agents.assert_called_once()
        self.assertEqual(agents.call_args.args[0], "attacker")
        self.assertEqual(agents.call_args.args[1].name, "AGENTS.md")


class BundledAssetTests(unittest.TestCase):
    def test_repo_skills_and_opencode_config_are_visible(self) -> None:
        from holyfw_assets import agents_md_path, opencode_config_path, skills_root

        self.assertTrue((skills_root() / "hr-skills").is_dir())
        self.assertFalse((skills_root() / "attacker-skills").exists())
        self.assertTrue((REPO_ROOT / "attacker" / "generator_system.md").is_file())
        self.assertTrue((REPO_ROOT / "attacker" / "skills" / "ad-attack" / "SKILL.md").is_file())
        self.assertTrue((REPO_ROOT / "attacker" / "AGENTS.md").is_file())
        self.assertTrue(opencode_config_path().is_file())
        self.assertTrue(agents_md_path().is_file())
        payload = json.loads(opencode_config_path().read_text(encoding="utf-8"))
        permission = payload.get("permission")
        self.assertIsInstance(permission, dict)
        self.assertEqual(permission.get("*"), "allow")
        self.assertEqual(permission.get("doom_loop"), "allow")
        self.assertEqual(permission.get("external_directory"), {"*": "allow"})
        self.assertIn("mcp", payload)
        self.assertNotIn("provider", payload)
        attacker_payload = json.loads((REPO_ROOT / "attacker" / "opencode.json").read_text(encoding="utf-8"))
        self.assertNotIn("provider", attacker_payload)
        commander_payload = json.loads((REPO_ROOT / "commander" / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(
            commander_payload["provider"]["deepseek"]["options"]["apiKey"],
            "{env:DEEPSEEK_API_KEY}",
        )
        self.assertNotIn("zhipu", commander_payload.get("provider", {}))

    def test_config_relative_path_falls_back_to_bundled_basename(self) -> None:
        from commander.runtime_config import resolve_config_relative_path

        resolved = resolve_config_relative_path("missing/domain_resource.md")
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.name, "domain_resource.md")


if __name__ == "__main__":
    unittest.main()
