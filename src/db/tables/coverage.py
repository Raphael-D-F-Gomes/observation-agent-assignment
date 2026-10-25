"""One disposition for every corpus file.

message_path: corpus file this label belongs to.
disposition: duplicate for a non-canonical copy, candidate for an event in a component, cue_only for a cue outside a component, fallback for a parsed event with neither, parse_failure for a canonical file that did not parse, ingested_unreviewed for a stored file with no other label.
"""

SQL = """
CREATE TABLE IF NOT EXISTS coverage (
  message_path  TEXT PRIMARY KEY REFERENCES messages(path),
  disposition   TEXT NOT NULL CHECK (
                  disposition IN (
                    'duplicate','candidate','cue_only','fallback','parse_failure',
                    'ingested_unreviewed'
                  )
                )
);
"""
