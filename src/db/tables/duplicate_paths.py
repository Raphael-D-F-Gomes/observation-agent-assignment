"""Every file that belongs to one duplicate group.

group_id: duplicate group this file belongs to.
path: one file in that group, including the canonical path.
"""

SQL = """
CREATE TABLE IF NOT EXISTS duplicate_paths (
  group_id  INTEGER NOT NULL REFERENCES duplicate_groups(id),
  path      TEXT NOT NULL REFERENCES messages(path),
  PRIMARY KEY (group_id, path)
);
"""
