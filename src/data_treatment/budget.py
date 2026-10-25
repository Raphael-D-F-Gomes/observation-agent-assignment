"""Project wall-clock time for a cold run.

The projection uses measured prepare time, a fixed allowance for the other
deterministic stages, and a p95 guess for each agent attempt. It is a gate,
not a measurement of a live model.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from src.data_treatment.constants import (
    BUDGET_LIMIT_S,
    CONSOLIDATE_ALLOWANCE_S,
    DASHBOARD_START_S,
    FINALIZE_ALLOWANCE_S,
    RETRY_WAVE_RESERVE,
)
from src.drafter.constants import DRAFT_CAP, DRAFTER_CONCURRENCY, P95_DRAFTER_S
from src.observer.constants import OBSERVER_CONCURRENCY, P95_OBSERVER_S


class BudgetError(RuntimeError):
    """Projected run time is past the 25-minute gate."""


def project_seconds(prepare_s: float, observer_jobs: int, drafter_jobs: int, timings: dict | None = None) -> float:
    """Projected seconds for prepare, agents, consolidate, finalize, and startup.

    The projection adds one reserved retry wave for observers and one for
    drafters. That reserve is a single extra wave, not every job retried to
    the attempt ceiling. Measured percentiles replace the defaults when a
    timings file has recorded them.
    """
    timings = timings or {}
    observer_p95 = float(timings.get("observer_p95", P95_OBSERVER_S))
    drafter_p95 = float(timings.get("drafter_p95", P95_DRAFTER_S))
    consolidate_s = float(timings.get("consolidate_p95", CONSOLIDATE_ALLOWANCE_S))
    finalize_s = float(timings.get("finalize_p95", FINALIZE_ALLOWANCE_S))
    observer_waves = math.ceil(observer_jobs / OBSERVER_CONCURRENCY) if observer_jobs else 0
    drafter_waves = math.ceil(drafter_jobs / DRAFTER_CONCURRENCY) if drafter_jobs else 0
    if observer_jobs:
        observer_waves += RETRY_WAVE_RESERVE
    if drafter_jobs:
        drafter_waves += RETRY_WAVE_RESERVE
    return (
        prepare_s
        + observer_waves * observer_p95
        + consolidate_s
        + drafter_waves * drafter_p95
        + finalize_s
        + DASHBOARD_START_S
    )


def planned_drafter_jobs(observer_jobs: int) -> int:
    """Upper bound on drafter calls. The real list is chosen after pricing."""
    if observer_jobs <= 0:
        return 0
    return min(DRAFT_CAP, observer_jobs)


def load_timings(output: Path) -> dict:
    """Measured percentiles, when a previous run has recorded them."""
    path = Path(output) / "logs" / "timings.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def record_duration(output: Path, stage: str, seconds: float) -> None:
    """Append one stage duration and refresh its percentile after three samples."""
    log_dir = Path(output) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    sample = {"stage": stage, "seconds": round(seconds, 3)}
    with (log_dir / "timings.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample) + "\n")
    samples: list[float] = []
    for line in (log_dir / "timings.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("stage") == stage:
            samples.append(float(item["seconds"]))
    if len(samples) < 3:
        return
    samples.sort()
    index = min(len(samples) - 1, math.ceil(0.95 * len(samples)) - 1)
    timings = load_timings(output)
    timings[f"{stage}_p95"] = samples[index]
    (log_dir / "timings.json").write_text(json.dumps(timings, indent=2), encoding="utf-8")


def gate_ok(projected_s: float) -> bool:
    """Whether the projection is inside the 25-minute limit."""
    return projected_s <= BUDGET_LIMIT_S
