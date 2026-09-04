"""Load the root llm.json catalog: one enabled provider with fixed model and env name."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

LLM_JSON_NAME = "llm.json"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    name: str
    base_url: str
    models: str
    env: str
    enable: bool


def llm_json_path() -> Path:
    """Return the workspace llm.json, else the packaged copy."""
    from common import locate_holyfw_root

    try:
        candidate = locate_holyfw_root() / LLM_JSON_NAME
        if candidate.is_file():
            return candidate
    except FileNotFoundError:
        pass
    from holyfw_assets import bundled_file

    found = bundled_file(LLM_JSON_NAME)
    if found is None or not found.is_file():
        raise FileNotFoundError(f"{LLM_JSON_NAME} not found in the workspace or package")
    return found


def load_llm_catalog(path: Path | None = None) -> dict[str, ProviderRecord]:
    """Parse and validate the provider catalog. Exactly one entry must be enabled."""
    source = path if path is not None else llm_json_path()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{source} must be a JSON object")
    providers = raw.get("provider")
    if not isinstance(providers, dict) or not providers:
        raise ValueError(f"{source} missing provider object")
    catalog: dict[str, ProviderRecord] = {}
    enabled: list[str] = []
    for name, body in providers.items():
        key = str(name).strip()
        if not key:
            raise ValueError(f"{source} has an empty provider name")
        record = _parse_provider(key, body, source=source)
        catalog[key] = record
        if record.enable:
            enabled.append(key)
    if len(enabled) != 1:
        raise ValueError(
            f"{source} must have exactly one provider with enable=true; found {len(enabled)}"
        )
    return catalog


def enabled_provider(path: Path | None = None) -> tuple[str, ProviderRecord]:
    catalog = load_llm_catalog(path)
    for name, record in catalog.items():
        if record.enable:
            return name, record
    raise ValueError("llm.json must have exactly one provider with enable=true")


def lookup_provider(name: str, path: Path | None = None) -> ProviderRecord:
    key = (name or "").strip()
    catalog = load_llm_catalog(path)
    record = catalog.get(key)
    if record is None:
        known = ", ".join(sorted(catalog))
        raise ValueError(f"Unknown LLM provider {key!r}; expected one of: {known}")
    return record


def opencode_model_spec(name: str, record: ProviderRecord) -> str:
    return f"{name}/{record.models}"


def _parse_provider(name: str, body: Any, *, source: Path) -> ProviderRecord:
    if not isinstance(body, Mapping):
        raise ValueError(f"{source} provider {name!r} must be an object")
    base_url = _required_str(body, "base_url", name=name, source=source)
    models = _required_str(body, "models", name=name, source=source)
    env = _required_str(body, "env", name=name, source=source)
    if not _ENV_NAME.match(env):
        raise ValueError(f"{source} provider {name!r} has an invalid env name")
    enable = body.get("enable")
    if not isinstance(enable, bool):
        raise ValueError(f"{source} provider {name!r} enable must be a boolean")
    if "api_key" in body:
        raise ValueError(f"{source} must not contain api_key")
    return ProviderRecord(name=name, base_url=base_url, models=models, env=env, enable=enable)


def _required_str(body: Mapping[str, Any], key: str, *, name: str, source: Path) -> str:
    raw = body.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{source} provider {name!r} missing {key}")
    return raw.strip()
