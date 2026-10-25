"""Parsed headers and body for one file. sent_at is UTC or null.

path: corpus-relative path, matching raw_files.
file_sha256: sha256 of the raw file bytes.
message_id: Message-ID header, or a generated id when that header is missing.
date_raw: Date header as written, including a timezone comment. Null when absent.
sent_at: Date header as UTC `YYYY-MM-DDTHH:MM:SSZ`. Null when missing or unparseable.
from_raw: From header as written.
from_addr: sender identity. An obvious email is lowercased; any other token stays as written. Null when From has no address.
subject_raw: Subject header as written. Empty when the subject is empty.
subject_norm: subject with a leading Re, Fw, or Fwd removed, whitespace collapsed, and letters lowercased.
mailbox: first directory of the corpus path.
folder: remaining directories of the corpus path.
origin_raw: X-Origin header as written. Empty when the header is absent.
cte: Content-Transfer-Encoding, such as 7bit or quoted-printable.
body: full decoded body text.
body_norm_sha256: sha256 of the whitespace-collapsed body.
thread_id: reconstructed thread. Null until threading assigns one.
"""

SQL = """
CREATE TABLE IF NOT EXISTS messages (
  path              TEXT PRIMARY KEY REFERENCES raw_files(path),
  file_sha256       TEXT NOT NULL,
  message_id        TEXT NOT NULL,
  date_raw          TEXT,
  sent_at           TEXT,
  from_raw          TEXT NOT NULL,
  from_addr         TEXT,
  subject_raw       TEXT NOT NULL,
  subject_norm      TEXT NOT NULL,
  mailbox           TEXT NOT NULL,
  folder            TEXT NOT NULL,
  origin_raw        TEXT NOT NULL,
  cte               TEXT,
  body              TEXT NOT NULL,
  body_norm_sha256  TEXT NOT NULL,
  thread_id         TEXT
);
"""
