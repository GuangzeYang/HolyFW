"""Shared CLI flags and local apply for commander/attacker ``config --api-key``."""

from __future__ import annotations

import argparse
from pathlib import Path

from common.llm_catalog import (
    ProviderRecord,
    resolve_config_selection,
    save_enabled_selection,
    workspace_llm_json_path,
)
from common.user_env import set_user_env
from common.opencode_install import bind_opencode_provider_api_key_env


def add_llm_config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api-key",
        required=True,
        help="API key for the selected provider (never stored in JSON)",
    )
    parser.add_argument(
        "--llm-provider",
        default=None,
        help="provider name from llm.json (default: the enable=true entry)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id for OpenCode --model provider/model (default: that provider's models in llm.json)",
    )


def apply_local_llm_config(
    *,
    api_key: str,
    llm_provider: str | None = None,
    model: str | None = None,
) -> tuple[str, ProviderRecord, str, Path]:
    """Write llm.json enable/models and the provider API-key user env. Never stores the key in JSON."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("--api-key is empty")
    if llm_provider is not None and not str(llm_provider).strip():
        raise ValueError("--llm-provider is empty")
    if model is not None and not str(model).strip():
        raise ValueError("--model is empty")
    name, record, resolved_model, _persist = resolve_config_selection(llm_provider, model)
    record = save_enabled_selection(name, resolved_model)
    set_user_env(record.env, key)
    bind_opencode_provider_api_key_env(
        name, record.env, base_url=record.base_url, model=record.models
    )
    return name, record, resolved_model, workspace_llm_json_path()


def format_local_config_line(catalog: Path, name: str, model: str, env_name: str) -> str:
    return f"local: wrote {catalog} enable={name} model={model}; set user environment {env_name}"
