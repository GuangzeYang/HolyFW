"""Windows-safe subprocess helpers for ad-attack skill scripts.

Skill scripts run as `python scripts/<name>.py` from the skill root (and are
copied into ~/.config/opencode/skills/ad-attack/scripts). They import this
module from the same folder. Bytes are decoded with UTF-8 first, then GBK,
so `tshark -D` Chinese interface names do not crash the pre-flight check.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any


def decode_bytes(data: bytes | None) -> str:
    if not data:
        return ""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            shell=False,
        )
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"
    return int(proc.returncode), decode_bytes(proc.stdout), decode_bytes(proc.stderr)


def pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_i <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid_i
        )
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) == 0:
                return False
            return int(code.value) == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid_i, 0)
    except OSError:
        return False
    return True


def looks_like_access_denied(returncode: int, text: str) -> bool:
    if int(returncode) == 5:
        return True
    blob = text or ""
    low = blob.lower()
    if "access is denied" in low or "access denied" in low:
        return True
    if "拒绝访问" in blob:
        return True
    if "0x00000005" in low:
        return True
    return False


def local_admin_creds(state: dict[str, Any] | None) -> tuple[str, str] | None:
    """Return (username, password) from campaign.local_admin or is_local_admin users."""
    data = state if isinstance(state, dict) else {}
    campaign = data.get("campaign") if isinstance(data.get("campaign"), dict) else {}
    raw = campaign.get("local_admin") if isinstance(campaign.get("local_admin"), dict) else {}
    user = str(raw.get("username") or raw.get("user") or "").strip()
    password = str(raw.get("password") or "").strip()
    if user and password:
        return user, password
    users = data.get("users") if isinstance(data.get("users"), list) else []
    for entry in users:
        if not isinstance(entry, dict):
            continue
        if not entry.get("is_local_admin"):
            continue
        name = str(entry.get("username") or "").strip()
        pw = str(entry.get("password") or "").strip()
        if name and pw:
            return name, pw
    return None
