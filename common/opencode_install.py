"""Install role skills and MCP into the user OpenCode config directory."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

ROLE_SKILL_PACKS: dict[str, str] = {
    "hr": "hr-skills",
    "accountancy": "accountancy-skills",
    "manager": "manager-skills",
    "programmer": "programmer-skills",
    "victim": "victim-skills",
}

SOLDIER_SKILL_PACKS: dict[str, str] = {
    key: pack for key, pack in ROLE_SKILL_PACKS.items() if key != "attacker"
}

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROVIDER_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
ROLE_OPENCODE_MERGE_KEYS = ("permission", "mcp")
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


def opencode_data_dir() -> Path:
    return Path.home() / ".local" / "share" / "opencode"


def opencode_auth_json_paths() -> tuple[Path, ...]:
    """Persisted provider keys; these override environment variables at runtime."""
    return (
        opencode_config_dir() / "auth.json",
        opencode_data_dir() / "auth.json",
    )


def opencode_runtime_cache_targets() -> list[Path]:
    """Files and trees that change the next `opencode run` if left stale."""
    targets: list[Path] = [opencode_cache_dir(), opencode_data_dir()]
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        targets.append(Path(local_app) / "opencode")
    targets.extend(opencode_auth_json_paths())
    seen: set[str] = set()
    unique: list[Path] = []
    for path in targets:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


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
    preserve_names = ("state.json", "changes.json")
    for src in skill_directories(pack_root):
        dest = dest_root / src.name
        preserved: dict[str, bytes] = {}
        if dest.exists():
            for name in preserve_names:
                path = dest / name
                if path.is_file():
                    try:
                        preserved[name] = path.read_bytes()
                    except OSError:
                        pass
            _remove_path(dest)
        shutil.copytree(src, dest, ignore=ignore)
        for name, data in preserved.items():
            try:
                (dest / name).write_bytes(data)
            except OSError:
                pass
        installed.append(src.name)
    if not installed:
        raise FileNotFoundError(f"No SKILL.md directories under {pack_root}")
    return installed


def _unlink_if_exists(path: Path) -> None:
    if path.is_file() or path.is_symlink():
        path.unlink()


def write_opencode_config(
    bundled_path: Path,
    dest_path: Path,
    *,
    keys: Sequence[str],
) -> dict[str, Any]:
    """Replace dest with a fresh config built only from bundled keys. Never read dest."""
    bundled = load_jsonc(bundled_path.read_text(encoding="utf-8"))
    if not isinstance(bundled, dict):
        bundled = {}
    schema = bundled.get("$schema") or _OPENCODE_SCHEMA
    payload: dict[str, Any] = {"$schema": schema}
    for key in keys:
        if key == "permission":
            if "permission" not in bundled:
                raise ValueError(f"No permission config in {bundled_path}")
            payload["permission"] = bundled["permission"]
            continue
        if key == "mcp":
            incoming = bundled.get("mcp")
            if not isinstance(incoming, dict) or not incoming:
                raise ValueError(f"No mcp servers in {bundled_path}")
            payload["mcp"] = dict(incoming)
            continue
        if key == "provider":
            incoming = bundled.get("provider")
            if not isinstance(incoming, dict) or not incoming:
                raise ValueError(f"No provider config in {bundled_path}")
            payload["provider"] = dict(incoming)
            continue
        raise ValueError(f"Unknown opencode config key {key!r}")

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    _unlink_if_exists(dest_path)
    dest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def write_host_opencode_configs(
    bundled_path: Path,
    config_dir: Path | None = None,
    *,
    keys: Sequence[str] = ROLE_OPENCODE_MERGE_KEYS,
) -> dict[str, Any]:
    """Delete existing OpenCode json/jsonc, then write a new opencode.json from the template."""
    root = config_dir if config_dir is not None else opencode_config_dir()
    root.mkdir(parents=True, exist_ok=True)
    _unlink_if_exists(root / "opencode.jsonc")
    return write_opencode_config(bundled_path, root / "opencode.json", keys=keys)


merge_opencode_config = write_opencode_config
merge_host_opencode_configs = write_host_opencode_configs


def bind_opencode_provider_api_key_env(
    provider: str,
    env_name: str,
    dest_path: Path | None = None,
) -> dict[str, Any]:
    """Point provider.<name>.options.apiKey at {env:ENV} in host opencode.json.

    Preserves permission, mcp, and other providers. Never writes the secret.
    Drops leftover baseURL/npm on that provider so OpenCode uses its built-in endpoint.
    """
    name = (provider or "").strip()
    env = (env_name or "").strip()
    if not name or not _PROVIDER_NAME.match(name):
        raise ValueError(f"OpenCode provider name is invalid: {provider!r}")
    if not env or not _ENV_NAME.match(env):
        raise ValueError(f"OpenCode API-key env name is invalid: {env_name!r}")
    dest = dest_path if dest_path is not None else opencode_json_path()
    payload: dict[str, Any] = {"$schema": _OPENCODE_SCHEMA}
    if dest.is_file():
        try:
            loaded = load_jsonc(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{dest} is not valid JSON: {exc}") from exc
        if isinstance(loaded, dict):
            payload = loaded
            if not payload.get("$schema"):
                payload["$schema"] = _OPENCODE_SCHEMA
        elif loaded is not None:
            raise ValueError(f"{dest} must be a JSON object")
    providers = payload.get("provider")
    if not isinstance(providers, dict):
        providers = {}
    body = providers.get(name)
    if not isinstance(body, dict):
        body = {}
    options = body.get("options")
    if not isinstance(options, dict):
        options = {}
    options["apiKey"] = f"{{env:{env}}}"
    options.pop("baseURL", None)
    body["options"] = options
    body.pop("npm", None)
    providers[name] = body
    payload["provider"] = providers
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.name == "opencode.json":
        _unlink_if_exists(dest.parent / "opencode.jsonc")
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def merge_mcp_config(bundled_path: Path, dest_path: Path) -> dict[str, Any]:
    bundled = load_jsonc(bundled_path.read_text(encoding="utf-8"))
    keys: list[str] = []
    if isinstance(bundled, dict) and "permission" in bundled:
        keys.append("permission")
    keys.append("mcp")
    saved = write_opencode_config(bundled_path, dest_path, keys=keys)
    mcp = saved.get("mcp")
    return mcp if isinstance(mcp, dict) else {}


def install_agents_md(role: str, template_path: Path, dest_path: Path | None = None) -> Path:
    dest = dest_path if dest_path is not None else opencode_agents_md_path()
    text = template_path.read_text(encoding="utf-8").replace("{{ROLE}}", role.strip().lower())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest


_REMOVE_RETRY_DELAYS = (0.05, 0.15, 0.4, 1.0, 2.0)


def _chmod_writable(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR)
    except OSError:
        pass


def _exists(path: Path) -> bool:
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return False


def _unlink_retry(path: Path) -> None:
    last_exc: OSError | None = None
    for delay in (0.0, *_REMOVE_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            _chmod_writable(path)
            path.unlink(missing_ok=True)
            if not _exists(path):
                return
        except OSError as exc:
            last_exc = exc
            if not _exists(path):
                return
    if last_exc is not None:
        raise last_exc
    raise OSError(f"Failed to delete file: {path}")


def _rmdir_retry(path: Path) -> None:
    last_exc: OSError | None = None
    for delay in (0.0, *_REMOVE_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            _chmod_writable(path)
            path.rmdir()
            if not _exists(path):
                return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_exc = exc
            if not _exists(path):
                return
    if last_exc is not None:
        raise last_exc
    raise OSError(f"Failed to delete directory: {path}")


def _remove_tree(path: Path) -> None:
    try:
        is_dir = path.is_dir() and not path.is_symlink()
    except OSError:
        is_dir = False
    if is_dir:
        try:
            children = list(path.iterdir())
        except OSError:
            children = []
        for child in children:
            _remove_tree(child)
        _rmdir_retry(path)
        return
    _unlink_retry(path)


def _cmd_rmdir(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def _remove_path(path: Path) -> bool:
    """Delete a file or directory tree. Return True if it existed."""
    if not _exists(path):
        return False
    try:
        _remove_tree(path)
    except OSError:
        if os.name == "nt" and _exists(path):
            _cmd_rmdir(path)
        if _exists(path):
            try:
                _remove_tree(path)
            except OSError as exc:
                raise OSError(
                    f"Failed to delete {path}: {exc}. "
                    "Stop leftover opencode processes and retry."
                ) from exc
    if _exists(path):
        raise OSError(
            f"Failed to delete {path}: the directory is not empty or in use. "
            "Stop leftover opencode processes and retry."
        )
    return True


def _stop_opencode_processes() -> bool:
    """Release locks on ~/.cache/opencode/bin held by a leftover opencode.exe."""
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/IM", "opencode.exe", "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        return False
    time.sleep(0.2)
    print("Stopped leftover opencode.exe processes.", flush=True)
    return True


def clear_opencode_cache(cache_dir: Path | None = None) -> list[Path]:
    """Delete OpenCode runtime cache so the next `opencode run` cannot reuse it.

    With no argument this removes the cache tree, the data/session tree, any
    Windows ``%LOCALAPPDATA%\\opencode`` tree, and persisted ``auth.json`` files.
    A leftover ``opencode.exe`` is stopped first so ``bin`` is not locked.
    Passing ``cache_dir`` only deletes that path (used by tests).
    """
    if cache_dir is not None:
        targets = [cache_dir]
        stop_processes = False
    else:
        targets = opencode_runtime_cache_targets()
        stop_processes = True
    if stop_processes:
        _stop_opencode_processes()
    cleared: list[Path] = []
    for target in targets:
        if _remove_path(target):
            print(f"Cleared OpenCode cache: {target}", flush=True)
            cleared.append(target)
    return cleared


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
            _remove_path(legacy)
        write_host_opencode_configs(opencode_config_path(), keys=ROLE_OPENCODE_MERGE_KEYS)
        install_agents_md(role_key, agents_md_path())
        clear_opencode_cache()
        ensure_playwright(command_name=command_name)
    except (ValueError, FileNotFoundError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"{command_name} failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"Installed skills for {role_key}: {', '.join(installed)}", flush=True)
    print(f"OpenCode config: {opencode_json_path()}", flush=True)
    print(f"OpenCode skills: {opencode_skill_dir()}", flush=True)
    print(f"OpenCode rules: {opencode_agents_md_path()}", flush=True)
    return 0


def install_commander_opencode(
    *,
    command_name: str = "commander build",
    bundled_path: Path | None = None,
) -> int:
    try:
        source = bundled_path
        if source is None:
            source = Path(__file__).resolve().parent.parent / "commander" / "opencode.json"
        if not source.is_file():
            raise FileNotFoundError(f"Commander OpenCode config not found: {source}")
        write_host_opencode_configs(source, keys=COMMANDER_OPENCODE_MERGE_KEYS)
        clear_opencode_cache()
    except (ValueError, FileNotFoundError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"{command_name} failed: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"OpenCode config: {opencode_json_path()}", flush=True)
    return 0
