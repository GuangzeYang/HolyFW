#!/usr/bin/env python3
"""Non-homogeneous Poisson time model for daily role schedules.

Thesis section 3.4: dual-Gaussian intensity, lunch-window mask, AR(1) busyness.
``tasks_per_role`` calibrates E[N] = ∫λ(t) dt. Sampling draws the time-node list
first; the realized length is counted afterwards and sent to the LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from common import _in_work_window, _next_work_minute, assign_task_id, minute_to_hhmm, parse_hhmm_to_minute

DAY_START_MINUTE = 9 * 60
DAY_END_MINUTE = 18 * 60
LUNCH_START_MINUTE = 12 * 60
LUNCH_END_MINUTE = 13 * 60
# Right endpoints t of [t-30, t). 09:00 is the empty pre-start bin; 18:30 holds 18:00.
STATISTIC_BIN_RIGHT_EDGES = tuple(range(DAY_START_MINUTE, DAY_END_MINUTE + 31, 30))


@dataclass(frozen=True, slots=True)
class TimeModelConfig:
    mu_am_minutes: float = 10 * 60 + 30
    mu_pm_minutes: float = 15 * 60
    sigma_am_minutes: float = 50.0
    sigma_pm_minutes: float = 65.0
    a_am: float = 1.0
    a_pm: float = 1.0
    phi: float = 0.85
    sigma_eta: float = 0.18
    avoid_five_minutes: bool = True

    def __post_init__(self) -> None:
        if self.sigma_am_minutes <= 0 or self.sigma_pm_minutes <= 0:
            raise ValueError("Time-model sigma_*_minutes must be > 0")
        if abs(self.phi) >= 1:
            raise ValueError("Time-model phi must satisfy |phi| < 1")
        if self.sigma_eta <= 0:
            raise ValueError("Time-model sigma_eta must be > 0")

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> TimeModelConfig:
        if not data:
            raise ValueError("Time-model mapping is required; use config.json generator.time_model defaults")
        return cls(
            mu_am_minutes=float(data["mu_am_minutes"]),
            mu_pm_minutes=float(data["mu_pm_minutes"]),
            sigma_am_minutes=float(data["sigma_am_minutes"]),
            sigma_pm_minutes=float(data["sigma_pm_minutes"]),
            a_am=float(data["a_am"]),
            a_pm=float(data["a_pm"]),
            phi=float(data["phi"]),
            sigma_eta=float(data["sigma_eta"]),
            avoid_five_minutes=bool(data["avoid_five_minutes"]),
        )


def _window_weight(minute: int) -> float:
    if minute < DAY_START_MINUTE or minute > DAY_END_MINUTE:
        return 0.0
    if LUNCH_START_MINUTE < minute < LUNCH_END_MINUTE:
        return 0.0
    return 1.0 if _in_work_window(minute) else 0.0


def _gaussian(minute: float, mu: float, sigma: float, amplitude: float) -> float:
    if sigma <= 0:
        return 0.0
    delta = (minute - mu) / sigma
    return amplitude * math.exp(-0.5 * delta * delta)


def _base_intensity(minute: float, config: TimeModelConfig) -> float:
    morning = _gaussian(minute, config.mu_am_minutes, config.sigma_am_minutes, config.a_am)
    afternoon = _gaussian(minute, config.mu_pm_minutes, config.sigma_pm_minutes, config.a_pm)
    return morning + afternoon


def _seed_for(role: str, day: date | str | None) -> int:
    if isinstance(day, date):
        day_text = day.isoformat()
    elif isinstance(day, str) and day.strip():
        day_text = day.strip()
    else:
        day_text = date.today().isoformat()
    payload = f"{role.strip().lower()}|{day_text}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return int(digest[:16], 16)


class _GaussianRng:
    """Deterministic Box-Muller generator."""

    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFFFFFFFFFF

    def _uniform(self) -> float:
        self._state = (6364136223846793005 * self._state + 1) & 0xFFFFFFFFFFFFFFFF
        return ((self._state >> 11) & 0x1FFFFFFFFFFFFF) / float(1 << 53)

    def normal(self) -> float:
        u1 = max(self._uniform(), 1e-12)
        u2 = self._uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _ar1_path(length: int, config: TimeModelConfig, rng: _GaussianRng) -> list[float]:
    phi = config.phi
    if abs(phi) >= 1:
        phi = math.copysign(0.99, phi) if phi else 0.0
    stationary_sigma = config.sigma_eta / math.sqrt(max(1e-12, 1.0 - phi * phi))
    eps = rng.normal() * stationary_sigma
    path = [eps]
    for _ in range(1, length):
        eps = phi * eps + config.sigma_eta * rng.normal()
        path.append(eps)
    return path


def _grid_minutes() -> list[int]:
    return list(range(DAY_START_MINUTE, DAY_END_MINUTE + 1))


def intensity_grid(config: TimeModelConfig, rng: _GaussianRng) -> tuple[list[int], list[float]]:
    minutes = _grid_minutes()
    eps = _ar1_path(len(minutes), config, rng)
    raw = []
    for minute, shock in zip(minutes, eps):
        weight = _window_weight(minute)
        value = weight * _base_intensity(float(minute), config) * math.exp(shock)
        raw.append(value)
    return minutes, raw


def _calibrate_expected_count(raw: Sequence[float], expected_count: int) -> list[float]:
    """Scale minute intensities so sum(λ) equals the expected daily count."""
    total = sum(raw)
    if total <= 0:
        positive = [1.0 if value > 0 else 0.0 for value in raw]
        total = sum(positive)
        raw = positive
    if total <= 0:
        return [0.0] * len(raw)
    scale = float(expected_count) / total
    return [value * scale for value in raw]


def _poisson(rng: _GaussianRng, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam < 20:
        limit = math.exp(-lam)
        count = 0
        product = 1.0
        while True:
            count += 1
            product *= max(rng._uniform(), 1e-16)
            if product <= limit:
                return count - 1
    value = rng.normal() * math.sqrt(lam) + lam
    return max(0, int(math.floor(value + 0.5)))


def _next_distinct_work_minute(value: int, previous: int | None, avoid_five: bool) -> int | None:
    current = _next_work_minute(value)
    while current is not None:
        if previous is not None and current <= previous:
            current = _next_work_minute(previous + 1)
            continue
        if avoid_five and current % 5 == 0:
            current = _next_work_minute(current + 1)
            continue
        return current
    return None


def sample_schedule_minutes(
    expected_count: int,
    *,
    role: str = "role",
    day: date | str | None = None,
    config: TimeModelConfig | None = None,
    seed: int | None = None,
    max_count: int | None = None,
) -> list[int]:
    """Sample work-window minutes from the NHPP. Length is random around ``expected_count``."""
    if expected_count <= 0:
        return []
    model = config or TimeModelConfig()
    rng = _GaussianRng(seed if seed is not None else _seed_for(role, day))
    minutes, raw = intensity_grid(model, rng)
    rates = _calibrate_expected_count(raw, expected_count)
    chosen: list[int] = []
    for minute, rate in zip(minutes, rates):
        if rate <= 0:
            continue
        for _ in range(_poisson(rng, rate)):
            nxt = _next_distinct_work_minute(
                minute,
                chosen[-1] if chosen else None,
                model.avoid_five_minutes,
            )
            if nxt is None:
                return chosen
            chosen.append(nxt)
            if max_count is not None and len(chosen) >= max_count:
                return chosen
    if chosen:
        return chosen
    nxt = _next_distinct_work_minute(DAY_START_MINUTE, None, model.avoid_five_minutes)
    return [nxt] if nxt is not None else []


def generate_schedule(
    expected_count: int,
    *,
    role: str = "role",
    day: date | str | None = None,
    config: TimeModelConfig | dict[str, Any] | None = None,
    seed: int | None = None,
    max_count: int | None = None,
) -> list[str]:
    """Return HH:MM strings sampled from the NHPP (variable length)."""
    if isinstance(config, TimeModelConfig):
        model = config
    elif config is None:
        model = TimeModelConfig()
    else:
        model = TimeModelConfig.from_mapping(config)
    minutes = sample_schedule_minutes(
        expected_count,
        role=role,
        day=day,
        config=model,
        seed=seed,
        max_count=max_count,
    )
    return [minute_to_hhmm(value) for value in minutes]


def zip_tasks_with_schedule(
    tasks: list[dict[str, Any]],
    schedule: Sequence[str],
) -> list[dict[str, Any]]:
    """Attach algorithm times onto LLM task bodies, preserving order."""
    if len(tasks) != len(schedule):
        raise ValueError(f"Task count {len(tasks)} does not match schedule length {len(schedule)}")
    zipped: list[dict[str, Any]] = []
    for item, time_text in zip(tasks, schedule):
        if not isinstance(item, dict):
            raise ValueError("Each generated task must be an object")
        row = dict(item)
        row["time"] = time_text
        if "is_load" not in row:
            row["is_load"] = False
        assign_task_id(row)
        zipped.append(row)
    return zipped


def half_hour_right_edge(minute: int) -> int:
    """Return t such that ``minute`` lies in [t-30, t)."""
    return (int(minute) // 30 + 1) * 30


def bin_times_half_hour(times: Sequence[str]) -> dict[str, int]:
    """Count timestamps into 30-minute bins labeled by the right endpoint t."""
    counts = {minute_to_hhmm(edge): 0 for edge in STATISTIC_BIN_RIGHT_EDGES}
    for text in times:
        minute = parse_hhmm_to_minute(text)
        if minute is None:
            continue
        key = minute_to_hhmm(half_hour_right_edge(minute))
        if key in counts:
            counts[key] += 1
    return counts


def _morning_afternoon_counts(times: Sequence[str]) -> tuple[int, int]:
    morning = 0
    afternoon = 0
    for text in times:
        minute = parse_hhmm_to_minute(text)
        if minute is None:
            continue
        if minute <= LUNCH_START_MINUTE:
            morning += 1
        elif minute >= LUNCH_END_MINUTE:
            afternoon += 1
    return morning, afternoon


def write_statistic_chart(
    series: dict[str, dict[str, int]],
    *,
    day: str,
    output_path: Path,
) -> None:
    """Draw one polyline per role. Point t is the count in [t-30, t)."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is required for --statistic") from exc

    labels = [minute_to_hhmm(edge) for edge in STATISTIC_BIN_RIGHT_EDGES]
    markers = ("o", "s", "D", "^", "v", "P")
    fig, ax = plt.subplots(figsize=(12, 5.5), dpi=140)
    for index, (role, counts) in enumerate(series.items()):
        ys = [counts.get(label, 0) for label in labels]
        ax.plot(labels, ys, marker=markers[index % len(markers)], linewidth=1.8, label=role)
    ax.set_xlabel("t  (count in [t-30, t))")
    ax.set_ylabel("Task count")
    ax.set_title(f"NHPP schedules ({day}), 30-minute bins")
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend()
    plt.xticks(rotation=45, ha="right")
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)


def _statistic_output_dir(raw: str) -> Path:
    text = raw.strip()
    if text:
        return Path(text).expanduser().resolve()
    try:
        from common import locate_holyfw_root

        return locate_holyfw_root(package_hint=Path(__file__).resolve().parent)
    except FileNotFoundError:
        return Path.cwd().resolve()


def schedules_from_role_tasks(
    data: dict[str, Any],
    roles: Sequence[str],
) -> dict[str, list[str]] | None:
    """Extract HH:MM lists from a persisted role-task file. None if any role is incomplete."""
    schedules: dict[str, list[str]] = {}
    for role in roles:
        tasks = data.get(role)
        if not isinstance(tasks, list) or not tasks:
            return None
        times: list[str] = []
        for item in tasks:
            if not isinstance(item, dict):
                return None
            time_text = item.get("time")
            if not isinstance(time_text, str) or not time_text.strip():
                return None
            times.append(time_text.strip())
        schedules[role] = times
    return schedules


def write_role_schedule_statistics(
    schedules: dict[str, Sequence[str]],
    *,
    day: str,
    output_dir: Path,
    expected_counts: dict[str, int] | None = None,
    max_count: int | None = None,
) -> dict[str, Any]:
    """Write JSON + PNG for already sampled (or persisted) time-node lists."""
    payload: dict[str, Any] = {
        "day": day,
        "max_count": max_count,
        "roles": {},
    }
    series: dict[str, dict[str, int]] = {}
    for role, times in schedules.items():
        time_list = [str(item) for item in times]
        half_hour = bin_times_half_hour(time_list)
        morning, afternoon = _morning_afternoon_counts(time_list)
        expected = expected_counts.get(role) if expected_counts else None
        payload["roles"][role] = {
            "expected": expected if expected is not None else len(time_list),
            "count": len(time_list),
            "morning": morning,
            "afternoon": afternoon,
            "times": time_list,
            "half_hour_interval": "[t-30, t)",
            "half_hour": half_hour,
        }
        series[role] = half_hour

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "role_schedule_times.json"
    png_path = output_dir / "role_schedule_30min.png"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_statistic_chart(series, day=day, output_path=png_path)
    payload["json_path"] = str(json_path)
    payload["png_path"] = str(png_path)
    return payload


def write_role_schedule_statistics_from_tasks(
    data: dict[str, Any],
    *,
    roles: Sequence[str],
    day: str,
    output_dir: Path,
    expected_counts: dict[str, int] | None = None,
    max_count: int | None = None,
) -> dict[str, Any]:
    schedules = schedules_from_role_tasks(data, roles)
    if schedules is None:
        raise ValueError("Role task file is incomplete; cannot write schedule statistics")
    return write_role_schedule_statistics(
        schedules,
        day=day,
        output_dir=output_dir,
        expected_counts=expected_counts,
        max_count=max_count,
    )


def run_statistic(
    *,
    day: str | None,
    output_dir: Path,
    seed: int | None = None,
) -> dict[str, Any]:
    """Sample every daily-generation role and write list + chart artifacts."""
    try:
        from runtime_config import (
            WORKDAY_MINUTES,
            get_generator_config,
            get_paths_config,
            load_runtime_config,
            resolve_config_relative_path,
        )
        from target_config import load_daily_generation_roles, load_role_time_model
    except ImportError:
        from commander.runtime_config import (
            WORKDAY_MINUTES,
            get_generator_config,
            get_paths_config,
            load_runtime_config,
            resolve_config_relative_path,
        )
        from commander.target_config import load_daily_generation_roles, load_role_time_model

    runtime_config = load_runtime_config()
    generator = get_generator_config(runtime_config)
    defaults = generator["time_model"]
    target_ini_path = resolve_config_relative_path(get_paths_config(runtime_config)["target_ini_file"])
    roles = load_daily_generation_roles(target_ini_path)
    min_internal = int(generator["min_internal"])
    max_count = max(1, WORKDAY_MINUTES // min_internal)
    day_text = day.strip() if isinstance(day, str) and day.strip() else date.today().isoformat()

    schedules: dict[str, list[str]] = {}
    expected_counts: dict[str, int] = {}
    for role in roles:
        mapping = load_role_time_model(target_ini_path, role, defaults)
        expected = int(mapping["tasks_per_role"])
        expected_counts[role] = expected
        schedules[role] = generate_schedule(
            expected,
            role=role,
            day=day_text,
            seed=seed,
            config=mapping,
            max_count=max_count,
        )
    return write_role_schedule_statistics(
        schedules,
        day=day_text,
        output_dir=output_dir,
        expected_counts=expected_counts,
        max_count=max_count,
    )


def format_statistic_report(payload: dict[str, Any]) -> str:
    lines = [
        f"DAY={payload['day']} MAX_COUNT={payload['max_count']}",
        "BIN=[t-30, t) labeled at t",
        "---",
    ]
    roles = payload.get("roles", {})
    if isinstance(roles, dict):
        for role, row in roles.items():
            if not isinstance(row, dict):
                continue
            times = row.get("times") or []
            half_hour = row.get("half_hour") or {}
            lines.append(
                f"ROLE={role} EXPECTED={row.get('expected')} COUNT={row.get('count')} "
                f"AM={row.get('morning')} PM={row.get('afternoon')}"
            )
            lines.append("TIMES=" + ",".join(str(item) for item in times))
            if isinstance(half_hour, dict):
                lines.append("HALF_HOUR=" + ",".join(f"{key}:{value}" for key, value in half_hour.items()))
            lines.append("---")
    lines.append(f"JSON={payload.get('json_path')}")
    lines.append(f"PNG={payload.get('png_path')}")
    return "\n".join(lines)


def _print_statistic(payload: dict[str, Any]) -> None:
    print(format_statistic_report(payload))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a HolyFW daily time schedule.")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Expected daily task count (intensity mean). Realized length is random.",
    )
    parser.add_argument("--role", default="role", help="Role name used for seeding")
    parser.add_argument("--date", default="", help="YYYY-MM-DD used for seeding (default: today)")
    parser.add_argument("--seed", type=int, default=None, help="Override the role/date seed")
    parser.add_argument("--json", action="store_true", help="Print a JSON array instead of one time per line")
    parser.add_argument(
        "--statistic",
        action="store_true",
        help="Sample every office role, print time lists, and write a 30-minute bin chart.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Directory for --statistic artifacts (default: repository root).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    day = args.date.strip() or None
    if args.statistic:
        payload = run_statistic(day=day, output_dir=_statistic_output_dir(args.output_dir), seed=args.seed)
        _print_statistic(payload)
        return 0
    if args.count is None or args.count <= 0:
        print("count must be > 0 (or pass --statistic)", file=sys.stderr)
        return 2
    try:
        from runtime_config import get_generator_config, get_paths_config, load_runtime_config, resolve_config_relative_path
        from target_config import load_role_time_model
    except ImportError:
        from commander.runtime_config import (
            get_generator_config,
            get_paths_config,
            load_runtime_config,
            resolve_config_relative_path,
        )
        from commander.target_config import load_role_time_model

    runtime_config = load_runtime_config()
    defaults = get_generator_config(runtime_config)["time_model"]
    target_ini_path = resolve_config_relative_path(get_paths_config(runtime_config)["target_ini_file"])
    mapping = load_role_time_model(target_ini_path, args.role, defaults)
    times = generate_schedule(args.count, role=args.role, day=day, seed=args.seed, config=mapping)
    if args.json:
        print(json.dumps(times, ensure_ascii=False))
    else:
        for item in times:
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
