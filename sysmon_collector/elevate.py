"""Privilege helpers for the manual Sysmon collector."""

from __future__ import annotations

import os


def is_elevated() -> bool:
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
