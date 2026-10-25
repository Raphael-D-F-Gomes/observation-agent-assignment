"""Read a corpus directory into the working store, one row per file.

After the rows are stored, duplicates, threads, candidate edges, components,
packs, and observer jobs are built for that same corpus.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.data_treatment.corpus import Corpus, list_corpus_files
from src.data_treatment.parse import ParsedMessage, parse_file, sha256_bytes
from src.data_treatment.types import PrepareStats
from src.db.connection import Store


class CoverageError(RuntimeError):
    """The stored file and message counts do not match the files on disk."""


def prepare_corpus(
    corpus: Path,
    db_path: Path,
    output_dir: Path | None = None,
    *,
    token_budget: int | None = None,
) -> PrepareStats:
    """Store one parsed row per file, then build candidate packs and observer jobs.

    The message count must equal the number of files measured on disk. A parse
    failure is stored, not skipped. Running again replaces the parsed rows.
    `token_budget` sets the observer pack size; when omitted, the budget rises
    from 12,000 tokens until the pack count fits the observer cap.
    """
    if output_dir is None:
        output_dir = db_path.parent / "output"
    from src.data_treatment.treatment import DataTreatment
    from src.jobs.queue import JobQueue
    from src.observer.service import Observer

    observer = Observer(JobQueue(db_path), output_dir)
    return DataTreatment(corpus, db_path, output_dir, observer, token_budget=token_budget).run()


def _parse_one(path: Path, relative: str) -> ParsedMessage:
    """Parse one corpus file. An unreadable file is a failed result with no bytes."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        parsed = parse_file(relative, b"")
        parsed.parse_ok = False
        parsed.size_bytes = 0
        parsed.error = f"{type(exc).__name__}: {exc}"
        parsed.message_id = f"<unparsed-{parsed.file_sha256}>"
        return parsed
    return parse_file(relative, data)


def require_full_coverage(conn: sqlite3.Connection, file_count: int) -> None:
    """Require the raw-file and message counts to equal `file_count`."""
    message_count = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    raw_count = int(conn.execute("SELECT COUNT(*) FROM raw_files").fetchone()[0])
    if message_count != file_count or raw_count != file_count:
        raise CoverageError(
            f"corpus files={file_count} messages={message_count} raw_files={raw_count}"
        )


def _insert(conn: sqlite3.Connection, parsed: ParsedMessage) -> None:
    """Write one parsed file, its participants, and its body segments."""
    conn.execute(
        """
        INSERT INTO raw_files (path, file_sha256, size_bytes, parse_ok, mtime_ns)
        VALUES (?, ?, ?, ?, ?)
        """,
        (parsed.path, parsed.file_sha256, parsed.size_bytes, int(parsed.parse_ok), parsed.mtime_ns),
    )
    conn.execute(
        """
        INSERT INTO messages (
          path, file_sha256, message_id, date_raw, sent_at, from_raw, from_addr,
          subject_raw, subject_norm, mailbox, folder, origin_raw, cte, body,
          body_norm_sha256, thread_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            parsed.path,
            parsed.file_sha256,
            parsed.message_id,
            parsed.date_raw,
            parsed.sent_at,
            parsed.from_raw,
            parsed.from_addr,
            parsed.subject_raw,
            parsed.subject_norm,
            parsed.mailbox,
            parsed.folder,
            parsed.origin_raw,
            parsed.cte,
            parsed.body,
            parsed.body_norm_sha256,
        ),
    )
    conn.executemany(
        """
        INSERT INTO participants (message_path, role, source, position, raw, addr)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (parsed.path, row.role, row.source, row.position, row.raw, row.addr)
            for row in parsed.participants
        ],
    )
    conn.executemany(
        """
        INSERT INTO body_segments (message_path, position, body)
        VALUES (?, ?, ?)
        """,
        [(parsed.path, index, segment) for index, segment in enumerate(parsed.segments)],
    )
    conn.executemany(
        """
        INSERT INTO message_refs (message_path, ref_message_id)
        VALUES (?, ?)
        """,
        [(parsed.path, ref) for ref in parsed.refs],
    )


def sync_messages(conn: sqlite3.Connection, corpus: Corpus) -> tuple[int, int, list[tuple[str, str]]]:
    """Reuse unchanged rows, reparse changed files, and delete removed files.

    A file is unchanged when its size and modification time match the stored
    row. The returned counts are files parsed, files removed, and parse errors
    for the files parsed on this call.
    """
    disk = {
        path.relative_to(corpus.path).as_posix(): path
        for path in corpus.files()
    }
    stored = {
        row["path"]: row
        for row in conn.execute("SELECT path, file_sha256, size_bytes, mtime_ns FROM raw_files")
    }
    removed = [path for path in stored if path not in disk]
    for path in removed:
        _delete_message(conn, path)
    parsed = 0
    errors: list[tuple[str, str]] = []
    for relative, path in disk.items():
        stat = path.stat()
        row = stored.get(relative)
        if (
            row is not None
            and row["mtime_ns"] is not None
            and int(row["size_bytes"]) == stat.st_size
            and int(row["mtime_ns"]) == stat.st_mtime_ns
        ):
            continue
        data = path.read_bytes()
        digest = sha256_bytes(data)
        if row is not None and row["file_sha256"] == digest:
            conn.execute(
                "UPDATE raw_files SET mtime_ns = ?, size_bytes = ? WHERE path = ?",
                (stat.st_mtime_ns, stat.st_size, relative),
            )
            continue
        if row is not None:
            _delete_message(conn, relative)
        message = parse_file(relative, data)
        message.mtime_ns = stat.st_mtime_ns
        if not message.parse_ok:
            errors.append((message.path, message.error or ""))
        _insert(conn, message)
        parsed += 1
    return parsed, len(removed), errors


def _delete_message(conn: sqlite3.Connection, path: str) -> None:
    """Delete one file and the rows that belong only to it."""
    conn.execute(
        """
        DELETE FROM observations
        WHERE evidence_id IN (
          SELECT id FROM evidence_spans WHERE message_path = ?
        )
        """,
        (path,),
    )
    for table, column in (
        ("evidence_spans", "message_path"),
        ("process_events", "message_path"),
        ("pack_spans", "message_path"),
        ("pack_members", "message_path"),
        ("component_members", "message_path"),
        ("coverage", "message_path"),
        ("message_refs", "message_path"),
        ("body_segments", "message_path"),
        ("participants", "message_path"),
    ):
        conn.execute(f"DELETE FROM {table} WHERE {column} = ?", (path,))
    conn.execute(
        "DELETE FROM candidate_edges WHERE left_path = ? OR right_path = ?",
        (path, path),
    )
    conn.execute(
        """
        DELETE FROM duplicate_paths
        WHERE path = ?
           OR group_id IN (SELECT id FROM duplicate_groups WHERE canonical_path = ?)
        """,
        (path, path),
    )
    conn.execute("DELETE FROM duplicate_groups WHERE canonical_path = ?", (path,))
    conn.execute("DELETE FROM messages WHERE path = ?", (path,))
    conn.execute("DELETE FROM raw_files WHERE path = ?", (path,))


class Ingest:
    """Store one parsed row for every file in a corpus."""

    def __init__(self, corpus: Corpus) -> None:
        """Remember the corpus and start with no parse failures."""
        self.corpus = corpus
        self.parse_error_paths: list[tuple[str, str]] = []
        self.files_parsed = 0
        self.files_removed = 0

    def run(self, store: Store) -> None:
        """Parse new or changed files and keep rows whose bytes are unchanged."""
        self.files_parsed, self.files_removed, self.parse_error_paths = sync_messages(
            store.connection, self.corpus
        )


class CoverageCheck:
    """Require one message row and one coverage row per file on disk."""

    def __init__(self, corpus: Corpus) -> None:
        """Remember the corpus whose file count is the required total."""
        self.corpus = corpus
        self.file_count = 0

    def run(self, store: Store) -> None:
        """Raise when the stored rows do not match the files on disk."""
        self.file_count = len(self.corpus.files())
        require_full_coverage(store.connection, self.file_count)
        coverage_count = store.count("SELECT COUNT(*) FROM coverage")
        if coverage_count != self.file_count:
            raise CoverageError(
                f"corpus files={self.file_count} coverage={coverage_count}"
            )
