#!/usr/bin/env python3
"""Adapter for dispatching tasks via dispatch.py CLI."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    from logging_setup import log_extra
    from runtime_config import get_dispatch_config, get_paths_config, load_runtime_config, resolve_config_relative_path
    from target_config import load_target_config
except ImportError:
    from commander.logging_setup import log_extra
    from commander.runtime_config import (
        get_dispatch_config,
        get_paths_config,
        load_runtime_config,
        resolve_config_relative_path,
    )
    from commander.target_config import load_target_config


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    ok: bool
    status: str
    error: str = ""
    task_ref: str = ""
    task_id: str = ""
    execution_deadline: str = ""
    soldier_host: str = ""
    soldier_port: int = 0

    @property
    def busy(self) -> bool:
        return self.status == "busy"

    def __bool__(self) -> bool:
        return self.ok


class DispatchClient:
    """Subprocess-based dispatch adapter."""

    def __init__(
        self,
        dispatch_script: Path,
        timeout_seconds: int | None = None,
        target_ini_path: Path | None = None,
    ):
        runtime_config = load_runtime_config()
        if timeout_seconds is None:
            dispatch_config = get_dispatch_config(runtime_config)
            timeout_seconds = dispatch_config["client_timeout_seconds"]
        if target_ini_path is None:
            paths_config = get_paths_config(runtime_config)
            target_ini_path = resolve_config_relative_path(paths_config["target_ini_file"])
        self.dispatch_script = dispatch_script
        self.timeout_seconds = timeout_seconds
        self.target_ini_path = target_ini_path

    def dispatch(
        self,
        role: str,
        task_text: str,
        task_time: str | None = None,
        *,
        task_id: str | None = None,
        role_index: int | None = None,
    ) -> DispatchOutcome:
        """Dispatch a task using dispatch.py one-shot command."""
        resolved_task_id = (task_id or uuid.uuid4().hex[:16]).strip()
        extras = log_extra(role, role_index)
        soldier_host = ""
        soldier_port = 0
        try:
            soldier_host, soldier_port = load_target_config(self.target_ini_path, role)
            logging.debug(
                "Running — %s — Dispatching to (%s,%s)",
                resolved_task_id,
                soldier_host,
                soldier_port,
                extra=extras,
            )
        except (FileNotFoundError, ValueError) as exc:
            logging.debug(
                "Running — %s — DispatchingError - %s",
                resolved_task_id,
                exc,
                extra=extras,
            )
            return DispatchOutcome(
                False,
                "error",
                str(exc),
                task_id=resolved_task_id,
            )

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
            "--task-id",
            resolved_task_id,
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
            logging.debug(
                "Running — %s — DispatchingError - timed out: %s",
                resolved_task_id,
                e,
                extra=extras,
            )
            return DispatchOutcome(
                False,
                "timeout",
                str(e),
                task_id=resolved_task_id,
                soldier_host=soldier_host,
                soldier_port=soldier_port,
            )
        except OSError as e:
            logging.debug(
                "Running — %s — DispatchingError - %s",
                resolved_task_id,
                e,
                extra=extras,
            )
            return DispatchOutcome(
                False,
                "error",
                str(e),
                task_id=resolved_task_id,
                soldier_host=soldier_host,
                soldier_port=soldier_port,
            )

        payload: dict = {}
        stdout_text = (result.stdout or "").strip()
        if stdout_text:
            try:
                parsed = json.loads(stdout_text.splitlines()[-1])
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {}

        task_ref = str(payload.get("task_ref") or "")
        if result.returncode != 0:
            stderr_text = (result.stderr or "").strip()
            status = str(payload.get("status") or payload.get("reason") or "failed")
            error = str(payload.get("error") or stderr_text or stdout_text or "dispatch failed")
            logging.debug(
                "Running — %s — DispatchingError - %s",
                resolved_task_id,
                error,
                extra=extras,
            )
            return DispatchOutcome(
                False,
                status,
                error,
                task_ref,
                resolved_task_id,
                str(payload.get("execution_deadline") or ""),
                soldier_host,
                soldier_port,
            )

        if not payload:
            logging.debug(
                "Running — %s — DispatchingError - dispatch.py returned no JSON acknowledgment",
                resolved_task_id,
                extra=extras,
            )
            return DispatchOutcome(
                False,
                "invalid_response",
                "dispatch.py returned no JSON acknowledgment",
                task_id=resolved_task_id,
                soldier_host=soldier_host,
                soldier_port=soldier_port,
            )

        ok = bool(payload.get("ok", False))
        if ok:
            logging.debug(
                "Running — %s — Dispatched",
                resolved_task_id,
                extra=extras,
            )
        else:
            logging.debug(
                "Running — %s — DispatchingError - %s",
                resolved_task_id,
                payload.get("error") or payload.get("status") or "failed",
                extra=extras,
            )
        return DispatchOutcome(
            ok,
            str(payload.get("status") or "invalid_response"),
            str(payload.get("error") or ""),
            task_ref,
            resolved_task_id,
            str(payload.get("execution_deadline") or ""),
            soldier_host,
            soldier_port,
        )
