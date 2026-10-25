"""Connected components of canonical events, and a coverage label per file.

A component needs at least three canonical messages. Fewer than three distinct
UTC dates makes it an incident. Three or more dates stay pending until a later
decision accepts or rejects them. Every file receives exactly one disposition.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from src.data_treatment.types import ComponentStatus, CoverageDisposition, EdgeSignal
from src.db.connection import Store


def build_components(conn: sqlite3.Connection) -> None:
    """Store reviewable components and one coverage row per file."""
    canonical = {
        row["canonical_path"]
        for row in conn.execute(
            "SELECT canonical_path FROM duplicate_groups ORDER BY canonical_path"
        )
    }
    sent = {
        row["path"]: row["sent_at"]
        for row in conn.execute("SELECT path, sent_at FROM messages")
    }
    parent = {path: path for path in canonical}

    def find(path: str) -> str:
        """Root path of a component cluster."""
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: str, right: str) -> None:
        """Join two canonical paths into the same component cluster."""
        if left == right or left not in parent or right not in parent:
            return
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    cue_paths: set[str] = set()
    for row in conn.execute(
        "SELECT left_path, right_path, signal FROM candidate_edges"
    ):
        if row["signal"] == EdgeSignal.CUE:
            cue_paths.add(row["left_path"])
        union(row["left_path"], row["right_path"])

    clusters: dict[str, list[str]] = defaultdict(list)
    for path in canonical:
        clusters[find(path)].append(path)

    component_paths: set[str] = set()
    ordered_clusters = sorted(clusters.values(), key=lambda members: min(members))
    for members in ordered_clusters:
        if len(members) < 3:
            continue
        ordered = sorted(members)
        dates = sorted({sent[path][:10] for path in ordered if sent.get(path)})
        status = ComponentStatus.INCIDENT if len(dates) < 3 else ComponentStatus.PENDING
        cursor = conn.execute(
            """
            INSERT INTO components (
              status, canonical_count, distinct_dates, started_on, ended_on
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                status,
                len(ordered),
                len(dates),
                dates[0] if dates else None,
                dates[-1] if dates else None,
            ),
        )
        component_id = cursor.lastrowid
        conn.executemany(
            """
            INSERT INTO component_members (component_id, message_path)
            VALUES (?, ?)
            """,
            [(component_id, path) for path in ordered],
        )
        component_paths.update(ordered)

    all_paths = [row["path"] for row in conn.execute("SELECT path FROM messages ORDER BY path")]
    canonical_paths = {
        row["canonical_path"]
        for row in conn.execute("SELECT canonical_path FROM duplicate_groups")
    }
    parse_ok = {
        row["path"]: bool(row["parse_ok"])
        for row in conn.execute("SELECT path, parse_ok FROM raw_files")
    }
    coverage = []
    for path in all_paths:
        if path not in canonical_paths:
            disposition = CoverageDisposition.DUPLICATE
        elif not parse_ok.get(path, False):
            disposition = CoverageDisposition.PARSE_FAILURE
        elif path in component_paths:
            disposition = CoverageDisposition.CANDIDATE
        elif path in cue_paths:
            disposition = CoverageDisposition.CUE_ONLY
        else:
            disposition = CoverageDisposition.FALLBACK
        coverage.append((path, disposition))
    conn.executemany(
        "INSERT INTO coverage (message_path, disposition) VALUES (?, ?)",
        coverage,
    )


class Components:
    """Connected components of canonical events, plus one coverage label per file."""

    def run(self, store: Store) -> None:
        """Store reviewable components and a coverage row for every file."""
        build_components(store.connection)
