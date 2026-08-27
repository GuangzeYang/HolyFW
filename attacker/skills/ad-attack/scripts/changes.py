#!/usr/bin/env python3
"""Record target-environment mutations so an operator can revert them by hand.

Subcommands:
    read              Print the full changes document.
    add <object>      Append one change record. <object> is JSON.

This file does not revert Active Directory. It only keeps an audit list.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CHANGES_PATH = SKILL_ROOT / "changes.json"

KINDS = (
    "create_user",
    "create_machine_account",
    "reset_password",
    "rbcd",
    "dc_config",
    "other",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_document() -> dict:
    return {"schema_version": 1, "last_updated": "", "changes": []}


def _load() -> dict:
    if CHANGES_PATH.exists():
        try:
            data = json.loads(CHANGES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            if not isinstance(data.get("changes"), list):
                data["changes"] = []
            return data
    return _empty_document()


def _save(data: dict) -> None:
    CHANGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHANGES_PATH.with_name(CHANGES_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CHANGES_PATH)


def _parse_value(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            raise ValueError(
                "value looks like JSON but is not valid; re-quote the argument so it "
                "reaches python intact: " + raw
            )
        return raw


def _normalize_record(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("change record must be a JSON object")
    kind = str(raw.get("kind") or "other").strip() or "other"
    if kind not in KINDS:
        kind = "other"
    summary = str(raw.get("summary") or "").strip()
    if not summary:
        raise ValueError("change record requires summary")
    record = {
        "id": str(raw.get("id") or uuid.uuid4().hex[:16]),
        "recorded_at": str(raw.get("recorded_at") or _now_iso()),
        "technique_id": str(raw.get("technique_id") or ""),
        "kind": kind,
        "target": str(raw.get("target") or ""),
        "summary": summary,
        "reversal": str(raw.get("reversal") or ""),
        "details": raw.get("details") if isinstance(raw.get("details"), dict) else {},
    }
    return record


def cmd_read(_args: argparse.Namespace) -> int:
    print(json.dumps(_load(), ensure_ascii=False, indent=2))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    try:
        value = _parse_value(args.object)
        record = _normalize_record(value)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    data = _load()
    changes = data.setdefault("changes", [])
    if not isinstance(changes, list):
        changes = []
        data["changes"] = changes
    changes.append(record)
    data["last_updated"] = _now_iso()
    if "schema_version" not in data:
        data["schema_version"] = 1
    _save(data)
    print(json.dumps({"ok": True, "id": record["id"]}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record target-environment mutations for manual rollback"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read", help="print the full changes document")
    p_add = sub.add_parser("add", help="append one JSON change record")
    p_add.add_argument("object", help="JSON object with kind, summary, optional reversal")
    args = parser.parse_args()
    if args.command == "read":
        return cmd_read(args)
    if args.command == "add":
        return cmd_add(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
