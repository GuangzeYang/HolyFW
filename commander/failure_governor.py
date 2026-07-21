#!/usr/bin/env python3
"""Persistent per-role failure cooldown, circuit breaking, and SMTP alerts."""

from __future__ import annotations

import json
import logging
import os
import smtplib
import threading
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from filelock import FileLock

try:
    from common import load_json_file, save_json_atomic
except ImportError:
    from ..common import load_json_file, save_json_atomic


class EmailAlerter:
    """Send a bounded QQ SMTP-over-SSL alert without invoking task agents."""

    def __init__(self, config: dict[str, Any]):
        self.enabled = bool(config["enabled"])
        self.smtp_host = str(config["smtp_host"])
        self.smtp_port = int(config["smtp_port"])
        self.sender = str(config["sender"]).strip()
        self.recipients = [str(item).strip() for item in config["recipients"] if str(item).strip()]
        self.auth_code_env = str(config["auth_code_env"]).strip()
        self.timeout_seconds = int(config["timeout_seconds"])
        self.retry_limit = int(config["retry_limit"])

    def send_role_opened(self, role: str, day: str, state: dict[str, Any]) -> tuple[bool, str | None]:
        if not self.enabled:
            return False, "email alerts are disabled"
        auth_code = os.environ.get(self.auth_code_env, "").strip()
        if not auth_code:
            return False, f"environment variable {self.auth_code_env} is empty"

        message = EmailMessage()
        message["Subject"] = f"[HolyFW] 角色任务已熔断: {role} ({day})"
        message["From"] = self.sender
        message["To"] = ", ".join(self.recipients)
        message.set_content(
            "\n".join(
                [
                    "HolyFramework 检测到角色连续失败，已暂停该角色当天剩余任务。",
                    "",
                    f"日期: {day}",
                    f"角色: {role}",
                    f"连续失败次数: {state.get('consecutive_failures', 0)}",
                    f"最后失败时间: {state.get('last_failure_at', '')}",
                    f"最后任务: {state.get('last_task_ref', '')}",
                    f"失败原因: {state.get('last_reason', '')}",
                    f"熔断时间: {state.get('opened_at', '')}",
                    "",
                    "请确认依赖恢复后，使用 breaker_control.py reset --role <角色> 人工解除。",
                ]
            )
        )

        last_error: str | None = None
        for attempt in range(1, self.retry_limit + 1):
            try:
                with smtplib.SMTP_SSL(
                    self.smtp_host,
                    self.smtp_port,
                    timeout=self.timeout_seconds,
                ) as smtp:
                    smtp.login(self.sender, auth_code)
                    smtp.send_message(message)
                return True, None
            except (OSError, smtplib.SMTPException) as exc:
                last_error = str(exc)
                logging.error(
                    "Email alert attempt %s/%s failed for role=%s: %s",
                    attempt,
                    self.retry_limit,
                    role,
                    exc,
                )
        return False, last_error or "unknown SMTP error"


class RoleFailureGovernor:
    """Persist role cooldowns and stop dispatch after repeated failures."""

    def __init__(
        self,
        state_file: Path,
        cooldown_seconds: int,
        max_consecutive_failures: int,
        email_alerter: EmailAlerter,
    ):
        self.state_file = state_file.resolve()
        self.cooldown_seconds = cooldown_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.email_alerter = email_alerter
        self._thread_lock = threading.RLock()

    def _locked_load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 1, "dates": {}}
        data = load_json_file(self.state_file)
        if not isinstance(data.get("dates"), dict):
            data["dates"] = {}
        data.setdefault("version", 1)
        return data

    def _role_state(
        self,
        data: dict[str, Any],
        day: str,
        role: str,
    ) -> dict[str, Any]:
        dates = data.setdefault("dates", {})
        day_states = dates.setdefault(day, {})
        state = day_states.setdefault(
            role,
            {
                "consecutive_failures": 0,
                "cooldown_until": "",
                "circuit_open": False,
                "opened_at": "",
                "last_failure_at": "",
                "last_reason": "",
                "last_task_ref": "",
                "alert_sent": False,
                "alert_error": "",
                "processed_results": {},
            },
        )
        state.setdefault("processed_results", {})
        return state

    def _with_file_lock(self) -> FileLock:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(self.state_file) + ".lock", timeout=30)

    def can_dispatch(self, role: str, day: str) -> tuple[bool, str | None]:
        now = datetime.now().astimezone()
        with self._thread_lock, self._with_file_lock():
            data = self._locked_load()
            state = self._role_state(data, day, role)
            if state.get("circuit_open") is True:
                return False, f"role circuit is open: {state.get('last_reason', '')}"
            cooldown_raw = state.get("cooldown_until")
            if isinstance(cooldown_raw, str) and cooldown_raw:
                try:
                    cooldown_until = datetime.fromisoformat(cooldown_raw.replace("Z", "+00:00"))
                except ValueError:
                    cooldown_until = None
                if cooldown_until is not None and now < cooldown_until:
                    return False, f"role cooling down until {cooldown_until.isoformat()}"
            return True, None

    def record_failure(
        self,
        role: str,
        day: str,
        reason: str,
        task_ref: str = "",
        *,
        result_key: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now().astimezone()
        should_alert = False
        state_snapshot: dict[str, Any]
        with self._thread_lock, self._with_file_lock():
            data = self._locked_load()
            state = self._role_state(data, day, role)
            processed = state["processed_results"]
            if result_key and processed.get(result_key) == "failed":
                return dict(state)
            if result_key:
                processed[result_key] = "failed"

            if state.get("circuit_open") is not True:
                state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
                state["last_failure_at"] = now.isoformat()
                state["last_reason"] = reason
                state["last_task_ref"] = task_ref
                if state["consecutive_failures"] >= self.max_consecutive_failures:
                    state["circuit_open"] = True
                    state["opened_at"] = now.isoformat()
                    state["cooldown_until"] = ""
                    should_alert = not bool(state.get("alert_sent"))
                    logging.critical(
                        "Role %s circuit opened after %s failures: %s",
                        role,
                        state["consecutive_failures"],
                        reason,
                    )
                else:
                    state["cooldown_until"] = (
                        now + timedelta(seconds=self.cooldown_seconds)
                    ).isoformat()
                    logging.warning(
                        "Role %s failure %s/%s; cooling down until %s: %s",
                        role,
                        state["consecutive_failures"],
                        self.max_consecutive_failures,
                        state["cooldown_until"],
                        reason,
                    )
            save_json_atomic(self.state_file, data)
            state_snapshot = dict(state)

        if should_alert:
            sent, error = self.email_alerter.send_role_opened(role, day, state_snapshot)
            with self._thread_lock, self._with_file_lock():
                data = self._locked_load()
                state = self._role_state(data, day, role)
                state["alert_sent"] = sent
                state["alert_error"] = error or ""
                state["alert_attempted_at"] = datetime.now().astimezone().isoformat()
                save_json_atomic(self.state_file, data)
                state_snapshot = dict(state)
        return state_snapshot

    def record_success(
        self,
        role: str,
        day: str,
        task_ref: str = "",
        *,
        result_key: str | None = None,
    ) -> dict[str, Any]:
        with self._thread_lock, self._with_file_lock():
            data = self._locked_load()
            state = self._role_state(data, day, role)
            processed = state["processed_results"]
            if result_key and processed.get(result_key) == "successed":
                return dict(state)
            if result_key:
                processed[result_key] = "successed"
            state["consecutive_failures"] = 0
            state["cooldown_until"] = ""
            state["last_success_at"] = datetime.now().astimezone().isoformat()
            state["last_task_ref"] = task_ref
            save_json_atomic(self.state_file, data)
            return dict(state)

    def status(self, day: str | None = None) -> dict[str, Any]:
        target_day = day or date.today().isoformat()
        with self._thread_lock, self._with_file_lock():
            data = self._locked_load()
            return dict(data.get("dates", {}).get(target_day, {}))

    def reset(self, role: str, day: str | None = None) -> bool:
        target_day = day or date.today().isoformat()
        with self._thread_lock, self._with_file_lock():
            data = self._locked_load()
            day_states = data.get("dates", {}).get(target_day)
            if not isinstance(day_states, dict) or role not in day_states:
                return False
            del day_states[role]
            save_json_atomic(self.state_file, data)
            logging.warning("Role circuit manually reset: role=%s day=%s", role, target_day)
            return True
