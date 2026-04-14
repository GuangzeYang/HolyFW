#!/usr/bin/env python3
"""Adapter for dispatching tasks via dispatch.py CLI."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path


class DispatchClient:
    """Subprocess-based dispatch adapter."""

    def __init__(self, dispatch_script: Path, timeout_seconds: int = 30):
        self.dispatch_script = dispatch_script
        self.timeout_seconds = timeout_seconds

    def dispatch(self, role: str, task_text: str, task_time: str | None = None) -> bool:
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
        except Exception as e:
            logging.error(f"Exception when dispatching task for role={role}: {e}")
            return False

        if result.returncode != 0:
            stderr_text = (result.stderr or "").strip()
            stdout_text = (result.stdout or "").strip()
            logging.error(
                f"dispatch.py failed for role={role}, returncode={result.returncode}, "
                f"stderr={stderr_text[:300]}, stdout={stdout_text[:300]}"
            )
            return False

        return True
