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
    def test_config_sets_enabled_env_and_fans_out(self) -> None:
        from commander.config_control import main as config_main

        with (
            mock.patch("commander.config_control.enabled_provider") as enabled,
            mock.patch("commander.config_control.set_user_env") as set_env,
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
            enabled.return_value = (
                "deepseek",
                ProviderRecord(
                    name="deepseek",
                    base_url="https://api.deepseek.com",
                    models="deepseek-v4-flash",
                    env="DEEPSEEK_API_KEY",
                    enable=True,
                ),
            )
            code = config_main(["--api-key", " sk-test "])
        self.assertEqual(code, 0)
        set_env.assert_called_once_with("DEEPSEEK_API_KEY", "sk-test")
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[:3], ("10.0.0.1", 38472, "deepseek"))
        self.assertEqual(send.call_args_list[0].args[3], "sk-test")

    def test_send_llm_config_payload_has_type_and_no_env_name(self) -> None:
        from commander.dispatch import send_llm_config

        with mock.patch("commander.dispatch.send_soldier_payload", return_value={"ok": True}) as send:
            send_llm_config("127.0.0.1", 38472, "zhipu", "secret", timeout=2.0)
        payload = send.call_args.args[2]
        self.assertEqual(payload["type"], "llm_config")
        self.assertEqual(payload["provider"], "zhipu")
        self.assertEqual(payload["api_key"], "secret")
        self.assertNotIn("env", payload)
        self.assertNotIn("models", payload)


class SoldierLlmConfigTests(unittest.TestCase):
    def test_apply_llm_config_sets_user_env_without_echoing_key(self) -> None:
        payload = {"type": "llm_config", "provider": "deepseek", "api_key": "sk-live"}
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
        ):
            ack = soldier.apply_llm_config(payload)
        set_env.assert_called_once_with("DEEPSEEK_API_KEY", "sk-live")
        self.assertEqual(ack["status"], "configured")
        self.assertNotIn("api_key", ack)

    def test_handle_dispatch_applies_llm_config_without_opencode(self) -> None:
        conn = FakeDispatchConnection()
        payload = {"type": "llm_config", "provider": "deepseek", "api_key": "sk-live"}
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
        payload = {"type": "llm_config", "provider": "deepseek", "api_key": "sk-live"}
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
