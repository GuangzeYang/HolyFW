"""Fill empty attacker task slots in batches of five via the model client."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from common import extract_react_finish_json, repair_json_text
from common.agent_request_abc import AgentRequestABC, AgentRequestError, AgentTimeoutError

from attacker.extract_pcap import ip_in_nets, lab_nets_from_config, parse_networks
from attacker.task_file import completed_task_texts, empty_slot_indices

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
) -> tuple[str, str]:
    nets = tuple(str(item) for item in lab_nets if str(item).strip()) or lab_nets_from_config(None)
    payload = {
        "batch_size": int(batch_size),
        "lab_nets": list(nets),
        "known_completed_tasks": completed_task_texts(tasks),
        "prompt_template": prompt_template,
        "state": state,
        "output": (
            "Return a JSON object {\"tasks\": [string, ...]} with exactly "
            f"{int(batch_size)} English ad-attack task strings. "
            "One technique per string. Follow the prompt template grammar. "
            "Reference only objects that exist in state. "
            f"against host <ip> and against subnet <cidr> must lie in lab_nets {list(nets)}. "
            "Never copy example IPs or subnets from the prompt template."
        ),
    }
    user_text = json.dumps(payload, ensure_ascii=False, indent=2)
    return system_prompt.strip(), user_text


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
) -> list[str]:
    nets = tuple(str(item) for item in lab_nets if str(item).strip()) or lab_nets_from_config(None)
    last_error = "empty model response"
    attempts = max(1, int(max_attempts))
    provider = getattr(agent_client, "provider_name", None) or "LLM"
    model = getattr(agent_client, "model", "") or ""
    base_url = getattr(agent_client, "api_base_url", "") or ""
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
        system_text, user_text = build_generation_messages(
            batch_size=batch_size,
            tasks=tasks,
            system_prompt=system_prompt,
            prompt_template=prompt_template,
            state=state,
            lab_nets=nets,
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
            continue
        except AgentRequestError as exc:
            last_error = str(exc)
            logger.warning("LLM fill attempt %s/%s failed: %s", attempt, attempts, exc)
            continue
        parsed = filter_tasks_to_lab_nets(
            parse_generated_tasks(response.response_text),
            nets,
            state=state,
        )
        if len(parsed) >= batch_size:
            logger.info("LLM returned %s task string(s)", batch_size)
            return parsed[:batch_size]
        if parsed and attempt == attempts:
            logger.info("LLM returned %s in-lab task string(s) (requested %s)", len(parsed), batch_size)
            return parsed
        if parsed:
            last_error = (
                f"attempt {attempt}: {len(parsed)} in-lab task string(s) (requested {batch_size})"
            )
            logger.warning("%s; retrying", last_error)
            continue
        last_error = f"attempt {attempt}: no in-lab task strings in model response"
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
) -> list[dict[str, str]]:
    indices = empty_slot_indices(tasks)
    if not indices:
        return tasks
    count = min(int(batch_size), len(indices))
    logger.info("Filling %s empty attacker slot(s)", count)
    requester = request_batch or request_task_batch
    nets = lab_nets if lab_nets is not None else lab_nets_from_config(None)
    contents = requester(
        batch_size=count,
        tasks=tasks,
        agent_client=agent_client,
        system_prompt=system_prompt,
        prompt_template=prompt_template,
        state=state or {},
        max_attempts=max_attempts,
        lab_nets=nets,
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
