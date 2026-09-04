"""Install attacker OpenCode skills from this package into ~/.config/opencode."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from common.opencode_install import (
    _remove_path,
    clear_opencode_cache,
    copy_skills,
    install_agents_md,
    opencode_agents_md_path,
    opencode_json_path,
    opencode_legacy_skill_dir,
    opencode_skill_dir,
    write_host_opencode_configs,
)

ATTACKER_PACKAGE_DIR = Path(__file__).resolve().parent
ATTACKER_SKILLS_DIR = ATTACKER_PACKAGE_DIR / "skills"
ATTACKER_AGENTS_MD = ATTACKER_PACKAGE_DIR / "AGENTS.md"
ATTACKER_OPENCODE_JSON = ATTACKER_PACKAGE_DIR / "opencode.json"
ATTACKER_OPENCODE_KEYS = ("permission",)


def run_build(*, run_test: bool = False) -> int:
    try:
        if not ATTACKER_SKILLS_DIR.is_dir():
            raise FileNotFoundError(f"Attacker skills not found: {ATTACKER_SKILLS_DIR}")
        if not ATTACKER_AGENTS_MD.is_file():
            raise FileNotFoundError(f"Attacker AGENTS.md not found: {ATTACKER_AGENTS_MD}")
        if not ATTACKER_OPENCODE_JSON.is_file():
            raise FileNotFoundError(f"Attacker opencode.json not found: {ATTACKER_OPENCODE_JSON}")
        installed = copy_skills(ATTACKER_SKILLS_DIR, opencode_skill_dir())
        legacy = opencode_legacy_skill_dir()
        if legacy.is_dir():
            _remove_path(legacy)
        write_host_opencode_configs(ATTACKER_OPENCODE_JSON, keys=ATTACKER_OPENCODE_KEYS)
        install_agents_md("attacker", ATTACKER_AGENTS_MD)
        clear_opencode_cache()
    except (ValueError, FileNotFoundError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"attacker build failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Installed skills for attacker: {', '.join(installed)}", flush=True)
    print(f"OpenCode config: {opencode_json_path()}", flush=True)
    print(f"OpenCode skills: {opencode_skill_dir()}", flush=True)
    print(f"OpenCode rules: {opencode_agents_md_path()}", flush=True)
    if not run_test:
        return 0
    from common.opencode_verify import verify_role_build

    return verify_role_build(
        "attacker",
        pack_root=ATTACKER_SKILLS_DIR,
        bundled_opencode_path=ATTACKER_OPENCODE_JSON,
    )
