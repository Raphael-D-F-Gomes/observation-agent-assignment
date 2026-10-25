"""Content hash of the files currently stored.

Scope: path and raw-byte hash, in path order. Database row ids are not included.
"""

from __future__ import annotations

import hashlib
import sqlite3


def stored_corpus_hash(conn: sqlite3.Connection) -> str:
    """SHA-256 of each stored path and file hash."""
    digest = hashlib.sha256()
    rows = conn.execute(
        "SELECT path, file_sha256 FROM raw_files ORDER BY path"
    ).fetchall()
    for row in rows:
        digest.update(row["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(row["file_sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()
