#!/usr/bin/env python3
"""Pre-flight validation for the ad-attack skill.

Run this before starting a day's attack activity. It verifies that every tool
required by the skill is installed and reachable, that the configured traffic
capture interface exists, and that the Sysmon log source is available.

Output is a single JSON object on stdout. Exit code 0 means the environment is
ready; any other exit code means at least one requirement is missing and attack
actions MUST NOT be started.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_ROOT / "config.json"

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
    "getTGT",
    "getST",
    "ticketer",
    "findDelegation",
    "psexec",
    "wmiexec",
    "smbexec",
    "atexec",
    "dcomexec",
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
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return proc.returncode, (proc.stdout or ""), (proc.stderr or "")
    except FileNotFoundError:
        return -1, "", "command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "command timed out"


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


def check_tshark_interfaces(tshark_path: str | None) -> list[str]:
    if not tshark_path:
        return []
    rc, out, _ = _run([tshark_path, "-D"])
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


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
    configured_python = (config.get("python") or "python").strip()

    executables = check_executables()
    impacket = check_impacket()
    tshark_interfaces = check_tshark_interfaces(executables.get("tshark"))
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
            warnings.append(
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
                warnings.append(
                    f"configured traffic interface '{configured_iface}' not found in tshark -D output"
                )
        if not tshark_interfaces:
            warnings.append("tshark reported no capture interfaces")

    if not sysmon["logs_found"]:
        errors.append("no Sysmon event log found via wevtutil el")
    elif configured_log not in sysmon["logs_found"]:
        warnings.append(
            f"configured sysmon log '{configured_log}' not found; found: {sysmon['logs_found']}"
        )
    if not sysmon["service_running"]:
        warnings.append("Sysmon service is not running (logs may still be present)")

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
        "tshark_interfaces": tshark_interfaces,
        "configured_traffic_interface": configured_iface,
        "configured_sysmon_log": configured_log,
        "errors": errors,
        "warnings": warnings,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
