"""Install office-role (and victim) skills and MCP into the user OpenCode config."""

from __future__ import annotations

import sys
from pathlib import Path

from common.opencode_install import (
    SOLDIER_SKILL_PACKS,
    clear_opencode_cache,
    copy_skills,
    ensure_playwright,
    install_agents_md,
    install_role,
    load_jsonc,
    merge_host_opencode_configs,
    merge_mcp_config,
    opencode_agents_md_path,
    opencode_cache_dir,
    opencode_config_dir,
    opencode_json_path,
    opencode_jsonc_path,
    opencode_legacy_skill_dir,
    opencode_skill_dir,
    playwright_available,
    role_skill_source as _role_skill_source,
    skill_directories,
)

ROLE_SKILL_PACKS = SOLDIER_SKILL_PACKS
ATTACKER_BUILD_HINT = "Use `attacker build` to install attacker skills."


def role_skill_source(role: str) -> Path:
    return _role_skill_source(role, packs=SOLDIER_SKILL_PACKS)


def run_build(role: str) -> int:
    key = (role or "").strip().lower()
    if key == "attacker":
        print(ATTACKER_BUILD_HINT, file=sys.stderr, flush=True)
        return 1
    return install_role(role, command_name="soldier build", skill_packs=SOLDIER_SKILL_PACKS)
