"""Shared Markdown transcripts for OpenCode task runs."""

from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import IO, Any, Mapping, Sequence

OPENCODE_RUN_FLAGS = ("--auto", "--thinking", "--format", "json")
_JSONL_EVENT_TYPES = frozenset(
    {"text", "reasoning", "tool_use", "step_start", "step_finish", "error"}
)
FRONTMATTER_OMIT = frozenset(
    {
        "updated_at",
        "completed_at",
        "execution_deadline",
        "exit_code",
        "message",
        "report",
        "command",
    }
)
BODY_ONLY_KEYS = frozenset(
    {
        "stdout",
        "stderr",
        "stdout_full",
        "stderr_full",
        "stdout_path",
        "stderr_path",
        "task",
        "output",
    }
)
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", re.DOTALL)
_HEADING_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_FENCE_OPEN_RE = re.compile(r"^(`{3,})(?:text)?\s*$")
_ANSI_RE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1B\\))",
    re.DOTALL,
)


def strip_process_output(text: str) -> str:
    """Turn an OpenCode TTY dump into readable plain text."""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    stripped = _ANSI_RE.sub("", normalized)
    lines = [line.rstrip() for line in stripped.split("\n")]
    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run == 1:
                collapsed.append("")
            continue
        blank_run = 0
        collapsed.append(line)
    while collapsed and collapsed[0] == "":
        collapsed.pop(0)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    if not collapsed:
        return ""
    return "\n".join(collapsed) + "\n"


def merge_process_output(stderr: str, stdout: str) -> str:
    """Fallback when stdout is not OpenCode JSONL: stderr then stdout, stripped."""
    left = strip_process_output(stderr or "")
    right = strip_process_output(stdout or "")
    if left and right:
        return left.rstrip("\n") + "\n\n" + right.lstrip("\n")
    return left or right


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def parse_opencode_jsonl_events(text: str) -> list[dict[str, Any]]:
    """Parse ``opencode run --format json`` stdout. Empty if it is not JSONL."""
    events: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("type") not in _JSONL_EVENT_TYPES:
            continue
        events.append(parsed)
    return events


def _format_tool_use(part: Mapping[str, Any]) -> str:
    tool = _as_text(part.get("tool")).strip()
    state = _as_dict(part.get("state"))
    payload = _as_dict(state.get("input")) or _as_dict(part.get("input"))
    status = _as_text(state.get("status"))
    output = _as_text(state.get("output")).strip()
    title = _as_text(state.get("title")).strip()
    if tool == "skill":
        name = _as_text(payload.get("name")).strip()
        return f'→ Skill "{name}"'
    if tool in {"bash", "shell"}:
        command = _as_text(payload.get("command")).strip() or title
        line = f"$ {command}" if command else "$"
        if status == "completed" and output:
            return f"{line}\n{output}"
        if status == "error" and output:
            return f"{line}\n{output}"
        return line
    if tool == "write":
        path = _as_text(payload.get("filePath") or payload.get("path")).strip()
        line = f"← Write {path}".rstrip()
        body = output or ("Wrote file successfully." if status == "completed" else "")
        return f"{line}\n{body}" if body else line
    if tool == "read":
        path = _as_text(payload.get("filePath") or payload.get("path")).strip()
        return f"→ Read {path}".rstrip()
    if tool == "edit":
        path = _as_text(payload.get("filePath") or payload.get("path")).strip()
        line = f"← Edit {path}".rstrip()
        return f"{line}\n{output}" if output else line
    if tool == "glob":
        pattern = _as_text(payload.get("pattern")).strip()
        return f'→ Glob "{pattern}"' if pattern else "→ Glob"
    if tool == "grep":
        pattern = _as_text(payload.get("pattern")).strip()
        return f'→ Grep "{pattern}"' if pattern else "→ Grep"
    label = title or tool or "tool"
    line = f"• {label}"
    return f"{line}\n{output}" if output else line


def _format_jsonl_event(event: Mapping[str, Any]) -> str:
    event_type = event.get("type")
    part = _as_dict(event.get("part"))
    if event_type == "reasoning":
        text = _as_text(part.get("text")).strip()
        return f"Thinking:\n{text}" if text else ""
    if event_type == "text":
        return _as_text(part.get("text")).strip()
    if event_type == "tool_use":
        return _format_tool_use(part)
    if event_type == "error":
        error = event.get("error")
        if isinstance(error, dict):
            data = _as_dict(error.get("data"))
            message = _as_text(data.get("message") or error.get("name")).strip()
        else:
            message = _as_text(error).strip()
        return f"Error: {message}" if message else "Error"
    return ""


def format_opencode_session(stdout: str, stderr: str = "") -> str:
    """Turn OpenCode JSONL (thinking, tools, replies) into readable transcript."""
    events = parse_opencode_jsonl_events(stdout or "")
    if events:
        chunks = [chunk for chunk in (_format_jsonl_event(event) for event in events) if chunk]
        rendered = "\n\n".join(chunks)
        extra = strip_process_output(stderr or "")
        if rendered and extra:
            return rendered.rstrip("\n") + "\n\n" + extra.lstrip("\n")
        if extra:
            return extra
        return rendered + "\n" if rendered and not rendered.endswith("\n") else rendered
    return merge_process_output(stderr, stdout)


def max_backtick_run(text: str) -> int:
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def fence_for(*texts: str) -> str:
    longest = 2
    for text in texts:
        longest = max(longest, max_backtick_run(text or ""))
    return "`" * (longest + 1)


def _as_path(value: object) -> Path | None:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _record_text(record: Mapping[str, Any], full_key: str, short_key: str) -> str:
    value = record.get(full_key)
    if isinstance(value, str):
        return value
    return str(record.get(short_key) or "")


def stream_texts_from_record(record: Mapping[str, Any]) -> tuple[str, str]:
    stdout_path = _as_path(record.get("stdout_path"))
    stderr_path = _as_path(record.get("stderr_path"))
    stdout = (
        _read_text_file(stdout_path)
        if stdout_path is not None and stdout_path.is_file()
        else _record_text(record, "stdout_full", "stdout")
    )
    stderr = (
        _read_text_file(stderr_path)
        if stderr_path is not None and stderr_path.is_file()
        else _record_text(record, "stderr_full", "stderr")
    )
    return stderr, stdout


def process_output_from_record(record: Mapping[str, Any]) -> str:
    stderr, stdout = stream_texts_from_record(record)
    return format_opencode_session(stdout, stderr)


def _unwrap_fence(raw: str) -> str:
    text = raw.strip("\n")
    if not text:
        return ""
    lines = text.split("\n")
    match = _FENCE_OPEN_RE.match(lines[0])
    if match is None:
        return text
    fence = match.group(1)
    close_at = None
    for index in range(len(lines) - 1, 0, -1):
        if lines[index].strip() == fence:
            close_at = index
            break
    if close_at is None:
        return "\n".join(lines[1:])
    return "\n".join(lines[1:close_at])


def parse_body_sections(body: str) -> dict[str, str]:
    headings = list(_HEADING_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        sections[title] = _unwrap_fence(body[start:end])
    return sections


def _parse_frontmatter(block: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, rest = stripped.partition(":")
        if not sep:
            continue
        payload = rest.strip()
        if not payload:
            record[key] = ""
            continue
        try:
            record[key] = json.loads(payload)
        except json.JSONDecodeError:
            record[key] = payload
    return record


def parse_task_markdown(text: str) -> dict[str, Any]:
    """Parse task Markdown (JSON-valued frontmatter) or leftover JSON."""
    raw = text.strip()
    if not raw:
        return {}
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    record = _parse_frontmatter(match.group(1))
    body = text[match.end() :]
    sections = parse_body_sections(body)
    if "Command" in sections and "command" not in record:
        record["command"] = sections["Command"]
    if "Output" in sections:
        record["output"] = strip_process_output(sections["Output"])
    else:
        stdout = sections.get("stdout", "")
        stderr = sections.get("stderr", "")
        if stdout or stderr:
            record["output"] = merge_process_output(stderr, stdout)
    return record


def report_from_task_record(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Commander report stored on disk, or rebuilt from the transcript."""
    if not isinstance(record, Mapping):
        return None
    existing = record.get("report")
    if isinstance(existing, dict):
        return existing
    task_ref = str(record.get("task_ref") or "")
    status = str(record.get("result_status") or "")
    if status not in {"successed", "failed"}:
        outcome = str(record.get("outcome") or "")
        if outcome == "Success":
            status = "successed"
        elif outcome in {"Fail", "Error"}:
            status = "failed"
        else:
            return None
    if not task_ref:
        return None
    output = record.get("output")
    if not isinstance(output, str):
        output = process_output_from_record(record)
    return {
        "task_ref": task_ref,
        "status": status,
        "exit_code": 0 if status == "successed" else -1,
        "stdout": output,
        "stderr": "",
    }


def write_frontmatter(
    handle: IO[bytes],
    record: Mapping[str, Any],
    meta_keys: Sequence[str],
) -> None:
    omit = FRONTMATTER_OMIT | BODY_ONLY_KEYS

    def write(text: str) -> None:
        handle.write(text.encode("utf-8"))

    write("---\n")
    seen: set[str] = set()
    for key in meta_keys:
        if key in omit or key not in record or record[key] is None:
            continue
        write(f"{key}: {json.dumps(record[key], ensure_ascii=False)}\n")
        seen.add(key)
    for key, value in record.items():
        if key in seen or key in omit or value is None or isinstance(value, Path):
            continue
        write(f"{key}: {json.dumps(value, ensure_ascii=False)}\n")
    write("---\n")


def write_task_markdown(
    handle: IO[bytes],
    record: Mapping[str, Any],
    *,
    meta_keys: Sequence[str],
) -> None:
    def write(text: str) -> None:
        handle.write(text.encode("utf-8"))

    command = str(record.get("command") or "")
    stderr, stdout = stream_texts_from_record(record)
    output = format_opencode_session(stdout, stderr)
    write_frontmatter(handle, record, meta_keys)
    fence = fence_for(command, output)
    write(f"\n## Command\n\n{fence}text\n")
    write(command)
    if command and not command.endswith("\n"):
        write("\n")
    write(f"{fence}\n")
    has_output = bool(stderr or stdout)
    if record.get("status") != "completed" and not has_output:
        return
    write(f"\n## Output\n\n{fence}text\n")
    write(output)
    if output and not output.endswith("\n"):
        write("\n")
    write(f"{fence}\n")


def render_task_markdown(record: Mapping[str, Any], *, meta_keys: Sequence[str]) -> str:
    buffer = BytesIO()
    write_task_markdown(buffer, record, meta_keys=meta_keys)
    return buffer.getvalue().decode("utf-8")
