"""Post-build OpenCode checks: load config, then run representative prompts."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from common.opencode_install import (
    ROLE_SKILL_PACKS,
    load_jsonc,
    opencode_json_path,
    opencode_skill_dir,
    role_skill_source,
    skill_directories,
)

VERIFY_RUN_TIMEOUT_SECONDS = 900
VERIFY_VERSION_TIMEOUT_SECONDS = 30
PREVIEW_CHARS = 240
TIMEOUT_EXIT_CODE = 124
MISSING_EXIT_CODE = 127
COMMANDER_SMOKE_PROMPT = "Reply with exactly the word pong and stop."
ATTACKER_SKILL_PROMPT = (
    "Use the ad-attack skill: execute discovery.orientation against domain."
)
OPENCODE_PERMISSION_ALLOW: dict[str, object] = {
    "*": "allow",
    "doom_loop": "allow",
    "external_directory": {"*": "allow"},
}
MCP_PROMPTS: dict[str, str] = {
    "playwright": (
        "Use the playwright MCP to open the browser, then execute: "
        "1. search, {query: Windows Active Directory backup} 2. follow, {nth: 1} "
        "3. scroll, {direction: down} 4. extract. Verify: article text is non-empty. "
        "Close the browser after verification. Do not create or modify files."
    ),
    "excel": (
        "Use the excel MCP only: list the available excel MCP tools and confirm "
        "the server responds. Do not create or modify any workbook."
    ),
    "github": (
        "Use the github MCP only: list the available github MCP tools and confirm "
        "the server responds. Do not create issues or change any repository."
    ),
}
_OPENCODE_RUN_RE = re.compile(r'opencode run\s+"([^"]*)"')
_HEADING_RE = re.compile(r"^##\s+([A-Za-z0-9_-]+)\s*$")
_PLACEHOLDER_RE = re.compile(r"<[^>]+>|REPLACE_")
_READ_RE = re.compile(r"\b(view|list|search|extract)\b", re.I)


@dataclass(frozen=True)
class CaseResult:
    target: str
    command: str
    status: str
    exit_code: int | None = None
    detail: str = ""


def preview_text(text: str, limit: int = PREVIEW_CHARS) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def format_run_command(prompt: str) -> str:
    return "opencode run --auto " + shlex.quote(prompt)


def opencode_run_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env["OPENCODE_PERMISSION"] = json.dumps(OPENCODE_PERMISSION_ALLOW, separators=(",", ":"))
    return env


def resolve_opencode_executable() -> str:
    found = shutil.which("opencode") or shutil.which("opencode.cmd")
    if not found:
        raise FileNotFoundError("opencode executable not found on PATH")
    return found


def parse_prompt_template_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = _HEADING_RE.match(line.strip())
        if heading is not None:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        for match in _OPENCODE_RUN_RE.finditer(line):
            sections[current].append(match.group(1).strip())
    return sections


def is_placeholder_prompt(prompt: str) -> bool:
    stripped = prompt.strip()
    if stripped in {"...", "…"}:
        return True
    return bool(_PLACEHOLDER_RE.search(prompt))


def pick_representative_prompt(prompts: Sequence[str]) -> str | None:
    complete = [item.strip() for item in prompts if item.strip() and not is_placeholder_prompt(item)]
    if not complete:
        return None
    scored = [(_READ_RE.search(item) is not None, index, item) for index, item in enumerate(complete)]
    scored.sort(key=lambda row: (not row[0], row[1]))
    return scored[0][2]


def bundled_mcp_names(bundled_path: Path | None = None) -> list[str]:
    from holyfw_assets import opencode_config_path

    path = bundled_path if bundled_path is not None else opencode_config_path()
    payload = load_jsonc(path.read_text(encoding="utf-8"))
    mcp = payload.get("mcp") if isinstance(payload, dict) else None
    if not isinstance(mcp, dict):
        return []
    return [str(name) for name in mcp]


def mcp_prompt_for(name: str) -> str | None:
    return MCP_PROMPTS.get(name)


def select_skill_prompt(skill_name: str, pack_root: Path) -> tuple[str | None, str | None]:
    """Return (prompt, skip_reason). One of the two is always set."""
    key = skill_name.strip().lower()
    templates = pack_root / "PROMPT_TEMPLATES.md"
    if templates.is_file():
        sections = parse_prompt_template_sections(templates.read_text(encoding="utf-8"))
        picked = pick_representative_prompt(sections.get(key) or [])
        if picked:
            return picked, None
        return None, f"no opencode run example in {templates.name} for {key}"
    if key == "ad-attack":
        return ATTACKER_SKILL_PROMPT, None
    return None, f"no prompt templates for {key}"


def _begin_case(index: int, total: int, target: str, command: str) -> None:
    print(f"[{index}/{total}] Target: {target}", flush=True)
    print(f"Command: {command}", flush=True)


def _finish_case(case: CaseResult) -> None:
    if case.status == "SKIP":
        result = f"SKIP  {case.detail}".rstrip()
    elif case.status == "PASS":
        result = f"PASS  exit={case.exit_code}"
    else:
        extra = f"  {case.detail}" if case.detail else ""
        result = f"FAIL  exit={case.exit_code}{extra}"
    print(f"Result: {result}", flush=True)
    print(flush=True)


def _print_summary(cases: Sequence[CaseResult]) -> None:
    passed = sum(1 for case in cases if case.status == "PASS")
    failed = sum(1 for case in cases if case.status == "FAIL")
    skipped = sum(1 for case in cases if case.status == "SKIP")
    print(
        f"Build test summary: {passed} passed, {failed} failed, {skipped} skipped",
        flush=True,
    )


class _Progress:
    def __init__(self, total: int) -> None:
        self.total = total
        self.index = 0
        self.cases: list[CaseResult] = []

    def begin(self, target: str, command: str, *, live_timeout: int | None = None) -> None:
        self.index += 1
        _begin_case(self.index, self.total, target, command)
        if live_timeout is not None:
            print(f"Running (timeout {live_timeout}s)...", flush=True)

    def finish(self, case: CaseResult) -> CaseResult:
        _finish_case(case)
        self.cases.append(case)
        return case

    def run(
        self,
        target: str,
        command: str,
        fn: Any,
        *,
        live_timeout: int | None = None,
    ) -> CaseResult:
        self.begin(target, command, live_timeout=live_timeout)
        return self.finish(fn())

    def skip(self, target: str, command: str, detail: str) -> CaseResult:
        self.begin(target, command)
        return self.finish(CaseResult(target, command, "SKIP", detail=detail))

    def has_fail(self) -> bool:
        return any(case.status == "FAIL" for case in self.cases)

    def done(self) -> int:
        _print_summary(self.cases)
        return 0 if all(case.status != "FAIL" for case in self.cases) else 1


def _run_captured(
    argv: Sequence[str],
    *,
    timeout: int,
    env: Mapping[str, str] | None = None,
    run: Any = subprocess.run,
) -> tuple[int, str]:
    try:
        completed = run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except FileNotFoundError as exc:
        return MISSING_EXIT_CODE, str(exc)
    except subprocess.TimeoutExpired:
        return TIMEOUT_EXIT_CODE, f"timed out after {timeout}s"
    stdout = getattr(completed, "stdout", "") or ""
    stderr = getattr(completed, "stderr", "") or ""
    combined = stdout if not stderr else f"{stdout}\n{stderr}".strip()
    return int(getattr(completed, "returncode", 1) or 0), combined


def run_opencode_prompt(
    prompt: str,
    *,
    timeout: int = VERIFY_RUN_TIMEOUT_SECONDS,
    run: Any = subprocess.run,
) -> tuple[int, str]:
    try:
        argv = [resolve_opencode_executable(), "run", "--auto", prompt]
    except FileNotFoundError as exc:
        return MISSING_EXIT_CODE, str(exc)
    return _run_captured(argv, timeout=timeout, env=opencode_run_env(), run=run)


def _fail_detail(output: str) -> str:
    preview = preview_text(output)
    return preview if preview else "no output"


def _load_opencode_binary(*, run: Any) -> CaseResult:
    command = "opencode --version"
    try:
        executable = resolve_opencode_executable()
    except FileNotFoundError as exc:
        return CaseResult("load:opencode", command, "FAIL", MISSING_EXIT_CODE, str(exc))
    code, output = _run_captured([executable, "--version"], timeout=VERIFY_VERSION_TIMEOUT_SECONDS, run=run)
    if code == 0:
        return CaseResult("load:opencode", command, "PASS", code)
    return CaseResult("load:opencode", command, "FAIL", code, _fail_detail(output))


def _load_opencode_config() -> CaseResult:
    command = str(opencode_json_path())
    path = opencode_json_path()
    if not path.is_file():
        return CaseResult("load:opencode.json", command, "FAIL", 1, "config file is missing")
    try:
        payload = load_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CaseResult("load:opencode.json", command, "FAIL", 1, str(exc))
    if not isinstance(payload, dict):
        return CaseResult("load:opencode.json", command, "FAIL", 1, "config is not a JSON object")
    return CaseResult("load:opencode.json", command, "PASS", 0)


def _mcp_is_enabled(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    if "enabled" not in entry:
        return True
    return bool(entry["enabled"])


def _skill_load_case(name: str) -> CaseResult:
    path = opencode_skill_dir() / name / "SKILL.md"
    command = str(path)
    if path.is_file():
        return CaseResult(f"load:skill:{name}", command, "PASS", 0)
    return CaseResult(f"load:skill:{name}", command, "FAIL", 1, "SKILL.md is missing")


def _mcp_config_state() -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = load_jsonc(opencode_json_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)
    mcp = payload.get("mcp") if isinstance(payload, dict) else None
    if not isinstance(mcp, dict):
        return {}, None
    return mcp, None


def _mcp_load_case(
    name: str,
    mapping: dict[str, Any] | None,
    error: str | None,
) -> CaseResult:
    command = str(opencode_json_path())
    if error is not None:
        return CaseResult(f"load:mcp:{name}", command, "FAIL", 1, error)
    entry = (mapping or {}).get(name)
    if entry is None:
        return CaseResult(f"load:mcp:{name}", command, "FAIL", 1, "missing from config")
    if not _mcp_is_enabled(entry):
        return CaseResult(f"load:mcp:{name}", command, "FAIL", 1, "enabled is false")
    return CaseResult(f"load:mcp:{name}", command, "PASS", 0)


def _live_prompt_case(
    target: str,
    prompt: str,
    *,
    run: Any,
    timeout: int,
) -> CaseResult:
    command = format_run_command(prompt)
    code, output = run_opencode_prompt(prompt, timeout=timeout, run=run)
    if code == 0:
        return CaseResult(target, command, "PASS", code)
    return CaseResult(target, command, "FAIL", code, _fail_detail(output))


def verify_commander_build(
    *,
    run: Any = subprocess.run,
    timeout: int = VERIFY_RUN_TIMEOUT_SECONDS,
) -> int:
    print("Running commander build --test (OpenCode load + provider smoke).", flush=True)
    progress = _Progress(3)
    progress.run("load:opencode", "opencode --version", lambda: _load_opencode_binary(run=run))
    progress.run(
        "load:opencode.json",
        str(opencode_json_path()),
        _load_opencode_config,
    )
    if progress.has_fail():
        return progress.done()
    progress.run(
        "provider:deepseek",
        format_run_command(COMMANDER_SMOKE_PROMPT),
        lambda: _live_prompt_case(
            "provider:deepseek", COMMANDER_SMOKE_PROMPT, run=run, timeout=timeout
        ),
        live_timeout=timeout,
    )
    return progress.done()


def verify_role_build(
    role: str,
    *,
    run: Any = subprocess.run,
    timeout: int = VERIFY_RUN_TIMEOUT_SECONDS,
    skill_packs: dict[str, str] | None = None,
) -> int:
    key = role.strip().lower()
    print(f"Running build --test for role {key} (load + skill/MCP prompts).", flush=True)
    pack_root = role_skill_source(key, packs=skill_packs or ROLE_SKILL_PACKS)
    skill_names = [path.name for path in skill_directories(pack_root)]
    mcp_names = bundled_mcp_names()
    progress = _Progress(2 + (2 * len(skill_names)) + (2 * len(mcp_names)))
    progress.run("load:opencode", "opencode --version", lambda: _load_opencode_binary(run=run))
    progress.run(
        "load:opencode.json",
        str(opencode_json_path()),
        _load_opencode_config,
    )
    for name in skill_names:
        progress.run(
            f"load:skill:{name}",
            str(opencode_skill_dir() / name / "SKILL.md"),
            lambda skill=name: _skill_load_case(skill),
        )
    mapping, mcp_error = _mcp_config_state()
    mcp_command = str(opencode_json_path())
    for name in mcp_names:
        progress.run(
            f"load:mcp:{name}",
            mcp_command,
            lambda mcp=name: _mcp_load_case(mcp, mapping, mcp_error),
        )
    if progress.has_fail():
        return progress.done()

    for name in skill_names:
        prompt, skip_reason = select_skill_prompt(name, pack_root)
        if prompt is None:
            progress.skip(
                f"skill:{name}",
                "(none)",
                skip_reason or "no representative prompt",
            )
            continue
        progress.run(
            f"skill:{name}",
            format_run_command(prompt),
            lambda current=prompt, skill=name: _live_prompt_case(
                f"skill:{skill}", current, run=run, timeout=timeout
            ),
            live_timeout=timeout,
        )

    for name in mcp_names:
        prompt = mcp_prompt_for(name)
        if not prompt:
            progress.skip(f"mcp:{name}", "(none)", "no built-in MCP smoke prompt")
            continue
        progress.run(
            f"mcp:{name}",
            format_run_command(prompt),
            lambda current=prompt, mcp=name: _live_prompt_case(
                f"mcp:{mcp}", current, run=run, timeout=timeout
            ),
            live_timeout=timeout,
        )
    return progress.done()
