"""Bundled HolyFW data files (skills, MCP config, domain markdown)."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent


def _first_existing(*candidates: Path) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None


def skills_root() -> Path:
    found = _first_existing(_PACKAGE_DIR / "skills", _REPO_ROOT / "skills")
    if found is None:
        raise FileNotFoundError("Bundled skills directory not found")
    return found


def mcp_config_path() -> Path:
    found = _first_existing(
        _PACKAGE_DIR / "mcp" / "opencode.json",
        _REPO_ROOT / "mcp" / "opencode.json",
    )
    if found is None:
        raise FileNotFoundError("Bundled mcp/opencode.json not found")
    return found


def agents_md_path() -> Path:
    found = _first_existing(
        _PACKAGE_DIR / "mcp" / "AGENTS.md",
        _REPO_ROOT / "mcp" / "AGENTS.md",
    )
    if found is None:
        raise FileNotFoundError("Bundled mcp/AGENTS.md not found")
    return found


def bundled_file(name: str) -> Path | None:
    return _first_existing(_PACKAGE_DIR / name, _REPO_ROOT / name)
