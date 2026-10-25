"""Price one process from counted days and snapped assumptions.

Occurrences are distinct UTC dates. The month span is at least one. Minutes
and the automatable fraction snap to fixed ladders. Sensitivity uses the
neighboring minute rungs and the same fraction. Prose is not an input.
"""

from __future__ import annotations

from datetime import date, datetime

from src.consolidate.constants import (
    FRACTION_LADDER,
    HOUR_RATE,
    MINUTE_LADDER,
    MONTH_DAYS,
)
from src.consolidate.types import Price


def snap_minutes(value: float) -> int:
    """Nearest minute rung. A tie stays on the lower rung."""
    return int(min(MINUTE_LADDER, key=lambda rung: (abs(rung - value), rung)))


def snap_fraction(value: float) -> float:
    """Nearest automatable-fraction rung. A tie stays on the lower rung."""
    return float(min(FRACTION_LADDER, key=lambda rung: (abs(rung - value), rung)))


def occurrence_facts(sent_ats: list[str | None]) -> tuple[int, float, int, list[str]]:
    """Occurrence count, months, span in days, and sorted UTC dates.

    Missing sent times are ignored. Several messages on one UTC date count once.
    """
    days = sorted({sent_at[:10] for sent_at in sent_ats if sent_at})
    if not days:
        return 0, 1.0, 0, []
    span = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days
    months = max(1.0, span / MONTH_DAYS)
    return len(days), months, span, days


def price(sent_ats: list[str | None], minutes: float, fraction: float) -> Price:
    """Hours and dollars for these sent times and assumption rungs."""
    snapped_minutes = snap_minutes(minutes)
    snapped_fraction = snap_fraction(fraction)
    occurrences, months, span, _days = occurrence_facts(sent_ats)
    hours = _hours(occurrences, months, snapped_minutes, snapped_fraction)
    low_minutes, high_minutes = _neighbors(snapped_minutes)
    return Price(
        minutes=snapped_minutes,
        fraction=snapped_fraction,
        occurrences=occurrences,
        months=months,
        span_days=span,
        hours_per_month=hours,
        dollars_per_month=hours * HOUR_RATE,
        hours_low=_hours(occurrences, months, low_minutes, snapped_fraction),
        hours_high=_hours(occurrences, months, high_minutes, snapped_fraction),
        dollars_low=_hours(occurrences, months, low_minutes, snapped_fraction) * HOUR_RATE,
        dollars_high=_hours(occurrences, months, high_minutes, snapped_fraction) * HOUR_RATE,
    )


def origin_mailbox(origin_raw: str) -> str:
    """Lowercased origin header, the mailbox id for attribution."""
    return origin_raw.strip().lower()


def _hours(occurrences: int, months: float, minutes: int, fraction: float) -> float:
    """Hours per month for one occurrence count and one ladder pair."""
    if months <= 0:
        return 0.0
    return occurrences / months * minutes / 60 * fraction


def _neighbors(minutes: int) -> tuple[int, int]:
    """Neighboring minute rungs. An end rung uses itself for the missing side."""
    ladder = list(MINUTE_LADDER)
    index = ladder.index(minutes)
    low = ladder[index - 1] if index > 0 else minutes
    high = ladder[index + 1] if index + 1 < len(ladder) else minutes
    return low, high


def sent_hours(left: str, right: str) -> float:
    """Absolute hours between two stored UTC timestamps."""
    start = datetime.strptime(left[:19], "%Y-%m-%dT%H:%M:%S")
    end = datetime.strptime(right[:19], "%Y-%m-%dT%H:%M:%S")
    return abs((end - start).total_seconds()) / 3600
