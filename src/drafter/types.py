"""Types for a drafter response."""

from __future__ import annotations

from enum import StrEnum


class DraftKind(StrEnum):
    """Form of language a drafter may propose."""

    SOP = "sop"
    EMAIL = "email"
    CHECKLIST = "checklist"
    TEMPLATE = "template"
