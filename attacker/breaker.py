"""Inspect or reset attacker day state: task file and/or APT state.json."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from attacker.runtime import load_config, resolve_workspace
from attacker.task_file import tasks_file_path

StatusCallback = Callable[[str], None]
MODE_ALL = "all"
MODE_TASK = "task"

ATTACKER_PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGED_SKILL_ROOT = ATTACKER_PACKAGE_DIR / "skills" / "ad-attack"
INSTALLED_SKILL_ROOT = Path.home() / ".config" / "opencode" / "skills" / "ad-attack"

BASELINE_STATE: dict[str, Any] = {
    "schema_version": 2,
    "last_updated": "",
    "domain": {
        "name": "",
        "netbios": "",
        "dc_fqdn": "",
        "dc_ip": "",
        "dcs": [],
        "domain_sid": "",
        "user_count": 0,
        "computer_count": 0,
        "usernames": [],
        "spns": [],
        "delegation": [],
        "groups": [],
        "password_policy": {},
        "trusts": [],
        "rbcd": [],
        "updated_at": "",
    },
    "hosts": [],
    "users": [],
    "wordlists": {
        "usernames": "wordlists/usernames.txt",
        "passwords": "wordlists/passwords.txt",
        "combos": "wordlists/combos.txt",
    },
    "campaign": {
        "machine_account": {"name": "", "password": ""},
        "tools_dir": "tools",
        "tools": [],
    },
    "tickets": {"tgt": [], "service": [], "golden": [], "silver": []},
    "files": [],
    "techniques": {},
    "notes": [],
}

BASELINE_CHANGES: dict[str, Any] = {
    "schema_version": 1,
    "last_updated": "",
    "changes": [],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(message: str) -> None:
    print(message, flush=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _remove_path(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return str(path)
    except OSError as exc:
        return f"{path} (failed: {exc})"


def parse_day(raw: str | None) -> date:
    if raw is None or not str(raw).strip():
        return date.today()
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc


def task_artifacts(data_dir: Path, day: date) -> list[Path]:
    task_file = tasks_file_path(data_dir, day)
    stem = task_file.name[: -len(task_file.suffix)] if task_file.suffix else task_file.name
    extras = [
        task_file.with_name(task_file.name + ".lock"),
        task_file.with_name(task_file.name + ".tmp"),
    ]
    matches = sorted(data_dir.glob(f"{stem}*")) if data_dir.is_dir() else []
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in [*matches, *extras, task_file]:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def discover_skill_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in (PACKAGED_SKILL_ROOT, INSTALLED_SKILL_ROOT):
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(candidate)
    return roots


def resolve_data_dir(config_path: Path | None = None) -> Path:
    loaded = load_config(config_path)
    workspace = resolve_workspace()
    raw = str((loaded.get("paths") or {}).get("data_dir") or "role_task")
    path = Path(raw)
    return path if path.is_absolute() else workspace / path


def clear_task_files(data_dir: Path, day: date) -> list[str]:
    removed: list[str] = []
    for path in task_artifacts(data_dir, day):
        result = _remove_path(path)
        if result:
            removed.append(result)
    return removed


def reset_skill_files(skill_roots: Sequence[Path]) -> dict[str, list[str]]:
    stamp = _now_iso()
    state_payload = dict(BASELINE_STATE)
    state_payload["last_updated"] = stamp
    changes_payload = dict(BASELINE_CHANGES)
    changes_payload["last_updated"] = stamp
    reset_state: list[str] = []
    reset_changes: list[str] = []
    for root in skill_roots:
        state_path = root / "state.json"
        changes_path = root / "changes.json"
        try:
            _write_json(state_path, state_payload)
            reset_state.append(str(state_path))
        except OSError as exc:
            reset_state.append(f"{state_path} (failed: {exc})")
        try:
            _write_json(changes_path, changes_payload)
            reset_changes.append(str(changes_path))
        except OSError as exc:
            reset_changes.append(f"{changes_path} (failed: {exc})")
    return {"reset_state_files": reset_state, "reset_changes_files": reset_changes}


def reset_attacker(
    *,
    mode: str = MODE_ALL,
    day: date | str | None = None,
    config_path: Path | None = None,
    data_dir: Path | None = None,
    skill_roots: Sequence[Path] | None = None,
    emit_status: StatusCallback = _emit,
) -> dict[str, Any]:
    """Delete today's attacker task file. With mode=all, also reset state.json and changes.json."""
    resolved_mode = (mode or MODE_ALL).strip().lower()
    if resolved_mode not in {MODE_ALL, MODE_TASK}:
        raise ValueError("mode must be 'all' or 'task'")
    target_day = day if isinstance(day, date) else parse_day(day)
    tasks_dir = data_dir if data_dir is not None else resolve_data_dir(config_path)
    emit_status(f"Resetting attacker day {target_day.isoformat()}: removing task artifacts")
    removed_tasks = clear_task_files(tasks_dir, target_day)
    payload: dict[str, Any] = {
        "ok": True,
        "day": target_day.isoformat(),
        "mode": resolved_mode,
        "removed_task_files": removed_tasks,
        "reset_state_files": [],
        "reset_changes_files": [],
    }
    if resolved_mode == MODE_TASK:
        emit_status(f"Cleared {len(removed_tasks)} task artifact(s); state.json left unchanged")
        return payload
    roots = list(skill_roots) if skill_roots is not None else discover_skill_roots()
    files = reset_skill_files(roots)
    payload.update(files)
    emit_status(
        f"Reset {len(files['reset_state_files'])} state.json and "
        f"{len(files['reset_changes_files'])} changes.json (does not revert Active Directory)"
    )
    return payload
