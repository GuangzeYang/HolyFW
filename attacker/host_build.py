"""Install attacker OpenCode skills and MCP into ~/.config/opencode."""

from __future__ import annotations

from common.opencode_install import ROLE_SKILL_PACKS, install_role


def run_build() -> int:
    return install_role(
        "attacker",
        command_name="attacker build",
        skill_packs=ROLE_SKILL_PACKS,
    )
