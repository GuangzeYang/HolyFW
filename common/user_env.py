"""Read and write Windows user-level environment variables (create if missing)."""

from __future__ import annotations

import os

_USER_ENV_KEY = "Environment"


def get_user_env(name: str) -> str:
    """Return a user/process environment value. Process env wins, then HKCU."""
    key = (name or "").strip()
    if not key:
        return ""
    current = os.environ.get(key, "").strip()
    if current:
        return current
    if os.name != "nt":
        return ""
    return _read_hkcu(key)


def set_user_env(name: str, value: str) -> None:
    """Create or overwrite a user-level environment variable and the current process."""
    key = (name or "").strip()
    if not key:
        raise ValueError("environment variable name is empty")
    text = str(value)
    os.environ[key] = text
    if os.name != "nt":
        return
    _write_hkcu(key, text)
    _broadcast_setting_change()


def _read_hkcu(name: str) -> str:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _USER_ENV_KEY) as handle:
            raw, _typ = winreg.QueryValueEx(handle, name)
    except OSError:
        return ""
    if raw is None:
        return ""
    return str(raw).strip()


def _write_hkcu(name: str, value: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _USER_ENV_KEY) as handle:
        winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)


def _broadcast_setting_change() -> None:
    import ctypes

    hwnd_broadcast = 0xFFFF
    wm_settingchange = 0x001A
    smto_abortifhung = 0x0002
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(
        hwnd_broadcast,
        wm_settingchange,
        0,
        "Environment",
        smto_abortifhung,
        5000,
        ctypes.byref(result),
    )
