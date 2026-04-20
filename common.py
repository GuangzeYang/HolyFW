#!/usr/bin/env python3
"""Common utilities for HolyFramework commander and soldier components."""

import json
import os
import re
import random
import time
from datetime import date
from pathlib import Path
from typing import Any

DATE_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_MD = re.compile(r"^\d{2}-\d{2}$")
UUID_HEX_NO_HYPHEN = re.compile(r"^[0-9a-fA-F]{8,32}$")

ROLE_NAMES = ("hr", "accountancy", "manager", "programmer", "local")
ROLE_ALIASES = {
    "hr": ("hr", "HR", "human resources", "Human Resources", "人事"),
    "accountancy": ("accountancy", "finance", "accounting", "Accountancy", "财务"),
    "manager": ("manager", "ceo", "general manager", "Manager", "总经理"),
    "programmer": ("programmer", "developer", "it", "Programmer", "程序员"),
    "local": ("local", "local operations", "Local", "本地"),
}
WORK_WINDOWS = ((9 * 60, 12 * 60), (13 * 60 + 30, 18 * 60))

ROLE_FALLBACK_TASKS = {
    "hr": [
        "查收并分类员工咨询邮件，整理成待处理清单",
        "登录OA系统核对当日人事审批流状态",
        "发送邮件给相关部门，确认招聘流程节点进度",
        "访问共享目录\\\\resource\\HR，归档当日人事文档",
        "核对新员工入职材料完整性并发送补件提醒",
    ],
    "accountancy": [
        "查收银行通知邮件并核对到账信息",
        "登录OA系统复核报销审批状态并记录差异",
        "访问共享目录\\\\resource\\Finance，更新付款计划表",
        "发送邮件给业务部门确认发票与合同匹配情况",
        "核对本日应收应付变动并整理汇总邮件",
    ],
    "manager": [
        "查收管理层汇报邮件并标记优先处理事项",
        "登录OA系统查看关键审批与风险提醒",
        "发送邮件给部门负责人确认当日重点任务进展",
        "访问共享目录\\\\resource\\Executive，查看经营数据看板",
        "回复跨部门协调邮件并明确执行时间点",
    ],
    "programmer": [
        "查收团队邮件并更新当日开发任务优先级",
        "访问共享目录\\\\resource\\Developer，拉取开发文档与脚本",
        "登录代码平台查看待处理Merge Request与评论",
        "查收测试反馈邮件并补充缺陷复现记录",
        "登录OA系统更新研发工作记录与进展说明",
    ],
    "local": [
        "查收本地环境任务邮件并更新执行清单",
        "登录本地系统控制台核对服务状态",
        "执行本地目录巡检并记录异常文件",
        "同步本地测试结果到项目日报",
        "复核本地自动化任务日志并标记待处理项",
    ],
}


def clean_old_files(dir_path: Path, pattern: str, days: int = 20) -> None:
    """Delete matched files older than the configured retention days."""
    if not dir_path.exists():
        return
    cutoff_time = time.time() - days * 86400
    for file_path in dir_path.glob(pattern):
        try:
            if os.path.getmtime(file_path) < cutoff_time:
                file_path.unlink()
        except OSError:
            pass  # Ignore deletion errors


def validate_task_id(task_id: str) -> str | None:
    """Task ID must be uuid.hex format (no hyphen), length 8-32."""
    if UUID_HEX_NO_HYPHEN.match(task_id):
        return None
    return "Task ID must be hyphen-free hex (uuid.uuid4().hex truncated or full 32 chars)"


def expand_date_segment(seg: str) -> tuple[str | None, str | None]:
    """YYYY-MM-DD or MM-DD -> normalized YYYY-MM-DD."""
    if DATE_FULL.match(seg):
        return seg, None
    if DATE_MD.match(seg):
        try:
            month_s, day_s = seg.split("-", 1)
            m, d = int(month_s), int(day_s)
            y = date.today().year
            date(y, m, d)  # validate
            return f"{y:04d}-{m:02d}-{d:02d}", None
        except (ValueError, OSError):
            return None, "task_ref date segment MM-DD is invalid"
    return None, "task_ref first segment must be YYYY-MM-DD or MM-DD"


def parse_task_ref(task_ref: str) -> tuple[tuple[str, str, str] | None, str | None]:
    """Parse ``(YYYY-MM-DD|MM-DD)_role_taskId``."""
    if not task_ref or not isinstance(task_ref, str):
        return None, "task_ref is empty or invalid"
    parts = task_ref.split("_")
    if len(parts) != 3:
        return None, (
            "task_ref format error: must be three segments date_role_taskId (taskId is uuid.hex, no hyphen)"
        )
    date_seg, role, task_id = parts[0], parts[1], parts[2]
    date_str, err = expand_date_segment(date_seg)
    if err:
        return None, err
    assert date_str is not None
    if "_" in role:
        return None, "task_ref format error: role name must not contain underscore"
    err = validate_task_id(task_id)
    if err:
        return None, err
    return (date_str, role, task_id), None


def tasks_path(data_dir: Path, date_str: str) -> Path:
    """Return path for tasks_MM-DD.json file."""
    month_day = date_str[5:] if len(date_str) >= 10 else date_str
    return data_dir / f"tasks_{month_day}.json"


def load_json_file(path: Path) -> dict[str, Any]:
    """Load JSON file, return empty dict if file doesn't exist."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Save JSON data atomically using temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    clean_old_files(path.parent, "tasks_*.json", days=20)


def _normalize_roles(roles: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    if roles is None:
        return ROLE_NAMES
    normalized: list[str] = []
    seen: set[str] = set()
    for role in roles:
        if not isinstance(role, str):
            continue
        role_name = role.strip().lower()
        if not role_name or role_name in seen:
            continue
        seen.add(role_name)
        normalized.append(role_name)
    return tuple(normalized) if normalized else ROLE_NAMES


def _role_display_name(role: str) -> str:
    aliases = ROLE_ALIASES.get(role)
    if aliases and len(aliases) >= 2:
        return aliases[-1]
    return role


def build_role_task_prompt(
    domain_context: str,
    min_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Build a constrained prompt for role task generation."""
    role_names = _normalize_roles(roles)
    role_display = "、".join(_role_display_name(role) for role in role_names)
    output_format = ", ".join(f'"{role}": [任务列表]' for role in role_names)

    return f'''基于以下企业环境描述，为每个角色生成一天的任务序列：

{domain_context}

硬性要求（必须全部满足）：
1. 角色必须完整：{role_display}。
2. 每个角色至少生成 {min_tasks_per_role} 条任务。
3. 仅输出一个 JSON 对象，禁止输出解释、Markdown、代码块、前后缀。
4. 禁止调用任何工具；禁止输出类似 [TOOL_CALL]、[/TOOL_CALL]、todowrite 等内容。
5. 输出前自行检查可被标准 JSON 解析器直接解析。
6. 每个任务元素格式：{{"time":"09:15","is_load":false,"task":"..."}}。
7. 时间必须在工作时段：09:00~12:00, 13:30~18:00。
8. 同角色任务时间严格递增。
9. 时间必须有随机扰动：
   - 至少 80% 的任务分钟值不能是 5 的倍数；
   - 相邻任务间隔避免固定步长，建议 12~35 分钟随机波动。
10. 任务要符合角色职责，并尽量涉及 Exchange、OA、SMB、FTP、浏览器等可观测网络行为。
11. 输出格式：{{{output_format}}}。
12. 任务内容必须遵循"任务内容模板"的句式与约束来编写。
13. 仅输出任务数据，不要复述企业环境描述中的章节标题、说明文字或模板解释。'''


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from model output text."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def parse_hhmm_to_minute(value: str) -> int | None:
    """Parse HH:MM to minute offset in a day."""
    if not isinstance(value, str) or ":" not in value:
        return None
    try:
        hour, minute = map(int, value.split(":", 1))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def minute_to_hhmm(value: int) -> str:
    """Convert minute offset to HH:MM format."""
    hour, minute = divmod(int(value), 60)
    return f"{hour:02d}:{minute:02d}"


def _in_work_window(value: int) -> bool:
    for start, end in WORK_WINDOWS:
        if start <= value < end:
            return True
    return False


def _next_work_minute(value: int) -> int | None:
    if value < WORK_WINDOWS[0][0]:
        return WORK_WINDOWS[0][0]
    for start, end in WORK_WINDOWS:
        if start <= value < end:
            return value
    for start, _ in WORK_WINDOWS:
        if value < start:
            return start
    return None


def _next_non_five_minute(value: int, prev: int | None = None) -> int | None:
    cur = _next_work_minute(value)
    while cur is not None:
        if prev is not None and cur <= prev:
            cur = _next_work_minute(prev + 1)
            prev = None
            continue
        if cur % 5 != 0:
            return cur
        cur = _next_work_minute(cur + 1)
    return None


def _build_schedule(count: int, seed: int | None = None) -> list[int]:
    rng = random.Random(seed)
    if count <= 0:
        return []

    minutes: list[int] = []
    start = WORK_WINDOWS[0][0] + rng.randint(1, 11)
    current = _next_non_five_minute(start)
    if current is None:
        return []
    minutes.append(current)

    while len(minutes) < count:
        gap = rng.randint(12, 35)
        candidate = minutes[-1] + gap
        next_value = _next_non_five_minute(candidate, minutes[-1])
        if next_value is None:
            break
        minutes.append(next_value)

    while len(minutes) < count:
        fallback = _next_non_five_minute(minutes[-1] + 1, minutes[-1])
        if fallback is None:
            break
        minutes.append(fallback)

    return minutes[:count]


def _get_role_items(data: dict[str, Any], role: str) -> list:
    aliases = ROLE_ALIASES.get(role, (role,))
    for key in aliases:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def normalize_role_tasks(
    data: dict[str, Any],
    min_tasks_per_role: int = 18,
    roles: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Normalize role tasks with deterministic structure, count floor, and jittered times."""
    role_names = _normalize_roles(roles)
    normalized: dict[str, Any] = {}

    for role in role_names:
        items = _get_role_items(data, role)

        descriptions: list[str] = []
        load_flags: list[bool] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            desc = item.get("task")
            if not isinstance(desc, str) or not desc.strip():
                continue
            descriptions.append(desc.strip())
            load_flags.append(bool(item.get("is_load", False)))

        target_count = max(min_tasks_per_role, len(descriptions))
        fallbacks = ROLE_FALLBACK_TASKS.get(role, ["处理当日业务任务并同步结果"])
        idx = 0
        while len(descriptions) < target_count:
            template = fallbacks[idx % len(fallbacks)]
            descriptions.append(template)
            load_flags.append(False)
            idx += 1

        schedule = _build_schedule(target_count)
        if len(schedule) < target_count:
            target_count = len(schedule)
            descriptions = descriptions[:target_count]
            load_flags = load_flags[:target_count]

        role_tasks: list[dict[str, Any]] = []
        for i in range(target_count):
            role_tasks.append(
                {
                    "time": minute_to_hhmm(schedule[i]),
                    "is_load": bool(load_flags[i]),
                    "task": descriptions[i],
                    "task_id": "",
                    "status": "planned",
                    "issued_at": "",
                    "expiry_time": "",
                    "completed_at": "",
                    "report_message": "",
                    "exit_code": None,
                    "stdout": "",
                    "stderr": "",
                }
            )

        normalized[role] = role_tasks

    return normalized


def validate_role_tasks(
    data: dict[str, Any],
    min_tasks_per_role: int = 18,
    min_non_five_ratio: float = 0.8,
    roles: tuple[str, ...] | list[str] | None = None,
) -> tuple[bool, str | None]:
    """Validate role tasks against structure and quality constraints."""
    role_names = _normalize_roles(roles)
    required_task_fields = {
        "time",
        "is_load",
        "task",
        "task_id",
        "status",
        "issued_at",
        "expiry_time",
        "completed_at",
        "report_message",
        "exit_code",
        "stdout",
        "stderr",
    }

    if not isinstance(data, dict):
        return False, "Generated JSON must be a dictionary"

    missing = set(role_names) - set(data.keys())
    if missing:
        return False, f"Missing roles: {sorted(missing)}"

    for role in role_names:
        tasks = data.get(role)
        if not isinstance(tasks, list):
            return False, f"Role '{role}' data is not a list"
        if len(tasks) < min_tasks_per_role:
            return False, f"Role '{role}' has too few tasks: {len(tasks)} < {min_tasks_per_role}"

        non_five = 0
        prev_minute: int | None = None
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                return False, f"Role '{role}' task#{index} is not an object"

            missing_fields = required_task_fields - set(task.keys())
            if missing_fields:
                return False, f"Role '{role}' task#{index} missing fields: {sorted(missing_fields)}"

            desc = task.get("task")
            if not isinstance(desc, str) or not desc.strip():
                return False, f"Role '{role}' task#{index} has empty task"

            minute = parse_hhmm_to_minute(task.get("time"))
            if minute is None:
                return False, f"Role '{role}' task#{index} has invalid time format"
            if not _in_work_window(minute):
                return False, f"Role '{role}' task#{index} time out of work window"
            if prev_minute is not None and minute <= prev_minute:
                return False, f"Role '{role}' tasks are not strictly increasing"
            prev_minute = minute

            if minute % 5 != 0:
                non_five += 1

        ratio = non_five / len(tasks) if tasks else 0.0
        if ratio < min_non_five_ratio:
            return False, (
                f"Role '{role}' random minute ratio too low: {ratio:.2f} < {min_non_five_ratio:.2f}"
            )

    return True, None
