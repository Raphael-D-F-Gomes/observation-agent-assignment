"""One try of a job, with timing and the response path.

id: generated attempt id.
job_id: job this try belongs to.
attempt: try number, starting at 1.
started_at: UTC time the try started.
ended_at: UTC time the try ended. Null when the try has no end time.
duration_ms: milliseconds from started_at to ended_at.
status: succeeded or failed for this try.
error: rejection reason. Null on success.
response_path: path of the response file for this try.
"""

SQL = """
CREATE TABLE IF NOT EXISTS job_attempts (
  id             INTEGER PRIMARY KEY,
  job_id         INTEGER NOT NULL REFERENCES jobs(id),
  attempt        INTEGER NOT NULL,
  started_at     TEXT NOT NULL,
  ended_at       TEXT,
  duration_ms    INTEGER,
  status         TEXT NOT NULL,
  error          TEXT,
  response_path  TEXT,
  UNIQUE (job_id, attempt)
);
"""
