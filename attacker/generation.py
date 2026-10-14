"""Fill empty attacker task slots in batches of five via the model client."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from common import extract_react_finish_json, repair_json_text
from common.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError

from attacker.extract_pcap import ip_in_nets, lab_nets_from_config, parse_networks
from attacker.logging_setup import write_request_task_md
from attacker.planner import (
    active_cooldowns,
    batch_constraints_for,
    campaign_stage,
    filter_planner_batch,
    merge_planner_config,
    planner_state_view,
    recent_actions,
)
from attacker.task_file import empty_slot_indices

DEFAULT_BATCH_SIZE = 5
ATTACKER_PACKAGE_DIR = Path(__file__).resolve().parent
_HOST_TARGET_RE = re.compile(r"\bagainst\s+host\s+(\S+)", re.IGNORECASE)
_SUBNET_TARGET_RE = re.compile(
    r"\bagainst\s+subnet\s+(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})",
    re.IGNORECASE,
)

FillClient = AgentRequestABC
logger = logging.getLogger(__name__)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def resolve_package_file(*relative: str) -> Path | None:
    candidate = ATTACKER_PACKAGE_DIR.joinpath(*relative)
    if candidate.is_file():
        return candidate
    return None


def resolve_generator_system_path() -> Path | None:
    return resolve_package_file("generator_system.md")


def resolve_prompt_template_path() -> Path | None:
    return resolve_package_file("attacker_prompt_template.md")


def resolve_state_json_path() -> Path | None:
    installed = Path.home() / ".config" / "opencode" / "skills" / "ad-attack" / "state.json"
    if installed.is_file():
        return installed
    return resolve_package_file("skills", "ad-attack", "state.json")


def _task_text_from_item(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        raw = item.get("task") or item.get("text") or item.get("content")
        if isinstance(raw, str):
            return raw.strip()
    return ""


def _extract_json_array(text: str) -> list[Any] | None:
    if not text:
        return None
    decoder = json.JSONDecoder()
    repaired = repair_json_text(text)
    for blob in (repaired, text):
        for idx, char in enumerate(blob):
            if char != "[":
                continue
            try:
                data, _ = decoder.raw_decode(blob, idx)
            except json.JSONDecodeError:
                continue
            if isinstance(data, list):
                return data
    return None


def parse_generated_tasks(response_text: str) -> list[str]:
    """Accept a JSON array, {\"tasks\": [...]}, or a ReAct Finish object."""
    if not response_text or not str(response_text).strip():
        return []
    text = str(response_text)
    finish = extract_react_finish_json(text)
    candidates: list[Any] = []
    if isinstance(finish, dict):
        if isinstance(finish.get("tasks"), list):
            candidates = finish["tasks"]
        elif isinstance(finish.get("task"), str):
            candidates = [finish]
    if not candidates:
        array = _extract_json_array(text)
        if array is not None:
            candidates = array
    if not candidates:
        try:
            parsed = json.loads(repair_json_text(text))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("tasks"), list):
            candidates = parsed["tasks"]
        elif isinstance(parsed, list):
            candidates = parsed
    texts = [_task_text_from_item(item) for item in candidates]
    return [item for item in texts if item]


def _strip_target_token(raw: str) -> str:
    return str(raw or "").strip().strip("\"'.,;:)")


def _host_aliases(state: dict[str, Any] | None) -> dict[str, str]:
    """Map hostname/fqdn/ip (lowercase) to an IPv4/IPv6 string from state."""
    aliases: dict[str, str] = {}
    data = state if isinstance(state, dict) else {}
    domain = data.get("domain") if isinstance(data.get("domain"), dict) else {}
    dc_ip = str(domain.get("dc_ip") or "").strip()
    dc_fqdn = str(domain.get("dc_fqdn") or "").strip()
    if dc_ip:
        aliases[dc_ip.lower()] = dc_ip
        if dc_fqdn:
            aliases[dc_fqdn.lower()] = dc_ip
    for entry in domain.get("dcs") if isinstance(domain.get("dcs"), list) else []:
        if not isinstance(entry, dict):
            continue
        ip = str(entry.get("ip") or "").strip()
        if not ip:
            continue
        aliases[ip.lower()] = ip
        for key in ("fqdn", "hostname", "name"):
            name = str(entry.get(key) or "").strip()
            if name:
                aliases[name.lower()] = ip
    for host in data.get("hosts") if isinstance(data.get("hosts"), list) else []:
        if not isinstance(host, dict):
            continue
        ip = str(host.get("ip") or "").strip()
        if not ip:
            continue
        aliases[ip.lower()] = ip
        for key in ("fqdn", "hostname", "name", "machine_account"):
            name = str(host.get(key) or "").strip()
            if name:
                aliases[name.lower()] = ip
    return aliases


def _subnet_in_lab(cidr: str, nets: Sequence[Any]) -> bool:
    try:
        candidate = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    for net in nets:
        if candidate.version != net.version:
            continue
        if candidate.subnet_of(net) or candidate == net:
            return True
    return False


def task_targets_in_lab_nets(
    text: str,
    lab_nets: Sequence[str],
    *,
    state: dict[str, Any] | None = None,
) -> bool:
    """True when every host/subnet clause in the task sits inside ``lab_nets``."""
    nets = parse_networks(lab_nets)
    if not nets:
        return True
    aliases = _host_aliases(state)
    for match in _SUBNET_TARGET_RE.finditer(text or ""):
        if not _subnet_in_lab(match.group(1), nets):
            return False
    for match in _HOST_TARGET_RE.finditer(text or ""):
        token = _strip_target_token(match.group(1))
        if not token:
            return False
        ip = aliases.get(token.lower(), token)
        if not ip_in_nets(ip, nets):
            return False
    return True


def filter_tasks_to_lab_nets(
    texts: Sequence[str],
    lab_nets: Sequence[str],
    *,
    state: dict[str, Any] | None = None,
) -> list[str]:
    kept: list[str] = []
    for text in texts:
        if task_targets_in_lab_nets(text, lab_nets, state=state):
            kept.append(text)
        else:
            logger.warning("Rejected attacker task outside lab_nets %s: %s", list(lab_nets), text)
    return kept


def build_generation_messages(
    *,
    batch_size: int,
    tasks: list[dict[str, str]],
    system_prompt: str,
    prompt_template: str,
    state: dict[str, Any],
    lab_nets: Sequence[str] = (),
    planner: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    rejected: Sequence[str] | None = None,
) -> tuple[str, str]:
    nets = tuple(str(item) for item in lab_nets if str(item).strip()) or lab_nets_from_config(None)
    config = merge_planner_config(planner)
    clock = now if now is not None else datetime.now().astimezone()
    view = planner_state_view(state)
    campaign = view.get("campaign") if isinstance(view.get("campaign"), dict) else {}
    payload: dict[str, Any] = {
        "batch_size": int(batch_size),
        "lab_nets": list(nets),
        "campaign": {
            "stage": campaign_stage(view),
            "goal": campaign.get("goal") or "",
            "objectives": campaign.get("objectives") or [],
        },
        "recent_actions": recent_actions(tasks, limit=int(config["recent_action_limit"])),
        "cooldowns": active_cooldowns(tasks, view, clock, config),
        "batch_constraints": batch_constraints_for(view, int(batch_size), config),
        "prompt_template": prompt_template,
        "state": view,
        "output": (
            "Return a JSON object {\"tasks\": [string, ...]} with exactly "
            f"{int(batch_size)} English ad-attack task strings. "
            "One technique per string. Follow the prompt template grammar. "
            "Reference only objects that exist in state. "
            f"against host <ip> and against subnet <cidr> must lie in lab_nets {list(nets)}. "
            "Never copy example IPs or subnets from the prompt template. "
            "Obey batch_constraints and cooldowns. "
            "A different query, path, tool, host, or user is a different action."
        ),
    }
    if rejected:
        payload["rejected_last_attempt"] = list(rejected)
    user_text = json.dumps(payload, ensure_ascii=False, indent=2)
    return system_prompt.strip(), user_text


def _fill_batch_number(tasks: list[dict[str, str]], slot_batch_size: int) -> int:
    filled = len(tasks) - len(empty_slot_indices(tasks))
    return filled // max(1, int(slot_batch_size)) + 1


def _write_fill_interaction(
    *,
    logs_dir: Path | None,
    day: date | None,
    batch: int,
    attempt: int,
    agent_client: FillClient,
    messages: list[dict[str, str]],
    status_code: int | None = None,
    finish_reason: str | None = None,
    response_text: str | bytes | None = None,
    raw_response_text: str | bytes | None = None,
    error_text: str | bytes | None = None,
    request_state: str,
) -> None:
    if logs_dir is None:
        return
    write_request_task_md(
        logs_dir,
        day=day if day is not None else date.today(),
        batch=batch,
        attempt=attempt,
        provider=getattr(agent_client, "provider_name", None),
        model=getattr(agent_client, "model", None),
        base_url=getattr(agent_client, "api_base_url", None),
        status_code=status_code,
        finish_reason=finish_reason,
        messages=messages,
        response_text=response_text,
        raw_response_text=raw_response_text,
        error_text=error_text,
        request_state=request_state,
        caller="request_task_batch",
    )


def _split_lab_tasks(
    texts: Sequence[str],
    lab_nets: Sequence[str],
    state: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    reasons: list[str] = []
    for text in texts:
        if task_targets_in_lab_nets(text, lab_nets, state=state):
            kept.append(text)
        else:
            reasons.append(f"outside lab_nets: {text}")
            logger.warning("Rejected attacker task outside lab_nets %s: %s", list(lab_nets), text)
    return kept, reasons


def request_task_batch(
    *,
    batch_size: int,
    tasks: list[dict[str, str]],
    agent_client: FillClient,
    system_prompt: str,
    prompt_template: str,
    state: dict[str, Any],
    max_attempts: int = 5,
    lab_nets: Sequence[str] = (),
    logs_dir: Path | None = None,
    day: date | None = None,
    batch: int | None = None,
    slot_batch_size: int | None = None,
    planner: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> list[str]:
    nets = tuple(str(item) for item in lab_nets if str(item).strip()) or lab_nets_from_config(None)
    planner_config = merge_planner_config(planner)
    last_error = "empty model response"
    attempts = max(1, int(max_attempts))
    provider = getattr(agent_client, "provider_name", None) or "LLM"
    model = getattr(agent_client, "model", "") or ""
    base_url = getattr(agent_client, "api_base_url", "") or ""
    batch_no = int(batch) if batch is not None else _fill_batch_number(
        tasks,
        slot_batch_size if slot_batch_size is not None else batch_size,
    )
    rejected: list[str] = []
    logger.info(
        "Requesting %s attacker task string(s) from %s model=%s base_url=%s (max_attempts=%s)",
        batch_size,
        provider,
        model,
        base_url,
        attempts,
    )
    for attempt in range(1, attempts + 1):
        logger.info(
            "LLM fill attempt %s/%s provider=%s model=%s base_url=%s",
            attempt,
            attempts,
            provider,
            model,
            base_url,
        )
        clock = now if now is not None else datetime.now().astimezone()
        system_text, user_text = build_generation_messages(
            batch_size=batch_size,
            tasks=tasks,
            system_prompt=system_prompt,
            prompt_template=prompt_template,
            state=state,
            lab_nets=nets,
            planner=planner_config,
            now=clock,
            rejected=rejected,
        )
        messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]
        try:
            response = agent_client.request_completion(
                user_text,
                messages=messages,
                response_format={"type": "json_object"} if attempt == attempts else None,
            )
        except AgentTimeoutError as exc:
            last_error = str(exc)
            logger.warning("LLM fill attempt %s/%s timed out: %s", attempt, attempts, exc)
            _write_fill_interaction(
                logs_dir=logs_dir,
                day=day,
                batch=batch_no,
                attempt=attempt,
                agent_client=agent_client,
                messages=messages,
                status_code=exc.status_code,
                error_text=exc.response_text or str(exc),
                request_state="failed",
            )
            continue
        except AgentRequestError as exc:
            last_error = str(exc)
            logger.warning("LLM fill attempt %s/%s failed: %s", attempt, attempts, exc)
            _write_fill_interaction(
                logs_dir=logs_dir,
                day=day,
                batch=batch_no,
                attempt=attempt,
                agent_client=agent_client,
                messages=messages,
                status_code=exc.status_code,
                raw_response_text=exc.response_text,
                error_text=exc.response_text or str(exc),
                request_state="failed",
            )
            continue
        _write_fill_interaction(
            logs_dir=logs_dir,
            day=day,
            batch=batch_no,
            attempt=attempt,
            agent_client=agent_client,
            messages=messages,
            status_code=response.status_code,
            finish_reason=response.finish_reason,
            response_text=response.response_text,
            raw_response_text=response.raw_response_text,
            request_state="finished",
        )
        in_lab, lab_reasons = _split_lab_tasks(
            parse_generated_tasks(response.response_text),
            nets,
            state,
        )
        kept, planner_reasons, quota_met = filter_planner_batch(
            in_lab,
            state=state,
            tasks=tasks,
            batch_size=batch_size,
            now=clock,
            config=planner_config,
        )
        rejected = lab_reasons + planner_reasons
        if rejected:
            logger.warning("Rejected attacker tasks on attempt %s: %s", attempt, rejected)
        if len(kept) >= batch_size and quota_met:
            logger.info("LLM returned %s task string(s)", batch_size)
            return kept[:batch_size]
        if kept and attempt == attempts:
            logger.info(
                "LLM returned %s planner-valid task string(s) (requested %s)",
                len(kept),
                batch_size,
            )
            return kept[:batch_size]
        if kept or rejected:
            last_error = (
                f"attempt {attempt}: {len(kept)} planner-valid task string(s) "
                f"(requested {batch_size})"
            )
            logger.warning("%s; retrying", last_error)
            continue
        last_error = f"attempt {attempt}: no planner-valid task strings in model response"
        logger.warning("%s", last_error)
    logger.error("Failed to generate %s attacker tasks: %s", batch_size, last_error)
    raise RuntimeError(f"Failed to generate {batch_size} attacker tasks: {last_error}")


def fill_next_batch(
    tasks: list[dict[str, str]],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    agent_client: FillClient,
    system_prompt: str = "",
    prompt_template: str = "",
    state: dict[str, Any] | None = None,
    max_attempts: int = 5,
    request_batch: Callable[..., list[str]] | None = None,
    lab_nets: Sequence[str] | None = None,
    logs_dir: Path | None = None,
    day: date | None = None,
    slot_batch_size: int | None = None,
    planner: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    indices = empty_slot_indices(tasks)
    if not indices:
        return tasks
    count = min(int(batch_size), len(indices))
    logger.info("Filling %s empty attacker slot(s)", count)
    requester = request_batch or request_task_batch
    nets = lab_nets if lab_nets is not None else lab_nets_from_config(None)
    divisor = slot_batch_size if slot_batch_size is not None else batch_size
    contents = requester(
        batch_size=count,
        tasks=tasks,
        agent_client=agent_client,
        system_prompt=system_prompt,
        prompt_template=prompt_template,
        state=state or {},
        max_attempts=max_attempts,
        lab_nets=nets,
        logs_dir=logs_dir,
        day=day,
        batch=_fill_batch_number(tasks, divisor),
        slot_batch_size=divisor,
        planner=planner,
        now=now,
    )
    for index, text in zip(indices[:count], contents):
        tasks[index]["task"] = text
    logger.info("Wrote %s task string(s) into empty slots", min(count, len(contents)))
    return tasks


def load_generation_resources() -> tuple[str, str, dict[str, Any]]:
    system_path = resolve_generator_system_path()
    template_path = resolve_prompt_template_path()
    state_path = resolve_state_json_path()
    system_prompt = _read_text(system_path) if system_path else ""
    prompt_template = _read_text(template_path) if template_path else ""
    state = _load_json_object(state_path) if state_path else {}
    if not system_prompt.strip():
        raise FileNotFoundError("attacker generator_system.md was not found")
    if not prompt_template.strip():
        raise FileNotFoundError("attacker_prompt_template.md was not found")
    return system_prompt, prompt_template, state
