#!/usr/bin/env python3
"""Regression tests for commander refactor modules."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import commander.deepseek_client as deepseek_client
import commander.role_task_generation as role_task_generation
from commander.agent_request_abc import AgentRequestABC, AgentRequestError, AgentResponse, AgentTimeoutError
from commander.deepseek_client import DeepSeekAgentClient, DeepSeekConfig
from commander.domain import STATUS_PLANNED, STATUS_WAITING
from commander.logging_setup import append_agent_output_log, write_agent_response_log
from commander.policies import EarliestPendingSelectionPolicy, task_needs_dispatch

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


class LoggingSetupTests(unittest.TestCase):
    def test_append_agent_output_log_writes_api_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = append_agent_output_log(
                Path(tmp),
                source="generate_role_task",
                attempt=2,
                prompt="中文prompt",
                note="parse_fail",
                model="deepseek-chat",
                response_text=b"\xe4\xb8\xad\xe6\x96\x87",
                error_text="plain error",
                status_code=200,
                request_timeout_seconds=60,
                failure_stage="response_parse",
                elapsed_seconds=12.5,
                expected_output_file="commander/role_task/tasks_04-21.candidate.json",
            )

            self.assertTrue(log_file.exists())
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["source"], "generate_role_task")
            self.assertEqual(payload["attempt"], 2)
            self.assertEqual(payload["model"], "deepseek-chat")
            self.assertEqual(payload["prompt_length"], len("中文prompt"))
            self.assertEqual(payload["response_text"], "中文")
            self.assertEqual(payload["response_preview"], "中文")
            self.assertEqual(payload["error_text"], "plain error")
            self.assertEqual(payload["note"], "parse_fail")
            self.assertEqual(payload["status_code"], 200)
            self.assertEqual(payload["request_timeout_seconds"], 60)
            self.assertEqual(payload["failure_stage"], "response_parse")
            self.assertEqual(payload["expected_output_file"], "commander/role_task/tasks_04-21.candidate.json")

    def test_write_agent_response_log_writes_separate_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = write_agent_response_log(
                Path(tmp),
                source="generate_role_task",
                attempt=3,
                note="api_response",
                provider="deepseek",
                model="deepseek-chat",
                status_code=200,
                role="hr",
                finish_reason="length",
                prompt_text="发送给 deepseek 的提示词",
                response_text="raw response body",
                raw_response_text='{"choices":[{"message":{"content":"raw response body"}}]}',
                error_text="",
            )

            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("source: generate_role_task", content)
            self.assertIn("attempt: 3", content)
            self.assertIn("note: api_response", content)
            self.assertIn("role: hr", content)
            self.assertIn("finish_reason: length", content)
            self.assertIn("--- PROMPT_TEXT ---", content)
            self.assertIn("发送给 deepseek 的提示词", content)
            self.assertIn("--- RAW_RESPONSE ---", content)
            self.assertIn("raw response body", content)


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
            '        {"time": "09:07", "is_load": false, "task": "查看邮件"}\n'
            "    ],\n"
            '    "programmer": [\n'
            '        {"time": "09:24", "is_load": false, "task": "查看IT-Dev目录"},\n'
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
                json.dumps({"hr": [{"time": "09:01", "is_load": False, "task": "处理入职材料"}]}, ensure_ascii=False),
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
            self.assertEqual(data["hr"][0]["task"], "处理入职材料")

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


class PromptTests(unittest.TestCase):
    def test_build_role_task_prompt_requires_json_only_and_chinese_templates(self) -> None:
        domain_context = "# 任务内容模板\n使用 smb-access 模板访问共享目录。"
        prompt = build_role_task_prompt(
            domain_context,
            min_tasks_per_role=2,
            max_tasks_per_role=6,
            roles=("hr", "accountancy"),
        )
        self.assertIn("\u53ea\u8fd4\u56de\u4e00\u4e2a JSON \u5bf9\u8c61", prompt)
        self.assertIn("\u5fc5\u987b\u9075\u5faa\u9886\u57df\u4e0a\u4e0b\u6587\u4e2d\u7684\u4efb\u52a1\u5185\u5bb9\u6a21\u677f\u548c\u7ea6\u675f", prompt)
        self.assertIn("\u4e0d\u5f97\u5c11\u4e8e 2 \u6761\uff0c\u4e14\u4e0d\u5f97\u591a\u4e8e 6 \u6761", prompt)
        self.assertIn('"hr": [tasks]', prompt)
        self.assertIn('"accountancy": [tasks]', prompt)
        self.assertNotIn("TASK_FILE_READY", prompt)
        self.assertNotIn("generate_tasks.py", prompt)
        self.assertNotIn("tasks_final.json", prompt)
        self.assertNotIn("All task descriptions must be in English", prompt)


class FakeAgentClient(AgentRequestABC):
    def __init__(
        self,
        *,
        response: AgentResponse | None = None,
        responses: list[AgentResponse] | None = None,
        side_effect: Exception | None = None,
    ) -> None:
        self._response = response
        self._responses = list(responses) if responses is not None else None
        self._side_effect = side_effect
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
        finish_reason: str | None = None,
    ) -> AgentResponse:
        response_text = json.dumps(
            {role: [{"time": "09:01", "is_load": False, "task": task}]},
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
            log_file = next(logs_dir.glob("agent_output_*.log"))
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["note"], "success")
            self.assertEqual(payload["failure_stage"], "promoted_final_file")
            self.assertEqual(payload["provider"], "fake")
            response_log = next((logs_dir / f"agent_responses_{date.today().isoformat()}").glob("*.log"))
            response_log_text = response_log.read_text(encoding="utf-8")
            self.assertIn("provider: fake", response_log_text)
            self.assertIn("role: hr", response_log_text)
            self.assertIn("--- PROMPT_TEXT ---", response_log_text)
            self.assertIn("--- RESPONSE_TEXT ---", response_log_text)
            self.assertIn('"hr": [tasks]', response_log_text)
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
            log_file = next(logs_dir.glob("agent_output_*.log"))
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["note"], "parse_fail")
            self.assertEqual(payload["failure_stage"], "response_parse")
            self.assertEqual(payload["provider"], "fake")
            self.assertEqual(payload["role"], "hr")

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
                    '        {"time": "09:07", "is_load": false, "task": "查看邮件"}\n'
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
            log_file = next(logs_dir.glob("agent_output_*.log"))
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["note"], "parse_fail: truncated_response")
            self.assertEqual(payload["failure_stage"], "response_parse")
            self.assertEqual(payload["finish_reason"], "length")
            self.assertEqual(payload["role"], "hr")

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
            log_file = next(logs_dir.glob("agent_output_*.log"))
            payload = json.loads(log_file.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["note"], "api_timeout")
            self.assertEqual(payload["failure_stage"], "api_request")
            self.assertEqual(payload["error_text"], "gateway timeout")
            self.assertEqual(payload["provider"], "fake")
            self.assertEqual(payload["role"], "hr")


if __name__ == "__main__":
    unittest.main()
