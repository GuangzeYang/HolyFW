"""Start the Sysmon collector with stored account credentials (no UAC)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sysmon_collector.paths import resolve_account_config_path

TASK_NAME = "HolyFW-SysmonCollector"
ELEVATE_ENV_KEYS = (
    "HOLYFW_ROOT",
    "HOLYFW_SYSMON",
    "SYSMON",
    "HOLYFW_SYSMON_CONFIG",
    "HOLYFW_SYSMON_LOG_DIR",
    "HOLYFW_SYSMON_ACCOUNT_CONFIG",
)
RunFn = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Account:
    username: str
    password: str
    domain: str = ""

    def task_user_id(self) -> str:
        if self.is_system():
            return "SYSTEM"
        name = self.username.strip()
        if "\\" in name or "@" in name:
            return name
        domain = self.domain.strip()
        if domain and domain not in {".", "local"}:
            return f"{domain}\\{name}"
        return f".\\{name}"

    def is_system(self) -> bool:
        name = self.username.strip().replace(" ", "").upper()
        domain = self.domain.strip().replace(" ", "").upper()
        if name in {"SYSTEM", "NTAUTHORITY\\SYSTEM", "NT AUTHORITY\\SYSTEM"}:
            return True
        return domain in {"NTAUTHORITY", "NT AUTHORITY"} and name == "SYSTEM"


def is_elevated() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def load_account_config(path: Path | None = None) -> Account | None:
    config_path = path or resolve_account_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(f"Cannot read Sysmon account config {config_path}: {exc}") from exc
    if not isinstance(data, dict):
        return None
    raw = data.get("account")
    if not isinstance(raw, dict):
        return None
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    domain = str(raw.get("domain") or "").strip()
    if not username or not password:
        return None
    return Account(username=username, password=password, domain=domain)


def snapshot_elevate_env(env: dict[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else os.environ
    return {key: source[key] for key in ELEVATE_ENV_KEYS if source.get(key)}


def _cmd_set_value(value: str) -> str:
    return value.replace("%", "%%").replace('"', "")


def write_system_wrapper(
    *,
    python: str,
    env: dict[str, str],
    cwd: str | None,
    wrapper_path: Path,
    log_path: Path,
) -> Path:
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["@echo off"]
    for key, value in snapshot_elevate_env(env).items():
        lines.append(f'set "{key}={_cmd_set_value(value)}"')
    if cwd:
        lines.append(f'cd /d "{_cmd_set_value(cwd)}"')
    lines.append(
        f'"{_cmd_set_value(python)}" -m sysmon_collector >> "{_cmd_set_value(str(log_path))}" 2>&1'
    )
    wrapper_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return wrapper_path


def _ps_single(value: str) -> str:
    return value.replace("'", "''")


def _register_task_script(
    *,
    wrapper: str,
    user_id: str | None,
    config_path: str | None = None,
) -> str:
    use_account = bool(
        user_id
        and config_path
        and user_id.upper() not in {"SYSTEM", "NT AUTHORITY\\SYSTEM", "NT AUTHORITY\\SYSTEM"}
    )
    if use_account:
        load_pw = (
            f"$cfg = Get-Content -Raw -Encoding UTF8 '{_ps_single(config_path)}' "
            "| ConvertFrom-Json; "
            "$pw = [string]$cfg.account.password; "
        )
        principal = (
            f"$principal = New-ScheduledTaskPrincipal -UserId '{_ps_single(user_id)}' "
            "-LogonType Password -RunLevel Highest; "
        )
        register = (
            "Register-ScheduledTask -TaskName $tn -Action $action -Principal $principal "
            f"-Settings $settings -User '{_ps_single(user_id)}' "
            "-Password $pw -Force | Out-Null; "
        )
    else:
        principal = (
            "$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' "
            "-LogonType ServiceAccount -RunLevel Highest; "
        )
        register = (
            "Register-ScheduledTask -TaskName $tn -Action $action -Principal $principal "
            "-Settings $settings -Force | Out-Null; "
        )
    body = (
        f"$tn = '{TASK_NAME}'; "
        f"$action = New-ScheduledTaskAction -Execute '{_ps_single(wrapper)}'; "
        + principal
        + "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero); "
        + register
        + "Start-ScheduledTask -TaskName $tn"
    )
    if use_account:
        return load_pw + body
    return body


def _run_captured(run_fn: RunFn, args: list[str]) -> subprocess.CompletedProcess:
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 60}
    if os.name == "nt":
        kwargs["encoding"] = "oem"
        kwargs["errors"] = "replace"
    return run_fn(args, **kwargs)


def _run_task_script(script: str, *, run_fn: RunFn | None = None) -> None:
    runner = run_fn or subprocess.run
    result = _run_captured(
        runner,
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "scheduled task failed").strip()
        raise RuntimeError(detail)


def _is_system_user(user_id: str | None) -> bool:
    if not user_id:
        return True
    key = user_id.replace(" ", "").upper()
    return key in {"SYSTEM", "NTAUTHORITY\\SYSTEM", ".\\SYSTEM"}


def _schtasks_create_and_run(
    *,
    wrapper: str,
    user_id: str | None,
    password: str | None,
    run_fn: RunFn | None = None,
) -> None:
    runner = run_fn or subprocess.run
    create = [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        wrapper,
        "/SC",
        "ONSTART",
        "/RL",
        "HIGHEST",
        "/F",
    ]
    if user_id and password is not None and not _is_system_user(user_id):
        create.extend(["/RU", user_id, "/RP", password])
    else:
        create.extend(["/RU", "SYSTEM"])
    created = _run_captured(runner, create)
    if created.returncode != 0:
        detail = (created.stderr or created.stdout or "schtasks /Create failed").strip()
        raise RuntimeError(detail)
    started = _run_captured(runner, ["schtasks", "/Run", "/TN", TASK_NAME])
    if started.returncode != 0:
        detail = (started.stderr or started.stdout or "schtasks /Run failed").strip()
        raise RuntimeError(detail)


def _register_and_start(
    *,
    wrapper: str,
    user_id: str | None = None,
    password: str | None = None,
    config_path: str | None = None,
    run_fn: RunFn | None = None,
) -> None:
    try:
        _run_task_script(
            _register_task_script(wrapper=wrapper, user_id=user_id, config_path=config_path),
            run_fn=run_fn,
        )
        return
    except RuntimeError as first:
        try:
            _schtasks_create_and_run(
                wrapper=wrapper,
                user_id=user_id,
                password=password,
                run_fn=run_fn,
            )
        except RuntimeError as second:
            raise RuntimeError(f"{first}; schtasks fallback: {second}") from second


def start_collector_as_system(
    *,
    python: str,
    env: dict[str, str],
    cwd: str | None,
    wrapper_path: Path,
    log_path: Path,
    run_fn: RunFn | None = None,
) -> None:
    write_system_wrapper(
        python=python,
        env=env,
        cwd=cwd,
        wrapper_path=wrapper_path,
        log_path=log_path,
    )
    wrapper = str(wrapper_path.resolve())
    _register_and_start(wrapper=wrapper, run_fn=run_fn)
    logging.info("Started Sysmon collector as SYSTEM via scheduled task %s", TASK_NAME)


def start_collector_as_account(
    *,
    account: Account,
    python: str,
    env: dict[str, str],
    cwd: str | None,
    wrapper_path: Path,
    log_path: Path,
    config_path: Path | None = None,
    run_fn: RunFn | None = None,
) -> None:
    """Register a Highest-privilege task as the configured account. No UAC prompt."""
    if account.is_system():
        start_collector_as_system(
            python=python,
            env=env,
            cwd=cwd,
            wrapper_path=wrapper_path,
            log_path=log_path,
            run_fn=run_fn,
        )
        return
    write_system_wrapper(
        python=python,
        env=env,
        cwd=cwd,
        wrapper_path=wrapper_path,
        log_path=log_path,
    )
    wrapper = str(wrapper_path.resolve())
    user_id = account.task_user_id()
    cfg = config_path
    if cfg is None:
        cfg = resolve_account_config_path()
    _register_and_start(
        wrapper=wrapper,
        user_id=user_id,
        password=account.password,
        config_path=str(cfg.resolve()),
        run_fn=run_fn,
    )
    logging.info(
        "Started Sysmon collector as %s via scheduled task %s (no UAC)",
        user_id,
        TASK_NAME,
    )


def start_collector_privileged(
    *,
    python: str,
    env: dict[str, str],
    cwd: str | None,
    wrapper_path: Path,
    log_path: Path,
    account: Account | None = None,
    run_fn: RunFn | None = None,
) -> str:
    """Start the collector via Task Scheduler. Prefer configured account; else SYSTEM if elevated."""
    resolved = account
    if resolved is None:
        try:
            resolved = load_account_config()
        except FileNotFoundError:
            resolved = None
    if resolved is not None:
        start_collector_as_account(
            account=resolved,
            python=python,
            env=env,
            cwd=cwd,
            wrapper_path=wrapper_path,
            log_path=log_path,
            run_fn=run_fn,
        )
        return resolved.task_user_id()
    if is_elevated():
        start_collector_as_system(
            python=python,
            env=env,
            cwd=cwd,
            wrapper_path=wrapper_path,
            log_path=log_path,
            run_fn=run_fn,
        )
        return "SYSTEM"
    raise RuntimeError(
        "Sysmon collector needs an admin account in sysmon_collector/config.json "
        "(account.username / account.password). UAC prompts are disabled."
    )
