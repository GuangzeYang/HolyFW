"""Install attacker OpenCode skills and MCP into ~/.config/opencode."""

from __future__ import annotations

from common.opencode_install import ROLE_SKILL_PACKS, install_role


def run_build(*, run_test: bool = False) -> int:
    code = install_role(
        "attacker",
        command_name="attacker build",
        skill_packs=ROLE_SKILL_PACKS,
    )
    if code != 0 or not run_test:
        return code
    from common.opencode_verify import verify_role_build

    return verify_role_build("attacker", skill_packs=ROLE_SKILL_PACKS)
