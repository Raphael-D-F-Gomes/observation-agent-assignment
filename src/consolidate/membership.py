"""Merge split components only when every subpack agrees."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from src.consolidate.types import Decision


def merge_agreed_components(
    conn: sqlite3.Connection,
    decisions: list[Decision],
    rejected_keys: set[str],
) -> list[Decision]:
    """Union directly reviewed paths when every split names the same process.

    A rejected split blocks the merge. Paths that are not merged keep
    `direct_review` and are never joined to a subpack that did not see them.
    """
    counts = {
        row["component_key"]: int(row["splits"])
        for row in conn.execute(
            """
            SELECT component_key, COUNT(DISTINCT pack_id) AS splits
            FROM pack_members
            WHERE component_key IS NOT NULL
            GROUP BY component_key
            """
        )
    }
    grouped: dict[str, list[Decision]] = defaultdict(list)
    kept: list[Decision] = []
    for decision in decisions:
        key = decision.component_key
        if key and counts.get(key, 1) > 1:
            grouped[key].append(decision)
        else:
            kept.append(decision)
    for key, items in grouped.items():
        if key in rejected_keys:
            kept.extend(items)
            continue
        by_title: dict[str, list[Decision]] = defaultdict(list)
        for item in items:
            title = " ".join(item.title.casefold().split())
            by_title[f"{title}|{item.minutes}|{item.fraction}"].append(item)
        consumed: set[int] = set()
        for group in by_title.values():
            packs = {item.pack_id for item in group}
            if len(packs) != counts[key]:
                continue
            base = group[0]
            for other in group[1:]:
                for path in other.paths:
                    if path not in base.paths:
                        base.paths.append(path)
                base.evidence.extend(other.evidence)
                base.stalls.extend(other.stalls)
                base.roles.extend(other.roles)
                consumed.add(id(other))
            base.inclusion_rule = "component_title_agreement"
            consumed.add(id(base))
            kept.append(base)
        for item in items:
            if id(item) not in consumed:
                kept.append(item)
    return kept
