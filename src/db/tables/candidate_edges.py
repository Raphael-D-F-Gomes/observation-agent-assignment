"""A link between two canonical events and the signal that created it.

id: generated edge id.
left_path: one canonical event.
right_path: the other canonical event. The same path as left_path for a cue.
signal: subject, template, thread, participant_set, cue, or token_similarity.
weight: stored link strength. Every current signal stores 1.0.
detail: the value that explains the signal, such as a normalized subject, body hash, thread id, cue phrase, or Jaccard score.
"""

SQL = """
CREATE TABLE IF NOT EXISTS candidate_edges (
  id            INTEGER PRIMARY KEY,
  left_path     TEXT NOT NULL REFERENCES messages(path),
  right_path    TEXT NOT NULL REFERENCES messages(path),
  signal        TEXT NOT NULL CHECK (
                  signal IN ('subject','template','thread','participant_set','cue','token_similarity')
                ),
  weight        REAL NOT NULL DEFAULT 1.0,
  detail        TEXT
);
"""
