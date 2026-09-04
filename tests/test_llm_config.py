#!/usr/bin/env python3
"""Tests for root llm.json, commander config --api-key, and soldier LLM config apply."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from common.llm_catalog import (
    ProviderRecord,
    enabled_provider,
    load_llm_catalog,
    lookup_provider,
    opencode_model_spec,
    resolve_config_selection,
    save_enabled_selection,
)
from common.user_env import get_user_env, set_user_env
import commander.dispatch  # noqa: F401 — bind real runtime_config helpers before tests patch them
import soldier.soldier as soldier
from tests.test_soldier_runtime import FakeDispatchConnection, _ok_result


def _write_catalog(path: Path, *, deepseek_enable: bool = True, zhipu_enable: bool = False) -> None:
    payload = {
        "provider": {
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "models": "deepseek-v4-flash",
                "env": "DEEPSEEK_API_KEY",
                "enable": deepseek_enable,
            },
            "zhipu": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "models": "GLM-4.7-Flash",
                "env": "ZHIPU_API_KEY",
                "enable": zhipu_enable,
            },
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class LlmCatalogTests(unittest.TestCase):
    def test_repo_llm_json_has_exactly_one_enable(self) -> None:
        repo = Path(__file__).resolve().parents[1] / "llm.json"
        catalog = load_llm_catalog(repo)
        self.assertIn("deepseek", catalog)
        self.assertIn("zhipu", catalog)
        enabled = [name for name, record in catalog.items() if record.enable]
        self.assertEqual(enabled, ["deepseek"])
        self.assertEqual(catalog["deepseek"].models, "deepseek-v4-flash")
        self.assertEqual(catalog["zhipu"].models, "GLM-4.7-Flash")
        self.assertNotIn("api_key", repo.read_text(encoding="utf-8"))

    def test_rejects_zero_or_two_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path, deepseek_enable=False, zhipu_enable=False)
            with self.assertRaises(ValueError):
                load_llm_catalog(path)
            _write_catalog(path, deepseek_enable=True, zhipu_enable=True)
            with self.assertRaises(ValueError):
                load_llm_catalog(path)

    def test_rejects_api_key_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": {
                            "deepseek": {
                                "base_url": "https://api.deepseek.com",
                                "models": "deepseek-v4-flash",
                                "env": "DEEPSEEK_API_KEY",
                                "enable": True,
                                "api_key": "must-not-appear",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_llm_catalog(path)
            self.assertIn("api_key", str(ctx.exception))

    def test_lookup_and_opencode_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path, deepseek_enable=False, zhipu_enable=True)
            name, record = enabled_provider(path)
            self.assertEqual(name, "zhipu")
            self.assertEqual(opencode_model_spec(name, record), "zhipu/GLM-4.7-Flash")
            zhipu = lookup_provider("zhipu", path)
            self.assertEqual(zhipu.env, "ZHIPU_API_KEY")
            with self.assertRaises(ValueError):
                lookup_provider("openai", path)

    def test_accepts_any_catalog_provider_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": {
                            "openai": {
                                "base_url": "https://api.openai.com/v1",
                                "models": "gpt-4o",
                                "env": "OPENAI_API_KEY",
                                "enable": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            catalog = load_llm_catalog(path)
            self.assertEqual(catalog["openai"].models, "gpt-4o")
            self.assertEqual(opencode_model_spec("openai", catalog["openai"]), "openai/gpt-4o")
            saved = save_enabled_selection("openai", "gpt-4.1", path=path)
            self.assertEqual(saved.models, "gpt-4.1")
            self.assertTrue(saved.enable)
            _write_catalog(path)
            with self.assertRaises(ValueError) as ctx:
                lookup_provider("openai", path)
            self.assertIn("Unknown", str(ctx.exception))
            with self.assertRaises(ValueError) as ctx:
                resolve_config_selection("openai", None, path=path)
            self.assertIn("Unknown", str(ctx.exception))
            with self.assertRaises(ValueError) as ctx:
                save_enabled_selection("openai", "gpt-4o", path=path)
            self.assertIn("Unknown", str(ctx.exception))

    def test_rejects_invalid_provider_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            path.write_text(
                json.dumps(
                    {
                        "provider": {
                            "1openai": {
                                "base_url": "https://api.openai.com/v1",
                                "models": "gpt-4o",
                                "env": "OPENAI_API_KEY",
                                "enable": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                load_llm_catalog(path)
            self.assertIn("not a valid name", str(ctx.exception))

    def test_resolve_and_save_enabled_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path, deepseek_enable=True, zhipu_enable=False)
            name, record, model, persist = resolve_config_selection(None, None, path=path)
            self.assertEqual(name, "deepseek")
            self.assertEqual(model, "deepseek-v4-flash")
            self.assertFalse(persist)
            name, record, model, persist = resolve_config_selection("zhipu", None, path=path)
            self.assertEqual(name, "zhipu")
            self.assertEqual(model, "GLM-4.7-Flash")
            self.assertTrue(persist)
            saved = save_enabled_selection("zhipu", "GLM-4.7-Flash", path=path)
            self.assertTrue(saved.enable)
            catalog = load_llm_catalog(path)
            self.assertTrue(catalog["zhipu"].enable)
            self.assertFalse(catalog["deepseek"].enable)
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))
            save_enabled_selection("deepseek", "deepseek-v4-pro", path=path)
            catalog = load_llm_catalog(path)
            self.assertEqual(catalog["deepseek"].models, "deepseek-v4-pro")
            self.assertTrue(catalog["deepseek"].enable)

    def test_format_enabled_llm_log_includes_model_and_base_url(self) -> None:
        from common.llm_catalog import format_enabled_llm_log

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path, deepseek_enable=False, zhipu_enable=True)
            text = format_enabled_llm_log(path)
        self.assertIn("LLM provider=zhipu", text)
        self.assertIn("model=GLM-4.7-Flash", text)
        self.assertIn("base_url=https://open.bigmodel.cn/api/paas/v4", text)

    def test_llm_json_path_does_not_use_packaged_catalog(self) -> None:
        from common.llm_catalog import llm_json_path

        missing = Path("/no/such/workspace/llm.json")
        with mock.patch("common.llm_catalog.workspace_llm_json_path", return_value=missing):
            with self.assertRaises(FileNotFoundError) as ctx:
                llm_json_path()
        self.assertIn("HOLYFW_ROOT", str(ctx.exception))


class UserEnvTests(unittest.TestCase):
    def test_set_user_env_updates_process_env(self) -> None:
        key = "HOLYFW_TEST_LLM_ENV"
        previous = os.environ.pop(key, None)
        try:
            with mock.patch("common.user_env._write_hkcu"), mock.patch(
                "common.user_env._broadcast_setting_change"
            ):
                set_user_env(key, "secret-value")
            self.assertEqual(get_user_env(key), "secret-value")
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


class CommanderConfigTests(unittest.TestCase):
    def test_config_help_lists_provider_model_and_required_key(self) -> None:
        from commander.config_control import build_parser

        text = build_parser().format_help()
        self.assertIn("--api-key", text)
        self.assertIn("--llm-provider", text)
        self.assertIn("llm.json", text)
        self.assertIn("--model", text)

    def test_config_rejects_unknown_provider(self) -> None:
        from commander.config_control import main as config_main

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path)
            with mock.patch("common.llm_catalog.workspace_llm_json_path", return_value=path):
                with mock.patch("common.llm_catalog.llm_json_path", return_value=path):
                    code = config_main(["--llm-provider", "openai", "--api-key", "sk-test"])
        self.assertEqual(code, 1)

    def test_config_requires_api_key(self) -> None:
        from commander.config_control import main as config_main

        with self.assertRaises(SystemExit) as ctx:
            config_main([])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_config_sets_enabled_env_and_fans_out(self) -> None:
        from commander.config_control import main as config_main

        record = ProviderRecord(
            name="deepseek",
            base_url="https://api.deepseek.com",
            models="deepseek-v4-flash",
            env="DEEPSEEK_API_KEY",
            enable=True,
        )
        with (
            mock.patch(
                "common.llm_config_cli.resolve_config_selection",
                return_value=("deepseek", record, "deepseek-v4-flash", False),
            ),
            mock.patch("common.llm_config_cli.save_enabled_selection", return_value=record) as save,
            mock.patch("common.llm_config_cli.set_user_env") as set_env,
            mock.patch("commander.runtime_config.load_runtime_config", return_value={}),
            mock.patch(
                "commander.runtime_config.get_dispatch_config",
                return_value={"soldier_timeout_seconds": 1.0},
            ),
            mock.patch(
                "commander.runtime_config.get_paths_config",
                return_value={"target_ini_file": "commander.ini"},
            ),
            mock.patch("commander.runtime_config.resolve_config_relative_path", return_value=Path("ini")),
            mock.patch("commander.target_config.load_all_roles", return_value=("hr", "manager")),
            mock.patch(
                "commander.target_config.load_target_config",
                side_effect=[("10.0.0.1", 38472), ("10.0.0.2", 38472)],
            ),
            mock.patch(
                "commander.dispatch.send_llm_config",
                return_value={"ok": True, "status": "configured"},
            ) as send,
        ):
            code = config_main(["--api-key", " sk-test "])
        self.assertEqual(code, 0)
        save.assert_called_once_with("deepseek", "deepseek-v4-flash")
        set_env.assert_called_once_with("DEEPSEEK_API_KEY", "sk-test")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[:4], ("10.0.0.1", 38472, "deepseek", "sk-test"))
        self.assertEqual(send.call_args_list[0].args[4], "deepseek-v4-flash")

    def test_config_provider_flips_enable_and_sets_zhipu_env(self) -> None:
        from commander.config_control import main as config_main

        record = ProviderRecord(
            name="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            models="GLM-4.7-Flash",
            env="ZHIPU_API_KEY",
            enable=False,
        )
        saved = ProviderRecord(
            name="zhipu",
            base_url=record.base_url,
            models=record.models,
            env=record.env,
            enable=True,
        )
        with (
            mock.patch(
                "common.llm_config_cli.resolve_config_selection",
                return_value=("zhipu", record, "GLM-4.7-Flash", True),
            ),
            mock.patch("common.llm_config_cli.save_enabled_selection", return_value=saved) as save,
            mock.patch("common.llm_config_cli.set_user_env") as set_env,
            mock.patch("commander.runtime_config.load_runtime_config", return_value={}),
            mock.patch(
                "commander.runtime_config.get_dispatch_config",
                return_value={"soldier_timeout_seconds": 1.0},
            ),
            mock.patch(
                "commander.runtime_config.get_paths_config",
                return_value={"target_ini_file": "commander.ini"},
            ),
            mock.patch("commander.runtime_config.resolve_config_relative_path", return_value=Path("ini")),
            mock.patch("commander.target_config.load_all_roles", return_value=("hr",)),
            mock.patch("commander.target_config.load_target_config", return_value=("10.0.0.1", 38472)),
            mock.patch(
                "commander.dispatch.send_llm_config",
                return_value={"ok": True, "status": "configured"},
            ) as send,
        ):
            code = config_main(["--llm-provider", "zhipu", "--api-key", "sk-z"])
        self.assertEqual(code, 0)
        save.assert_called_once_with("zhipu", "GLM-4.7-Flash")
        set_env.assert_called_once_with("ZHIPU_API_KEY", "sk-z")
        self.assertEqual(send.call_args.args[2:5], ("zhipu", "sk-z", "GLM-4.7-Flash"))

    def test_config_model_persists_for_current_provider(self) -> None:
        from commander.config_control import main as config_main

        record = ProviderRecord(
            name="deepseek",
            base_url="https://api.deepseek.com",
            models="deepseek-v4-flash",
            env="DEEPSEEK_API_KEY",
            enable=True,
        )
        saved = ProviderRecord(
            name="deepseek",
            base_url=record.base_url,
            models="deepseek-v4-pro",
            env=record.env,
            enable=True,
        )
        with (
            mock.patch(
                "common.llm_config_cli.resolve_config_selection",
                return_value=("deepseek", record, "deepseek-v4-pro", True),
            ),
            mock.patch("common.llm_config_cli.save_enabled_selection", return_value=saved) as save,
            mock.patch("common.llm_config_cli.set_user_env"),
            mock.patch("commander.runtime_config.load_runtime_config", return_value={}),
            mock.patch(
                "commander.runtime_config.get_dispatch_config",
                return_value={"soldier_timeout_seconds": 1.0},
            ),
            mock.patch(
                "commander.runtime_config.get_paths_config",
                return_value={"target_ini_file": "commander.ini"},
            ),
            mock.patch("commander.runtime_config.resolve_config_relative_path", return_value=Path("ini")),
            mock.patch("commander.target_config.load_all_roles", return_value=("hr",)),
            mock.patch("commander.target_config.load_target_config", return_value=("10.0.0.1", 38472)),
            mock.patch(
                "commander.dispatch.send_llm_config",
                return_value={"ok": True, "status": "configured"},
            ),
        ):
            code = config_main(["--api-key", "sk-test", "--model", "deepseek-v4-pro"])
        self.assertEqual(code, 0)
        save.assert_called_once_with("deepseek", "deepseek-v4-pro")

    def test_config_hints_when_soldier_is_too_old(self) -> None:
        from commander.config_control import main as config_main

        record = ProviderRecord(
            name="deepseek",
            base_url="https://api.deepseek.com",
            models="deepseek-v4-flash",
            env="DEEPSEEK_API_KEY",
            enable=True,
        )
        buf = []

        with (
            mock.patch(
                "common.llm_config_cli.resolve_config_selection",
                return_value=("deepseek", record, "deepseek-v4-flash", False),
            ),
            mock.patch("common.llm_config_cli.save_enabled_selection", return_value=record),
            mock.patch("common.llm_config_cli.set_user_env"),
            mock.patch("commander.runtime_config.load_runtime_config", return_value={}),
            mock.patch(
                "commander.runtime_config.get_dispatch_config",
                return_value={"soldier_timeout_seconds": 1.0},
            ),
            mock.patch(
                "commander.runtime_config.get_paths_config",
                return_value={"target_ini_file": "commander.ini"},
            ),
            mock.patch("commander.runtime_config.resolve_config_relative_path", return_value=Path("ini")),
            mock.patch("commander.target_config.load_all_roles", return_value=("hr",)),
            mock.patch("commander.target_config.load_target_config", return_value=("10.0.0.1", 38472)),
            mock.patch(
                "commander.dispatch.send_llm_config",
                return_value={"ok": False, "error": "Missing or invalid task_ref", "task_ref": "llm_config"},
            ),
            mock.patch("builtins.print", side_effect=lambda *a, **k: buf.append(" ".join(str(x) for x in a))),
        ):
            code = config_main(["--api-key", "sk-test"])
        self.assertEqual(code, 1)
        joined = "\n".join(buf)
        self.assertIn("too old for llm_config", joined)
        self.assertIn("restart soldier listen", joined)

    def test_send_llm_config_payload_has_type_model_and_no_env_name(self) -> None:
        from commander.dispatch import send_llm_config

        with mock.patch("commander.dispatch.send_soldier_payload", return_value={"ok": True}) as send:
            send_llm_config("127.0.0.1", 38472, "zhipu", "secret", "GLM-4.7-Flash", timeout=2.0)
        payload = send.call_args.args[2]
        self.assertEqual(payload["type"], "llm_config")
        self.assertEqual(payload["provider"], "zhipu")
        self.assertEqual(payload["api_key"], "secret")
        self.assertEqual(payload["model"], "GLM-4.7-Flash")
        self.assertNotIn("env", payload)
        self.assertNotIn("models", payload)


class AttackerConfigTests(unittest.TestCase):
    def test_config_help_matches_commander_flags(self) -> None:
        from attacker.config_control import build_parser
        from commander.config_control import build_parser as commander_parser

        attacker_help = build_parser().format_help()
        commander_help = commander_parser().format_help()
        for flag in ("--api-key", "--llm-provider", "--model", "llm.json"):
            self.assertIn(flag, attacker_help)
            self.assertIn(flag, commander_help)

    def test_config_sets_enabled_env_without_fan_out(self) -> None:
        from attacker.config_control import main as config_main

        record = ProviderRecord(
            name="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            models="GLM-4.7-Flash",
            env="ZHIPU_API_KEY",
            enable=True,
        )
        with (
            mock.patch(
                "common.llm_config_cli.resolve_config_selection",
                return_value=("zhipu", record, "GLM-4.7-Flash", True),
            ),
            mock.patch("common.llm_config_cli.save_enabled_selection", return_value=record) as save,
            mock.patch("common.llm_config_cli.set_user_env") as set_env,
            mock.patch(
                "common.llm_config_cli.workspace_llm_json_path",
                return_value=Path("llm.json"),
            ),
            mock.patch("commander.dispatch.send_llm_config") as send,
        ):
            code = config_main(["--llm-provider", "zhipu", "--api-key", " sk-z "])
        self.assertEqual(code, 0)
        save.assert_called_once_with("zhipu", "GLM-4.7-Flash")
        set_env.assert_called_once_with("ZHIPU_API_KEY", "sk-z")
        send.assert_not_called()

    def test_cli_config_does_not_start_run_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.config_control.main", return_value=0) as config,
            mock.patch("attacker.cli.run_loop") as run,
            mock.patch("attacker.cli.configure_attacker_logging") as logs,
        ):
            code = attacker_cli.main(["config", "--llm-provider", "zhipu", "--api-key", "sk-z"])
        self.assertEqual(code, 0)
        config.assert_called_once_with(["--llm-provider", "zhipu", "--api-key", "sk-z"])
        run.assert_not_called()
        logs.assert_not_called()


class SoldierLlmConfigTests(unittest.TestCase):
    def test_apply_llm_config_sets_user_env_without_echoing_key(self) -> None:
        payload = {
            "type": "llm_config",
            "provider": "deepseek",
            "api_key": "sk-live",
            "model": "deepseek-v4-flash",
        }
        with (
            mock.patch(
                "soldier.soldier.lookup_provider",
                return_value=ProviderRecord(
                    name="deepseek",
                    base_url="https://api.deepseek.com",
                    models="deepseek-v4-flash",
                    env="DEEPSEEK_API_KEY",
                    enable=True,
                ),
            ),
            mock.patch("soldier.soldier.set_user_env") as set_env,
            mock.patch("soldier.soldier.save_enabled_selection", return_value=ProviderRecord(
                name="deepseek",
                base_url="https://api.deepseek.com",
                models="deepseek-v4-flash",
                env="DEEPSEEK_API_KEY",
                enable=True,
            )) as save,
        ):
            ack = soldier.apply_llm_config(payload)
        save.assert_called_once_with("deepseek", "deepseek-v4-flash")
        set_env.assert_called_once_with("DEEPSEEK_API_KEY", "sk-live")
        self.assertEqual(ack["status"], "configured")
        self.assertEqual(ack["model"], "deepseek-v4-flash")
        self.assertTrue(ack["json_written"])
        self.assertNotIn("api_key", ack)

    def test_apply_llm_config_rejects_unknown_provider(self) -> None:
        with (
            mock.patch(
                "soldier.soldier.lookup_provider",
                side_effect=ValueError("Unknown LLM provider 'openai'; expected one of: deepseek, zhipu"),
            ),
            mock.patch("soldier.soldier.set_user_env") as set_env,
        ):
            with self.assertRaises(ValueError) as ctx:
                soldier.apply_llm_config(
                    {"type": "llm_config", "provider": "openai", "api_key": "sk", "model": "gpt-4o"}
                )
        set_env.assert_not_called()
        self.assertIn("Unknown", str(ctx.exception))

    def test_apply_llm_config_flips_enable_in_workspace_json(self) -> None:
        payload = {
            "type": "llm_config",
            "provider": "zhipu",
            "api_key": "sk-live",
            "model": "GLM-4.7-Flash",
        }
        zhipu = ProviderRecord(
            name="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            models="GLM-4.7-Flash",
            env="ZHIPU_API_KEY",
            enable=False,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.json"
            _write_catalog(path, deepseek_enable=True, zhipu_enable=False)
            with (
                mock.patch("soldier.soldier.lookup_provider", return_value=zhipu),
                mock.patch(
                    "soldier.soldier.save_enabled_selection",
                    side_effect=lambda name, models: save_enabled_selection(name, models, path=path),
                ),
                mock.patch("soldier.soldier.set_user_env"),
            ):
                ack = soldier.apply_llm_config(payload)
            catalog = load_llm_catalog(path)
            self.assertTrue(catalog["zhipu"].enable)
            self.assertFalse(catalog["deepseek"].enable)
            self.assertTrue(ack["json_written"])
            self.assertNotIn("api_key", path.read_text(encoding="utf-8"))

    def test_build_opencode_argv_uses_llm_json_enable(self) -> None:
        record = ProviderRecord(
            name="zhipu",
            base_url="https://open.bigmodel.cn/api/paas/v4",
            models="GLM-4.7-Flash",
            env="ZHIPU_API_KEY",
            enable=True,
        )
        with (
            mock.patch("soldier.soldier.resolve_opencode_executable", return_value="opencode"),
            mock.patch("soldier.soldier.enabled_provider", return_value=("zhipu", record)),
        ):
            argv = soldier.build_opencode_argv("hi")
        self.assertEqual(argv[argv.index("--model") + 1], "zhipu/GLM-4.7-Flash")

    def test_build_opencode_argv_uses_catalog_provider_name(self) -> None:
        record = ProviderRecord(
            name="openai",
            base_url="https://api.openai.com/v1",
            models="gpt-4o",
            env="OPENAI_API_KEY",
            enable=True,
        )
        with (
            mock.patch("soldier.soldier.resolve_opencode_executable", return_value="opencode"),
            mock.patch("soldier.soldier.enabled_provider", return_value=("openai", record)),
        ):
            argv = soldier.build_opencode_argv("hi")
        self.assertEqual(argv[argv.index("--model") + 1], "openai/gpt-4o")

    def test_handle_dispatch_applies_llm_config_without_opencode(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "type": "llm_config",
            "provider": "deepseek",
            "api_key": "sk-live",
            "model": "deepseek-v4-flash",
        }
        execute_mock = mock.Mock(return_value=_ok_result())
        with (
            mock.patch("soldier.soldier.recv_one_line", return_value=json.dumps(payload).encode("utf-8")),
            mock.patch(
                "soldier.soldier.apply_llm_config",
                return_value={
                    "ok": True,
                    "status": "configured",
                    "provider": "deepseek",
                    "env": "DEEPSEEK_API_KEY",
                    "model": "deepseek-v4-flash",
                },
            ) as apply_cfg,
            mock.patch("soldier.soldier.execute_command", execute_mock),
            mock.patch("soldier.soldier.send_report") as report,
        ):
            soldier.handle_dispatch_connection(conn, "127.0.0.1", 38471, 5)
        apply_cfg.assert_called_once()
        execute_mock.assert_not_called()
        report.assert_not_called()
        ack = json.loads(conn.sent[0].decode("utf-8"))
        self.assertTrue(ack["ok"])
        self.assertEqual(ack["status"], "configured")
        self.assertNotIn("api_key", ack)

    def test_full_slots_still_accept_llm_config(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "type": "llm_config",
            "provider": "deepseek",
            "api_key": "sk-live",
            "model": "deepseek-v4-flash",
        }
        slots = mock.Mock()
        slots.acquire.return_value = False
        executor = mock.Mock(spec=ThreadPoolExecutor)
        with (
            mock.patch("soldier.soldier.read_dispatch_payload", return_value=(payload, None)),
            mock.patch("soldier.soldier.handle_dispatch_connection") as handle,
        ):
            soldier.process_accepted_connection(
                conn,
                ("10.0.0.9", 1),
                "127.0.0.1",
                38471,
                5,
                execution_slots=slots,
                executor=executor,
                worker_threads=3,
                run_claimed=lambda *_a, **_k: None,
            )
        slots.acquire.assert_not_called()
        executor.submit.assert_not_called()
        handle.assert_called_once()
        self.assertEqual(handle.call_args.kwargs["payload"], payload)

    def test_full_slots_reject_tasks(self) -> None:
        conn = FakeDispatchConnection()
        payload = {
            "task_ref": "2026-04-29_hr_c01b883dfefd4c85",
            "task": "Check email",
        }
        slots = mock.Mock()
        slots.acquire.return_value = False
        executor = mock.Mock(spec=ThreadPoolExecutor)
        with mock.patch("soldier.soldier.read_dispatch_payload", return_value=(payload, None)):
            soldier.process_accepted_connection(
                conn,
                ("10.0.0.9", 1),
                "127.0.0.1",
                38471,
                5,
                execution_slots=slots,
                executor=executor,
                worker_threads=3,
                run_claimed=lambda *_a, **_k: None,
            )
        slots.acquire.assert_called_once_with(blocking=False)
        executor.submit.assert_not_called()
        ack = json.loads(conn.sent[0].decode("utf-8"))
        self.assertEqual(ack["status"], "busy")
        self.assertTrue(conn.closed)
