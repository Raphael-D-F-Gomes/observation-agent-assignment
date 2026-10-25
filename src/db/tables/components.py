"""A connected set of canonical events and its review status.

id: generated component id.
status: pending while unreviewed, recurring after a published process, incident when the dates are too few, rejected when the process is not accepted.
canonical_count: number of canonical events in the component.
distinct_dates: number of distinct UTC dates among those events.
started_on: earliest UTC date. Null when no member is dated.
ended_on: latest UTC date. Null when no member is dated.
"""

SQL = """
CREATE TABLE IF NOT EXISTS components (
  id               INTEGER PRIMARY KEY,
  status           TEXT NOT NULL CHECK (status IN ('pending','recurring','incident','rejected')),
  canonical_count  INTEGER NOT NULL,
  distinct_dates   INTEGER NOT NULL,
  started_on       TEXT,
  ended_on         TEXT
);
"""
