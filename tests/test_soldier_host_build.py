#!/usr/bin/env python3
"""Tests for soldier build: skill overwrite, MCP merge, Playwright."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soldier.host_build import (
    copy_skills,
    ensure_playwright,
    load_jsonc,
    merge_mcp_config,
    playwright_available,
    role_skill_source,
    run_build,
    skill_directories,
)


def _write_skill(pack: Path, name: str, body: str = "# skill\n") -> Path:
    skill = pack / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(body, encoding="utf-8")
    return skill


class JsoncLoadTests(unittest.TestCase):
    def test_strips_comments_and_trailing_commas(self) -> None:
        payload = load_jsonc(
            '{\n  // line\n  "mcp": {\n    "a": {"type": "local"},\n  },\n}\n'
        )
        self.assertEqual(payload["mcp"]["a"]["type"], "local")

    def test_keeps_https_urls_inside_strings(self) -> None:
        payload = load_jsonc(
            '{"$schema": "https://opencode.ai/config.json", "mcp": {}}\n'
        )
        self.assertEqual(payload["$schema"], "https://opencode.ai/config.json")


class RoleSkillSourceTests(unittest.TestCase):
    def test_unknown_role(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            role_skill_source("intern")
        self.assertIn("Unknown role", str(ctx.exception))

    def test_missing_pack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("holyfw_assets.skills_root", return_value=Path(tmp)):
                with self.assertRaises(FileNotFoundError):
                    role_skill_source("hr")


class CopySkillTests(unittest.TestCase):
    def test_overwrites_existing_skill_dir_and_skips_non_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            _write_skill(pack, "exchange-use", "new\n")
            (pack / "PROMPT_TEMPLATES.md").write_text("ignore", encoding="utf-8")
            dest_root = root / "skills"
            stale = dest_root / "exchange-use"
            stale.mkdir(parents=True)
            (stale / "SKILL.md").write_text("old\n", encoding="utf-8")
            (stale / "stale.txt").write_text("gone", encoding="utf-8")

            installed = copy_skills(pack, dest_root)

            self.assertEqual(installed, ["exchange-use"])
            self.assertEqual((dest_root / "exchange-use" / "SKILL.md").read_text(encoding="utf-8"), "new\n")
            self.assertFalse((dest_root / "exchange-use" / "stale.txt").exists())
            self.assertEqual(skill_directories(pack), [pack / "exchange-use"])

    def test_empty_pack_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            pack.mkdir()
            with self.assertRaises(FileNotFoundError):
                copy_skills(pack, Path(tmp) / "skills")


class MergeMcpTests(unittest.TestCase):
    def test_overwrites_same_name_and_keeps_other_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled.json"
            dest = Path(tmp) / "opencode.json"
            bundled.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "playwright": {"type": "local", "command": ["npx", "new"]},
                            "excel": {"type": "local"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            dest.write_text(
                json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "mcp": {
                            "keep-me": {"type": "local", "command": ["old"]},
                            "playwright": {"type": "local", "command": ["stale"]},
                        },
                    }
                ),
                encoding="utf-8",
            )

            merged = merge_mcp_config(bundled, dest)
            saved = json.loads(dest.read_text(encoding="utf-8"))

            self.assertEqual(merged["keep-me"]["command"], ["old"])
            self.assertEqual(merged["playwright"]["command"], ["npx", "new"])
            self.assertIn("excel", merged)
            self.assertEqual(saved["$schema"], "https://opencode.ai/config.json")


class PlaywrightTests(unittest.TestCase):
    def test_unavailable_without_npx(self) -> None:
        with mock.patch("soldier.host_build.shutil.which", return_value=None):
            self.assertFalse(playwright_available())

    def test_installs_chromium_when_version_check_fails(self) -> None:
        calls: list[list[str]] = []
        version_hits = {"n": 0}

        def which(name: str) -> str | None:
            if name in {"npx", "npx.cmd"}:
                return "npx"
            return None

        def run(cmd, **kwargs):
            calls.append(list(cmd))
            if "install" in cmd:
                return subprocess.CompletedProcess(cmd, 0)
            version_hits["n"] += 1
            code = 1 if version_hits["n"] == 1 else 0
            return subprocess.CompletedProcess(cmd, code)

        with mock.patch("soldier.host_build.shutil.which", side_effect=which):
            ensure_playwright(run=run)

        self.assertTrue(any("install" in cmd and "chromium" in cmd for cmd in calls))
        self.assertGreaterEqual(sum(1 for cmd in calls if "--version" in cmd), 2)


class RunBuildTests(unittest.TestCase):
    def test_run_build_writes_skills_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skills = root / "skills"
            pack = skills / "hr-skills"
            _write_skill(pack, "exchange-use")
            mcp_file = root / "mcp.json"
            mcp_file.write_text(
                json.dumps({"mcp": {"playwright": {"type": "local", "command": ["npx"]}}}),
                encoding="utf-8",
            )
            oc = root / "opencode"
            existing = oc / "skill" / "exchange-use"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("old\n", encoding="utf-8")

            with (
                mock.patch("holyfw_assets.skills_root", return_value=skills),
                mock.patch("holyfw_assets.mcp_config_path", return_value=mcp_file),
                mock.patch("soldier.host_build.opencode_config_dir", return_value=oc),
                mock.patch("soldier.host_build.ensure_playwright"),
            ):
                code = run_build("hr")

            self.assertEqual(code, 0)
            self.assertEqual(
                (oc / "skills" / "exchange-use" / "SKILL.md").read_text(encoding="utf-8"),
                "# skill\n",
            )
            self.assertFalse((oc / "skill").exists())
            saved = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["mcp"]["playwright"]["command"], ["npx"])

    def test_run_build_unknown_role_returns_one(self) -> None:
        self.assertEqual(run_build("not-a-role"), 1)

    def test_cli_build_skips_listen_logging(self) -> None:
        from soldier.soldier import main

        with (
            mock.patch("soldier.host_build.run_build", return_value=0) as build,
            mock.patch("soldier.soldier.configure_soldier_root_logging") as logs,
        ):
            code = main(["build", "hr"])

        self.assertEqual(code, 0)
        build.assert_called_once_with("hr")
        logs.assert_not_called()


if __name__ == "__main__":
    unittest.main()
