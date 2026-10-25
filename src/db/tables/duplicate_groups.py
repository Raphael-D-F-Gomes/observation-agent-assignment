"""One event, its canonical path, and how many files cite it.

id: generated group id.
body_norm_sha256: normalized-body hash shared by the copies.
sent_at: shared UTC send time. Null means this file is its own group.
from_addr: shared sender address. Null means this file is its own group.
canonical_path: lexicographically first path in the group.
copy_count: number of files in the group, including the canonical file.
"""

SQL = """
CREATE TABLE IF NOT EXISTS duplicate_groups (
  id                INTEGER PRIMARY KEY,
  body_norm_sha256  TEXT NOT NULL,
  sent_at           TEXT,
  from_addr         TEXT,
  canonical_path    TEXT NOT NULL REFERENCES messages(path),
  copy_count        INTEGER NOT NULL,
  UNIQUE (body_norm_sha256, sent_at, from_addr)
);
"""
