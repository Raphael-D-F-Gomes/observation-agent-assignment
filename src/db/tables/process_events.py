"""Primary or secondary assignment of one event to one process.

process_id: stable id of the proposed process.
message_path: canonical event assigned to that process.
assignment: primary when this process owns the event, secondary when a later process also claimed it.
"""

SQL = """
CREATE TABLE IF NOT EXISTS process_events (
  process_id    TEXT NOT NULL,
  message_path  TEXT NOT NULL REFERENCES messages(path),
  assignment    TEXT NOT NULL CHECK (assignment IN ('primary','secondary')),
  PRIMARY KEY (process_id, message_path)
);
"""
