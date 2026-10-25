"""Types for quote checks, ownership, and pricing."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Assignment(StrEnum):
    """Whether a process owns an event or only claimed it."""

    PRIMARY = "primary"
    SECONDARY = "secondary"


class OpportunityStatus(StrEnum):
    """Whether a priced process is in the dollar total."""

    PUBLISHED = "published"
    INCIDENT = "incident"
    REJECTED = "rejected"


class ObservationKind(StrEnum):
    """Kind of finding stored for a process."""

    EVIDENCE = "evidence"
    STALL = "stall"
    ROLE = "role"
    CADENCE = "cadence"
    DELAY = "delay"
    SECONDARY = "secondary"


@dataclass(frozen=True)
class QuoteCheck:
    """Result of one raw-byte quote search."""

    ok: bool
    path: str | None
    byte_start: int | None
    byte_end: int | None
    fail_reason: str | None


@dataclass(frozen=True)
class Price:
    """Hours and dollars for one set of ladder inputs."""

    minutes: int
    fraction: float
    occurrences: int
    months: float
    span_days: int
    hours_per_month: float
    dollars_per_month: float
    hours_low: float
    hours_high: float
    dollars_low: float
    dollars_high: float


@dataclass
class Decision:
    """One observer result after quote checks and ownership."""

    title: str
    component_id: int | None
    job_id: int
    paths: list[str]
    evidence: list[dict]
    stalls: list[dict]
    roles: list[dict]
    cadence: dict | None
    minutes: float
    fraction: float
    rationale: str
    ordinal: int = 0
    provisional_dollars: float = 0.0
    primary: list[str] = field(default_factory=list)
    secondary: list[str] = field(default_factory=list)
    pack_id: str = ""
    component_key: str | None = None
    inclusion_rule: str = "direct_review"
