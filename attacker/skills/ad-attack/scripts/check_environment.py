#!/usr/bin/env python3
"""Pre-flight validation for the ad-attack skill.

Run this before starting a day's attack activity. It verifies that every tool
required by the skill is installed and reachable, that the configured traffic
capture interface exists, and that the Sysmon log source is available.

Output is a single JSON object on stdout. Exit code 0 means every check passed;
any other exit code means at least one requirement failed. The `errors` /
`warnings` arrays are diagnostic so the agent can remediate (for example
elevate a blocked log export) and continue the dispatched technique — they are
not a hard stop.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib.util
import json
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import winproc  # noqa: E402


def check_admin() -> dict:
    """Detect whether the current process is elevated (Windows).

    This is a *reference* field only. Elevation does not guarantee log access
    (and SYSTEM can read the log without being in the Administrators group),
    so the authoritative gate is `check_log_readable`, which actually reads a
    record from the configured log.
    """
    if sys.platform != "win32":
        return {"is_admin": None}
    try:
        return {"is_admin": bool(ctypes.windll.shell32.IsUserAnAdmin())}
    except Exception as exc:  # pragma: no cover - defensive
        return {"is_admin": None, "error": str(exc)}


def check_log_readable(log_name: str) -> dict:
    """Actually read one record from a log to prove it can be exported.

    `wevtutil epl` (used by capture_logs.py stop) requires read access to the
    log. Group membership (Administrators, SYSTEM, Event Log Readers, Backup
    Operators, Server Operators) is an unreliable proxy, so probe the real
    capability with `wevtutil qe`.
    """
    rc, out, err = _run(["wevtutil", "qe", log_name, "/c:1", "/rd:true"], timeout=30)
    combined = (out + "\n" + err)
    combined_l = combined.lower()
    if rc == 0:
        return {"readable": True, "returncode": rc, "detail": ""}
    # An empty but accessible log can return non-zero with "no events"; that is
    # still readable for export purposes. Match English and Chinese wevtutil.
    empty_markers = ("no events", "0 events", "没有事件", "无事件", "找不到任何")
    if any(marker in combined_l or marker in combined for marker in empty_markers):
        return {"readable": True, "returncode": rc, "detail": "empty log (accessible)"}
    return {
        "readable": False,
        "returncode": rc,
        "detail": (err or out or "").strip()[:200],
    }


AUDIT_GUIDS = {
    "logon": "{0CCE9215-69AE-11D9-BED3-505054503030}",
    "logoff": "{0CCE9216-69AE-11D9-BED3-505054503030}",
    "account_lockout": "{0CCE9217-69AE-11D9-BED3-505054503030}",
    "special_logon": "{0CCE921B-69AE-11D9-BED3-505054503030}",
    "other_logon_logoff": "{0CCE921C-69AE-11D9-BED3-505054503030}",
    "credential_validation": "{0CCE923F-69AE-11D9-BED3-505054503030}",
    "kerberos_service_ticket": "{0CCE9240-69AE-11D9-BED3-505054503030}",
    "kerberos_auth_service": "{0CCE9242-69AE-11D9-BED3-505054503030}",
}

AUDIT_EXPECTED = {
    "logon": "成功和失败",
    "logoff": "成功",
    "account_lockout": "成功和失败",
    "special_logon": "成功",
    "other_logon_logoff": "成功和失败",
    "credential_validation": "成功和失败",
    "kerberos_service_ticket": "成功和失败",
    "kerberos_auth_service": "成功和失败",
}

AUDIT_EVENT_IDS = {
    "logon": "4624/4625",
    "logoff": "4634",
    "account_lockout": "4740",
    "special_logon": "4672",
    "other_logon_logoff": "4648",
    "credential_validation": "4776",
    "kerberos_service_ticket": "4769",
    "kerberos_auth_service": "4768",
}


def check_security_auditing() -> dict:
    """Report whether the key logon/authentication audit subcategories are on.

    Queries each subcategory by GUID (ASCII — no locale/encoding issues) via
    `auditpol /get /subcategory:<GUID>`, which needs elevation. The localized
    state string is matched by its numeric value: "成功和失败"/success+failure
    is the required state for most; "成功"/success for logoff/special-logon.
    """
    subcats: dict[str, dict] = {}
    for key, guid in AUDIT_GUIDS.items():
        entry = {"guid": guid, "expected": AUDIT_EXPECTED[key], "event_ids": AUDIT_EVENT_IDS[key]}
        rc, out, err = _run(["auditpol", "/get", "/subcategory:" + guid], timeout=20)
        text = (out + "\n" + err)
        if rc != 0:
            entry["status"] = "unknown"
            entry["detail"] = (err or out or "").strip()[:160]
            subcats[key] = entry
            continue
        # auditpol prints a single subcategory line: <name>   <state>
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            entry["status"] = "unknown"
            entry["detail"] = "no output"
            subcats[key] = entry
            continue
        last = lines[-1]
        last_l = last.lower()
        both = ("成功和失败", "success and failure")
        success_only = key in ("logoff", "special_logon")
        if any(m in last or m in last_l for m in both):
            entry["status"] = "enabled"
        elif success_only and (
            "成功" in last
            or ("success" in last_l and "failure" not in last_l)
        ):
            entry["status"] = "enabled"
        else:
            entry["status"] = "disabled"
        entry["detail"] = last
        subcats[key] = entry

    enabled_count = sum(1 for v in subcats.values() if v.get("status") == "enabled")
    return {
        "available": True,
        "enabled_count": enabled_count,
        "total": len(subcats),
        "subcategories": subcats,
    }

REQUIRED_EXECUTABLES = [
    "nmap",
    "kerbrute",
    "tshark",
    "wevtutil",
]

IMPACKET_BASE_SCRIPTS = [
    "secretsdump",
    "GetADUsers",
    "lookupsid",
    "GetNPUsers",
    "GetUserSPNs",
    "Get-GPPPassword",
    "getTGT",
    "getST",
    "ticketer",
    "findDelegation",
    "psexec",
    "wmiexec",
    "smbexec",
    "atexec",
    "dcomexec",
    "smbclient",
    "addcomputer",
    "rbcd",
    "smbpasswd",
]


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    return {}


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    return winproc.run(args, timeout=timeout)


def check_impacket() -> dict:
    importable = False
    impacket_file = None
    try:
        import impacket  # noqa: F401

        importable = True
        impacket_file = getattr(impacket, "__file__", None)
    except Exception:
        importable = False

    modules_found: list[str] = []
    modules_missing: list[str] = []
    for base in IMPACKET_BASE_SCRIPTS:
        if importlib.util.find_spec(f"impacket.examples.{base}") is not None:
            modules_found.append(base)
        else:
            modules_missing.append(base)

    path_scripts: dict[str, str] = {}
    for base in IMPACKET_BASE_SCRIPTS:
        for candidate in (f"impacket-{base}", f"{base}.py", base):
            resolved = _which(candidate)
            if resolved:
                path_scripts[base] = candidate
                break

    return {
        "importable": importable,
        "impacket_file": impacket_file,
        "modules_found": modules_found,
        "modules_missing": modules_missing,
        "path_scripts": path_scripts,
        "naming": _detect_naming(path_scripts),
    }


def check_impacket_runnable(python_exe: str) -> dict:
    """Run `python -m impacket.examples.secretsdump --help` and report whether it executes."""
    rc, out, err = _run(
        [python_exe, "-m", "impacket.examples.secretsdump", "--help"], timeout=60
    )
    return {
        "python": python_exe,
        "returncode": rc,
        "ran": rc == 0,
        "stdout_head": (out or "")[:200],
        "stderr_head": (err or "")[:200],
    }


def _detect_naming(path_scripts: dict[str, str]) -> str:
    if not path_scripts:
        return "none"
    counts: dict[str, int] = {"impacket-": 0, ".py": 0, "bare": 0}
    for candidate in path_scripts.values():
        if candidate.startswith("impacket-"):
            counts["impacket-"] += 1
        elif candidate.endswith(".py"):
            counts[".py"] += 1
        else:
            counts["bare"] += 1
    best = "none"
    best_count = -1
    for form, count in counts.items():
        if count > best_count:
            best = form
            best_count = count
    return best


def check_executables() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in REQUIRED_EXECUTABLES:
        result[name] = _which(name)
    return result


def check_tshark_interfaces(tshark_path: str | None) -> dict:
    if not tshark_path:
        return {"interfaces": [], "returncode": -1, "stderr": ""}
    rc, out, err = _run([tshark_path, "-D"])
    interfaces = [line.strip() for line in out.splitlines() if line.strip()]
    return {
        "interfaces": interfaces,
        "returncode": rc,
        "stderr": (err or "")[:500],
    }


def check_sysmon() -> dict:
    rc, out, _ = _run(["wevtutil", "el"])
    sysmon_logs = [ln.strip() for ln in out.splitlines() if "sysmon" in ln.lower()]

    svc_rc, _, _ = _run(["sc", "query", "Sysmon"])
    svc_rc64, _, _ = _run(["sc", "query", "Sysmon64"])
    service_running = svc_rc == 0 or svc_rc64 == 0

    return {
        "logs_found": sysmon_logs,
        "service_running": service_running,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the ad-attack environment")
    parser.add_argument(
        "--json", action="store_true", help="always print JSON (default)"
    )
    args = parser.parse_args()

    config = load_config()
    traffic_cfg = config.get("traffic", {})
    logs_cfg = config.get("logs", {})
    configured_iface = traffic_cfg.get("interface", "")
    configured_log = logs_cfg.get("sysmon_log", "Microsoft-Windows-Sysmon/Operational")
    configured_security_log = logs_cfg.get("security_log") or None
    configured_python = (config.get("python") or "python").strip()

    executables = check_executables()
    impacket = check_impacket()
    tshark_probe = check_tshark_interfaces(executables.get("tshark"))
    tshark_interfaces = tshark_probe.get("interfaces") or []
    sysmon = check_sysmon()

    errors: list[str] = []
    warnings: list[str] = []

    for name, path in executables.items():
        if not path:
            errors.append(f"executable not found on PATH: {name}")

    if not impacket["importable"] and not impacket["modules_found"]:
        errors.append(
            "impacket package is not importable and no impacket example modules were found"
        )
    elif impacket["modules_missing"]:
        warnings.append(
            "impacket example modules missing: "
            + ", ".join(impacket["modules_missing"])
        )

    # Hard gate: actually run `python -m impacket.examples.secretsdump --help`
    # using the same interpreter that runs this script (== the agent's `python`).
    runnable = check_impacket_runnable(sys.executable)
    if not runnable["ran"]:
        errors.append(
            f"impacket is not runnable via `{sys.executable} -m impacket.examples.secretsdump` "
            f"(returncode={runnable['returncode']}); impacket must be installed into the same "
            f"interpreter the soldier uses. stderr: {runnable['stderr_head']}"
        )

    # If a specific interpreter is pinned in config.json, verify it too.
    configured_runnable = None
    if configured_python and configured_python.lower() != sys.executable.lower():
        configured_runnable = check_impacket_runnable(configured_python)
        if not configured_runnable["ran"]:
            errors.append(
                f"configured python '{configured_python}' cannot run impacket "
                f"(returncode={configured_runnable['returncode']})"
            )
        else:
            warnings.append(
                f"configured python '{configured_python}' differs from the running "
                f"interpreter '{sys.executable}'; ensure the soldier resolves `python` "
                f"to the interpreter that has impacket"
            )

    if executables.get("tshark"):
        if configured_iface:
            matched = any(configured_iface in iface for iface in tshark_interfaces)
            if not matched:
                errors.append(
                    f"configured traffic interface '{configured_iface}' not found in tshark -D output"
                )
        if not tshark_interfaces:
            stderr_bit = (tshark_probe.get("stderr") or "").strip()
            extra = f" (tshark -D rc={tshark_probe.get('returncode')}"
            extra += f"; stderr: {stderr_bit[:200]})" if stderr_bit else ")"
            errors.append("tshark reported no capture interfaces" + extra)

    if not sysmon["logs_found"]:
        errors.append("no Sysmon event log found via wevtutil el")
    elif configured_log not in sysmon["logs_found"]:
        errors.append(
            f"configured sysmon log '{configured_log}' not found; found: {sysmon['logs_found']}"
        )
    if not sysmon["service_running"]:
        errors.append("Sysmon service is not running (logs may still be present)")

    admin = check_admin()

    # Per-channel readability for every channel capture_logs.py will export.
    channels = [configured_log]
    if configured_security_log and configured_security_log not in channels:
        channels.append(configured_security_log)
    channels_readable: dict[str, dict] = {}
    for ch in channels:
        channels_readable[ch] = check_log_readable(ch)
    all_readable = all(cr["readable"] for cr in channels_readable.values())
    if not all_readable:
        bad = [ch for ch, cr in channels_readable.items() if not cr["readable"]]
        errors.append(
            f"not all capture log channels are readable: {bad}; "
            f"`wevtutil epl` export (capture_logs.py stop) will fail — run the "
            f"agent elevated or as a log-reader"
        )

    # Logon / authentication audit policy status (informational; not a gate).
    auditing = check_security_auditing()
    if auditing.get("available"):
        disabled = [
            k for k, v in auditing["subcategories"].items()
            if v.get("status") == "disabled"
        ]
        if disabled:
            warnings.append(
                "audit subcategories not fully enabled: "
                + ", ".join(f"{k} (produces {AUDIT_EVENT_IDS[k]})" for k in disabled)
                + " — enable them with auditpol to capture logon/Kerberos events"
            )
    else:
        warnings.append(
            "could not read audit policy (auditpol needs elevation); logon/Kerberos "
            "audit status unknown"
        )

    report = {
        "ok": not errors,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "configured_python": configured_python,
        "impacket": impacket,
        "impacket_runnable": runnable,
        "configured_python_runnable": configured_runnable,
        "executables": executables,
        "sysmon": sysmon,
        "admin": admin,
        "log_readable": channels_readable[configured_log],
        "channels_readable": channels_readable,
        "auditing": auditing,
        "tshark_interfaces": tshark_interfaces,
        "tshark_interfaces_returncode": tshark_probe.get("returncode"),
        "tshark_interfaces_stderr": tshark_probe.get("stderr") or "",
        "configured_traffic_interface": configured_iface,
        "configured_sysmon_log": configured_log,
        "configured_security_log": configured_security_log,
        "errors": errors,
        "warnings": warnings,
    }

    payload = json.dumps(report, ensure_ascii=False, indent=2)
    try:
        print(payload)
    except UnicodeEncodeError:
        print(payload.encode("utf-8", errors="replace").decode("ascii", errors="replace"))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
