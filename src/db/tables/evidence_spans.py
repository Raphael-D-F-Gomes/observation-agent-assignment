"""A quote checked against the raw file, with byte offsets.

id: generated span id.
message_path: file the quote was checked against.
quote: text that was searched or confirmed.
byte_start: start offset in the raw file. Null when the check failed.
byte_end: end offset in the raw file. Null when the check failed.
raw_sha256: sha256 of the raw bytes between those offsets.
display_text: text those bytes display as, after transfer decoding.
check_result: pass or fail.
fail_reason: why the check failed. Null on a pass.
"""

SQL = """
CREATE TABLE IF NOT EXISTS evidence_spans (
  id            INTEGER PRIMARY KEY,
  message_path  TEXT NOT NULL REFERENCES messages(path),
  quote         TEXT NOT NULL,
  byte_start    INTEGER,
  byte_end      INTEGER,
  raw_sha256    TEXT,
  display_text  TEXT,
  check_result  TEXT NOT NULL CHECK (check_result IN ('pass','fail')),
  fail_reason   TEXT
);
"""
