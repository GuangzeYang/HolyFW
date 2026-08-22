"""Environment variables and workspace paths for the Sysmon collector."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

try:
    from common import locate_holyfw_root, soldier_workspace_dir
except ImportError:  # ``python -m sysmon_collector`` from a raw checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from common import locate_holyfw_root, soldier_workspace_dir

HOLYFW_SYSMON = "HOLYFW_SYSMON"
SYSMON_ENV = "SYSMON"
HOLYFW_SYSMON_CONFIG = "HOLYFW_SYSMON_CONFIG"
HOLYFW_SYSMON_LOG_DIR = "HOLYFW_SYSMON_LOG_DIR"

SYSMON_CHANNEL = "Microsoft-Windows-Sysmon/Operational"
SECURITY_CHANNEL = "Security"
# Logon/Logoff + Account Logon (Kerberos/NTLM) — Event Viewer's logon/auth set.
SECURITY_LOGON_AUTH_EVENT_IDS = (
    4624,
    4625,
    4627,
    4634,
    4647,
    4648,
    4672,
    4768,
    4769,
    4770,
    4771,
    4772,
    4773,
    4776,
    4777,
    4778,
    4779,
    4800,
    4801,
    4802,
    4803,
    4964,
)
SECURITY_EVTX_PREFIX = "security_logon"
PID_FILE_NAME = "sysmon_collector.pid"
OBSERVE_FILE_NAME = "sysmon_observe.txt"
EVTX_DIR_NAME = "sysmon"
CONFIG_FILE_NAME = "sysmonconfig.xml"
SYSMON_EXE_CANDIDATES = ("Sysmon64.exe", "Sysmon.exe", "Sysmon64", "Sysmon")


def _workspace(base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        return Path(base_dir)
    return soldier_workspace_dir(package_hint=Path(__file__).resolve().parent)


def resolve_sysmon_exe() -> Path:
    """Resolve Sysmon from HOLYFW_SYSMON, then SYSMON, then PATH."""
    for key in (HOLYFW_SYSMON, SYSMON_ENV):
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"{key} is set but is not a file: {raw}")

    import shutil

    for name in SYSMON_EXE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    raise FileNotFoundError(
        "Sysmon executable not found. Set HOLYFW_SYSMON to Sysmon64.exe (or Sysmon.exe)."
    )


def resolve_sysmon_config() -> Path:
    raw = os.environ.get(HOLYFW_SYSMON_CONFIG, "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"{HOLYFW_SYSMON_CONFIG} is set but is not a file: {raw}")

    packaged = Path(__file__).resolve().parent / CONFIG_FILE_NAME
    if packaged.is_file():
        return packaged

    try:
        root = locate_holyfw_root(package_hint=Path(__file__).resolve().parent)
    except FileNotFoundError:
        root = None
    if root is not None:
        checkout = root / CONFIG_FILE_NAME
        if checkout.is_file():
            return checkout.resolve()

    raise FileNotFoundError(
        f"{CONFIG_FILE_NAME} not found. Set {HOLYFW_SYSMON_CONFIG} or keep the file in the HolyFW root."
    )


def evtx_dir(base_dir: Path | None = None) -> Path:
    raw = os.environ.get(HOLYFW_SYSMON_LOG_DIR, "").strip()
    if raw:
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
    path = _workspace(base_dir) / "logs" / EVTX_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def collector_logs_dir(base_dir: Path | None = None) -> Path:
    path = _workspace(base_dir) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def collector_log_path(logs_dir: Path, day: date | None = None) -> Path:
    stamp = (day or date.today()).isoformat()
    return logs_dir / f"sysmon_collector_{stamp}.log"


def evtx_path(out_dir: Path, day: date, prefix: str = "sysmon") -> Path:
    return out_dir / f"{prefix}_{day.isoformat()}.evtx"


def security_evtx_path(out_dir: Path, day: date) -> Path:
    return evtx_path(out_dir, day, prefix=SECURITY_EVTX_PREFIX)


def pid_file(base_dir: Path | None = None) -> Path:
    runtime = _workspace(base_dir) / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime / PID_FILE_NAME


def observe_stamp_file(base_dir: Path | None = None) -> Path:
    return pid_file(base_dir).with_name(OBSERVE_FILE_NAME)
