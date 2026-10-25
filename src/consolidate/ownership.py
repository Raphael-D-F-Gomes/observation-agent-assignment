"""Give each canonical path to one primary process."""

from __future__ import annotations

from src.consolidate.types import Decision


def assign_ownership(decisions: list[Decision]) -> None:
    """Give each path to the highest-priced claim, and mark later claims secondary."""
    ranked = sorted(
        decisions,
        key=lambda item: (-item.provisional_dollars, -len(item.paths), item.title),
    )
    taken: set[str] = set()
    for decision in ranked:
        primary = []
        secondary = []
        for path in decision.paths:
            if path in taken:
                secondary.append(path)
            else:
                primary.append(path)
                taken.add(path)
        decision.primary = primary
        decision.secondary = secondary
