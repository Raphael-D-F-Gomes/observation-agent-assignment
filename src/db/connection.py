"""Open connection to the working store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.db.schema import INDEXES, SCHEMA
from src.db.tables.coverage import SQL as COVERAGE_SQL


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the working store and ensure the tables exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)
    _migrate(conn)
    return conn


def clear_derived(conn: sqlite3.Connection) -> None:
    """Delete findings and graph rows. Parsed messages stay."""
    for table in (
        "observations",
        "evidence_spans",
        "process_events",
        "opportunities",
        "pack_spans",
        "pack_members",
        "component_members",
        "components",
        "coverage",
        "candidate_edges",
        "duplicate_paths",
        "duplicate_groups",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("UPDATE messages SET thread_id = NULL")


def clear_ingest(conn: sqlite3.Connection) -> None:
    """Delete parsed mail and findings.

    Job rows and attempt history are left in place.
    """
    clear_derived(conn)
    for table in (
        "message_refs",
        "body_segments",
        "participants",
        "messages",
        "raw_files",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM stage_meta")


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns and coverage values introduced after the first schema."""
    raw_columns = {row[1] for row in conn.execute("PRAGMA table_info(raw_files)")}
    if "mtime_ns" not in raw_columns:
        conn.execute("ALTER TABLE raw_files ADD COLUMN mtime_ns INTEGER")
    evidence_columns = {row[1] for row in conn.execute("PRAGMA table_info(evidence_spans)")}
    if "raw_sha256" not in evidence_columns:
        conn.execute("ALTER TABLE evidence_spans ADD COLUMN raw_sha256 TEXT")
    if "display_text" not in evidence_columns:
        conn.execute("ALTER TABLE evidence_spans ADD COLUMN display_text TEXT")
    coverage = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'coverage'"
    ).fetchone()
    if coverage is not None and "parse_failure" not in coverage[0]:
        conn.execute("DROP TABLE coverage")
        conn.executescript(COVERAGE_SQL)


class Store:
    """One open working store and the counts read from it."""

    def __init__(self, path: Path) -> None:
        """Open `path`, creating the file and tables when needed."""
        self.path = path
        self.connection = connect(path)

    def close(self) -> None:
        """Close the connection."""
        self.connection.close()

    def clear_ingest(self) -> None:
        """Delete parsed mail and findings. Job rows stay."""
        clear_ingest(self.connection)

    def clear_derived(self) -> None:
        """Delete findings and graph rows. Parsed messages stay."""
        clear_derived(self.connection)

    def count(self, sql: str) -> int:
        """Integer result of a single-value query."""
        return int(self.connection.execute(sql).fetchone()[0])
