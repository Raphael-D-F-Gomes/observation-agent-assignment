"""Body text split on Original Message separators. Position 0 is the newest text.

message_path: message this segment belongs to.
position: 0 is the newest text. Later positions are quoted Original Message parts.
body: text of this segment.
"""

SQL = """
CREATE TABLE IF NOT EXISTS body_segments (
  message_path  TEXT NOT NULL REFERENCES messages(path),
  position      INTEGER NOT NULL,
  body          TEXT NOT NULL,
  PRIMARY KEY (message_path, position)
);
"""
