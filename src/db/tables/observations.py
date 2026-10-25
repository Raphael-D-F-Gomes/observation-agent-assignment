"""A finding attached to a process and, when present, one evidence span.

id: generated observation id.
process_id: process this finding belongs to. Null when there is none.
component_id: component the finding came from. Null when there is none.
job_id: agent job that produced the finding.
obs_type: evidence, stall, role, cadence, delay, or secondary.
payload_json: the finding as JSON.
evidence_id: evidence span that supports the finding. Null when the finding has no quote.
"""

SQL = """
CREATE TABLE IF NOT EXISTS observations (
  id              INTEGER PRIMARY KEY,
  process_id      TEXT,
  component_id    INTEGER REFERENCES components(id),
  job_id          INTEGER NOT NULL,
  obs_type        TEXT NOT NULL,
  payload_json    TEXT NOT NULL,
  evidence_id     INTEGER REFERENCES evidence_spans(id)
);
"""
