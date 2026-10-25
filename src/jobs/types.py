"""Types for the shared agent job cache."""

from __future__ import annotations

from enum import StrEnum


class AgentName(StrEnum):
    """Which agent a job is for."""

    OBSERVER = "observer"
    DRAFTER = "drafter"


class JobStatus(StrEnum):
    """Whether a job is waiting, finished, or out of attempts."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
