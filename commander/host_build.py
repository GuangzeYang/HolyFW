"""Write DeepSeek provider env-key config into ~/.config/opencode (no skills)."""

from __future__ import annotations

from common.opencode_install import install_commander_opencode


def run_build(*, run_test: bool = False) -> int:
    code = install_commander_opencode(command_name="commander build")
    if code != 0 or not run_test:
        return code
    from common.opencode_verify import verify_commander_build

    return verify_commander_build()
