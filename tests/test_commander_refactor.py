#!/usr/bin/env python3
"""Regression tests for commander refactor modules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from unittest import mock

import commander.deepseek_client as deepseek_client
import commander.role_task_generation as role_task_generation
from commander.agent_request_abc import AgentRequestABC, AgentRequestError, AgentResponse, AgentTimeoutError
from commander.deepseek_client import DeepSeekAgentClient, DeepSeekConfig
from commander.domain import STATUS_PLANNED, STATUS_WAITING
from commander.logging_setup import write_interactive_log
from commander.policies import EarliestPendingSelectionPolicy, task_needs_dispatch
from commander.role_file_service import RoleTaskFileService
from commander.role_task_generation import RoleTaskGenerationResult

try:
    from commander.repository import DailyTaskRepository
except ModuleNotFoundError:
    DailyTaskRepository = None
from common import (
    build_controlled_task_file_paths,
    build_role_task_prompt,
    classify_validation_failure,
    extract_json_object,
    validate_generated_task_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSTRAINTS_PATH = REPO_ROOT / "task_generation_constraints.md"
CONSTRAINTS_TEMPLATE = CONSTRAINTS_PATH.read_text(encoding="utf-8")


class PolicyTests(unittest.TestCase):
    def test_loaded_without_task_id_is_pending(self) -> None:
        task = {
            "is_load": True,
            "task_id": "",
            "status": "planned",
        }
        self.assertTrue(task_needs_dispatch(task))

    def test_policy_finds_earliest_pending_index(self) -> None:
        policy = EarliestPendingSelectionPolicy()
        tasks = [
            {"is_load": True, "task_id": "abc", "status": "waiting"},
            {"is_load": True, "task_id": "", "status": "planned"},
            {"is_load": False, "task_id": "", "status": "planned"},
        ]
        self.assertEqual(policy.find_next_pending_index(tasks), 1)
        self.assertEqual(policy.find_next_pending_index(tasks, start_index=2), 2)


@unittest.skipIf(DailyTaskRepository is None, "filelock is not installed in this environment")
class RepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp_dir.name)
        self.repo = DailyTaskRepository(self.data_dir)
        self.today = date.today().isoformat()

        initial = {
            "hr": [
                {
                    "time": "09:01",
                    "is_load": True,
                    "task": "t1",
                    "task_id": "",
                    "status": STATUS_PLANNED,
                    "issued_at": "",
                    "expiry_time": "",
                    "completed_at": "",
                    "report_message": "",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            ]
        }
        self.repo.save_day(self.today, initial)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_bind_dispatched_task_updates_waiting_state(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )
        data = self.repo.load_day(self.today)
        item = data["hr"][0]
        self.assertEqual(item["task_id"], "abc12345")
        self.assertEqual(item["status"], STATUS_WAITING)
        self.assertTrue(item["issued_at"])

    def test_report_requires_waiting_transition(self) -> None:
        result = self.repo.update_task_report(
            task_ref=f"{self.today}_hr_abc12345",
            status="successed",
            message="",
            exit_code=0,
            stdout="ok",
            stderr="",
        )
        self.assertFalse(result["ok"])

    def test_waiting_visibility_respects_expiry(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=future,
            planned_time="09:01",
        )
        self.assertTrue(self.repo.has_active_waiting_task("hr", self.today))

    def test_expired_waiting_task_is_marked_failed(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=past,
            planned_time="09:01",
        )

        expired = self.repo.expire_waiting_tasks(self.today)

        self.assertEqual(len(expired), 1)
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["exit_code"], -1)
        self.assertIn("deadline expired", item["report_message"])

    def test_report_overwrites_successed_with_latest_failed_result(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        task_ref = f"{self.today}_hr_abc12345"
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )

        first = self.repo.update_task_report(
            task_ref=task_ref,
            status="successed",
            message="first ok",
            exit_code=0,
            stdout="old stdout",
            stderr="old stderr",
        )
        second = self.repo.update_task_report(
            task_ref=task_ref,
            status="failed",
            message="latest failed",
            exit_code=7,
            stdout="new stdout",
            stderr="new stderr",
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["report_message"], "latest failed")
        self.assertEqual(item["exit_code"], 7)
        self.assertEqual(item["stdout"], "new stdout")
        self.assertEqual(item["stderr"], "new stderr")
        self.assertTrue(item["completed_at"])

    def test_report_overwrites_failed_with_latest_successed_result(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        task_ref = f"{self.today}_hr_abc12345"
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )

        first = self.repo.update_task_report(
            task_ref=task_ref,
            status="failed",
            message="first failed",
            exit_code=1,
            stdout="old stdout",
            stderr="old stderr",
        )
        second = self.repo.update_task_report(
            task_ref=task_ref,
            status="successed",
            message="latest ok",
            exit_code=0,
            stdout="new stdout",
            stderr="",
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["status"], "successed")
        self.assertEqual(item["report_message"], "latest ok")
        self.assertEqual(item["exit_code"], 0)
        self.assertEqual(item["stdout"], "new stdout")
        self.assertEqual(item["stderr"], "")
        self.assertTrue(item["completed_at"])

    def test_atomic_index_update_applies_to_unissued_task(self) -> None:
        changed = self.repo.update_task_fields_by_index(
            date_str=self.today,
            role="hr",
            index=0,
            fields={"is_load": True, "report_message": "queued"},
            only_if_no_task_id=True,
        )
        self.assertTrue(changed)
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertTrue(item["is_load"])
        self.assertEqual(item["report_message"], "queued")

    def test_atomic_index_update_does_not_overwrite_issued_task(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )

        changed = self.repo.update_task_fields_by_index(
            date_str=self.today,
            role="hr",
            index=0,
            fields={"is_load": False, "status": STATUS_PLANNED, "task_id": ""},
            only_if_no_task_id=True,
        )
        self.assertFalse(changed)

        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["task_id"], "abc12345")
        self.assertEqual(item["status"], STATUS_WAITING)

    def test_rollback_dispatched_task_restores_retryable_state(self) -> None:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        self.repo.bind_dispatched_task(
            date_str=self.today,
            role="hr",
            task_id="abc12345",
            task_text="t1",
            expiry_time=expiry,
            planned_time="09:01",
        )

        rolled_back = self.repo.rollback_dispatched_task(
            self.today,
            "hr",
            "abc12345",
            "dispatch failed",
        )

        self.assertTrue(rolled_back)
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["task_id"], "")
        self.assertEqual(item["status"], STATUS_PLANNED)
        self.assertFalse(item["is_load"])
        self.assertEqual(item["report_message"], "dispatch failed")

    def test_dispatch_binds_before_send_and_rolls_back_on_failure(self) -> None:
        import commander.dispatch as dispatch

        cfg = self.data_dir / "commander.ini"
        cfg.write_text("[hr]\nhost = 127.0.0.1\nport = 38472\n", encoding="utf-8")
        argv = [
            "dispatch.py",
            "--data-dir",
            str(self.data_dir),
            "--config",
            str(cfg),
            "--target",
            "hr",
            "--command",
            "echo ok",
            "--task",
            "t1",
            "--planned-time",
            "09:01",
        ]

        def fail_after_verifying_waiting(*args, **kwargs):
            item = self.repo.load_day(self.today)["hr"][0]
            self.assertEqual(item["task_id"], args[2].rsplit("_", 1)[-1])
            self.assertEqual(item["status"], STATUS_WAITING)
            return {"ok": False, "error": "down"}

        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch("commander.dispatch.send_to_soldier", side_effect=fail_after_verifying_waiting),
        ):
            rc = dispatch.main()

        self.assertEqual(rc, 1)
        item = self.repo.load_day(self.today)["hr"][0]
        self.assertEqual(item["task_id"], "")
        self.assertEqual(item["status"], STATUS_PLANNED)


class LoggingSetupTests(unittest.TestCase):
    def test_write_interactive_log_uses_role_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = write_interactive_log(
                Path(tmp),
                role="hr",
                attempt=3,
                provider="deepseek",
                model="deepseek-chat",
                status_code=200,
                finish_reason="stop",
                response_text="role work content",
                raw_response_text='{"choices":[{"message":{"content":"role work content"}}]}',
                error_text="",
                request_state="finished",
                caller="generate_role_task",
            )

            self.assertTrue(log_file.exists())
            self.assertTrue(log_file.name.startswith("hr_attempt3_"))
            self.assertTrue(log_file.name.endswith("_interactive.log"))
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("role: hr", content)
            self.assertIn("attempt: 3", content)
            self.assertIn("note: interactive", content)
            self.assertIn("caller: generate_role_task", content)
            self.assertIn("finish_reason: stop", content)
            self.assertIn("request_state: finished", content)
            self.assertNotIn("--- PROMPT_TEXT ---", content)
            self.assertIn("--- RESPONSE_TEXT ---", content)
            self.assertIn("role work content", content)


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.payload = payload
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class DeepSeekClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DeepSeekConfig(
            api_base_url="https://api.deepseek.com",
            api_key="test-key",
            model="deepseek-chat",
            request_timeout_seconds=10,
            max_tokens=8192,
        )

    def test_request_deepseek_completion_returns_message_content(self) -> None:
        payload = {
            "model": "deepseek-chat",
            "choices": [{"message": {"content": '{"hr": []}'}, "finish_reason": "stop"}],
        }
        with mock.patch.object(
            deepseek_client.urllib.request,
            "urlopen",
            return_value=FakeHTTPResponse(payload),
        ) as mocked:
            response = DeepSeekAgentClient(self.config).request_completion("hello")

        self.assertEqual(response.model, "deepseek-chat")
        self.assertEqual(response.response_text, '{"hr": []}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.finish_reason, "stop")
        self.assertIn('"choices"', response.raw_response_text)
        request_obj = mocked.call_args.args[0]
        self.assertEqual(request_obj.full_url, "https://api.deepseek.com/chat/completions")
        request_body = json.loads(request_obj.data.decode("utf-8"))
        self.assertEqual(request_body["max_tokens"], 8192)

    def test_request_deepseek_completion_supports_content_part_lists(self) -> None:
        payload = {
            "model": "deepseek-chat",
            "choices": [
                {
                    "message": {"content": [{"text": '{"hr": '}, {"text": '[]}'}, {"type": "ignored"}]},
                    "finish_reason": "stop",
                }
            ],
        }
        with mock.patch.object(
            deepseek_client.urllib.request,
            "urlopen",
            return_value=FakeHTTPResponse(payload),
        ):
            response = DeepSeekAgentClient(self.config).request_completion("hello")
        self.assertEqual(response.response_text, '{"hr": []}')
        self.assertEqual(response.finish_reason, "stop")

    def test_request_deepseek_completion_extracts_finish_reason(self) -> None:
        payload = {
            "model": "deepseek-chat",
            "choices": [{"message": {"content": '{"hr": []}'}, "finish_reason": "length"}],
        }
        with mock.patch.object(
            deepseek_client.urllib.request,
            "urlopen",
            return_value=FakeHTTPResponse(payload),
        ):
            response = DeepSeekAgentClient(self.config).request_completion("hello")
        self.assertEqual(response.finish_reason, "length")

    def test_request_deepseek_completion_raises_api_error_on_http_error(self) -> None:
        error_body = b'{"error":"bad request"}'
        http_error = urllib.error.HTTPError(
            url="https://api.deepseek.com/chat/completions",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=None,
        )
        http_error.read = lambda: error_body
        with mock.patch.object(deepseek_client.urllib.request, "urlopen", side_effect=http_error):
            with self.assertRaises(AgentRequestError) as ctx:
                DeepSeekAgentClient(self.config).request_completion("hello")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("bad request", ctx.exception.response_text)

    def test_request_deepseek_completion_raises_timeout_on_url_error(self) -> None:
        with mock.patch.object(
            deepseek_client.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("timed out"),
        ):
            with self.assertRaises(AgentTimeoutError):
                DeepSeekAgentClient(self.config).request_completion("hello")


class ExtractorTests(unittest.TestCase):
    def test_extract_json_object_from_wrapped_text(self) -> None:
        text = "prefix\n{\"hr\": []}\nsuffix"
        self.assertEqual(extract_json_object(text), {"hr": []})

    def test_extract_json_object_returns_none_for_invalid_text(self) -> None:
        self.assertIsNone(extract_json_object("not json"))

    def test_extract_json_object_accepts_fenced_root_object(self) -> None:
        text = "```json\n{\"hr\": [], \"accountancy\": []}\n```"
        self.assertEqual(extract_json_object(text), {"hr": [], "accountancy": []})

    def test_extract_json_object_rejects_truncated_outer_object(self) -> None:
        text = (
            "```json\n"
            "{\n"
            '    "hr": [\n'
            '        {"time": "09:07", "is_load": false, "task": "View email"}\n'
            "    ],\n"
            '    "programmer": [\n'
            '        {"time": "09:24", "is_load": false, "task": "View the IT-Dev directory"},\n'
            '        {"time": "09:41", "is_load": false\n'
        )
        self.assertIsNone(extract_json_object(text))

    def test_classify_validation_failure_distinguishes_schema_and_quality(self) -> None:
        self.assertEqual(classify_validation_failure("Missing roles: ['hr']"), "schema_fail")
        self.assertEqual(
            classify_validation_failure("Role 'hr' random minute ratio too low: 0.50 < 0.80"),
            "quality_fail",
        )


class FileContractTests(unittest.TestCase):
    def test_build_controlled_task_file_paths_returns_candidate_and_final(self) -> None:
        candidate, final = build_controlled_task_file_paths(Path("commander/role_task"), date(2026, 4, 21))
        self.assertEqual(candidate.as_posix(), "commander/role_task/tasks_04-21.candidate.json")
        self.assertEqual(final.as_posix(), "commander/role_task/tasks_04-21.json")

    def test_validate_generated_task_file_accepts_valid_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "tasks_04-21.candidate.json"
            file_path.write_text(
                json.dumps({"hr": [{"time": "09:01", "is_load": False, "task": "Process onboarding documents"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            failure_type, reason, data, file_size = validate_generated_task_file(
                file_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
            )
            self.assertIsNone(failure_type)
            self.assertIsNone(reason)
            self.assertGreater(file_size, 0)
            assert data is not None
            self.assertIn("task_id", data["hr"][0])
            self.assertEqual(data["hr"][0]["task"], "Process onboarding documents")

    def test_validate_generated_task_file_distinguishes_parse_and_schema_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parse_path = Path(tmp) / "tasks_04-21.candidate.json"
            parse_path.write_text('{"hr": [', encoding="utf-8")
            failure_type, reason, data, _ = validate_generated_task_file(
                parse_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
            )
            self.assertEqual(failure_type, "parse_fail")
            self.assertIn("JSONDecodeError", reason or "")
            self.assertIsNone(data)

            schema_path = Path(tmp) / "tasks_04-22.candidate.json"
            schema_path.write_text(json.dumps([{"hr": []}]), encoding="utf-8")
            failure_type, reason, data, _ = validate_generated_task_file(
                schema_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
            )
            self.assertEqual(failure_type, "schema_fail")
            self.assertEqual(reason, "Generated JSON must be a dictionary")
            self.assertIsNone(data)

    def test_validate_generated_task_file_rejects_too_many_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "tasks_04-23.candidate.json"
            file_path.write_text(
                json.dumps(
                    {
                        "hr": [
                            {"time": "09:01", "is_load": False, "task": "a"},
                            {"time": "09:13", "is_load": False, "task": "b"},
                            {"time": "09:26", "is_load": False, "task": "c"},
                            {"time": "09:39", "is_load": False, "task": "d"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            failure_type, reason, data, _ = validate_generated_task_file(
                file_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
            )
            self.assertEqual(failure_type, "quality_fail")
            self.assertEqual(reason, "Role 'hr' has too many tasks: 4 > 3")
            self.assertIsNone(data)

    def test_validate_generated_task_file_sorts_preserved_times_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "tasks_04-24.candidate.json"
            file_path.write_text(
                json.dumps(
                    {
                        "hr": [
                            {"time": "09:31", "is_load": False, "task": "later"},
                            {"time": "09:01", "is_load": True, "task": "earlier"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            failure_type, reason, data, _ = validate_generated_task_file(
                file_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
                preserve_generated_times=True,
            )
            self.assertIsNone(failure_type)
            self.assertIsNone(reason)
            assert data is not None
            self.assertEqual([task["time"] for task in data["hr"]], ["09:01", "09:31"])
            self.assertEqual([task["task"] for task in data["hr"]], ["earlier", "later"])
            self.assertEqual([task["is_load"] for task in data["hr"]], [True, False])

    def test_validate_generated_task_file_still_rejects_duplicate_times_after_sorting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "tasks_04-25.candidate.json"
            file_path.write_text(
                json.dumps(
                    {
                        "hr": [
                            {"time": "09:01", "is_load": False, "task": "first"},
                            {"time": "09:01", "is_load": True, "task": "second"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            failure_type, reason, data, _ = validate_generated_task_file(
                file_path,
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                roles=("hr",),
                preserve_generated_times=True,
            )
            self.assertEqual(failure_type, "quality_fail")
            self.assertEqual(reason, "Role 'hr' tasks are not strictly increasing")
            self.assertIsNone(data)


class PromptTests(unittest.TestCase):
    def test_build_role_task_prompt_requires_json_only_and_english_templates(self) -> None:
        domain_context = "# Task Content Templates\nUse the smb-access template to access the shared directory."
        prompt = build_role_task_prompt(
            domain_context,
            CONSTRAINTS_TEMPLATE,
            min_tasks_per_role=2,
            max_tasks_per_role=6,
            roles=("hr", "accountancy"),
        )
        self.assertIn("Return exactly one JSON object", prompt)
        self.assertIn("Follow every task-content template and constraint in the domain context", prompt)
        self.assertIn("exactly 4 tasks", prompt)
        self.assertIn("ceil((min+max)/2)", prompt)
        self.assertIn("Related dependency facts", prompt)
        self.assertIn("only to infer implicit relationships and ordering", prompt)
        self.assertIn("task times must be strictly increasing in JSON array order", prompt)
        self.assertIn("strictly greater than the preceding task's time", prompt)
        self.assertIn("With 4 total tasks, at least 1 task(s) must have a minute value that is not divisible by 5", prompt)
        self.assertIn('"hr": [tasks]', prompt)
        self.assertIn('"accountancy": [tasks]', prompt)
        self.assertNotIn("TASK_FILE_READY", prompt)
        self.assertNotIn("generate_tasks.py", prompt)
        self.assertNotIn("tasks_final.json", prompt)
        self.assertIn("All task descriptions and all natural-language parameter values must be written in English", prompt)

    def test_build_role_task_prompt_inserts_dependency_between_domain_and_rules(self) -> None:
        domain_context = "DOMAIN_BLOCK"
        dep = "DEPENDENCY_BLOCK"
        prompt = build_role_task_prompt(
            domain_context,
            CONSTRAINTS_TEMPLATE,
            min_tasks_per_role=2,
            max_tasks_per_role=2,
            roles=("hr",),
            dependency_context=dep,
        )
        d = prompt.index("DOMAIN_BLOCK")
        dep_i = prompt.index("DEPENDENCY_BLOCK")
        h = prompt.index("Hard requirements")
        self.assertLess(d, dep_i)
        self.assertLess(dep_i, h)


class RuntimeConfigGeneratorFeasibilityTests(unittest.TestCase):
    def test_load_raises_when_tasks_exceed_workday_feasible_count(self) -> None:
        from commander.runtime_config import load_runtime_config

        base_path = Path(__file__).resolve().parent.parent / "commander" / "config.json"
        data = json.loads(base_path.read_text(encoding="utf-8"))
        data["generator"]["min_tasks_per_role"] = 100
        data["generator"]["max_tasks_per_role"] = 100
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config(cfg_path)
        self.assertIn("each role can have at most", str(ctx.exception))

    def test_load_raises_when_min_internal_below_10(self) -> None:
        from commander.runtime_config import load_runtime_config

        base_path = Path(__file__).resolve().parent.parent / "commander" / "config.json"
        data = json.loads(base_path.read_text(encoding="utf-8"))
        data["generator"]["min_internal"] = 9
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config(cfg_path)
        self.assertIn("min_internal", str(ctx.exception))

    def test_load_raises_when_dispatch_client_timeout_is_too_short(self) -> None:
        from commander.runtime_config import load_runtime_config

        base_path = Path(__file__).resolve().parent.parent / "commander" / "config.json"
        data = json.loads(base_path.read_text(encoding="utf-8"))
        data["dispatch"]["soldier_timeout_seconds"] = 120.0
        data["dispatch"]["client_timeout_seconds"] = 30
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                load_runtime_config(cfg_path)
        self.assertIn("client_timeout_seconds", str(ctx.exception))

    def test_load_reads_worker_threads(self) -> None:
        from commander.runtime_config import get_server_config, load_runtime_config

        base_path = Path(__file__).resolve().parent.parent / "commander" / "config.json"
        data = json.loads(base_path.read_text(encoding="utf-8"))
        data["server"]["worker_threads"] = 6
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            loaded = load_runtime_config(cfg_path)
        self.assertEqual(get_server_config(loaded)["worker_threads"], 6)


class FakeAgentClient(AgentRequestABC):
    def __init__(
        self,
        *,
        response: AgentResponse | None = None,
        responses: list[AgentResponse] | None = None,
        side_effect: Exception | None = None,
        on_request: Callable[[int, str], None] | None = None,
    ) -> None:
        self._response = response
        self._responses = list(responses) if responses is not None else None
        self._side_effect = side_effect
        self._on_request = on_request
        self.prompts: list[str] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    @property
    def request_timeout_seconds(self) -> int:
        return 60

    def request_completion(self, prompt: str) -> AgentResponse:
        self.prompts.append(prompt)
        if self._on_request is not None:
            self._on_request(len(self.prompts), prompt)
        if self._side_effect is not None:
            raise self._side_effect
        if self._responses is not None:
            if not self._responses:
                raise AssertionError("No fake responses left")
            return self._responses.pop(0)
        assert self._response is not None
        return self._response


class RoleTaskGenerationTests(unittest.TestCase):
    def _valid_response(
        self,
        role: str = "hr",
        task: str = "approve onboarding",
        *,
        time: str = "09:01",
        finish_reason: str | None = None,
    ) -> AgentResponse:
        response_text = json.dumps(
            {role: [{"time": time, "is_load": False, "task": task}]},
            ensure_ascii=False,
        )
        return AgentResponse(
            model="deepseek-chat",
            response_text=response_text,
            status_code=200,
            elapsed_seconds=1.25,
            raw_response_text=response_text,
            finish_reason=finish_reason,
        )

    def _saved_task(self, task: str, *, time: str = "09:01") -> dict[str, object]:
        return {
            "time": time,
            "is_load": False,
            "task": task,
            "task_id": "",
            "status": "planned",
            "issued_at": "",
            "expiry_time": "",
            "completed_at": "",
            "report_message": "",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    def test_generate_role_tasks_promotes_final_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            statuses: list[str] = []

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(response=self._valid_response()),
                emit_status=statuses.append,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.output_file, final_file)
            self.assertTrue(final_file.exists())
            self.assertFalse(final_file.with_name("tasks_04-21.candidate.json").exists())
            saved = json.loads(final_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["hr"][0]["task"], "approve onboarding")
            response_logs = list((logs_dir / f"agent_responses_{date.today().isoformat()}").glob("hr_*_interactive.log"))
            self.assertEqual(len(response_logs), 1)
            response_log_text = response_logs[0].read_text(encoding="utf-8")
            self.assertIn("provider: fake", response_log_text)
            self.assertIn("role: hr", response_log_text)
            self.assertIn("note: interactive", response_log_text)
            self.assertIn("--- RESPONSE_TEXT ---", response_log_text)
            self.assertNotIn("--- PROMPT_TEXT ---", response_log_text)
            self.assertTrue(any("Successfully generated unified tasks" in item for item in statuses))

    def test_generate_role_tasks_merges_single_role_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[
                    self._valid_response("hr", "approve onboarding"),
                    self._valid_response("programmer", "review code"),
                ]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "programmer"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            saved = json.loads(final_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["hr"][0]["task"], "approve onboarding")
            self.assertEqual(saved["programmer"][0]["task"], "review code")
            self.assertEqual(len(client.prompts), 2)
            self.assertIn('"hr": [tasks]', client.prompts[0])
            self.assertNotIn('"programmer": [tasks]', client.prompts[0])
            self.assertIn('"programmer": [tasks]', client.prompts[1])
            self.assertNotIn('"accountancy": [tasks]', client.prompts[1])

    def test_generate_role_tasks_persists_each_role_before_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")

            def on_request(index: int, _prompt: str) -> None:
                if index != 2:
                    return
                self.assertTrue(final_file.exists())
                saved = json.loads(final_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["hr"][0]["task"], "approve onboarding")
                self.assertNotIn("programmer", saved)

            client = FakeAgentClient(
                responses=[
                    self._valid_response("hr", "approve onboarding", time="09:01"),
                    self._valid_response("programmer", "review code", time="09:16"),
                ],
                on_request=on_request,
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "programmer"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)

    def test_generate_role_tasks_skips_completed_roles_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text(
                json.dumps({"hr": [self._saved_task("approve onboarding", time="09:01")]}, ensure_ascii=False),
                encoding="utf-8",
            )
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[self._valid_response("programmer", "review code", time="09:16")]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "programmer"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertEqual(len(client.prompts), 1)
            saved = json.loads(final_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["hr"][0]["task"], "approve onboarding")
            self.assertEqual(saved["programmer"][0]["task"], "review code")

    def test_generate_role_tasks_injects_related_context_from_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text(
                json.dumps(
                    {
                        "hr": [
                            self._saved_task(
                                "Use the exchange-use skill to send email, {recipient: manager@ndrtest.local, subject: onboarding process}",
                                time="10:01",
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[
                    self._valid_response(
                        "manager",
                        "Use the exchange-use skill to view email from hr@ndrtest.local",
                        time="10:16",
                    )
                ]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "manager"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=client,
                emit_status=lambda message: None,
            )
            self.assertIn("Related dependency facts (for inferring implicit relationships and ordering only):", client.prompts[0])
            self.assertIn('"hr": ["10:01 sent an email to manager"]', client.prompts[0])
            self.assertEqual(len(client.prompts), 1)

    def test_generate_role_tasks_ignores_file_based_dependency_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text(
                json.dumps(
                    {
                        "hr": [
                            self._saved_task(
                                r"use smb-access skill to copy handoff.txt into \\fileserver\company_data\management",
                                time="10:01",
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[self._valid_response("manager", "review backlog", time="10:16")]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "manager"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=client,
                emit_status=lambda message: None,
            )
            self.assertNotIn("Related dependency facts (for inferring implicit relationships and ordering only):", client.prompts[0])
            self.assertTrue(result.success)
            self.assertEqual(len(client.prompts), 1)
            self.assertNotIn("Use today's tasks for other roles as a reference:", client.prompts[0])

    def test_generate_role_tasks_falls_back_when_dependency_provider_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text(
                json.dumps({"hr": [self._saved_task("approve onboarding", time="09:01")]}, ensure_ascii=False),
                encoding="utf-8",
            )
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[self._valid_response("manager", "review backlog", time="09:16")]
            )

            with mock.patch.object(role_task_generation, "_load_dependency_provider", return_value=(None, None)):
                result = role_task_generation.generate_role_tasks(
                    source="generate_role_task",
                    final_file=final_file,
                    logs_dir=logs_dir,
                    domain_resource_path=domain_resource_path,
                    constraints_resource_path=CONSTRAINTS_PATH,
                    roles=("hr", "manager"),
                    min_tasks_per_role=1,
                    max_tasks_per_role=3,
                    min_non_five_ratio=0.8,
                    max_attempts=1,
                    agent_client=client,
                    emit_status=lambda message: None,
                )
            self.assertNotIn("Related dependency facts (for inferring implicit relationships and ordering only):", client.prompts[0])
            self.assertTrue(result.success)
            self.assertEqual(len(client.prompts), 1)
            self.assertNotIn("Use today's tasks for other roles as a reference:", client.prompts[0])

    def test_generate_role_tasks_retries_illegal_cross_role_time_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text(
                json.dumps(
                    {
                        "hr": [
                            self._saved_task(
                                "Use the exchange-use skill to send email, {recipient: manager@ndrtest.local, subject: onboarding process}",
                                time="10:01",
                            )
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template\nrole tasks", encoding="utf-8")
            client = FakeAgentClient(
                responses=[
                    self._valid_response(
                        "manager",
                        "Use the exchange-use skill to view email from hr@ndrtest.local",
                        time="09:01",
                    ),
                    self._valid_response(
                        "manager",
                        "Use the exchange-use skill to view email from hr@ndrtest.local",
                        time="10:16",
                    ),
                ]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr", "manager"),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=2,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.stats["quality_fail"], 1)
            self.assertEqual(len(client.prompts), 2)
            saved = json.loads(final_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["manager"][0]["time"], "10:16")

    def test_generate_role_tasks_writes_one_interactive_log_per_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")

            def on_request(_index: int, _prompt: str) -> None:
                response_dir = logs_dir / f"agent_responses_{date.today().isoformat()}"
                self.assertFalse(response_dir.exists() and any(response_dir.glob("*")))

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(response=self._valid_response(), on_request=on_request),
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertFalse(any(logs_dir.glob("agent_output_*.log")))
            response_logs = list((logs_dir / f"agent_responses_{date.today().isoformat()}").glob("hr_*_interactive.log"))
            self.assertEqual(len(response_logs), 1)
            self.assertIn("request_state: finished", response_logs[0].read_text(encoding="utf-8"))

    def test_generate_role_tasks_adds_retry_feedback_after_quality_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")
            bad_text = json.dumps(
                {"hr": [{"time": "09:00", "is_load": False, "task": "approve onboarding"}]},
                ensure_ascii=False,
            )
            bad_response = AgentResponse(
                model="deepseek-chat",
                response_text=bad_text,
                status_code=200,
                elapsed_seconds=1.0,
                raw_response_text=bad_text,
                finish_reason="stop",
            )
            client = FakeAgentClient(
                responses=[bad_response, self._valid_response("hr", "approve onboarding", time="09:01")]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=2,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.stats["quality_fail"], 1)
            self.assertEqual(len(client.prompts), 2)
            self.assertIn("The previous output failed validation", client.prompts[1])
            self.assertIn("At least 80% of task minute values must not be divisible by 5", client.prompts[1])

    def test_generate_role_tasks_adds_strict_order_retry_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")
            bad_text = json.dumps(
                {
                    "hr": [
                        {"time": "09:01", "is_load": False, "task": "first"},
                        {"time": "09:01", "is_load": True, "task": "second"},
                    ]
                },
                ensure_ascii=False,
            )
            bad_response = AgentResponse(
                model="deepseek-chat",
                response_text=bad_text,
                status_code=200,
                elapsed_seconds=1.0,
                raw_response_text=bad_text,
                finish_reason="stop",
            )
            client = FakeAgentClient(
                responses=[bad_response, self._valid_response("hr", "approve onboarding", time="09:01")]
            )

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=2,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.stats["quality_fail"], 1)
            self.assertEqual(len(client.prompts), 2)
            self.assertIn("The previous output failed validation", client.prompts[1])
            self.assertIn("task times must be strictly increasing in JSON array order", client.prompts[1])
            self.assertIn("times cannot repeat, move backward, or be equal", client.prompts[1])

    def test_generate_role_tasks_time_remediation_fixes_out_of_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")
            bad_text = json.dumps(
                {"hr": [{"time": "12:05", "is_load": False, "task": "do work"}]},
                ensure_ascii=False,
            )
            bad_response = AgentResponse(
                model="deepseek-chat",
                response_text=bad_text,
                status_code=200,
                elapsed_seconds=1.0,
                raw_response_text=bad_text,
                finish_reason="stop",
            )
            fix_text = json.dumps(
                {"hr": [{"time": "14:01", "is_load": False, "task": "do work"}]},
                ensure_ascii=False,
            )
            fix_response = AgentResponse(
                model="deepseek-chat",
                response_text=fix_text,
                status_code=200,
                elapsed_seconds=1.0,
                raw_response_text=fix_text,
                finish_reason="stop",
            )
            client = FakeAgentClient(responses=[bad_response, fix_response])

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=1,
                min_non_five_ratio=0.8,
                max_attempts=2,
                agent_client=client,
                emit_status=lambda message: None,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.stats.get("quality_fail", 0), 0)
            self.assertEqual(len(client.prompts), 2)
            self.assertIn("task schedule correction assistant", client.prompts[1])
            self.assertIn("Invalid indices", client.prompts[1])
            saved = json.loads(final_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["hr"][0]["time"], "14:01")

    def test_generate_role_tasks_classifies_parse_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")

            bad_response = AgentResponse(
                model="deepseek-chat",
                response_text="not json output",
                status_code=200,
                elapsed_seconds=0.8,
                raw_response_text="{}",
            )
            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(response=bad_response),
                emit_status=lambda message: None,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.stats["parse_fail"], 1)
            self.assertEqual(result.failure_reason, "Model response for role 'hr' did not contain a valid JSON object")
            self.assertFalse(final_file.exists())

    def test_generate_role_tasks_classifies_truncated_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")

            truncated_response = AgentResponse(
                model="deepseek-chat",
                response_text=(
                    "```json\n"
                    "{\n"
                    '    "hr": [\n'
                    '        {"time": "09:07", "is_load": false, "task": "View email"}\n'
                ),
                status_code=200,
                elapsed_seconds=0.8,
                raw_response_text="{}",
                finish_reason="length",
            )
            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(response=truncated_response),
                emit_status=lambda message: None,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.stats["parse_fail"], 1)
            self.assertEqual(
                result.failure_reason,
                "Model response for role 'hr' was truncated by provider (finish_reason=length)",
            )
            self.assertFalse(final_file.exists())
            self.assertFalse(final_file.with_name("tasks_04-21.candidate.json").exists())

    def test_generate_role_tasks_classifies_api_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs_dir = root / "logs"
            final_file = root / "role_task" / "tasks_04-21.json"
            domain_resource_path = root / "domain_resource.md"
            domain_resource_path.write_text("# template", encoding="utf-8")

            result = role_task_generation.generate_role_tasks(
                source="generate_role_task",
                final_file=final_file,
                logs_dir=logs_dir,
                domain_resource_path=domain_resource_path,
                constraints_resource_path=CONSTRAINTS_PATH,
                roles=("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(
                    side_effect=AgentTimeoutError(
                        "timeout",
                        response_text="gateway timeout",
                        elapsed_seconds=2.5,
                    )
                ),
                emit_status=lambda message: None,
            )

            self.assertFalse(result.success)
            self.assertEqual(result.stats["api_timeout"], 1)
            self.assertEqual(result.failure_reason, "Role 'hr' request timed out: timeout")


class RoleTaskFileServiceTests(unittest.TestCase):
    def test_generate_failure_returns_false_without_process_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            data_dir.mkdir()
            logs_dir = root / "logs"
            domain = root / "domain.md"
            domain.write_text("# x", encoding="utf-8")
            constraints = root / "constraints.md"
            constraints.write_text(CONSTRAINTS_TEMPLATE, encoding="utf-8")
            role_file = data_dir / "tasks_01-01.json"
            service = RoleTaskFileService(
                data_dir,
                ("hr",),
                min_tasks_per_role=1,
                max_tasks_per_role=3,
                min_non_five_ratio=0.8,
                max_attempts=1,
                agent_client=FakeAgentClient(
                    response=AgentResponse(
                        model="m",
                        response_text="{}",
                        status_code=200,
                        elapsed_seconds=1.0,
                        raw_response_text="{}",
                    )
                ),
                domain_resource_file=domain,
                constraints_resource_file=constraints,
                logs_dir=logs_dir,
            )
            failed = RoleTaskGenerationResult(
                False, None, "quality", {"quality_fail": 1}
            )
            with mock.patch(
                "commander.role_file_service.generate_role_tasks", return_value=failed
            ) as mock_gen:
                with mock.patch("os._exit", side_effect=AssertionError("os._exit must not be called")):
                    ok = service.generate_role_tasks(role_file)
            self.assertFalse(ok)
            mock_gen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
