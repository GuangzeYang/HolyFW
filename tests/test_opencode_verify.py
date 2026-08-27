#!/usr/bin/env python3
"""Tests for build --test OpenCode verification (mocked subprocess)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

from common.opencode_verify import (
    ATTACKER_SKILL_PROMPT,
    COMMANDER_SMOKE_PROMPT,
    bundled_mcp_names,
    parse_prompt_template_sections,
    pick_representative_prompt,
    select_skill_prompt,
    verify_commander_build,
    verify_role_build,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


HR_PROMPT_FIXTURE = """# HR templates

## exchange-use

```text
Use the exchange-use skill, open the Exchange mailbox, <action>, {<field>: <value>, ...}
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, send email, {recipient: manager, subject: Weekly staffing update, min_words: 400}"
```

```text
opencode run "Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}"
```

## odoo-use

```text
opencode run "Use the odoo-use skill, log in to the Odoo system, use the Employees module, add employee, {name: DemoNew1, job position: Sales, work email: demonew1@ndrtest.local}"
```

## playwright-browser

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: REPLACE_QUERY} 2. follow, {nth: 2}"
```

```text
opencode run "Use the playwright-browser skill, open the browser, then execute: 1. search, {query: Windows Active Directory backup} 2. follow, {nth: 1} 3. extract."
```

## pdf
"""


def _completed(code: int = 0, stdout: str = "ok", stderr: str = "") -> mock.Mock:
    result = mock.Mock()
    result.returncode = code
    result.stdout = stdout
    result.stderr = stderr
    return result


def _write_skill(pack: Path, name: str) -> None:
    skill = pack / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")


class PromptPickerTests(unittest.TestCase):
    def test_prefers_view_over_send(self) -> None:
        sections = parse_prompt_template_sections(HR_PROMPT_FIXTURE)
        picked = pick_representative_prompt(sections["exchange-use"])
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertIn("view email", picked)
        self.assertNotIn("send email", picked)

    def test_ellipsis_prompt_is_placeholder(self) -> None:
        picked = pick_representative_prompt(
            [
                "...",
                "Use the exchange-use skill, open the Exchange mailbox, view email, {target: first email}",
            ]
        )
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertIn("view email", picked)

    def test_skips_placeholder_playwright_line(self) -> None:
        sections = parse_prompt_template_sections(HR_PROMPT_FIXTURE)
        picked = pick_representative_prompt(sections["playwright-browser"])
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertNotIn("REPLACE_", picked)
        self.assertIn("extract", picked)

    def test_odoo_falls_back_to_first_complete_example(self) -> None:
        sections = parse_prompt_template_sections(HR_PROMPT_FIXTURE)
        picked = pick_representative_prompt(sections["odoo-use"])
        self.assertIsNotNone(picked)
        assert picked is not None
        self.assertIn("add employee", picked)

    def test_pdf_without_examples_is_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp)
            (pack / "PROMPT_TEMPLATES.md").write_text(HR_PROMPT_FIXTURE, encoding="utf-8")
            prompt, reason = select_skill_prompt("pdf", pack)
            self.assertIsNone(prompt)
            self.assertIn("no opencode run example", reason or "")

    def test_bundled_hr_templates_pick_read_oriented_prompts(self) -> None:
        from holyfw_assets import skills_root

        pack = skills_root() / "hr-skills"
        exchange, exchange_skip = select_skill_prompt("exchange-use", pack)
        smb, smb_skip = select_skill_prompt("smb-access", pack)
        ftp, ftp_skip = select_skill_prompt("ftp-use", pack)
        pdf, pdf_skip = select_skill_prompt("pdf", pack)
        self.assertIsNone(exchange_skip)
        self.assertIsNone(smb_skip)
        self.assertIsNone(ftp_skip)
        self.assertIn("view email", exchange or "")
        self.assertIn("view to view a folder", smb or "")
        self.assertIn("list to list a folder", ftp or "")
        self.assertIsNone(pdf)
        self.assertIn("no opencode run example", pdf_skip or "")

    def test_attacker_uses_discovery_orientation(self) -> None:
        pack = REPO_ROOT / "attacker" / "skills"
        prompt, reason = select_skill_prompt("ad-attack", pack)
        self.assertIsNone(reason)
        self.assertEqual(prompt, ATTACKER_SKILL_PROMPT)


class BundledMcpTests(unittest.TestCase):
    def test_bundled_mcp_names_include_lab_servers(self) -> None:
        names = bundled_mcp_names()
        self.assertEqual(names, ["github", "playwright", "excel"])


class VerifyCommanderTests(unittest.TestCase):
    def test_runs_version_and_provider_smoke_not_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            oc = Path(tmp)
            (oc / "opencode.json").write_text("{}", encoding="utf-8")
            calls: list[list[str]] = []

            def run(argv, **kwargs):
                calls.append(list(argv))
                return _completed(0)

            with (
                mock.patch("common.opencode_verify.resolve_opencode_executable", return_value="opencode"),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc / "opencode.json"),
            ):
                code = verify_commander_build(run=run, timeout=5)

        self.assertEqual(code, 0)
        self.assertEqual(calls[0][-1], "--version")
        self.assertEqual(calls[1][-2:], ["--auto", COMMANDER_SMOKE_PROMPT])
        self.assertTrue(all("skill:" not in " ".join(item) for item in calls))

    def test_skips_smoke_when_opencode_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            oc = Path(tmp)
            (oc / "opencode.json").write_text("{}", encoding="utf-8")
            run = mock.Mock(return_value=_completed(0))
            with (
                mock.patch(
                    "common.opencode_verify.resolve_opencode_executable",
                    side_effect=FileNotFoundError("opencode executable not found on PATH"),
                ),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc / "opencode.json"),
            ):
                code = verify_commander_build(run=run, timeout=5)
        self.assertEqual(code, 1)
        run.assert_not_called()


class VerifyRoleTests(unittest.TestCase):
    def test_skips_pdf_and_runs_skill_and_mcp_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "hr-skills"
            _write_skill(pack, "exchange-use")
            _write_skill(pack, "pdf")
            (pack / "PROMPT_TEMPLATES.md").write_text(HR_PROMPT_FIXTURE, encoding="utf-8")
            skills_dest = root / "skills"
            _write_skill(skills_dest, "exchange-use")
            _write_skill(skills_dest, "pdf")
            bundled = root / "bundled.json"
            bundled.write_text(
                json.dumps({"mcp": {"playwright": {"type": "local", "enabled": True}}}),
                encoding="utf-8",
            )
            oc_json = root / "opencode.json"
            oc_json.write_text(
                json.dumps({"mcp": {"playwright": {"type": "local", "enabled": True}}}),
                encoding="utf-8",
            )
            prompts: list[str] = []

            def run(argv, **kwargs):
                if argv[-1] == "--version":
                    return _completed(0)
                prompts.append(argv[-1])
                return _completed(0)

            with (
                mock.patch("common.opencode_verify.resolve_opencode_executable", return_value="opencode"),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc_json),
                mock.patch("common.opencode_verify.opencode_skill_dir", return_value=skills_dest),
                mock.patch("common.opencode_verify.role_skill_source", return_value=pack),
                mock.patch("common.opencode_verify.bundled_mcp_names", return_value=["playwright"]),
            ):
                code = verify_role_build("hr", run=run, timeout=5)

        self.assertEqual(code, 0)
        self.assertTrue(any("view email" in item for item in prompts))
        self.assertTrue(any("playwright MCP" in item for item in prompts))
        self.assertFalse(any("pdf" in item.lower() and "skill" in item.lower() for item in prompts))

    def test_prints_target_before_skill_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "hr-skills"
            _write_skill(pack, "exchange-use")
            (pack / "PROMPT_TEMPLATES.md").write_text(HR_PROMPT_FIXTURE, encoding="utf-8")
            skills_dest = root / "skills"
            _write_skill(skills_dest, "exchange-use")
            oc_json = root / "opencode.json"
            oc_json.write_text(json.dumps({"mcp": {}}), encoding="utf-8")
            stdout = StringIO()
            snapshots: list[str] = []

            def run(argv, **kwargs):
                if argv[-1] != "--version":
                    snapshots.append(stdout.getvalue())
                return _completed(0)

            with (
                mock.patch("sys.stdout", stdout),
                mock.patch("common.opencode_verify.resolve_opencode_executable", return_value="opencode"),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc_json),
                mock.patch("common.opencode_verify.opencode_skill_dir", return_value=skills_dest),
                mock.patch("common.opencode_verify.role_skill_source", return_value=pack),
                mock.patch("common.opencode_verify.bundled_mcp_names", return_value=[]),
            ):
                code = verify_role_build("hr", run=run, timeout=5)

        self.assertEqual(code, 0)
        self.assertTrue(snapshots)
        at_skill_run = snapshots[0]
        self.assertRegex(at_skill_run, r"\[\d+/\d+\] Target: skill:exchange-use")
        idx = at_skill_run.rfind("Target: skill:exchange-use")
        current = at_skill_run[idx:]
        self.assertIn("Command:", current)
        self.assertIn("Running (timeout 5s)...", current)
        self.assertNotIn("Result:", current)

    def test_nonzero_opencode_exit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "hr-skills"
            _write_skill(pack, "exchange-use")
            (pack / "PROMPT_TEMPLATES.md").write_text(HR_PROMPT_FIXTURE, encoding="utf-8")
            skills_dest = root / "skills"
            _write_skill(skills_dest, "exchange-use")
            oc_json = root / "opencode.json"
            oc_json.write_text(json.dumps({"mcp": {}}), encoding="utf-8")

            def run(argv, **kwargs):
                if argv[-1] == "--version":
                    return _completed(0)
                return _completed(2, stdout="", stderr="boom")

            with (
                mock.patch("common.opencode_verify.resolve_opencode_executable", return_value="opencode"),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc_json),
                mock.patch("common.opencode_verify.opencode_skill_dir", return_value=skills_dest),
                mock.patch("common.opencode_verify.role_skill_source", return_value=pack),
                mock.patch("common.opencode_verify.bundled_mcp_names", return_value=[]),
            ):
                code = verify_role_build("hr", run=run, timeout=5)
        self.assertEqual(code, 1)

    def test_timeout_is_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            oc = Path(tmp)
            (oc / "opencode.json").write_text("{}", encoding="utf-8")

            def run(argv, **kwargs):
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout") or 1)

            with (
                mock.patch("common.opencode_verify.resolve_opencode_executable", return_value="opencode"),
                mock.patch("common.opencode_verify.opencode_json_path", return_value=oc / "opencode.json"),
            ):
                code = verify_commander_build(run=run, timeout=1)
        self.assertEqual(code, 1)


class BuildFlagRoutingTests(unittest.TestCase):
    def test_soldier_build_test_flag_reaches_run_build(self) -> None:
        from soldier.soldier import main

        with mock.patch("soldier.host_build.run_build", return_value=0) as build:
            self.assertEqual(main(["build", "hr", "--test"]), 0)
        build.assert_called_once_with("hr", run_test=True)

    def test_soldier_build_without_test_keeps_positional_call(self) -> None:
        from soldier.soldier import main

        with mock.patch("soldier.host_build.run_build", return_value=0) as build:
            self.assertEqual(main(["build", "hr"]), 0)
        build.assert_called_once_with("hr")

    def test_attacker_build_test_flag_reaches_run_build(self) -> None:
        import attacker.cli as attacker_cli

        with mock.patch("attacker.host_build.run_build", return_value=0) as build:
            self.assertEqual(attacker_cli.main(["build", "--test"]), 0)
        build.assert_called_once_with(run_test=True)

    def test_commander_build_test_flag_reaches_run_build(self) -> None:
        import commander.cli as commander_cli

        with mock.patch("commander.host_build.run_build", return_value=0) as build:
            with self.assertRaises(SystemExit) as ctx:
                commander_cli.main(["build", "--test"])
        self.assertEqual(ctx.exception.code, 0)
        build.assert_called_once_with(run_test=True)

    def test_soldier_run_build_test_skips_verify_when_install_fails(self) -> None:
        from soldier.host_build import run_build

        with (
            mock.patch("soldier.host_build.install_role", return_value=1) as install,
            mock.patch("common.opencode_verify.verify_role_build") as verify,
        ):
            self.assertEqual(run_build("hr", run_test=True), 1)
        install.assert_called_once()
        verify.assert_not_called()

    def test_soldier_run_build_test_calls_verify_after_install(self) -> None:
        from soldier.host_build import run_build

        with (
            mock.patch("soldier.host_build.install_role", return_value=0),
            mock.patch("common.opencode_verify.verify_role_build", return_value=0) as verify,
        ):
            self.assertEqual(run_build("hr", run_test=True), 0)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0], "hr")

    def test_commander_run_build_test_calls_verify_after_install(self) -> None:
        from commander.host_build import run_build

        with (
            mock.patch("commander.host_build.install_commander_opencode", return_value=0),
            mock.patch("common.opencode_verify.verify_commander_build", return_value=0) as verify,
        ):
            self.assertEqual(run_build(run_test=True), 0)
        verify.assert_called_once_with()

    def test_attacker_run_build_test_calls_verify_after_install(self) -> None:
        from attacker.host_build import run_build

        with (
            mock.patch("attacker.host_build.copy_skills", return_value=["ad-attack"]),
            mock.patch("attacker.host_build.write_host_opencode_configs"),
            mock.patch("attacker.host_build.install_agents_md"),
            mock.patch("attacker.host_build.clear_opencode_cache", return_value=False),
            mock.patch("attacker.host_build.opencode_legacy_skill_dir") as legacy,
            mock.patch("common.opencode_verify.verify_role_build", return_value=0) as verify,
        ):
            legacy.return_value.is_dir.return_value = False
            self.assertEqual(run_build(run_test=True), 0)
        verify.assert_called_once()
        self.assertEqual(verify.call_args.args[0], "attacker")
        self.assertEqual(verify.call_args.kwargs["pack_root"].name, "skills")
        self.assertEqual(verify.call_args.kwargs["bundled_opencode_path"].name, "opencode.json")


if __name__ == "__main__":
    unittest.main()
