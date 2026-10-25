"""In-Reply-To and References identifiers for one message.

message_path: message that carries the reference.
ref_message_id: one In-Reply-To or References identifier.
"""

SQL = """
CREATE TABLE IF NOT EXISTS message_refs (
  message_path    TEXT NOT NULL REFERENCES messages(path),
  ref_message_id  TEXT NOT NULL,
  PRIMARY KEY (message_path, ref_message_id)
);
"""
