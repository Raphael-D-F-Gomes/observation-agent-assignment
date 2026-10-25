"""One row per corpus file, with its hash and whether parsing succeeded.

path: corpus-relative path of the file.
file_sha256: sha256 of the raw file bytes.
size_bytes: length of the file in bytes.
parse_ok: 1 when parsing succeeded, 0 when the file was kept as a parse failure.
mtime_ns: file modification time in nanoseconds, so an unchanged file can be reused.
"""

SQL = """
CREATE TABLE IF NOT EXISTS raw_files (
  path          TEXT PRIMARY KEY,
  file_sha256   TEXT NOT NULL,
  size_bytes    INTEGER NOT NULL,
  parse_ok      INTEGER NOT NULL,
  mtime_ns      INTEGER
);
"""
