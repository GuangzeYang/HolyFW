#!/usr/bin/env python3
"""Tests for attacker breaker reset and changes.json recording."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from attacker.breaker import MODE_ALL, MODE_TASK, reset_attacker
from attacker.task_file import tasks_file_path


def _load_changes_module(script: Path):
    spec = importlib.util.spec_from_file_location("attacker_changes_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResetAttackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "role_task"
        self.skill = self.root / "ad-attack"
        self.data_dir.mkdir()
        self.skill.mkdir()
        self.day = date(2026, 8, 27)
        self.task_file = tasks_file_path(self.data_dir, self.day)
        self.task_file.write_text("[]", encoding="utf-8")
        (self.task_file.with_name(self.task_file.name + ".lock")).write_text("lock", encoding="utf-8")
        (self.skill / "state.json").write_text(
            json.dumps({"schema_version": 2, "users": [{"username": "old"}]}),
            encoding="utf-8",
        )
        (self.skill / "changes.json").write_text(
            json.dumps({"schema_version": 1, "changes": [{"summary": "old"}]}),
            encoding="utf-8",
        )

    def test_all_clears_tasks_and_resets_state_and_changes(self) -> None:
        payload = reset_attacker(
            mode=MODE_ALL,
            day=self.day,
            data_dir=self.data_dir,
            skill_roots=[self.skill],
            emit_status=lambda _message: None,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "all")
        self.assertFalse(self.task_file.exists())
        state = json.loads((self.skill / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["users"], [])
        self.assertEqual(state["hosts"], [])
        self.assertEqual(state["domain"]["name"], "")
        changes = json.loads((self.skill / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(changes["changes"], [])

    def test_task_mode_keeps_state_and_changes(self) -> None:
        payload = reset_attacker(
            mode=MODE_TASK,
            day=self.day,
            data_dir=self.data_dir,
            skill_roots=[self.skill],
            emit_status=lambda _message: None,
        )
        self.assertEqual(payload["mode"], "task")
        self.assertFalse(self.task_file.exists())
        self.assertEqual(payload["reset_state_files"], [])
        state = json.loads((self.skill / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["users"][0]["username"], "old")
        changes = json.loads((self.skill / "changes.json").read_text(encoding="utf-8"))
        self.assertEqual(changes["changes"][0]["summary"], "old")


class BreakerCliTests(unittest.TestCase):
    def test_reset_all_is_default_and_does_not_start_run_loop(self) -> None:
        import attacker.cli as attacker_cli

        with (
            mock.patch("attacker.breaker.reset_attacker", return_value={"ok": True, "mode": "all"}) as reset,
            mock.patch("attacker.cli.run_loop") as run,
        ):
            code = attacker_cli.main(["breaker", "reset"])
        self.assertEqual(code, 0)
        reset.assert_called_once()
        self.assertEqual(reset.call_args.kwargs["mode"], "all")
        run.assert_not_called()

    def test_reset_task_flag(self) -> None:
        import attacker.cli as attacker_cli

        with mock.patch("attacker.breaker.reset_attacker", return_value={"ok": True, "mode": "task"}) as reset:
            code = attacker_cli.main(["breaker", "reset", "--task", "--date", "2026-08-27"])
        self.assertEqual(code, 0)
        self.assertEqual(reset.call_args.kwargs["mode"], "task")
        self.assertEqual(reset.call_args.kwargs["day"].isoformat(), "2026-08-27")

    def test_all_and_task_together_fail(self) -> None:
        import attacker.cli as attacker_cli

        with mock.patch("attacker.cli.run_loop") as run:
            code = attacker_cli.main(["breaker", "reset", "--all", "--task"])
        self.assertEqual(code, 2)
        run.assert_not_called()


class ChangesScriptTests(unittest.TestCase):
    def test_add_appends_record(self) -> None:
        script = (
            Path(__file__).resolve().parents[1]
            / "attacker"
            / "skills"
            / "ad-attack"
            / "scripts"
            / "changes.py"
        )
        module = _load_changes_module(script)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "changes.json"
            module.CHANGES_PATH = path
            args = mock.Mock(
                object=json.dumps(
                    {
                        "kind": "create_machine_account",
                        "technique_id": "persistence.add-computer",
                        "target": "ATTACKER$",
                        "summary": "Created machine account ATTACKER$",
                        "reversal": "Remove-ADComputer -Identity ATTACKER$",
                    }
                )
            )
            self.assertEqual(module.cmd_add(args), 0)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["changes"]), 1)
            self.assertEqual(data["changes"][0]["kind"], "create_machine_account")
            self.assertEqual(data["changes"][0]["target"], "ATTACKER$")
            self.assertTrue(data["changes"][0]["id"])


if __name__ == "__main__":
    unittest.main()
