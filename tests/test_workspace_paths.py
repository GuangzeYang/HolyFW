#!/usr/bin/env python3
"""Workspace path resolution and opencode-run stripping."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import (
    locate_holyfw_root,
    normalize_role_tasks,
    strip_opencode_run_prefix,
)
from commander.dispatch_client import DispatchClient
from commander.runtime_config import resolve_config_relative_path
import soldier.soldier as soldier


class StripOpencodeRunTests(unittest.TestCase):
    def test_plain_prompt_is_unchanged(self) -> None:
        text = "Use the exchange-use skill, open the Exchange mailbox, view email"
        self.assertEqual(strip_opencode_run_prefix(text), text)

    def test_quoted_wrapper(self) -> None:
        self.assertEqual(strip_opencode_run_prefix('opencode run "Check email"'), "Check email")

    def test_json_dumps_wrapper(self) -> None:
        prompt = "Use the skill, {path: /Company_Data/HR-Private/}"
        wrapped = f"opencode run {json.dumps(prompt, ensure_ascii=False)}"
        self.assertEqual(strip_opencode_run_prefix(wrapped), prompt)

    def test_unquoted_wrapper(self) -> None:
        self.assertEqual(strip_opencode_run_prefix("opencode run Check email"), "Check email")

    def test_normalize_role_tasks_strips_wrapper(self) -> None:
        data = {"hr": [{"is_load": False, "task": 'opencode run "View inbox"'}]}
        normalized = normalize_role_tasks(data, roles=("hr",), preserve_generated_times=False)
        self.assertEqual(normalized["hr"][0]["task"], "View inbox")
        self.assertNotIn("opencode run", normalized["hr"][0]["task"])


class WorkspaceLocatorTests(unittest.TestCase):
    def test_resolve_role_task_stays_in_repo_commander(self) -> None:
        resolved = resolve_config_relative_path("role_task")
        self.assertEqual(resolved.name, "role_task")
        self.assertEqual(resolved.parent.name, "commander")
        self.assertNotIn("site-packages", resolved.parts)
        self.assertNotIn("dist-packages", resolved.parts)

    def test_cwd_workspace_wins_over_site_packages_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commander = root / "commander"
            commander.mkdir()
            (commander / "config.json").write_text("{}", encoding="utf-8")
            (root / "soldier").mkdir()
            fake_pkg = root / ".venv" / "Lib" / "site-packages" / "commander"
            fake_pkg.mkdir(parents=True)
            old = os.getcwd()
            try:
                os.chdir(root)
                found = locate_holyfw_root(package_hint=fake_pkg)
                resolved = resolve_config_relative_path("role_task")
            finally:
                os.chdir(old)
            self.assertEqual(found, root.resolve())
            self.assertEqual(resolved, (commander / "role_task").resolve())
            self.assertNotIn("site-packages", resolved.parts)

    def test_soldier_logs_follow_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "commander").mkdir()
            (root / "commander" / "config.json").write_text("{}", encoding="utf-8")
            (root / "soldier").mkdir()
            old = os.getcwd()
            try:
                os.chdir(root)
                logs = soldier.get_logs_dir()
            finally:
                os.chdir(old)
            self.assertEqual(logs, (root / "soldier" / "logs").resolve())
            self.assertNotIn("site-packages", logs.parts)

    def test_missing_workspace_does_not_use_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cwd = Path(tmp) / "elsewhere"
            cwd.mkdir()
            pkg = Path(tmp) / "Lib" / "site-packages" / "commander"
            pkg.mkdir(parents=True)
            (pkg / "config.json").write_text("{}", encoding="utf-8")
            old = os.getcwd()
            previous = os.environ.pop("HOLYFW_ROOT", None)
            try:
                os.chdir(cwd)
                with self.assertRaises(FileNotFoundError):
                    locate_holyfw_root(package_hint=pkg)
            finally:
                os.chdir(old)
                if previous is not None:
                    os.environ["HOLYFW_ROOT"] = previous


class DispatchClientPromptTests(unittest.TestCase):
    def test_dispatch_client_sends_prompt_without_opencode_run(self) -> None:
        client = DispatchClient(
            Path("dispatch.py"),
            timeout_seconds=5,
            target_ini_path=Path("x.ini"),
        )
        prompt = "Use the exchange-use skill, view email"
        with (
            mock.patch(
                "commander.dispatch_client.load_target_config",
                return_value=("127.0.0.1", 38472),
            ),
            mock.patch("commander.dispatch_client.subprocess.run") as run,
        ):
            run.return_value = mock.Mock(
                returncode=0,
                stdout='{"ok": true, "status": "accepted"}\n',
                stderr="",
            )
            client.dispatch("hr", f'opencode run "{prompt}"', task_id="abc12345abc12345")
        args = run.call_args[0][0]
        self.assertIn("--task", args)
        self.assertIn(prompt, args)
        self.assertNotIn("--command", args)
        self.assertNotIn("opencode run", " ".join(str(item) for item in args))


if __name__ == "__main__":
    unittest.main()
