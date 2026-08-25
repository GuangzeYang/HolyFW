"""Bundled HolyFW data files (role profiles, domain markdown)."""

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
    found = _first_existing(_PACKAGE_DIR / "role_profiles", _REPO_ROOT / "role_profiles")
    if found is None:
        raise FileNotFoundError("Bundled role_profiles directory not found")
    return found


def opencode_config_path() -> Path:
    found = _first_existing(
        _PACKAGE_DIR / "role_profiles" / "opencode.json",
        _REPO_ROOT / "role_profiles" / "opencode.json",
    )
    if found is None:
        raise FileNotFoundError("Bundled role_profiles/opencode.json not found")
    return found


def agents_md_path() -> Path:
    found = _first_existing(
        _PACKAGE_DIR / "role_profiles" / "AGENTS.md",
        _REPO_ROOT / "role_profiles" / "AGENTS.md",
    )
    if found is None:
        raise FileNotFoundError("Bundled role_profiles/AGENTS.md not found")
    return found


def bundled_file(name: str) -> Path | None:
    return _first_existing(_PACKAGE_DIR / name, _REPO_ROOT / name)
