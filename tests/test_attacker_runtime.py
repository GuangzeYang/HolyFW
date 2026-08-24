#!/usr/bin/env python3
"""Tests for attacker batch generation, serial execution, and result logs."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from common.agent_request_abc import AgentResponse

from attacker.execute import append_execution_log, execute_task
from attacker.generation import fill_next_batch, load_generation_resources, parse_generated_tasks
from attacker.runtime import run_loop, step
from attacker.task_file import (
    all_completed,
    empty_slot_indices,
    load_attacker_payload,
    load_attacker_tasks,
    pending_ready,
    save_attacker_tasks,
    tasks_from_schedule,
)


def _now(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 23, hour, minute, tzinfo=datetime.now().astimezone().tzinfo)


class ParseGeneratedTasksTests(unittest.TestCase):
    def test_tasks_object_and_raw_array(self) -> None:
        self.assertEqual(
            parse_generated_tasks('{"tasks": ["Use the ad-attack skill: execute discovery.orientation against domain."]}'),
            ["Use the ad-attack skill: execute discovery.orientation against domain."],
        )
        self.assertEqual(
            parse_generated_tasks('["alpha", {"task": "beta"}]'),
            ["alpha", "beta"],
        )

    def test_empty_response(self) -> None:
        self.assertEqual(parse_generated_tasks(""), [])
        self.assertEqual(parse_generated_tasks("not json"), [])


class TaskFileTests(unittest.TestCase):
    def test_schedule_creates_empty_content_slots(self) -> None:
        tasks = tasks_from_schedule(["09:11", "10:22"])
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["planned_time"], "09:11")
        self.assertEqual(tasks[0]["task"], "")
        self.assertEqual(tasks[0]["started_at"], "")
        self.assertEqual(tasks[0]["completed_at"], "")

    def test_round_trip_list_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks_08-23.json"
            original = tasks_from_schedule(["09:11"])
            save_attacker_tasks(path, original)
            loaded = load_attacker_tasks(path)
            self.assertEqual(loaded[0]["planned_time"], "09:11")


class BatchFillTests(unittest.TestCase):
    def test_fills_five_then_remainder(self) -> None:
        tasks = tasks_from_schedule(["09:11", "09:22", "10:01", "14:07", "15:20", "16:33"])
        batches: list[int] = []

        def request_batch(*, batch_size: int, **_kwargs: object) -> list[str]:
            batches.append(batch_size)
            return [f"task-{index}" for index in range(batch_size)]

        fill_next_batch(
            tasks,
            batch_size=5,
            agent_client=mock.Mock(),
            request_batch=request_batch,
        )
        self.assertEqual(batches, [5])
        self.assertEqual(empty_slot_indices(tasks), [5])
        self.assertEqual(len(pending_ready(tasks)), 5)

        fill_next_batch(
            tasks,
            batch_size=5,
            agent_client=mock.Mock(),
            request_batch=request_batch,
        )
        self.assertEqual(batches, [5, 1])
        self.assertEqual(empty_slot_indices(tasks), [])
        self.assertEqual(tasks[5]["task"], "task-0")


class SchedulerStepTests(unittest.TestCase):
    def test_no_pending_triggers_fill(self) -> None:
        tasks = tasks_from_schedule(["09:11", "09:22"])
        fills: list[int] = []

        def fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
            fills.append(size)
            for item in current:
                if not item["task"]:
                    item["task"] = "generated"
                    break
            return current

        action = step(
            tasks,
            now=_now(8, 0),
            batch_size=5,
            fill_batch=fill,
            execute_one=lambda _item: None,
        )
        self.assertEqual(action, "filled")
        self.assertEqual(fills, [2])
        self.assertEqual(pending_ready(tasks)[0]["task"], "generated")

    def test_due_and_overdue_run_serially(self) -> None:
        tasks = tasks_from_schedule(["09:11", "09:22", "16:00"])
        tasks[0]["task"] = "one"
        tasks[1]["task"] = "two"
        tasks[2]["task"] = "later"
        order: list[str] = []

        def execute_one(item: dict[str, str]) -> None:
            order.append(item["task"])
            item["completed_at"] = "done"

        action = step(
            tasks,
            now=_now(12, 0),
            batch_size=5,
            fill_batch=lambda current, _size: current,
            execute_one=execute_one,
        )
        self.assertEqual(action, "executed")
        self.assertEqual(order, ["one", "two"])
        self.assertEqual(tasks[2]["completed_at"], "")

    def test_future_tasks_wait(self) -> None:
        tasks = tasks_from_schedule(["16:00"])
        tasks[0]["task"] = "later"
        executed = []
        action = step(
            tasks,
            now=_now(9, 0),
            batch_size=5,
            fill_batch=lambda current, _size: current,
            execute_one=lambda item: executed.append(item),
        )
        self.assertEqual(action, "wait")
        self.assertEqual(executed, [])

    def test_all_completed_is_done(self) -> None:
        tasks = tasks_from_schedule(["09:11"])
        tasks[0]["task"] = "one"
        tasks[0]["completed_at"] = "2026-08-23T09:12:00"
        self.assertTrue(all_completed(tasks))
        action = step(
            tasks,
            now=_now(18, 0),
            batch_size=5,
            fill_batch=lambda current, _size: current,
            execute_one=lambda _item: None,
        )
        self.assertEqual(action, "done")


class ExecutionLogTests(unittest.TestCase):
    def test_append_log_has_result_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            path = append_execution_log(
                logs_dir,
                planned_time="09:15",
                task="Use the ad-attack skill: execute discovery.orientation against domain.",
                result="orientation complete",
                exit_code=0,
                day=datetime(2026, 8, 23).date(),
            )
            line = path.read_text(encoding="utf-8").strip()
            record = json.loads(line)
            self.assertEqual(record["planned_time"], "09:15")
            self.assertEqual(record["result"], "orientation complete")
            self.assertEqual(record["exit_code"], 0)
            self.assertIn("ad-attack skill", record["task"])

    def test_execute_task_writes_one_jsonl_line(self) -> None:
        item = {
            "task": "run discovery",
            "planned_time": "09:15",
            "started_at": "",
            "completed_at": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            execute_task(
                item,
                logs_dir=logs_dir,
                timeout_seconds=30,
                now=_now(9, 16),
                runner=lambda _task, _timeout: (0, "agent output"),
                day=datetime(2026, 8, 23).date(),
            )
            self.assertTrue(item["started_at"])
            self.assertTrue(item["completed_at"])
            records = [
                json.loads(line)
                for line in (logs_dir / "tasks_2026-08-23.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["result"], "agent output")
            self.assertEqual(records[0]["exit_code"], 0)


class RunLoopTests(unittest.TestCase):
    def test_loop_fills_executes_and_exits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "config.json").write_text(
                json.dumps(
                    {
                        "batch_size": 5,
                        "poll_interval_seconds": 1,
                        "exec": {"timeout_seconds": 30},
                        "generator": {
                            "max_attempts": 1,
                            "time_model": {
                                "tasks_per_role": 3,
                                "mu_am_minutes": 630,
                                "mu_pm_minutes": 900,
                                "sigma_am_minutes": 50,
                                "sigma_pm_minutes": 65,
                                "a_am": 1.0,
                                "a_pm": 1.0,
                                "phi": 0.85,
                                "sigma_eta": 0.18,
                                "avoid_five_minutes": True,
                            },
                        },
                        "paths": {"data_dir": "role_task", "logs_dir": "logs"},
                    }
                ),
                encoding="utf-8",
            )
            fills: list[int] = []

            def fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
                fills.append(size)
                filled = 0
                for item in current:
                    if not item["task"] and filled < size:
                        item["task"] = f"generated-{filled}"
                        filled += 1
                return current

            def execute_one(item: dict[str, str]) -> None:
                item["started_at"] = "start"
                item["completed_at"] = "done"

            with mock.patch("attacker.runtime.resolve_workspace", return_value=workspace):
                with mock.patch(
                    "attacker.runtime.generate_schedule",
                    return_value=["09:11", "09:22", "10:03"],
                ):
                    code = run_loop(
                        config_path=workspace / "config.json",
                        day=datetime(2026, 8, 23).date(),
                        now_fn=lambda: _now(18, 0),
                        sleep_fn=lambda _seconds: None,
                        fill_batch=fill,
                        execute_one=execute_one,
                    )
            self.assertEqual(code, 0)
            self.assertEqual(fills, [3])
            stored = load_attacker_tasks(workspace / "role_task" / "tasks_08-23.json")
            self.assertTrue(all_completed(stored))
            self.assertEqual([item["task"] for item in stored], ["generated-0", "generated-1", "generated-2"])


class SchedulerLogTests(unittest.TestCase):
    def test_run_loop_logs_fill_wait_execute_done(self) -> None:
        tz = datetime.now().astimezone().tzinfo
        clock = {"now": datetime(2026, 8, 23, 8, 0, tzinfo=tz)}
        sleeps = {"n": 0}

        def now_fn() -> datetime:
            return clock["now"]

        def sleep_fn(_seconds: float) -> None:
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                clock["now"] = datetime(2026, 8, 23, 19, 0, tzinfo=tz)

        def fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
            filled = 0
            for item in current:
                if not item["task"] and filled < size:
                    item["task"] = f"generated-{filled}"
                    filled += 1
            return current

        def execute_one(item: dict[str, str]) -> None:
            item["started_at"] = "start"
            item["completed_at"] = "done"

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "config.json").write_text(
                json.dumps(
                    {
                        "batch_size": 5,
                        "poll_interval_seconds": 15,
                        "exec": {"timeout_seconds": 30},
                        "generator": {
                            "max_attempts": 1,
                            "time_model": {
                                "tasks_per_role": 1,
                                "mu_am_minutes": 630,
                                "mu_pm_minutes": 900,
                                "sigma_am_minutes": 50,
                                "sigma_pm_minutes": 65,
                                "a_am": 1.0,
                                "a_pm": 1.0,
                                "phi": 0.85,
                                "sigma_eta": 0.18,
                                "avoid_five_minutes": True,
                            },
                        },
                        "paths": {"data_dir": "role_task", "logs_dir": "logs"},
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch("attacker.runtime.resolve_workspace", return_value=workspace):
                with mock.patch("attacker.runtime.generate_schedule", return_value=["18:11"]):
                    with self.assertLogs("attacker", level="INFO") as captured:
                        code = run_loop(
                            config_path=workspace / "config.json",
                            day=datetime(2026, 8, 23).date(),
                            now_fn=now_fn,
                            sleep_fn=sleep_fn,
                            fill_batch=fill,
                            execute_one=execute_one,
                        )
        self.assertEqual(code, 0)
        text = "\n".join(captured.output)
        self.assertIn("Filling next batch", text)
        self.assertIn("Waiting until planned_time=18:11", text)
        self.assertEqual(text.count("Waiting until planned_time=18:11"), 1)
        self.assertIn("Executing due task planned_time=18:11", text)
        self.assertIn("All attacker tasks completed", text)


class BaseTimeTests(unittest.TestCase):
    def test_shift_rewrites_planned_time_and_stamps_file(self) -> None:
        from datetime import date

        from commander.schedule_shift import SCHEDULE_SHIFT_KEY

        from attacker.runtime import apply_attacker_base_time

        tasks = tasks_from_schedule(["09:06", "13:03", "17:46"])
        shifted, stamp, changed = apply_attacker_base_time(
            tasks,
            base_time=21,
            file_day=date(2026, 8, 21),
        )
        self.assertTrue(changed)
        self.assertEqual([item["planned_time"] for item in shifted], ["21:06", "01:03", "05:46"])
        assert stamp is not None
        self.assertEqual(stamp["base_time"], 21)
        self.assertEqual(stamp["origin_hour"], 9)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks_08-21.json"
            save_attacker_tasks(path, shifted, shift=stamp)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload[SCHEDULE_SHIFT_KEY]["base_time"], 21)
            loaded, loaded_stamp = load_attacker_payload(path)
            self.assertEqual(loaded[0]["planned_time"], "21:06")
            assert loaded_stamp is not None
            self.assertEqual(loaded_stamp["base_time"], 21)

    def test_wrapped_midnight_task_is_not_due_before_next_day(self) -> None:
        from datetime import date

        from attacker.runtime import due_ready

        tasks = tasks_from_schedule(["21:06", "01:03"])
        tasks[0]["task"] = "evening"
        tasks[1]["task"] = "after-midnight"
        file_day = date(2026, 8, 21)
        evening_due = due_ready(tasks, datetime(2026, 8, 21, 22, 0), file_day=file_day)
        self.assertEqual([item["task"] for item in evening_due], ["evening"])
        morning_due = due_ready(tasks, datetime(2026, 8, 22, 1, 10), file_day=file_day)
        self.assertEqual([item["task"] for item in morning_due], ["evening", "after-midnight"])

    def test_cli_base_time_forwards_and_rejects_24(self) -> None:
        import attacker.cli as attacker_cli

        parser = attacker_cli.build_parser()
        args = parser.parse_args(["--base-time", "21"])
        self.assertEqual(args.base_time, 21)
        args = parser.parse_args(["run", "--base-time", "21"])
        self.assertEqual(args.base_time, 21)
        args = parser.parse_args(["--base-time", "21", "run"])
        self.assertEqual(args.base_time, 21)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--base-time", "24"])

    def test_existing_list_file_is_restamped(self) -> None:
        from datetime import date

        from attacker.runtime import ensure_task_file

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks_08-21.json"
            save_attacker_tasks(path, tasks_from_schedule(["09:06", "13:03"]))
            tasks, stamp = ensure_task_file(
                path,
                expected_count=2,
                day=date(2026, 8, 21),
                time_model={"tasks_per_role": 2},
                base_time=21,
            )
            self.assertEqual([item["planned_time"] for item in tasks], ["21:06", "01:03"])
            assert stamp is not None
            self.assertEqual(stamp["base_time"], 21)
            loaded, loaded_stamp = load_attacker_payload(path)
            self.assertEqual([item["planned_time"] for item in loaded], ["21:06", "01:03"])
            assert loaded_stamp is not None
            self.assertEqual(loaded_stamp["base_time"], 21)

    def test_resolve_run_day_pins_yesterday_while_window_open(self) -> None:
        from datetime import date

        from attacker.runtime import resolve_run_day

        yesterday = date(2026, 8, 21)
        tasks = tasks_from_schedule(["21:06", "01:03"])
        stamp = {"origin_hour": 9, "base_time": 21, "file_day": yesterday.isoformat()}
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            save_attacker_tasks(data_dir / "tasks_08-21.json", tasks, shift=stamp)
            self.assertEqual(
                resolve_run_day(
                    data_dir,
                    now=datetime(2026, 8, 22, 1, 0),
                ),
                yesterday,
            )
            self.assertEqual(
                resolve_run_day(
                    data_dir,
                    now=datetime(2026, 8, 22, 6, 0),
                ),
                date(2026, 8, 22),
            )

    def test_loop_applies_cli_base_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "config.json").write_text(
                json.dumps(
                    {
                        "batch_size": 5,
                        "poll_interval_seconds": 1,
                        "base_time": 9,
                        "exec": {"timeout_seconds": 30},
                        "generator": {
                            "max_attempts": 1,
                            "time_model": {
                                "tasks_per_role": 3,
                                "mu_am_minutes": 630,
                                "mu_pm_minutes": 900,
                                "sigma_am_minutes": 50,
                                "sigma_pm_minutes": 65,
                                "a_am": 1.0,
                                "a_pm": 1.0,
                                "phi": 0.85,
                                "sigma_eta": 0.18,
                                "avoid_five_minutes": True,
                            },
                        },
                        "paths": {"data_dir": "role_task", "logs_dir": "logs"},
                    }
                ),
                encoding="utf-8",
            )

            def fill(current: list[dict[str, str]], size: int) -> list[dict[str, str]]:
                filled = 0
                for item in current:
                    if not item["task"] and filled < size:
                        item["task"] = f"generated-{filled}"
                        filled += 1
                return current

            def execute_one(item: dict[str, str]) -> None:
                item["started_at"] = "start"
                item["completed_at"] = "done"

            with mock.patch("attacker.runtime.resolve_workspace", return_value=workspace):
                with mock.patch(
                    "attacker.runtime.generate_schedule",
                    return_value=["09:11", "09:22", "10:03"],
                ):
                    code = run_loop(
                        config_path=workspace / "config.json",
                        day=datetime(2026, 8, 23).date(),
                        now_fn=lambda: _now(23, 0),
                        sleep_fn=lambda _seconds: None,
                        fill_batch=fill,
                        execute_one=execute_one,
                        base_time=21,
                    )
            self.assertEqual(code, 0)
            stored, stamp = load_attacker_payload(workspace / "role_task" / "tasks_08-23.json")
            self.assertEqual(
                [item["planned_time"] for item in stored],
                ["21:11", "21:22", "22:03"],
            )
            assert stamp is not None
            self.assertEqual(stamp["base_time"], 21)
            self.assertTrue(all_completed(stored))


class GenerationMessageTests(unittest.TestCase):
    def test_fill_uses_model_client_payload(self) -> None:
        tasks = tasks_from_schedule(["09:11"])
        client = mock.Mock()
        client.request_completion.return_value = AgentResponse(
            model="test",
            response_text='{"tasks": ["Use the ad-attack skill: execute discovery.orientation against domain."]}',
            status_code=200,
            elapsed_seconds=0.1,
            raw_response_text="{}",
        )
        fill_next_batch(
            tasks,
            batch_size=5,
            agent_client=client,
            system_prompt="You are an automated planner",
            prompt_template="Use the ad-attack skill",
            state={"domain": {"name": "ndrtest.local"}},
            max_attempts=1,
        )
        self.assertIn("ad-attack skill", tasks[0]["task"])
        _args, kwargs = client.request_completion.call_args
        messages = kwargs["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("automated planner", messages[0]["content"])
        user_payload = json.loads(messages[1]["content"])
        self.assertEqual(user_payload["prompt_template"], "Use the ad-attack skill")
        self.assertEqual(user_payload["state"]["domain"]["name"], "ndrtest.local")

    def test_load_generation_resources_from_skill_pack(self) -> None:
        system_prompt, prompt_template, state = load_generation_resources()
        self.assertIn("automated planner", system_prompt.lower())
        self.assertIn("ad-attack", prompt_template)
        self.assertIsInstance(state, dict)


if __name__ == "__main__":
    unittest.main()
