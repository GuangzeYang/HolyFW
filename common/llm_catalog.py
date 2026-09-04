"""Load the root llm.json catalog: one enabled provider with fixed model and env name."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

LLM_JSON_NAME = "llm.json"
SUPPORTED_PROVIDERS = frozenset({"deepseek", "zhipu"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class ProviderRecord:
    name: str
    base_url: str
    models: str
    env: str
    enable: bool


def workspace_llm_json_path() -> Path:
    """Return the writable checkout llm.json. Never a site-packages copy."""
    from common import is_install_tree, locate_holyfw_root

    path = locate_holyfw_root(package_hint=Path(__file__)) / LLM_JSON_NAME
    if is_install_tree(path):
        raise FileNotFoundError(f"Refusing packaged llm.json path: {path}")
    return path


def llm_json_path() -> Path:
    """Return the workspace llm.json. Never the packaged default catalog."""
    candidate = workspace_llm_json_path()
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"{LLM_JSON_NAME} not found at {candidate}. "
        "Run from the HolyFW workspace, or set HOLYFW_ROOT to that workspace."
    )


def _bundled_llm_json() -> Path | None:
    from holyfw_assets import bundled_file

    return bundled_file(LLM_JSON_NAME)


def ensure_workspace_llm_json() -> Path:
    """Return a writable workspace llm.json, copying the packaged catalog if needed."""
    dest = workspace_llm_json_path()
    if dest.is_file():
        return dest
    src = _bundled_llm_json()
    if src is None or not src.is_file():
        raise FileNotFoundError(f"{LLM_JSON_NAME} not found in the workspace or package")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


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
        if key not in SUPPORTED_PROVIDERS:
            allowed = ", ".join(sorted(SUPPORTED_PROVIDERS))
            raise ValueError(
                f"{source} provider {key!r} is not supported; expected one of: {allowed}"
            )
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
            return require_supported_provider(name), record
    raise ValueError("llm.json must have exactly one provider with enable=true")


def format_enabled_llm_log(path: Path | None = None) -> str:
    """Return a commander log line for the enabled catalog entry."""
    name, record = enabled_provider(path)
    return f"LLM provider={name} model={record.models} base_url={record.base_url}"


def require_supported_provider(name: str) -> str:
    """Return a supported provider name or raise. Does not execute anything else."""
    key = (name or "").strip()
    if key not in SUPPORTED_PROVIDERS:
        allowed = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"Unsupported LLM provider {key!r}; expected one of: {allowed}")
    return key


def lookup_provider(name: str, path: Path | None = None) -> ProviderRecord:
    key = require_supported_provider(name)
    catalog = load_llm_catalog(path)
    record = catalog.get(key)
    if record is None:
        known = ", ".join(sorted(catalog))
        raise ValueError(f"Unknown LLM provider {key!r}; expected one of: {known}")
    return record


def opencode_model_spec(name: str, record: ProviderRecord) -> str:
    return f"{name}/{record.models}"


def resolve_config_selection(
    provider: str | None = None,
    model: str | None = None,
    *,
    path: Path | None = None,
) -> tuple[str, ProviderRecord, str, bool]:
    """Resolve CLI provider/model against llm.json. persist is True when either flag is set."""
    persist = bool((provider or "").strip() or (model or "").strip())
    if (provider or "").strip():
        record = lookup_provider(provider, path)
        name = record.name
    else:
        name, record = enabled_provider(path)
    require_supported_provider(name)
    resolved_model = (model or "").strip() or record.models
    if not resolved_model:
        raise ValueError("model is empty")
    return name, record, resolved_model, persist


def save_enabled_selection(name: str, models: str, path: Path | None = None) -> ProviderRecord:
    """Flip enable and update models in the workspace llm.json. Does not write api_key."""
    from common import is_install_tree

    source = path if path is not None else ensure_workspace_llm_json()
    if is_install_tree(source):
        raise ValueError(f"Refusing to write packaged llm.json: {source}")
    if not source.is_file():
        raise FileNotFoundError(f"Cannot write {source}; need a workspace llm.json")
    key = require_supported_provider(name)
    model = (models or "").strip()
    if not key or not model:
        raise ValueError("provider name and model are required")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{source} must be a JSON object")
    providers = raw.get("provider")
    if not isinstance(providers, dict) or key not in providers:
        known = ", ".join(sorted(str(item) for item in providers)) if isinstance(providers, dict) else ""
        raise ValueError(f"Unknown LLM provider {key!r}; expected one of: {known}")
    for item_name, body in providers.items():
        if not isinstance(body, dict):
            raise ValueError(f"{source} provider {item_name!r} must be an object")
        if "api_key" in body:
            raise ValueError(f"{source} must not contain api_key")
        body["enable"] = str(item_name) == key
        if str(item_name) == key:
            body["models"] = model
    source.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = load_llm_catalog(source)
    return catalog[key]


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
