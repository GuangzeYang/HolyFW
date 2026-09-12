"""Output filenames and transcript discovery for dataset extract."""

from __future__ import annotations

from pathlib import Path

TASK_ID_ENV = "HOLYFW_ATTACKER_TASK_ID"


def safe_token(value: str, fallback: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(value or ""))
    return cleaned or fallback


def technique_pcap_stem(task_id: str, technique: str) -> str:
    tokens: list[str] = []
    tid = str(task_id or "").strip()
    if tid:
        tokens.append(safe_token(tid, "task"))
    tokens.append(safe_token(technique, "capture"))
    return "_".join(tokens)


def list_task_transcripts(day_dir: Path) -> list[Path]:
    if not day_dir.is_dir():
        return []
    return sorted(path for path in day_dir.glob("*.md") if path.is_file())
