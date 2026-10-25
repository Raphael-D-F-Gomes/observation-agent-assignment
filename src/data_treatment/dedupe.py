"""Group true duplicate files and choose one canonical path.

A duplicate is the same normalized body, the same sent time, and the same
sender address. An undated message, or a message with no sender address, is
its own event. Copies on different days stay separate.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from src.db.connection import Store


def assign_duplicates(conn: sqlite3.Connection) -> None:
    """Write one duplicate group per event and every path that cites it.

    The canonical path is the lexicographically first path in the group.
    """
    rows = conn.execute(
        """
        SELECT path, body_norm_sha256, sent_at, from_addr
        FROM messages
        ORDER BY path
        """
    ).fetchall()
    grouped: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    singletons: list[tuple[str, str, str | None, str | None]] = []
    for row in rows:
        if row["sent_at"] and row["from_addr"]:
            key = (row["body_norm_sha256"], row["sent_at"], row["from_addr"])
            grouped[key].append(row["path"])
        else:
            singletons.append(
                (
                    row["path"],
                    row["body_norm_sha256"],
                    row["sent_at"],
                    row["from_addr"],
                )
            )

    for (body_hash, sent_at, from_addr), paths in grouped.items():
        ordered = sorted(paths)
        _insert_group(conn, body_hash, sent_at, from_addr, ordered)
    for path, body_hash, sent_at, from_addr in singletons:
        _insert_group(conn, body_hash, sent_at, from_addr, [path])


def canonical_paths(conn: sqlite3.Connection) -> set[str]:
    """Paths that count as events. Non-canonical copies are omitted."""
    rows = conn.execute("SELECT canonical_path FROM duplicate_groups")
    return {row["canonical_path"] for row in rows}


def _insert_group(
    conn: sqlite3.Connection,
    body_hash: str,
    sent_at: str | None,
    from_addr: str | None,
    paths: list[str],
) -> None:
    """Store one duplicate group and every path in it."""
    canonical = paths[0]
    cursor = conn.execute(
        """
        INSERT INTO duplicate_groups (
          body_norm_sha256, sent_at, from_addr, canonical_path, copy_count
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (body_hash, sent_at, from_addr, canonical, len(paths)),
    )
    group_id = cursor.lastrowid
    conn.executemany(
        "INSERT INTO duplicate_paths (group_id, path) VALUES (?, ?)",
        [(group_id, path) for path in paths],
    )


class Dedupe:
    """Collapse true copies onto one canonical path per event."""

    def run(self, store: Store) -> None:
        """Write one duplicate group per event."""
        assign_duplicates(store.connection)
