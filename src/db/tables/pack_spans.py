"""Raw-byte spans shown to the observer that built a pack.

span_id: stable id of this slice.
pack_id: pack that showed this slice.
message_path: file the slice was taken from.
byte_start: start offset in the raw file.
byte_end: end offset in the raw file.
raw_sha256: sha256 of the raw bytes between those offsets.
display_text: text those bytes display as.
transfer_encoding: Content-Transfer-Encoding of the file. Null when the file has none.
coverage: complete for one slice of the whole body, chunk for one window of a long body, encoded_chunk when the display text is not itself present in the raw bytes.
"""

SQL = """
CREATE TABLE IF NOT EXISTS pack_spans (
  span_id            TEXT PRIMARY KEY,
  pack_id            TEXT NOT NULL,
  message_path       TEXT NOT NULL,
  byte_start         INTEGER NOT NULL,
  byte_end           INTEGER NOT NULL,
  raw_sha256         TEXT NOT NULL,
  display_text       TEXT NOT NULL,
  transfer_encoding  TEXT,
  coverage           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pack_spans_pack ON pack_spans(pack_id);
"""
