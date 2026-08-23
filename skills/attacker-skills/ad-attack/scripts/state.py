#!/usr/bin/env python3
"""Read and update the attacker's long-term APT state file (state.json).

Subcommands:
    read                   Print the full state document.
    get <path>             Print the value at a dotted path (e.g. domain.dc_ip).
    set <path> <value>     Set a value; <value> is parsed as JSON.
    add <path> <value>     Append <value> (parsed as JSON) to the list at <path>.
    merge <path> <object>  Deep-merge a JSON object into the object at <path>.
    mark-stale <path>      Set "stale": true on the object at <path>.
    unset-stale <path>     Clear the "stale" flag on the object at <path>.
    touch <path>           Set "updated_at" to now on the object at <path>.

Paths are dot-separated and may contain integer list indexes, for example
``hosts[0].compromised``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = SKILL_ROOT / "state.json"

_INDEX_RE = re.compile(r"^([^\[]+)\[(\d+)\]$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load() -> dict:
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            return data
    return {}


def _save(data: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _split_path(path: str) -> list[str]:
    if not path or not path.strip():
        raise ValueError("path must not be empty")
    return [part for part in path.split(".") if part]


def _resolve_container(container, part: str, create: bool = False):
    m = _INDEX_RE.match(part)
    if m:
        key = m.group(1)
        idx = int(m.group(2))
        if key not in container:
            if create:
                container[key] = []
            else:
                raise KeyError(key)
        seq = container[key]
        if not isinstance(seq, list):
            raise TypeError(f"'{key}' is not a list")
        while len(seq) <= idx:
            if create:
                seq.append({})
            else:
                raise IndexError(idx)
        return seq[idx]
    if part not in container:
        if create:
            container[part] = {}
        else:
            raise KeyError(part)
    return container[part]


def _get(data: dict, parts: list[str]):
    cur = data
    for part in parts:
        cur = _resolve_container(cur, part)
    return cur


def _set(data: dict, parts: list[str], value):
    cur = data
    for part in parts[:-1]:
        cur = _resolve_container(cur, part, create=True)
    last = parts[-1]
    m = _INDEX_RE.match(last)
    if m:
        key = m.group(1)
        idx = int(m.group(2))
        if key not in cur or not isinstance(cur[key], list):
            cur[key] = []
        seq = cur[key]
        while len(seq) <= idx:
            seq.append(None)
        seq[idx] = value
    else:
        cur[last] = value


def _deep_merge(base: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _parse_value(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            raise ValueError(
                "value looks like JSON but is not valid (inner double quotes may "
                "have been stripped by the shell); re-quote the argument so it "
                "reaches python intact: " + raw
            )
        return raw


def _require_dict(value, path: str):
    if not isinstance(value, dict):
        raise TypeError(
            f"target at '{path}' is not an object (it is a scalar value); "
            f"scalar fields cannot be marked stale — re-run the producing "
            f"technique and overwrite with `set` instead"
        )


def cmd_read(args) -> int:
    print(json.dumps(_load(), ensure_ascii=False, indent=2))
    return 0


def cmd_get(args) -> int:
    data = _load()
    try:
        value = _get(data, _split_path(args.path))
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cmd_set(args) -> int:
    data = _load()
    try:
        value = _parse_value(args.value)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        _set(data, _split_path(args.path), value)
    except (KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def cmd_add(args) -> int:
    data = _load()
    try:
        value = _parse_value(args.value)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    try:
        target = _get(data, _split_path(args.path))
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if not isinstance(target, list):
        print(
            json.dumps({"ok": False, "error": f"target at '{args.path}' is not a list"})
        )
        return 1
    target.append(value)
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def cmd_merge(args) -> int:
    data = _load()
    try:
        patch = json.loads(args.object)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON object: {exc}"}))
        return 1
    if not isinstance(patch, dict):
        print(json.dumps({"ok": False, "error": "merge value must be a JSON object"}))
        return 1
    try:
        target = _get(data, _split_path(args.path))
        _require_dict(target, args.path)
        _deep_merge(target, patch)
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def cmd_mark_stale(args) -> int:
    data = _load()
    try:
        target = _get(data, _split_path(args.path))
        _require_dict(target, args.path)
        target["stale"] = True
        target["stale_at"] = _now_iso()
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def cmd_unset_stale(args) -> int:
    data = _load()
    try:
        target = _get(data, _split_path(args.path))
        _require_dict(target, args.path)
        target.pop("stale", None)
        target.pop("stale_at", None)
        target["updated_at"] = _now_iso()
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def cmd_touch(args) -> int:
    data = _load()
    try:
        target = _get(data, _split_path(args.path))
        _require_dict(target, args.path)
        target["updated_at"] = _now_iso()
    except (KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data["last_updated"] = _now_iso()
    _save(data)
    print(json.dumps({"ok": True}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read and update the attacker APT state file"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("read", help="print the full state document")

    p_get = sub.add_parser("get", help="get a value by dotted path")
    p_get.add_argument("path")

    p_set = sub.add_parser("set", help="set a value (value is parsed as JSON)")
    p_set.add_argument("path")
    p_set.add_argument("value")

    p_add = sub.add_parser(
        "add", help="append a value to a list (value is parsed as JSON)"
    )
    p_add.add_argument("path")
    p_add.add_argument("value")

    p_merge = sub.add_parser("merge", help="deep-merge a JSON object into a path")
    p_merge.add_argument("path")
    p_merge.add_argument("object")

    p_stale = sub.add_parser(
        "mark-stale", help="flag an entry as stale (needs re-collection)"
    )
    p_stale.add_argument("path")

    p_unstale = sub.add_parser(
        "unset-stale", help="clear the stale flag and refresh updated_at"
    )
    p_unstale.add_argument("path")

    p_touch = sub.add_parser("touch", help="refresh the updated_at timestamp")
    p_touch.add_argument("path")

    args = parser.parse_args()
    handlers = {
        "read": cmd_read,
        "get": cmd_get,
        "set": cmd_set,
        "add": cmd_add,
        "merge": cmd_merge,
        "mark-stale": cmd_mark_stale,
        "unset-stale": cmd_unset_stale,
        "touch": cmd_touch,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
