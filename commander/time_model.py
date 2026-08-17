#!/usr/bin/env python3
"""Non-homogeneous Poisson time model for daily role schedules.

Thesis section 3.4: dual-Gaussian intensity, lunch-window mask, AR(1) busyness,
normalization to a prescribed count N, then time-transformation sampling.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import _in_work_window, _next_work_minute, minute_to_hhmm

DAY_START_MINUTE = 9 * 60
DAY_END_MINUTE = 18 * 60
LUNCH_START_MINUTE = 12 * 60
LUNCH_END_MINUTE = 13 * 60


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


def _normalized_intensity(raw: Sequence[float], count: int) -> list[float]:
    total = sum(raw)
    if total <= 0:
        positive = [1.0 if value > 0 else 0.0 for value in raw]
        total = sum(positive)
        raw = positive
    if total <= 0:
        return [0.0] * len(raw)
    scale = float(count) / total
    return [value * scale for value in raw]


def _invert_cumulative(cum: Sequence[float], target: float) -> int:
    lo = 0
    hi = len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo


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
    count: int,
    *,
    role: str = "role",
    day: date | str | None = None,
    config: TimeModelConfig | None = None,
    seed: int | None = None,
) -> list[int]:
    """Return ``count`` strictly increasing work-window minutes."""
    if count <= 0:
        return []
    model = config or TimeModelConfig()
    rng = _GaussianRng(seed if seed is not None else _seed_for(role, day))
    minutes, raw = intensity_grid(model, rng)
    lam = _normalized_intensity(raw, count)
    cum: list[float] = []
    running = 0.0
    for value in lam:
        running += value
        cum.append(running)
    total = cum[-1] if cum else 0.0
    if total <= 0:
        fallback: list[int] = []
        cursor = DAY_START_MINUTE
        while len(fallback) < count:
            nxt = _next_distinct_work_minute(cursor, fallback[-1] if fallback else None, model.avoid_five_minutes)
            if nxt is None:
                break
            fallback.append(nxt)
            cursor = nxt + 1
        return fallback[:count]

    uniforms = sorted(max(1e-12, min(1.0 - 1e-12, rng._uniform())) * total for _ in range(count))
    chosen: list[int] = []
    for target in uniforms:
        idx = _invert_cumulative(cum, target)
        candidate = minutes[idx]
        nxt = _next_distinct_work_minute(
            candidate,
            chosen[-1] if chosen else None,
            model.avoid_five_minutes,
        )
        if nxt is None:
            break
        chosen.append(nxt)
    cursor = (chosen[-1] + 1) if chosen else DAY_START_MINUTE
    while len(chosen) < count:
        nxt = _next_distinct_work_minute(cursor, chosen[-1] if chosen else None, model.avoid_five_minutes)
        if nxt is None:
            break
        chosen.append(nxt)
        cursor = nxt + 1
    return chosen[:count]


def generate_schedule(
    count: int,
    *,
    role: str = "role",
    day: date | str | None = None,
    config: TimeModelConfig | dict[str, Any] | None = None,
    seed: int | None = None,
) -> list[str]:
    """Return HH:MM strings for a role-day schedule of length ``count``."""
    if isinstance(config, TimeModelConfig):
        model = config
    elif config is None:
        model = TimeModelConfig()
    else:
        model = TimeModelConfig.from_mapping(config)
    minutes = sample_schedule_minutes(count, role=role, day=day, config=model, seed=seed)
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
        zipped.append(row)
    return zipped


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a HolyFW daily time schedule.")
    parser.add_argument("--count", type=int, required=True, help="Number of time nodes to emit")
    parser.add_argument("--role", default="role", help="Role name used for seeding")
    parser.add_argument("--date", default="", help="YYYY-MM-DD used for seeding (default: today)")
    parser.add_argument("--seed", type=int, default=None, help="Override the role/date seed")
    parser.add_argument("--json", action="store_true", help="Print a JSON array instead of one time per line")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.count <= 0:
        print("count must be > 0", file=sys.stderr)
        return 2
    day = args.date.strip() or None
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
