"""Assign a thread id to every message.

Links come from References and In-Reply-To. A dated reply may also join dated
messages with the same normalized subject inside a 60-day window. A message
with no sent time does not join on subject alone. An empty subject never joins.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime

from src.data_treatment.constants import SUBJECT_FALLBACK_DAYS
from src.db.connection import Store

_REPLY_SUBJECT = re.compile(r"(?i)^(re|fw|fwd)\s*:")


def assign_threads(conn: sqlite3.Connection) -> None:
    """Set `thread_id` on every message and keep reply identifiers."""
    messages = conn.execute(
        """
        SELECT path, message_id, sent_at, subject_raw, subject_norm
        FROM messages
        ORDER BY path
        """
    ).fetchall()
    by_path = {row["path"]: row for row in messages}
    paths = list(by_path)
    parent = {path: path for path in paths}

    def find(path: str) -> str:
        """Root path of a message's thread cluster."""
        while parent[path] != path:
            parent[path] = parent[parent[path]]
            path = parent[path]
        return path

    def union(left: str, right: str) -> None:
        """Join two messages into the same thread cluster."""
        if left not in parent or right not in parent:
            return
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    id_to_paths: dict[str, list[str]] = defaultdict(list)
    for row in messages:
        id_to_paths[_norm_id(row["message_id"])].append(row["path"])
    for holders in id_to_paths.values():
        for other in holders[1:]:
            union(holders[0], other)

    ref_rows = conn.execute(
        "SELECT message_path, ref_message_id FROM message_refs"
    ).fetchall()
    for row in ref_rows:
        for holder in id_to_paths.get(_norm_id(row["ref_message_id"]), []):
            union(row["message_path"], holder)

    dated_by_subject: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in messages:
        if row["sent_at"] and row["subject_norm"]:
            dated_by_subject[row["subject_norm"]].append(row)
    for group in dated_by_subject.values():
        replies = [row for row in group if _REPLY_SUBJECT.match(row["subject_raw"].strip())]
        for reply in replies:
            reply_at = _sent_day(reply["sent_at"])
            for other in group:
                if other["path"] == reply["path"]:
                    continue
                if abs((_sent_day(other["sent_at"]) - reply_at).days) <= SUBJECT_FALLBACK_DAYS:
                    union(reply["path"], other["path"])

    clusters: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        clusters[find(path)].append(path)
    updates = []
    for members in clusters.values():
        thread_id = min(members)
        updates.extend((thread_id, path) for path in members)
    conn.executemany("UPDATE messages SET thread_id = ? WHERE path = ?", updates)


def _norm_id(value: str) -> str:
    """Message-id comparison key."""
    return value.strip().lower()


def _sent_day(sent_at: str) -> datetime:
    """UTC calendar day of a stored sent time."""
    return datetime.strptime(sent_at[:10], "%Y-%m-%d")


class Threads:
    """Assign a thread id from reply headers and the subject fallback."""

    def run(self, store: Store) -> None:
        """Set a thread id on every message."""
        assign_threads(store.connection)
