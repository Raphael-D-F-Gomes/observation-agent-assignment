"""Canonical events that belong to one component.

component_id: component this event belongs to.
message_path: one canonical event in that component.
"""

SQL = """
CREATE TABLE IF NOT EXISTS component_members (
  component_id  INTEGER NOT NULL REFERENCES components(id),
  message_path  TEXT NOT NULL REFERENCES messages(path),
  PRIMARY KEY (component_id, message_path)
);
"""
