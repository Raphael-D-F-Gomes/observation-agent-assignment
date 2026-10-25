"""Canonical events placed in one observer pack.

pack_id: pack this event was placed in.
message_path: corpus file included in that pack.
component_key: component the file came from. Null when the pack is not a component pack.
"""

SQL = """
CREATE TABLE IF NOT EXISTS pack_members (
  pack_id        TEXT NOT NULL,
  message_path   TEXT NOT NULL,
  component_key  TEXT,
  PRIMARY KEY (pack_id, message_path)
);
CREATE INDEX IF NOT EXISTS idx_pack_members_path ON pack_members(message_path);
"""
