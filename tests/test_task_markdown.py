#!/usr/bin/env python3
"""Tests for shared OpenCode task Markdown helpers."""

from __future__ import annotations

import json
import unittest

from common.task_markdown import (
    FRONTMATTER_OMIT,
    format_opencode_session,
    merge_process_output,
    parse_task_markdown,
    report_from_task_record,
    render_task_markdown,
    strip_process_output,
)

_SOLDIER_META = (
    "task_id",
    "task_ref",
    "date",
    "status",
    "outcome",
    "result_status",
    "received_at",
    "started_at",
    "finished_at",
    "reported_at",
    "argv",
)


class StripProcessOutputTests(unittest.TestCase):
    def test_strips_ansi_and_normalizes_cr(self) -> None:
        raw = "\x1b[0m\r\n> build · model\r\n\x1b[0m\r\n\x1b[0m→ \x1b[0mSkill \"ftp-use\"\r"
        text = strip_process_output(raw)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)
        self.assertIn("> build · model", text)
        self.assertIn('→ Skill "ftp-use"', text)

    def test_collapses_blank_lines_left_by_color_resets(self) -> None:
        raw = "\x1b[0m\n\x1b[0m\n\x1b[0m\nkeep\n\x1b[0m\n"
        text = strip_process_output(raw)
        self.assertEqual(text, "keep\n")


class FormatOpencodeSessionTests(unittest.TestCase):
    def test_jsonl_keeps_thinking_tools_and_reply_in_order(self) -> None:
        stdout = "\n".join(
            [
                json.dumps(
                    {
                        "type": "reasoning",
                        "part": {"text": "I should load ftp-use and upload the file."},
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "part": {
                            "tool": "skill",
                            "state": {"status": "completed", "input": {"name": "ftp-use"}},
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "tool_use",
                        "part": {
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "python upload.py"},
                                "output": "STOR 226 Transfer complete.",
                            },
                        },
                    }
                ),
                json.dumps({"type": "text", "part": {"text": "Upload complete."}}),
                json.dumps({"type": "step_finish", "part": {"reason": "stop"}}),
            ]
        )
        text = format_opencode_session(stdout, "")
        self.assertIn("Thinking:\nI should load ftp-use and upload the file.", text)
        self.assertIn('→ Skill "ftp-use"', text)
        self.assertIn("$ python upload.py", text)
        self.assertIn("STOR 226 Transfer complete.", text)
        self.assertIn("Upload complete.", text)
        self.assertLess(text.find("Thinking:"), text.find("Skill"))
        self.assertLess(text.find("Skill"), text.find("python upload.py"))
        self.assertLess(text.find("python upload.py"), text.find("Upload complete."))
        self.assertNotIn("step_finish", text)

    def test_plain_streams_still_merge(self) -> None:
        text = format_opencode_session("Upload complete.\n", "\x1b[0m→ Skill\n")
        self.assertIn("→ Skill", text)
        self.assertIn("Upload complete.", text)
        self.assertLess(text.find("Skill"), text.find("Upload complete."))


class MergeProcessOutputTests(unittest.TestCase):
    def test_stderr_then_stdout_with_blank_line(self) -> None:
        merged = merge_process_output("session\n", "final\n")
        self.assertEqual(merged, "session\n\nfinal\n")

    def test_skips_empty_side(self) -> None:
        self.assertEqual(merge_process_output("", "only-out\n"), "only-out\n")
        self.assertEqual(merge_process_output("only-err\n", ""), "only-err\n")


class RenderAndParseTests(unittest.TestCase):
    def test_omits_frontmatter_keys_and_merges_output(self) -> None:
        rendered = render_task_markdown(
            {
                "task_id": "c01b883dfefd4c85",
                "task_ref": "2026-04-29_accountancy_c01b883dfefd4c85",
                "date": "2026-04-29",
                "status": "completed",
                "outcome": "Success",
                "result_status": "successed",
                "command": "opencode run --auto --thinking --format json 'Check email'",
                "argv": ["opencode", "run", "--auto", "--thinking", "--format", "json", "Check email"],
                "updated_at": "skip-me",
                "completed_at": "skip-me",
                "execution_deadline": "skip-me",
                "exit_code": 0,
                "message": "",
                "report": {"stdout": "hidden"},
                "stdout": "Upload complete.\n",
                "stderr": "\x1b[0m\r\n→ Skill\r\n",
            },
            meta_keys=_SOLDIER_META,
        )
        yaml_block = rendered.split("---", 2)[1]
        for key in FRONTMATTER_OMIT:
            self.assertNotIn(f"{key}:", yaml_block)
        self.assertIn("## Command", rendered)
        self.assertIn("## Output", rendered)
        self.assertNotIn("## stdout", rendered)
        self.assertNotIn("## stderr", rendered)
        output = rendered.split("## Output", 1)[1]
        self.assertIn("→ Skill", output)
        self.assertIn("Upload complete.", output)
        self.assertLess(output.find("→ Skill"), output.find("Upload complete."))
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\\n", output)

        parsed = parse_task_markdown(rendered)
        self.assertEqual(parsed["task_id"], "c01b883dfefd4c85")
        self.assertIn("opencode", parsed["command"])
        self.assertNotIn("exit_code", parsed)
        self.assertNotIn("report", parsed)
        self.assertIn("→ Skill", parsed["output"])
        self.assertIn("Upload complete.", parsed["output"])

        report = report_from_task_record(parsed)
        assert report is not None
        self.assertEqual(report["status"], "successed")
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["stderr"], "")
        self.assertIn("Upload complete.", report["stdout"])

    def test_parses_legacy_stdout_stderr_headings(self) -> None:
        text = (
            "---\n"
            'task_id: "abc"\n'
            'task_ref: "ref"\n'
            'status: "completed"\n'
            'result_status: "failed"\n'
            "---\n\n"
            "## stdout\n\n"
            "```text\nfinal\n```\n\n"
            "## stderr\n\n"
            "```text\n\x1b[0msession\n```\n"
        )
        parsed = parse_task_markdown(text)
        self.assertEqual(parsed["output"].strip(), "session\n\nfinal")
        report = report_from_task_record(parsed)
        assert report is not None
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["exit_code"], -1)

    def test_keeps_legacy_json_report(self) -> None:
        report = {"task_ref": "ref", "status": "successed", "exit_code": 0, "stdout": "ok", "stderr": ""}
        parsed = parse_task_markdown(
            '{"task_id": "abc", "status": "completed", "report": {"task_ref": "ref", '
            '"status": "successed", "exit_code": 0, "stdout": "ok", "stderr": ""}}'
        )
        self.assertEqual(report_from_task_record(parsed), report)


if __name__ == "__main__":
    unittest.main()
