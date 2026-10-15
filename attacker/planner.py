"""Campaign stage, action signatures, and batch quotas for attacker planning."""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

READ_ONLY_QUERIES = frozenset(
    {
        "systeminfo",
        "whoami",
        "hostname",
        "ipconfig",
        "net-user",
        "tasklist",
        "sc-query",
        "dir",
    }
)

EMPLOYEE_TECHNIQUES = frozenset(
    {
        "collection.local-file-download",
        "collection.share-download",
        "steal.smb",
        "steal.psexec",
        "steal.sftp",
    }
)
USER_ENUM_TECHNIQUES = frozenset(
    {
        "discovery.user-enum-kerbrute",
        "discovery.user-enum-ldap",
        "discovery.user-enum-sid",
    }
)
ROLE_PRIVATE_MARKER = "\\desktop\\role_private_files"
SHARE_MARKER = "company_data"
SHARE_FOLDERS = frozenset(
    {"hr-private", "public", "exchange", "accountancy", "it-dev", "management"}
)
PAYLOAD_TECHNIQUES = frozenset({"lateral.tool-transfer"})
DC_TECHNIQUES = frozenset(
    {
        "collection.sysvol",
        "collection.gpo-files",
        "collection.ldap-export",
    }
)

DEFAULT_GOAL = (
    "Collect employee and infrastructure files, stage them, and upload one "
    "benign lab payload per compromised member host."
)
DEFAULT_TOOLS = [
    "lab-payload-1m.bin",
    "lab-payload-20m.bin",
    "lab-payload-64m.bin",
]
DEFAULT_OBJECTIVES: list[dict[str, Any]] = [
    {
        "id": "employee-documents",
        "goal": "Collect at least 3 distinct files from 2 member hosts",
        "status": "active",
        "progress": {"files": 0, "hosts": [], "paths": []},
    },
    {
        "id": "payload-deployment",
        "goal": "Upload one benign lab payload to 2 compromised hosts",
        "status": "pending",
        "progress": {"hosts": [], "tools": []},
    },
    {
        "id": "dc-configuration",
        "goal": "Collect SYSVOL, GPO, and directory export from the DC",
        "status": "pending",
        "progress": {"techniques": []},
    },
]

DEFAULT_PLANNER: dict[str, Any] = {
    "recent_action_limit": 20,
    "foothold_max_discovery_per_batch": 1,
    "foothold_min_collection_lateral": 2,
    "min_batch_for_collection_quota": 3,
    "max_same_technique": 2,
    "cooldowns_hours": {
        "success": 12,
        "failure": 2,
        "discovery.orientation": 24,
        "discovery.host-scan": 24,
        "discovery.port-scan": 12,
        "discovery.share-enum": 4,
    },
}

_TECHNIQUE_RE = re.compile(
    r"\bexecute\s+(?P<technique>[a-z0-9]+(?:[.-][a-z0-9]+)*)\s+against\s+"
    r"(?:host\s+(?P<host>\S+)|subnet\s+(?P<subnet>\S+)|domain\b)",
    re.IGNORECASE,
)
_SOURCE_NAMED_RE = re.compile(
    r"\busing\s+the\s+(?P<field>\S+)\s+of\s+"
    r"(?P<kind>user|host|wordlists|ticket)\s+(?P<name>\S+)",
    re.IGNORECASE,
)
_SOURCE_PLAIN_RE = re.compile(
    r"\busing\s+the\s+(?P<field>\S+)\s+of\s+(?P<kind>domain|campaign)\b",
    re.IGNORECASE,
)
_QUERY_RE = re.compile(r"\bquery\s+([a-z0-9-]+)\b", re.IGNORECASE)
_PATH_RE = re.compile(r"\bpath\s+(\S+)", re.IGNORECASE)
_TOOL_RE = re.compile(r"\btool\s+(\S+)", re.IGNORECASE)
_TOKEN_TRAIL = ".,;\"')"


def merge_planner_config(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return planner defaults with ``raw`` overrides applied."""
    config = copy.deepcopy(DEFAULT_PLANNER)
    if not isinstance(raw, dict):
        return config
    hours = raw.get("cooldowns_hours")
    if isinstance(hours, dict):
        config["cooldowns_hours"].update(hours)
    for key, value in raw.items():
        if key == "cooldowns_hours" or value is None:
            continue
        if key in config:
            config[key] = value
    return config


def normalize_path(path: str) -> str:
    text = str(path or "").strip().strip("\"'")
    text = text.rstrip(_TOKEN_TRAIL)
    text = text.replace("/", "\\")
    while text.endswith("\\"):
        text = text[:-1]
    return text.lower()


def _strip_token(raw: str) -> str:
    return str(raw or "").strip().strip(_TOKEN_TRAIL)


def _local_clock(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def parse_stamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _local_clock(parsed)


def parse_task_action(text: str) -> dict[str, str] | None:
    """Parse a task string into technique, source, target, and intent fields."""
    match = _TECHNIQUE_RE.search(text or "")
    if match is None:
        return None
    if match.group("host"):
        target = "host:" + _strip_token(match.group("host")).lower()
    elif match.group("subnet"):
        target = "subnet:" + _strip_token(match.group("subnet")).lower()
    else:
        target = "domain"
    prefix = text[: match.start()]
    named = _SOURCE_NAMED_RE.search(prefix)
    plain = _SOURCE_PLAIN_RE.search(prefix)
    source = ""
    if named is not None:
        source = (
            f"{named.group('field').lower()}:{named.group('kind').lower()}:"
            f"{_strip_token(named.group('name')).lower()}"
        )
    elif plain is not None:
        source = f"{plain.group('field').lower()}:{plain.group('kind').lower()}"
    tail = text[match.end() :]
    query_match = _QUERY_RE.search(tail)
    path_match = _PATH_RE.search(tail)
    tool_match = _TOOL_RE.search(tail)
    return {
        "technique": match.group("technique").lower(),
        "source": source,
        "target": target,
        "query": query_match.group(1).lower() if query_match else "",
        "path": normalize_path(path_match.group(1)) if path_match else "",
        "tool": _strip_token(tool_match.group(1)).lower() if tool_match else "",
    }


def action_signature(parsed: Mapping[str, str]) -> str:
    """Stable identity of one action. Different query, path, or tool values differ."""
    params: list[str] = []
    if parsed.get("query"):
        params.append(f"query={parsed['query']}")
    if parsed.get("path"):
        params.append(f"path={parsed['path']}")
    if parsed.get("tool"):
        params.append(f"tool={parsed['tool']}")
    return "|".join(
        (
            str(parsed.get("technique") or ""),
            str(parsed.get("source") or ""),
            str(parsed.get("target") or ""),
            ";".join(params),
        )
    )


def foothold_ready(state: Mapping[str, Any] | None) -> bool:
    """True once the domain, one identified host, and one credential are known."""
    data = state if isinstance(state, Mapping) else {}
    domain = data.get("domain") if isinstance(data.get("domain"), dict) else {}
    if not str(domain.get("name") or "").strip() or not str(domain.get("dc_ip") or "").strip():
        return False
    hosts = [host for host in data.get("hosts") or [] if isinstance(host, dict) and host.get("ip")]
    identified = [
        host
        for host in hosts
        if host.get("open_ports") or host.get("shares") or host.get("fqdn")
    ]
    if not identified:
        return False
    users = data.get("users") if isinstance(data.get("users"), list) else []
    return any(
        isinstance(user, dict) and (user.get("password") or user.get("ntlm_hash"))
        for user in users
    )


def campaign_stage(state: Mapping[str, Any] | None) -> str:
    data = state if isinstance(state, Mapping) else {}
    domain = data.get("domain") if isinstance(data.get("domain"), dict) else {}
    if not str(domain.get("dc_ip") or "").strip():
        return "orientation"
    if foothold_ready(data):
        return "collection"
    return "enumeration"


def planner_state_view(state: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy ``state`` and fill missing campaign goal, objectives, and tool names.

    The caller's state object is not modified.
    """
    view = copy.deepcopy(state) if isinstance(state, Mapping) else {}
    if not isinstance(view, dict):
        view = {}
    campaign = view.get("campaign")
    if not isinstance(campaign, dict):
        campaign = {}
        view["campaign"] = campaign
    if not str(campaign.get("goal") or "").strip():
        campaign["goal"] = DEFAULT_GOAL
    objectives = campaign.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        campaign["objectives"] = copy.deepcopy(DEFAULT_OBJECTIVES)
    tools = campaign.get("tools")
    if not isinstance(tools, list) or not tools:
        campaign["tools"] = list(DEFAULT_TOOLS)
    return view


def _domain(state: Mapping[str, Any]) -> dict[str, Any]:
    domain = state.get("domain")
    return domain if isinstance(domain, dict) else {}


def _matching_host(state: Mapping[str, Any], token: str) -> dict[str, Any] | None:
    wanted = _strip_token(token).lower()
    if not wanted:
        return None
    hosts = state.get("hosts") if isinstance(state.get("hosts"), list) else []
    for host in hosts:
        if not isinstance(host, dict):
            continue
        for key in ("ip", "fqdn", "hostname", "name", "machine_account"):
            if str(host.get(key) or "").strip().lower() == wanted:
                return host
    return None


def _host_token(target: str) -> str:
    if target.startswith("host:"):
        return target.split(":", 1)[1]
    return ""


def _cooldown_exempt(parsed: Mapping[str, str], state: Mapping[str, Any]) -> bool:
    technique = str(parsed.get("technique") or "")
    if not technique.startswith("discovery."):
        return False
    target = str(parsed.get("target") or "")
    if target == "domain" or target.startswith("subnet:"):
        return bool(_domain(state).get("stale"))
    host = _matching_host(state, _host_token(target))
    return bool(host and host.get("stale"))


def _cooldown_hours(technique: str, status: str, config: Mapping[str, Any]) -> float:
    hours = config.get("cooldowns_hours") if isinstance(config.get("cooldowns_hours"), dict) else {}
    if status in {"failed", "timeout"}:
        return float(hours.get("failure", 2))
    if technique in hours and technique.startswith("discovery."):
        return float(hours[technique])
    return float(hours.get("success", 12))


def _parsed_from_item(item: Mapping[str, str]) -> dict[str, str] | None:
    parsed = parse_task_action(str(item.get("task") or ""))
    if parsed is not None:
        return parsed
    technique = str(item.get("technique_id") or "").strip()
    if not technique:
        return None
    return {
        "technique": technique,
        "source": "",
        "target": "",
        "query": "",
        "path": "",
        "tool": "",
    }


def recent_actions(
    tasks: Sequence[Mapping[str, str]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    completed = [
        item
        for item in tasks
        if str(item.get("completed_at") or "").strip() and str(item.get("task") or "").strip()
    ]
    rows: list[dict[str, str]] = []
    for item in completed[-max(0, int(limit)) :]:
        parsed = _parsed_from_item(item) or {}
        signature = str(item.get("action_signature") or "")
        if not signature and parsed:
            signature = action_signature(parsed)
        rows.append(
            {
                "technique": str(item.get("technique_id") or parsed.get("technique") or ""),
                "target": str(parsed.get("target") or ""),
                "query": str(parsed.get("query") or ""),
                "path": str(parsed.get("path") or ""),
                "tool": str(parsed.get("tool") or ""),
                "status": str(item.get("status") or "unknown"),
                "completed_at": str(item.get("completed_at") or ""),
                "signature": signature,
            }
        )
    return rows


def executed_today(
    tasks: Sequence[Mapping[str, str]],
    *,
    now: datetime,
) -> list[dict[str, str]]:
    """Full text of tasks completed on the same local day as ``now``."""
    day = _local_clock(now).date()
    rows: list[dict[str, str]] = []
    for item in tasks:
        task = str(item.get("task") or "").strip()
        completed_at = str(item.get("completed_at") or "").strip()
        if not task or not completed_at:
            continue
        completed = parse_stamp(completed_at)
        if completed is None or completed.date() != day:
            continue
        parsed = _parsed_from_item(item) or {}
        signature = str(item.get("action_signature") or "")
        if not signature and parsed:
            signature = action_signature(parsed)
        rows.append(
            {
                "task": task,
                "status": str(item.get("status") or "unknown"),
                "completed_at": completed_at,
                "technique_id": str(item.get("technique_id") or parsed.get("technique") or ""),
                "action_signature": signature,
            }
        )
    return rows


def active_cooldowns(
    tasks: Sequence[Mapping[str, str]],
    state: Mapping[str, Any],
    now: datetime,
    config: Mapping[str, Any],
) -> list[dict[str, str]]:
    clock = _local_clock(now)
    cooling: dict[str, dict[str, str]] = {}
    for item in tasks:
        if not str(item.get("completed_at") or "").strip() or not str(item.get("task") or "").strip():
            continue
        completed = parse_stamp(str(item.get("completed_at") or ""))
        if completed is None:
            continue
        parsed = _parsed_from_item(item)
        if parsed is None or _cooldown_exempt(parsed, state):
            continue
        status = str(item.get("status") or "unknown")
        hours = _cooldown_hours(str(parsed.get("technique") or ""), status, config)
        until = completed + timedelta(hours=hours)
        if until <= clock:
            continue
        signature = str(item.get("action_signature") or "") or action_signature(parsed)
        previous = cooling.get(signature)
        if previous is not None and previous["cooled_until"] >= until.isoformat(timespec="seconds"):
            continue
        cooling[signature] = {
            "signature": signature,
            "technique": str(parsed.get("technique") or ""),
            "status": status,
            "cooled_until": until.isoformat(timespec="seconds"),
        }
    return list(cooling.values())


def _user_enum_eligible(state: Mapping[str, Any]) -> bool:
    """True when the state can support at least one username-enumeration row."""
    domain = state.get("domain") if isinstance(state.get("domain"), dict) else {}
    if not str(domain.get("name") or "").strip() or not str(domain.get("dc_ip") or "").strip():
        return False
    wordlists = state.get("wordlists") if isinstance(state.get("wordlists"), dict) else {}
    if str(wordlists.get("usernames") or "").strip():
        return True
    users = state.get("users") if isinstance(state.get("users"), list) else []
    return any(
        isinstance(user, dict) and (user.get("password") or user.get("ntlm_hash"))
        for user in users
    )


def _share_relative(path: str) -> str:
    """Return the path from ``company_data`` downward, or an empty string."""
    lowered = str(path or "").lower()
    index = lowered.find(SHARE_MARKER)
    if index < 0:
        return ""
    if index > 0 and lowered[index - 1] not in "\\":
        return ""
    return lowered[index:]


def _share_directory(relative: str) -> bool:
    parts = [part for part in relative.split("\\") if part]
    if parts == [SHARE_MARKER]:
        return True
    return len(parts) == 2 and parts[0] == SHARE_MARKER and parts[1] in SHARE_FOLDERS


def _is_dc_target(host: Mapping[str, Any], state: Mapping[str, Any], token: str) -> bool:
    domain = _domain(state)
    dc_ip = str(domain.get("dc_ip") or "").strip().lower()
    dc_fqdn = str(domain.get("dc_fqdn") or "").strip().lower()
    ip = str(host.get("ip") or "").strip().lower()
    fqdn = str(host.get("fqdn") or "").strip().lower()
    role = str(host.get("role") or "").strip().lower()
    wanted = token.strip().lower()
    return (
        role == "dc"
        or bool(dc_ip and wanted == dc_ip)
        or bool(dc_ip and ip == dc_ip)
        or bool(dc_fqdn and wanted == dc_fqdn)
        or bool(dc_fqdn and fqdn == dc_fqdn)
    )


def _steal_host(state: Mapping[str, Any], token: str, *, share: bool) -> dict[str, Any] | None:
    host = _matching_host(state, token)
    if host is not None:
        return host
    if not share:
        return None
    dc_ip = str(_domain(state).get("dc_ip") or "").strip()
    if token.strip().lower() == dc_ip.lower() and dc_ip:
        return {"ip": dc_ip, "role": "dc"}
    return None


def _steal_rejection(parsed: Mapping[str, str], state: Mapping[str, Any]) -> str:
    path = str(parsed.get("path") or "")
    share_relative = _share_relative(path)
    on_desktop = ROLE_PRIVATE_MARKER in path
    if not on_desktop and not share_relative:
        return "steal path must be under Desktop\\role_private_files or Company_Data"
    token = _host_token(str(parsed.get("target") or ""))
    host = _steal_host(state, token, share=bool(share_relative))
    if host is None:
        return "steal host is not in state"
    if share_relative and not _is_dc_target(host, state, token):
        return "Company_Data theft targets the domain controller"
    technique = str(parsed.get("technique") or "")
    if technique == "steal.sftp" and share_relative:
        return "Company_Data theft uses SMB"
    if technique == "steal.sftp":
        ports = host.get("open_ports") if isinstance(host.get("open_ports"), list) else []
        if ports and 22 not in ports and "22" not in {str(port) for port in ports}:
            return "sftp requires port 22"
    if on_desktop and path.endswith(ROLE_PRIVATE_MARKER):
        return ""
    if share_relative and _share_directory(share_relative):
        return ""
    files = host.get("discovered_files") if isinstance(host.get("discovered_files"), list) else []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if normalize_path(str(entry.get("path") or "")) != path:
            continue
        if entry.get("collected"):
            return "file already collected"
        return ""
    return "steal file is not an uncollected discovered file"


def _download_rejection(parsed: Mapping[str, str], state: Mapping[str, Any]) -> str:
    path = str(parsed.get("path") or "")
    if not path:
        return "download requires a path"
    host = _matching_host(state, _host_token(str(parsed.get("target") or "")))
    if host is None:
        return "download host is not in state"
    files = host.get("discovered_files") if isinstance(host.get("discovered_files"), list) else []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if normalize_path(str(entry.get("path") or "")) != path:
            continue
        if entry.get("collected"):
            return "file already collected"
        return ""
    return "path is not an uncollected discovered file"


def _query_rejection(parsed: Mapping[str, str]) -> str:
    query = str(parsed.get("query") or "")
    if query not in READ_ONLY_QUERIES:
        return "query is not a read-only command"
    if query == "dir" and not parsed.get("path"):
        return "dir query requires a path"
    return ""


def batch_constraints_for(
    state: Mapping[str, Any],
    batch_size: int,
    config: Mapping[str, Any],
    tasks: Sequence[Mapping[str, str]] = (),
    now: datetime | None = None,
) -> dict[str, int | bool | None]:
    opening = now is not None and not executed_today(tasks, now=now)
    if opening:
        return {
            "max_discovery": None,
            "min_collection_or_lateral": 0,
            "max_same_technique": int(config["max_same_technique"]),
            "opening_probe": True,
        }
    foothold = foothold_ready(state)
    minimum = 0
    if foothold and int(batch_size) >= int(config["min_batch_for_collection_quota"]):
        minimum = int(config["foothold_min_collection_lateral"])
    return {
        "max_discovery": int(config["foothold_max_discovery_per_batch"]) if foothold else None,
        "min_collection_or_lateral": minimum,
        "max_same_technique": int(config["max_same_technique"]),
        "opening_probe": False,
    }


def filter_planner_batch(
    texts: Sequence[str],
    *,
    state: Mapping[str, Any],
    tasks: Sequence[Mapping[str, str]],
    batch_size: int,
    now: datetime,
    config: Mapping[str, Any] | None = None,
) -> tuple[list[str], list[str], bool]:
    """Keep tasks that satisfy signature, cooldown, and stage quotas.

    The third value is false when a foothold batch still needs more collection
    or lateral tasks. Callers retry unless this is the final attempt.
    """
    planner = config if isinstance(config, Mapping) else merge_planner_config(None)
    constraints = batch_constraints_for(state, batch_size, planner, tasks, now)
    opening = bool(constraints.get("opening_probe"))
    cooling = {
        row["signature"]
        for row in active_cooldowns(tasks, state, now, planner)
        if row.get("signature")
    }
    max_discovery = constraints["max_discovery"]
    max_same = int(constraints["max_same_technique"] or 0)
    kept: list[str] = []
    reasons: list[str] = []
    seen: set[str] = set()
    technique_counts: dict[str, int] = {}
    discovery_count = 0
    action_count = 0
    for text in texts:
        parsed = parse_task_action(text)
        if parsed is None:
            reasons.append(f"unparsed task: {text}")
            continue
        signature = action_signature(parsed)
        technique = parsed["technique"]
        if signature in seen:
            reasons.append(f"duplicate signature in batch: {signature}")
            continue
        if max_same and technique_counts.get(technique, 0) >= max_same:
            reasons.append(f"technique cap: {technique}")
            continue
        if signature in cooling and not _cooldown_exempt(parsed, state):
            reasons.append(f"cooldown: {signature}")
            continue
        if technique == "lateral.remote-query":
            query_reason = _query_rejection(parsed)
            if query_reason:
                reasons.append(f"{query_reason}: {text}")
                continue
        if technique == "collection.local-file-download":
            download_reason = _download_rejection(parsed, state)
            if download_reason:
                reasons.append(f"{download_reason}: {text}")
                continue
        if technique.startswith("steal."):
            steal_reason = _steal_rejection(parsed, state)
            if steal_reason:
                reasons.append(f"{steal_reason}: {text}")
                continue
        if opening and not technique.startswith("discovery."):
            reasons.append(f"opening probe: {text}")
            continue
        if (
            technique.startswith("discovery.")
            and max_discovery is not None
            and discovery_count >= int(max_discovery)
        ):
            reasons.append(f"discovery quota: {text}")
            continue
        kept.append(text)
        seen.add(signature)
        technique_counts[technique] = technique_counts.get(technique, 0) + 1
        if technique.startswith("discovery."):
            discovery_count += 1
        if technique.startswith(("collection.", "lateral.")):
            action_count += 1
        if len(kept) >= int(batch_size):
            break
    if opening:
        user_enum_count = sum(
            1
            for text in kept
            if (parse_task_action(text) or {}).get("technique") in USER_ENUM_TECHNIQUES
        )
        needs_user_enum = _user_enum_eligible(state)
        quota_met = discovery_count > 0 and (user_enum_count > 0 or not needs_user_enum)
        if not quota_met:
            reasons.append(
                "opening probe needs a username enumeration"
                if needs_user_enum
                else "opening probe needs discovery"
            )
    else:
        minimum = int(constraints["min_collection_or_lateral"] or 0)
        quota_met = action_count >= minimum
        if not quota_met:
            reasons.append(
                f"need at least {minimum} collection or lateral tasks; got {action_count}"
            )
    return kept, reasons, quota_met


def _campaign(state: dict[str, Any]) -> dict[str, Any]:
    campaign = state.get("campaign")
    if not isinstance(campaign, dict):
        campaign = {}
        state["campaign"] = campaign
    return campaign


def _objective(state: dict[str, Any], objective_id: str) -> tuple[dict[str, Any], bool]:
    campaign = _campaign(state)
    objectives = campaign.get("objectives")
    if not isinstance(objectives, list):
        objectives = []
        campaign["objectives"] = objectives
    for row in objectives:
        if isinstance(row, dict) and row.get("id") == objective_id:
            if not isinstance(row.get("progress"), dict):
                row["progress"] = {}
            return row, False
    template = next(
        copy.deepcopy(row) for row in DEFAULT_OBJECTIVES if row["id"] == objective_id
    )
    objectives.append(template)
    return template, True


def _mark_status(row: dict[str, Any], *, done: bool) -> None:
    if done:
        row["status"] = "completed"
    elif row.get("status") in (None, "", "pending"):
        row["status"] = "active"


def _mark_collected(state: dict[str, Any], parsed: Mapping[str, str]) -> bool:
    path = str(parsed.get("path") or "")
    if not path:
        return False
    host = _matching_host(state, _host_token(str(parsed.get("target") or "")))
    if host is None:
        return False
    files = host.get("discovered_files") if isinstance(host.get("discovered_files"), list) else []
    changed = False
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if normalize_path(str(entry.get("path") or "")) != path:
            continue
        if not entry.get("collected"):
            entry["collected"] = True
            changed = True
    return changed


def _remember(progress: dict[str, Any], key: str, value: str) -> bool:
    current = progress.get(key)
    if not isinstance(current, list):
        current = []
        progress[key] = current
    if not value or value in current:
        return False
    current.append(value)
    return True


def record_campaign_progress(state: dict[str, Any], item: Mapping[str, str]) -> bool:
    """Advance campaign objectives after a successful task. Returns True if changed."""
    if str(item.get("status") or "") != "success":
        return False
    parsed = parse_task_action(str(item.get("task") or "")) or {
        "technique": str(item.get("technique_id") or ""),
        "source": "",
        "target": "",
        "query": "",
        "path": "",
        "tool": "",
    }
    technique = str(item.get("technique_id") or parsed.get("technique") or "")
    if technique not in EMPLOYEE_TECHNIQUES | PAYLOAD_TECHNIQUES | DC_TECHNIQUES:
        return False
    changed = False
    host = _host_token(str(parsed.get("target") or "")) or str(parsed.get("target") or "")
    if technique in EMPLOYEE_TECHNIQUES:
        changed = _mark_collected(state, parsed) or changed
        row, created = _objective(state, "employee-documents")
        progress = row["progress"]
        path_key = str(parsed.get("path") or "") or str(item.get("action_signature") or technique)
        changed = _remember(progress, "paths", path_key) or changed
        changed = _remember(progress, "hosts", host) or changed
        progress["files"] = len(progress.get("paths") or [])
        previous = row.get("status")
        _mark_status(
            row,
            done=int(progress["files"]) >= 3 and len(progress.get("hosts") or []) >= 2,
        )
        changed = created or previous != row.get("status") or changed
    elif technique in PAYLOAD_TECHNIQUES:
        row, created = _objective(state, "payload-deployment")
        progress = row["progress"]
        changed = _remember(progress, "hosts", host) or changed
        changed = _remember(progress, "tools", str(parsed.get("tool") or "")) or changed
        previous = row.get("status")
        _mark_status(row, done=len(progress.get("hosts") or []) >= 2)
        changed = created or previous != row.get("status") or changed
    else:
        row, created = _objective(state, "dc-configuration")
        progress = row["progress"]
        changed = _remember(progress, "techniques", technique) or changed
        have = set(progress.get("techniques") or [])
        previous = row.get("status")
        _mark_status(row, done=DC_TECHNIQUES <= have)
        changed = created or previous != row.get("status") or changed
    return changed


def record_campaign_progress_file(item: Mapping[str, str], path: Path | None) -> bool:
    """Persist objective progress into ``state.json`` when the task succeeded."""
    if path is None or not path.is_file():
        return False
    if str(item.get("status") or "") != "success":
        return False
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read attacker state %s: %s", path, exc)
        return False
    if not isinstance(loaded, dict):
        return False
    if not record_campaign_progress(loaded, item):
        return False
    loaded["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(loaded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        logger.warning("Could not write attacker state %s: %s", path, exc)
        return False
    return True
