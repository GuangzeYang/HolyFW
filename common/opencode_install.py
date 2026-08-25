"""Install role skills and MCP into the user OpenCode config directory."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

ROLE_SKILL_PACKS: dict[str, str] = {
    "hr": "hr-skills",
    "accountancy": "accountancy-skills",
    "manager": "manager-skills",
    "programmer": "programmer-skills",
    "attacker": "attacker-skills",
    "victim": "victim-skills",
}

SOLDIER_SKILL_PACKS: dict[str, str] = {
    key: pack for key, pack in ROLE_SKILL_PACKS.items() if key != "attacker"
}

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
ROLE_OPENCODE_MERGE_KEYS = ("permission", "mcp", "provider")
COMMANDER_OPENCODE_MERGE_KEYS = ("provider",)
_OPENCODE_SCHEMA = "https://opencode.ai/config.json"


def opencode_config_dir() -> Path:
    return Path.home() / ".config" / "opencode"


def opencode_skill_dir() -> Path:
    return opencode_config_dir() / "skills"


def opencode_legacy_skill_dir() -> Path:
    return opencode_config_dir() / "skill"


def opencode_json_path() -> Path:
    return opencode_config_dir() / "opencode.json"


def opencode_jsonc_path() -> Path:
    return opencode_config_dir() / "opencode.jsonc"


def opencode_agents_md_path() -> Path:
    return opencode_config_dir() / "AGENTS.md"


def opencode_cache_dir() -> Path:
    return Path.home() / ".cache" / "opencode"


def _strip_jsonc_comments(text: str) -> str:
    """Remove // and /* */ comments without touching JSON string contents."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                while i < n and text[i] not in "\n\r":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def load_jsonc(text: str) -> Any:
    stripped = _strip_jsonc_comments(text)
    stripped = _TRAILING_COMMA.sub(r"\1", stripped)
    return json.loads(stripped)


def role_skill_source(role: str, *, packs: dict[str, str] | None = None) -> Path:
    key = role.strip().lower()
    mapping = packs if packs is not None else ROLE_SKILL_PACKS
    pack = mapping.get(key)
    if pack is None:
        known = ", ".join(sorted(mapping))
        raise ValueError(f"Unknown role {role!r}. Expected one of: {known}")
    from holyfw_assets import skills_root

    source = skills_root() / pack
    if not source.is_dir():
        raise FileNotFoundError(f"Skill pack for role {key!r} not found: {source}")
    return source


def skill_directories(pack_root: Path) -> list[Path]:
    found: list[Path] = []
    for child in sorted(pack_root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            found.append(child)
    return found


def copy_skills(pack_root: Path, dest_root: Path) -> list[str]:
    dest_root.mkdir(parents=True, exist_ok=True)
    installed: list[str] = []
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".gitignore")
    for src in skill_directories(pack_root):
        dest = dest_root / src.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest, ignore=ignore)
        installed.append(src.name)
    if not installed:
        raise FileNotFoundError(f"No SKILL.md directories under {pack_root}")
    return installed


def _load_opencode_dest(dest_path: Path) -> dict[str, Any]:
    if dest_path.is_file():
        existing = load_jsonc(dest_path.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            return existing
    return {"$schema": _OPENCODE_SCHEMA}


def _merge_named_mapping(current: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current) if isinstance(current, dict) else {}
    merged.update(incoming)
    return merged


def _merge_provider_block(current: Any, incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(current) if isinstance(current, dict) else {}
    for name, cfg in incoming.items():
        if not isinstance(cfg, dict):
            merged[name] = cfg
            continue
        existing_cfg = merged.get(name)
        base = dict(existing_cfg) if isinstance(existing_cfg, dict) else {}
        new_cfg = dict(cfg)
        if "options" in new_cfg:
            cur_opts = base.get("options") if isinstance(base.get("options"), dict) else {}
            inc_opts = new_cfg["options"] if isinstance(new_cfg["options"], dict) else {}
            new_cfg["options"] = {**cur_opts, **inc_opts}
        base.update(new_cfg)
        merged[name] = base
    return merged


def merge_opencode_config(
    bundled_path: Path,
    dest_path: Path,
    *,
    keys: Sequence[str],
) -> dict[str, Any]:
    bundled = load_jsonc(bundled_path.read_text(encoding="utf-8"))
    if not isinstance(bundled, dict):
        bundled = {}
    existing = _load_opencode_dest(dest_path)

    for key in keys:
        if key == "permission":
            if "permission" in bundled:
                existing["permission"] = bundled["permission"]
            continue
        if key == "mcp":
            incoming = bundled.get("mcp")
            if not isinstance(incoming, dict) or not incoming:
                raise ValueError(f"No mcp servers in {bundled_path}")
            existing["mcp"] = _merge_named_mapping(existing.get("mcp"), incoming)
            continue
        if key == "provider":
            incoming = bundled.get("provider")
            if not isinstance(incoming, dict) or not incoming:
                raise ValueError(f"No provider config in {bundled_path}")
            existing["provider"] = _merge_provider_block(existing.get("provider"), incoming)
            continue
        raise ValueError(f"Unknown opencode merge key {key!r}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return existing


def merge_mcp_config(bundled_path: Path, dest_path: Path) -> dict[str, Any]:
    bundled = load_jsonc(bundled_path.read_text(encoding="utf-8"))
    keys: list[str] = []
    if isinstance(bundled, dict) and "permission" in bundled:
        keys.append("permission")
    keys.append("mcp")
    if isinstance(bundled, dict) and isinstance(bundled.get("provider"), dict) and bundled["provider"]:
        keys.append("provider")
    saved = merge_opencode_config(bundled_path, dest_path, keys=keys)
    mcp = saved.get("mcp")
    return mcp if isinstance(mcp, dict) else {}


def merge_host_opencode_configs(
    bundled_path: Path,
    config_dir: Path | None = None,
    *,
    keys: Sequence[str] = ROLE_OPENCODE_MERGE_KEYS,
) -> dict[str, Any]:
    root = config_dir if config_dir is not None else opencode_config_dir()
    merged = merge_opencode_config(bundled_path, root / "opencode.json", keys=keys)
    jsonc_path = root / "opencode.jsonc"
    if jsonc_path.is_file():
        merge_opencode_config(bundled_path, jsonc_path, keys=keys)
    return merged


def install_agents_md(role: str, template_path: Path, dest_path: Path | None = None) -> Path:
    dest = dest_path if dest_path is not None else opencode_agents_md_path()
    text = template_path.read_text(encoding="utf-8").replace("{{ROLE}}", role.strip().lower())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


def clear_opencode_cache(cache_dir: Path | None = None) -> bool:
    target = cache_dir if cache_dir is not None else opencode_cache_dir()
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def _npx_executable() -> str | None:
    return shutil.which("npx") or shutil.which("npx.cmd")


def playwright_available(run: Any = subprocess.run) -> bool:
    npx = _npx_executable()
    if not npx:
        return False
    try:
        completed = run(
            [npx, "--yes", "playwright", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return int(getattr(completed, "returncode", 1) or 0) == 0


def ensure_playwright(run: Any = subprocess.run, *, command_name: str = "build") -> None:
    if playwright_available(run=run):
        print("Playwright is available.", flush=True)
        return
    npx = _npx_executable()
    if not npx:
        raise RuntimeError(f"npx was not found; install Node.js before {command_name}")
    print("Playwright is not available; installing Chromium via npx playwright install.", flush=True)
    completed = run(
        [npx, "--yes", "playwright", "install", "chromium"],
        check=False,
    )
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        raise RuntimeError("playwright install chromium failed")
    if not playwright_available(run=run):
        raise RuntimeError("Playwright is still unavailable after install")
    print("Playwright Chromium installed.", flush=True)


def install_role(
    role: str,
    *,
    command_name: str,
    skill_packs: dict[str, str] | None = None,
) -> int:
    try:
        role_key = role.strip().lower()
        pack_root = role_skill_source(role_key, packs=skill_packs)
        from holyfw_assets import agents_md_path, opencode_config_path

        installed = copy_skills(pack_root, opencode_skill_dir())
        legacy = opencode_legacy_skill_dir()
        if legacy.is_dir():
            shutil.rmtree(legacy)
        merge_host_opencode_configs(opencode_config_path(), keys=ROLE_OPENCODE_MERGE_KEYS)
        install_agents_md(role_key, agents_md_path())
        if clear_opencode_cache():
            print(f"Cleared OpenCode cache: {opencode_cache_dir()}", flush=True)
        ensure_playwright(command_name=command_name)
    except (ValueError, FileNotFoundError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"{command_name} failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Installed skills for {role_key}: {', '.join(installed)}", flush=True)
    print(f"OpenCode config: {opencode_json_path()}", flush=True)
    print(f"OpenCode skills: {opencode_skill_dir()}", flush=True)
    print(f"OpenCode rules: {opencode_agents_md_path()}", flush=True)
    return 0


def install_commander_opencode(*, command_name: str = "commander build") -> int:
    try:
        from holyfw_assets import opencode_config_path

        merge_host_opencode_configs(
            opencode_config_path(),
            keys=COMMANDER_OPENCODE_MERGE_KEYS,
        )
        if clear_opencode_cache():
            print(f"Cleared OpenCode cache: {opencode_cache_dir()}", flush=True)
    except (ValueError, FileNotFoundError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"{command_name} failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"OpenCode config: {opencode_json_path()}", flush=True)
    return 0
