"""Propose candidate links among canonical events.

Every canonical file is considered. Edges keep the signal that created them.
Self-edges record cue hits and do not connect the graph. Token overlap is a
last resort for events that no other signal linked, using Jaccard on
position-0 words, not embeddings.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from src.data_treatment.dedupe import canonical_paths
from src.data_treatment.types import EdgeSignal
from src.db.connection import Store

CUES: tuple[tuple[str, bool], ...] = (
    ("every day", False),
    ("daily", True),
    ("each week", False),
    ("monthly", True),
    ("same as yesterday", False),
    ("following up", False),
    ("any update", False),
    ("please advise", False),
    ("again", True),
)

_TOKEN = re.compile(r"[a-z0-9']{3,}")
_REPLY_SUBJECT = re.compile(r"(?i)^(re|fw|fwd)\s*:")
_STOP = {
    "the", "and", "for", "that", "this", "with", "from", "have", "your", "you",
    "are", "was", "were", "but", "not", "can", "will", "our", "has", "been",
    "they", "their", "what", "when", "who", "how", "all", "any", "out", "get",
    "got", "just", "about", "into", "than", "then", "them", "his", "her", "she",
    "him", "its", "also", "would", "could", "should", "there", "here", "please",
    "thanks", "thank", "regards", "subject", "sent", "monday", "tuesday",
    "wednesday", "thursday", "friday", "saturday", "sunday",
}
_JACCARD_MIN = 0.85
_MIN_TOKENS = 10
_NEIGHBOR_LIMIT = 5
_COMPARE_LIMIT = 40


def build_candidate_edges(conn: sqlite3.Connection) -> None:
    """Insert candidate edges for subject, template, thread, people, cues, and tokens."""
    canonical = canonical_paths(conn)
    rows = conn.execute(
        """
        SELECT path, sent_at, subject_raw, subject_norm, body_norm_sha256, thread_id
        FROM messages
        ORDER BY path
        """
    ).fetchall()
    events = [row for row in rows if row["path"] in canonical]
    segments = {
        row["message_path"]: row["body"]
        for row in conn.execute(
            "SELECT message_path, body FROM body_segments WHERE position = 0"
        )
    }
    participants: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT message_path, addr FROM participants WHERE addr IS NOT NULL"
    ):
        if row["addr"]:
            participants[row["message_path"]].add(row["addr"])

    edges: list[tuple[str, str, str, str | None]] = []
    edges.extend(_chain_edges(_dated_groups(events, "subject_norm"), EdgeSignal.SUBJECT))
    edges.extend(_template_edges(events))
    edges.extend(_thread_edges(events))
    edges.extend(_participant_edges(events, participants))
    linked = {path for left, right, _, _ in edges for path in (left, right) if left != right}
    edges.extend(_cue_edges(events, segments))
    edges.extend(_token_edges(events, segments, linked))
    conn.executemany(
        """
        INSERT INTO candidate_edges (left_path, right_path, signal, weight, detail)
        VALUES (?, ?, ?, 1.0, ?)
        """,
        edges,
    )


def cues_in(text: str) -> list[str]:
    """Cue phrases present in text, in list order.

    Single-word cues match a whole word. Phrases match a substring.
    """
    folded = text.casefold()
    found = []
    for phrase, as_word in CUES:
        if as_word:
            if re.search(rf"\b{re.escape(phrase)}\b", folded):
                found.append(phrase)
        elif phrase in folded:
            found.append(phrase)
    return found


def _dated_groups(events: list[sqlite3.Row], field: str) -> dict[str, list[str]]:
    """Dated events that share one field, ordered by sent time."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in events:
        if row["sent_at"] and row[field]:
            groups[row[field]].append(row)
    ordered: dict[str, list[str]] = {}
    for key, rows in groups.items():
        rows.sort(key=lambda item: (item["sent_at"], item["path"]))
        ordered[key] = [item["path"] for item in rows]
    return ordered


def _chain_edges(
    groups: dict[str, list[str]], signal: EdgeSignal
) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """One edge between each consecutive path in a group."""
    edges = []
    for key, paths in groups.items():
        if signal == EdgeSignal.SUBJECT and not key:
            continue
        for left, right in zip(paths, paths[1:]):
            edges.append((left, right, signal, key))
    return edges


def _template_edges(events: list[sqlite3.Row]) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """Edges for one normalized body that appears on two or more dates."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in events:
        if row["sent_at"]:
            groups[row["body_norm_sha256"]].append(row)
    edges = []
    for body_hash, rows in groups.items():
        dates = {row["sent_at"][:10] for row in rows}
        if len(dates) < 2:
            continue
        rows.sort(key=lambda item: (item["sent_at"], item["path"]))
        paths = [item["path"] for item in rows]
        for left, right in zip(paths, paths[1:]):
            edges.append((left, right, EdgeSignal.TEMPLATE, body_hash))
    return edges


def _thread_edges(events: list[sqlite3.Row]) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """Edges for a thread of three messages, or of two replies."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in events:
        if row["thread_id"]:
            groups[row["thread_id"]].append(row)
    edges = []
    for thread_id, rows in groups.items():
        replies = sum(1 for row in rows if _REPLY_SUBJECT.match(row["subject_raw"].strip()))
        if len(rows) < 3 and replies < 2:
            continue
        rows.sort(key=lambda item: (item["sent_at"] or "", item["path"]))
        paths = [item["path"] for item in rows]
        for left, right in zip(paths, paths[1:]):
            edges.append((left, right, EdgeSignal.THREAD, thread_id))
    return edges


def _participant_edges(
    events: list[sqlite3.Row], participants: dict[str, set[str]]
) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """Edges between different-day subject neighbors that share an address."""
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in events:
        if row["sent_at"] and row["subject_norm"]:
            groups[row["subject_norm"]].append(row)
    edges = []
    for subject, rows in groups.items():
        rows.sort(key=lambda item: (item["sent_at"], item["path"]))
        for left, right in zip(rows, rows[1:]):
            if left["sent_at"][:10] == right["sent_at"][:10]:
                continue
            shared = participants.get(left["path"], set()) & participants.get(right["path"], set())
            if shared:
                edges.append((left["path"], right["path"], EdgeSignal.PARTICIPANT_SET, subject))
    return edges


def _cue_edges(
    events: list[sqlite3.Row], segments: dict[str, str]
) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """A self-edge for each cue phrase in the newest body segment."""
    edges = []
    for row in events:
        text = segments.get(row["path"], "")
        for phrase in cues_in(text):
            edges.append((row["path"], row["path"], EdgeSignal.CUE, phrase))
    return edges


def _token_edges(
    events: list[sqlite3.Row],
    segments: dict[str, str],
    linked: set[str],
) -> list[tuple[str, str, EdgeSignal, str | None]]:
    """Jaccard edges among events that no other signal linked."""
    unlinked = [row["path"] for row in events if row["path"] not in linked]
    tokens = {path: _tokens(segments.get(path, "")) for path in unlinked}
    tokens = {path: words for path, words in tokens.items() if len(words) >= _MIN_TOKENS}
    inverted: dict[str, list[str]] = defaultdict(list)
    for path, words in tokens.items():
        for word in words:
            inverted[word].append(path)
    common = {word for word, paths in inverted.items() if len(paths) > 50}
    edges = []
    seen: set[tuple[str, str]] = set()
    for path, words in tokens.items():
        rare = [word for word in words if word not in common]
        candidates: list[str] = []
        for word in sorted(rare, key=lambda item: len(inverted[item])):
            for other in inverted[word]:
                if other != path and other not in candidates:
                    candidates.append(other)
                if len(candidates) >= _COMPARE_LIMIT:
                    break
            if len(candidates) >= _COMPARE_LIMIT:
                break
        scored = []
        for other in candidates:
            score = _jaccard(words, tokens[other])
            if score >= _JACCARD_MIN:
                scored.append((score, other))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for score, other in scored[:_NEIGHBOR_LIMIT]:
            pair = tuple(sorted((path, other)))
            if pair in seen:
                continue
            seen.add(pair)
            edges.append((pair[0], pair[1], EdgeSignal.TOKEN_SIMILARITY, f"{score:.3f}"))
    return edges


def _tokens(text: str) -> set[str]:
    """Words of three or more characters, with stopwords removed."""
    return {token for token in _TOKEN.findall(text.casefold()) if token not in _STOP}


def _jaccard(left: set[str], right: set[str]) -> float:
    """Size of the intersection divided by the size of the union."""
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


class Candidates:
    """Propose candidate links among canonical events."""

    def run(self, store: Store) -> None:
        """Insert subject, template, thread, people, cue, and token edges."""
        build_candidate_edges(store.connection)
