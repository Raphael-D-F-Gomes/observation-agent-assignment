"""Small values that decide whether a prepare stage can be reused.

key: name of the stored value, such as fingerprint, pipeline version, token budget, prompt version, schema version, cue list version, or prepare time.
value: the value stored for that key.
"""

SQL = """
CREATE TABLE IF NOT EXISTS stage_meta (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
"""
