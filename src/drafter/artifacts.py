"""Choose draft targets and check drafted factual claims.

A published opportunity at or above the dollar threshold is drafted. When
fewer than five clear that line, the highest-priced five are drafted instead.
The list is capped at eight. Proposed language is not evidence. Each factual
claim needs a quote in the raw file and a path that belongs to the opportunity.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.consolidate.quotes import check_quote
from src.consolidate.types import OpportunityStatus
from src.drafter.constants import DRAFT_CAP, DRAFT_MINIMUM, DRAFT_THRESHOLD_DOLLARS


def select_draft_targets(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Published opportunities that should receive an artifact.

    Rows are ordered by dollars per month, highest first.
    """
    published = [row for row in rows if row["status"] == OpportunityStatus.PUBLISHED]
    published.sort(key=lambda row: (-row["dollars_per_month"], row["id"]))
    above = [row for row in published if row["dollars_per_month"] >= DRAFT_THRESHOLD_DOLLARS]
    chosen = above if len(above) >= DRAFT_MINIMUM else published[:DRAFT_MINIMUM]
    return chosen[:DRAFT_CAP]


def validate_draft(
    corpus: Path,
    evidence_paths: set[str],
    draft: dict,
    allowed_quotes: list[dict] | None = None,
) -> tuple[bool, str]:
    """Whether a draft's factual claims are allowed to ship.

    An empty claim list does not fail. A claim must repeat a quote that was
    shown for the opportunity, and that span must still match the raw file.
    """
    from src.consolidate.quotes import check_span

    allowed = allowed_quotes or []
    for claim in draft.get("factual_claims") or []:
        path = claim.get("path") or ""
        quote = claim.get("quote") or ""
        if path not in evidence_paths:
            return False, "path_not_in_opportunity"
        match = next(
            (
                item
                for item in allowed
                if item.get("path") == path and (item.get("quote") == quote or item.get("display_text") == quote)
            ),
            None,
        )
        if match and match.get("byte_start") is not None and match.get("raw_sha256"):
            result = check_span(
                corpus,
                path,
                match.get("display_text") or quote,
                int(match["byte_start"]),
                int(match["byte_end"]),
                match["raw_sha256"],
                match.get("transfer_encoding"),
            )
            if not result.ok:
                return False, result.fail_reason or "not_in_file"
            continue
        result = check_quote(corpus, path, quote)
        if not result.ok:
            return False, result.fail_reason or "not_in_file"
    return True, ""
