#!/usr/bin/env python3
"""Tests for soldier build: skill overwrite, OpenCode config replace, Playwright."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from soldier.host_build import (
    clear_opencode_cache,
    copy_skills,
    ensure_playwright,
    install_agents_md,
    load_jsonc,
    merge_mcp_config,
    playwright_available,
    role_skill_source,
    run_build,
    skill_directories,
    write_host_opencode_configs,
)
from common.opencode_install import COMMANDER_OPENCODE_MERGE_KEYS, bind_opencode_provider_api_key_env, write_opencode_config


ALLOW_PERMISSION = {
    "*": "allow",
    "doom_loop": "allow",
    "external_directory": {"*": "allow"},
}

DEEPSEEK_PROVIDER = {
    "deepseek": {
        "options": {
            "apiKey": "{env:DEEPSEEK_API_KEY}",
        }
    }
}


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

    def test_preserves_state_and_changes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            _write_skill(pack, "ad-attack", "new skill\n")
            (pack / "ad-attack" / "state.json").write_text('{"from":"pack"}', encoding="utf-8")
            (pack / "ad-attack" / "changes.json").write_text('{"from":"pack"}', encoding="utf-8")
            dest = root / "skills" / "ad-attack"
            dest.mkdir(parents=True)
            (dest / "SKILL.md").write_text("old\n", encoding="utf-8")
            (dest / "state.json").write_text('{"from":"live"}', encoding="utf-8")
            (dest / "changes.json").write_text('{"from":"live-changes"}', encoding="utf-8")
            (dest / "stale.txt").write_text("gone", encoding="utf-8")

            copy_skills(pack, root / "skills")

            self.assertEqual((dest / "SKILL.md").read_text(encoding="utf-8"), "new skill\n")
            self.assertEqual((dest / "state.json").read_text(encoding="utf-8"), '{"from":"live"}')
            self.assertEqual(
                (dest / "changes.json").read_text(encoding="utf-8"),
                '{"from":"live-changes"}',
            )
            self.assertFalse((dest / "stale.txt").exists())

    def test_empty_pack_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            pack.mkdir()
            with self.assertRaises(FileNotFoundError):
                copy_skills(pack, Path(tmp) / "skills")


class WriteOpencodeConfigTests(unittest.TestCase):
    def test_replaces_dest_and_drops_old_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled.json"
            dest = Path(tmp) / "opencode.json"
            bundled.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "playwright": {"type": "local", "command": ["npx", "new"]},
                            "excel": {"type": "local"},
                        },
                        "provider": DEEPSEEK_PROVIDER,
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

            written = merge_mcp_config(bundled, dest)
            saved = json.loads(dest.read_text(encoding="utf-8"))

            self.assertNotIn("keep-me", written)
            self.assertEqual(written["playwright"]["command"], ["npx", "new"])
            self.assertIn("excel", written)
            self.assertNotIn("provider", saved)
            self.assertEqual(saved["$schema"], "https://opencode.ai/config.json")
            self.assertNotIn("keep-me", saved["mcp"])

    def test_writes_bundled_permission_and_drops_old_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled.json"
            dest = Path(tmp) / "opencode.json"
            bundled.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": DEEPSEEK_PROVIDER,
                    }
                ),
                encoding="utf-8",
            )
            dest.write_text(
                json.dumps(
                    {
                        "$schema": "https://opencode.ai/config.json",
                        "mcp": {"keep-me": {"type": "local"}},
                    }
                ),
                encoding="utf-8",
            )

            merge_mcp_config(bundled, dest)
            saved = json.loads(dest.read_text(encoding="utf-8"))

            self.assertEqual(saved["permission"], ALLOW_PERMISSION)
            self.assertNotIn("keep-me", saved["mcp"])
            self.assertIn("playwright", saved["mcp"])
            self.assertNotIn("provider", saved)

    def test_host_write_deletes_jsonc_and_ignores_empty_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled.json"
            bundled.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": DEEPSEEK_PROVIDER,
                    }
                ),
                encoding="utf-8",
            )
            (root / "opencode.json").write_text("", encoding="utf-8")
            (root / "opencode.jsonc").write_text("", encoding="utf-8")

            write_host_opencode_configs(bundled, root)
            saved_json = json.loads((root / "opencode.json").read_text(encoding="utf-8"))

            self.assertFalse((root / "opencode.jsonc").exists())
            self.assertEqual(saved_json["permission"], ALLOW_PERMISSION)
            self.assertEqual(saved_json["mcp"]["playwright"]["command"], ["npx"])
            self.assertNotIn("provider", saved_json)
            self.assertNotIn("keep-me", saved_json.get("mcp", {}))

    def test_role_keys_drop_existing_custom_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "bundled.json"
            bundled.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                    }
                ),
                encoding="utf-8",
            )
            (root / "opencode.json").write_text(
                json.dumps(
                    {
                        "provider": {
                            "zhipu": {
                                "npm": "@ai-sdk/openai-compatible",
                                "options": {
                                    "baseURL": "https://open.bigmodel.cn/api/paas/v4",
                                    "apiKey": "{env:ZHIPU_API_KEY}",
                                },
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            saved = write_host_opencode_configs(bundled, root)
            written = json.loads((root / "opencode.json").read_text(encoding="utf-8"))

            self.assertNotIn("provider", saved)
            self.assertNotIn("provider", written)
            self.assertEqual(written["permission"], ALLOW_PERMISSION)
            self.assertEqual(written["mcp"]["playwright"]["command"], ["npx"])


class BindProviderEnvTests(unittest.TestCase):
    def test_updates_env_name_preserves_mcp_strips_base_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "opencode.json"
            dest.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": {
                            "deepseek": {
                                "options": {"apiKey": "{env:DEEPSEEK_API_KEY}"},
                            },
                            "zhipuai": {
                                "npm": "@ai-sdk/openai-compatible",
                                "options": {
                                    "baseURL": "https://open.bigmodel.cn/api/paas/v4",
                                    "apiKey": "{env:OLD_KEY}",
                                },
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            jsonc = Path(tmp) / "opencode.jsonc"
            jsonc.write_text("{}", encoding="utf-8")

            saved = bind_opencode_provider_api_key_env("zhipuai", "ZHIPU_API_KEY", dest_path=dest)
            written = json.loads(dest.read_text(encoding="utf-8"))

            self.assertEqual(
                saved["provider"]["zhipuai"]["options"]["apiKey"],
                "{env:ZHIPU_API_KEY}",
            )
            self.assertNotIn("baseURL", written["provider"]["zhipuai"]["options"])
            self.assertNotIn("npm", written["provider"]["zhipuai"])
            self.assertEqual(
                written["provider"]["deepseek"]["options"]["apiKey"],
                "{env:DEEPSEEK_API_KEY}",
            )
            self.assertEqual(written["mcp"]["playwright"]["command"], ["npx"])
            self.assertEqual(written["permission"], ALLOW_PERMISSION)
            self.assertNotIn("sk-", dest.read_text(encoding="utf-8"))
            self.assertFalse(jsonc.exists())

    def test_creates_provider_block_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "opencode.json"
            dest.write_text(
                json.dumps({"permission": ALLOW_PERMISSION, "mcp": {"playwright": {}}}),
                encoding="utf-8",
            )
            bind_opencode_provider_api_key_env("deepseek", "DEEPSEEK_API_KEY", dest_path=dest)
            written = json.loads(dest.read_text(encoding="utf-8"))
            self.assertEqual(
                written["provider"]["deepseek"]["options"]["apiKey"],
                "{env:DEEPSEEK_API_KEY}",
            )
            self.assertIn("mcp", written)
    def test_provider_write_keeps_only_bundled_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled.json"
            dest = Path(tmp) / "opencode.json"
            bundled.write_text(
                json.dumps({"provider": DEEPSEEK_PROVIDER}),
                encoding="utf-8",
            )
            dest.write_text(
                json.dumps(
                    {
                        "provider": {
                            "deepseek": {"options": {"baseURL": "https://api.deepseek.com"}},
                            "keep-me": {"options": {"apiKey": "other"}},
                        }
                    }
                ),
                encoding="utf-8",
            )

            saved = write_opencode_config(bundled, dest, keys=("provider",))

            self.assertEqual(
                saved["provider"]["deepseek"]["options"]["apiKey"],
                "{env:DEEPSEEK_API_KEY}",
            )
            self.assertNotIn("baseURL", saved["provider"]["deepseek"]["options"])
            self.assertNotIn("keep-me", saved["provider"])

    def test_commander_keys_do_not_write_mcp_or_permission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled.json"
            dest = Path(tmp) / "opencode.json"
            bundled.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": DEEPSEEK_PROVIDER,
                    }
                ),
                encoding="utf-8",
            )
            dest.write_text(json.dumps({"mcp": {"keep-me": {"type": "local"}}}), encoding="utf-8")

            saved = write_opencode_config(bundled, dest, keys=COMMANDER_OPENCODE_MERGE_KEYS)

            self.assertEqual(saved["provider"], DEEPSEEK_PROVIDER)
            self.assertNotIn("mcp", saved)
            self.assertNotIn("permission", saved)


class CommanderBuildTests(unittest.TestCase):
    def test_run_build_writes_provider_clears_cache_skips_skills(self) -> None:
        from commander.host_build import run_build as commander_run_build

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "opencode.json"
            bundled.write_text(
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": DEEPSEEK_PROVIDER,
                    }
                ),
                encoding="utf-8",
            )
            oc = root / "opencode"
            oc.mkdir()
            (oc / "opencode.json").write_text("", encoding="utf-8")
            (oc / "opencode.jsonc").write_text("", encoding="utf-8")
            cache = root / "cache"
            cache.mkdir()
            (cache / "stale.bin").write_text("x", encoding="utf-8")

            with (
                mock.patch("commander.host_build.COMMANDER_OPENCODE_JSON", bundled),
                mock.patch("common.opencode_install.opencode_config_dir", return_value=oc),
                mock.patch("common.opencode_install.opencode_cache_dir", return_value=cache),
                mock.patch("common.opencode_install.opencode_runtime_cache_targets", return_value=[cache]),
                mock.patch("common.opencode_install._stop_opencode_processes"),
                mock.patch("common.opencode_install.ensure_playwright") as playwright,
                mock.patch("common.opencode_install.copy_skills") as copy_skills_fn,
            ):
                code = commander_run_build()

            self.assertEqual(code, 0)
            playwright.assert_not_called()
            copy_skills_fn.assert_not_called()
            self.assertFalse(cache.exists())
            self.assertFalse((oc / "skills").exists())
            self.assertFalse((oc / "AGENTS.md").exists())
            saved = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["provider"], DEEPSEEK_PROVIDER)
            self.assertNotIn("mcp", saved)
            self.assertNotIn("permission", saved)
            self.assertFalse((oc / "opencode.jsonc").exists())

    def test_cli_build_skips_server_main(self) -> None:
        import commander.cli as commander_cli

        with (
            mock.patch("commander.host_build.run_build", return_value=0) as build,
            mock.patch("commander.commander.main") as serve,
        ):
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["build"])
        self.assertEqual(ctx.exception.code, 0)
        build.assert_called_once_with()
        serve.assert_not_called()


class AgentsAndCacheTests(unittest.TestCase):
    def test_install_agents_md_stamps_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / "AGENTS.md"
            dest = Path(tmp) / "out" / "AGENTS.md"
            template.write_text(
                "You are the **{{ROLE}}** employee.\nNever ask the user a question.\n",
                encoding="utf-8",
            )

            written = install_agents_md("HR", template, dest)

            text = written.read_text(encoding="utf-8")
            self.assertIn("**hr**", text)
            self.assertNotIn("{{ROLE}}", text)
            self.assertIn("Never ask the user a question", text)

    def test_clear_opencode_cache_removes_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            (cache / "stale.bin").write_text("x", encoding="utf-8")

            self.assertEqual(clear_opencode_cache(cache), [cache])
            self.assertFalse(cache.exists())
            self.assertEqual(clear_opencode_cache(cache), [])

    def test_clears_readonly_nested_bin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            nested = cache / "bin"
            nested.mkdir(parents=True)
            locked = nested / "opencode.exe"
            locked.write_text("x", encoding="utf-8")
            os.chmod(locked, stat.S_IREAD)

            self.assertEqual(clear_opencode_cache(cache), [cache])
            self.assertFalse(cache.exists())

    def test_retries_when_rmdir_reports_not_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            nested = cache / "bin" / "ripgrep"
            nested.mkdir(parents=True)
            (nested / "rg.exe").write_text("x", encoding="utf-8")
            real_rmdir = Path.rmdir
            hits = {"bin": 0}

            def flaky(self: Path) -> None:
                if self.name == "bin" and hits["bin"] == 0:
                    hits["bin"] += 1
                    raise OSError(145, "The directory is not empty", str(self))
                return real_rmdir(self)

            with (
                mock.patch.object(Path, "rmdir", flaky),
                mock.patch("common.opencode_install.time.sleep"),
            ):
                self.assertEqual(clear_opencode_cache(cache), [cache])
            self.assertFalse(cache.exists())

    def test_clears_data_dir_and_auth_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = root / "cache"
            data = root / "data"
            config = root / "config"
            cache.mkdir()
            (cache / "bin").mkdir()
            (cache / "bin" / "stale").write_text("x", encoding="utf-8")
            data.mkdir()
            (data / "auth.json").write_text("{}", encoding="utf-8")
            (data / "session.json").write_text("{}", encoding="utf-8")
            config.mkdir()
            auth = config / "auth.json"
            auth.write_text("{}", encoding="utf-8")
            (config / "opencode.json").write_text("{}", encoding="utf-8")

            with (
                mock.patch(
                    "common.opencode_install.opencode_runtime_cache_targets",
                    return_value=[cache, data, auth],
                ),
                mock.patch("common.opencode_install._stop_opencode_processes") as stop,
            ):
                cleared = clear_opencode_cache()

            stop.assert_called_once()
            self.assertEqual(cleared, [cache, data, auth])
            self.assertFalse(cache.exists())
            self.assertFalse(data.exists())
            self.assertFalse(auth.exists())
            self.assertTrue((config / "opencode.json").exists())

    def test_explicit_dir_does_not_stop_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            cache.mkdir()
            with mock.patch("common.opencode_install._stop_opencode_processes") as stop:
                clear_opencode_cache(cache)
            stop.assert_not_called()

    def test_stops_opencode_exe_before_full_clear(self) -> None:
        completed = subprocess.CompletedProcess(["taskkill"], 0, "", "")
        with (
            mock.patch("common.opencode_install.os.name", "nt"),
            mock.patch("common.opencode_install.subprocess.run", return_value=completed) as run,
            mock.patch("common.opencode_install.opencode_runtime_cache_targets", return_value=[]),
            mock.patch("common.opencode_install.time.sleep"),
        ):
            self.assertEqual(clear_opencode_cache(), [])
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/IM", "opencode.exe", "/T", "/F"])

    def test_auth_json_paths_include_config_and_share(self) -> None:
        from common.opencode_install import opencode_auth_json_paths

        paths = [path.as_posix() for path in opencode_auth_json_paths()]
        self.assertTrue(any(item.endswith(".config/opencode/auth.json") for item in paths))
        self.assertTrue(any(item.endswith(".local/share/opencode/auth.json") for item in paths))


class PlaywrightTests(unittest.TestCase):
    def test_unavailable_without_npx(self) -> None:
        with mock.patch("common.opencode_install.shutil.which", return_value=None):
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

        with mock.patch("common.opencode_install.shutil.which", side_effect=which):
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
                json.dumps(
                    {
                        "permission": ALLOW_PERMISSION,
                        "mcp": {"playwright": {"type": "local", "command": ["npx"]}},
                        "provider": DEEPSEEK_PROVIDER,
                    }
                ),
                encoding="utf-8",
            )
            agents_template = root / "AGENTS.md"
            agents_template.write_text(
                "You are the **{{ROLE}}** employee.\nNever ask the user a question.\n",
                encoding="utf-8",
            )
            oc = root / "opencode"
            existing = oc / "skill" / "exchange-use"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("old\n", encoding="utf-8")
            (oc / "opencode.json").write_text("", encoding="utf-8")
            (oc / "opencode.jsonc").write_text(
                '{"permission": "ask", "mcp": {"stale": {"type": "local"}}}\n',
                encoding="utf-8",
            )
            cache = root / "cache"
            cache.mkdir()
            (cache / "stale.bin").write_text("x", encoding="utf-8")

            with (
                mock.patch("holyfw_assets.skills_root", return_value=skills),
                mock.patch("holyfw_assets.opencode_config_path", return_value=mcp_file),
                mock.patch("holyfw_assets.agents_md_path", return_value=agents_template),
                mock.patch("common.opencode_install.opencode_config_dir", return_value=oc),
                mock.patch("common.opencode_install.opencode_cache_dir", return_value=cache),
                mock.patch("common.opencode_install.opencode_runtime_cache_targets", return_value=[cache]),
                mock.patch("common.opencode_install._stop_opencode_processes"),
                mock.patch("common.opencode_install.ensure_playwright"),
            ):
                code = run_build("hr")

            self.assertEqual(code, 0)
            self.assertEqual(
                (oc / "skills" / "exchange-use" / "SKILL.md").read_text(encoding="utf-8"),
                "# skill\n",
            )
            self.assertFalse((oc / "skill").exists())
            self.assertFalse(cache.exists())
            saved = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["mcp"]["playwright"]["command"], ["npx"])
            self.assertEqual(saved["permission"], ALLOW_PERMISSION)
            self.assertNotIn("provider", saved)
            self.assertFalse((oc / "opencode.jsonc").exists())
            self.assertNotIn("stale", saved["mcp"])
            agents_text = (oc / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("**hr**", agents_text)
            self.assertIn("Never ask the user a question", agents_text)

    def test_run_build_unknown_role_returns_one(self) -> None:
        self.assertEqual(run_build("not-a-role"), 1)

    def test_run_build_rejects_attacker(self) -> None:
        with mock.patch("soldier.host_build.install_role") as install:
            code = run_build("attacker")
        self.assertEqual(code, 1)
        install.assert_not_called()

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

    def test_cli_build_attacker_does_not_install(self) -> None:
        from soldier.soldier import main

        with mock.patch("soldier.host_build.install_role") as install:
            code = main(["build", "attacker"])

        self.assertEqual(code, 1)
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
