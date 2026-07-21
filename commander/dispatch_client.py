#!/usr/bin/env python3
"""Adapter for dispatching tasks via dispatch.py CLI."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from runtime_config import get_dispatch_config, load_runtime_config
except ImportError:
    from commander.runtime_config import get_dispatch_config, load_runtime_config


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    ok: bool
    status: str
    error: str = ""
    task_ref: str = ""
    execution_deadline: str = ""

    @property
    def busy(self) -> bool:
        return self.status == "busy"

    def __bool__(self) -> bool:
        return self.ok


class DispatchClient:
    """Subprocess-based dispatch adapter."""

    def __init__(self, dispatch_script: Path, timeout_seconds: int | None = None):
        if timeout_seconds is None:
            runtime_config = load_runtime_config()
            dispatch_config = get_dispatch_config(runtime_config)
            timeout_seconds = dispatch_config["client_timeout_seconds"]
        self.dispatch_script = dispatch_script
        self.timeout_seconds = timeout_seconds

    def dispatch(self, role: str, task_text: str, task_time: str | None = None) -> DispatchOutcome:
        """Dispatch a task using dispatch.py one-shot command."""
        # JSON string literal preserves quotes and backslashes inside task text.
        command = f"opencode run {json.dumps(task_text, ensure_ascii=False)}"
        args = [
            sys.executable,
            str(self.dispatch_script),
            "--target",
            role,
            "--command",
            command,
            "--task",
            task_text,
        ]
        if task_time:
            args.extend(["--planned-time", task_time])

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as e:
            logging.error(f"dispatch.py timed out for role={role}: {e}")
            return DispatchOutcome(False, "timeout", str(e))
        except OSError as e:
            logging.error(f"Exception when dispatching task for role={role}: {e}")
            return DispatchOutcome(False, "error", str(e))

        payload: dict = {}
        stdout_text = (result.stdout or "").strip()
        if stdout_text:
            try:
                parsed = json.loads(stdout_text.splitlines()[-1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

        if result.returncode != 0:
            stderr_text = (result.stderr or "").strip()
            logging.error(
                f"dispatch.py failed for role={role}, returncode={result.returncode}, "
                f"stderr={stderr_text[:300]}, stdout={stdout_text[:300]}"
            )
            status = str(payload.get("status") or payload.get("reason") or "failed")
            error = str(payload.get("error") or stderr_text or stdout_text or "dispatch failed")
            return DispatchOutcome(
                False,
                status,
                error,
                str(payload.get("task_ref") or ""),
                str(payload.get("execution_deadline") or ""),
            )

        if not payload:
            return DispatchOutcome(
                False,
                "invalid_response",
                "dispatch.py returned no JSON acknowledgment",
            )
        return DispatchOutcome(
            bool(payload.get("ok", False)),
            str(payload.get("status") or "invalid_response"),
            str(payload.get("error") or ""),
            str(payload.get("task_ref") or ""),
            str(payload.get("execution_deadline") or ""),
        )
