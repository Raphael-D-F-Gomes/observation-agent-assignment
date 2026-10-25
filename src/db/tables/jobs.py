"""One cached agent task for an input hash, a prompt version, and a schema version.

id: generated job id.
agent: observer or drafter.
input_sha256: pack id or insight hash this job reads.
prompt_version: prompt identity stored with this row.
schema_version: schema identity stored with this row.
status: pending, succeeded, or failed.
response_path: path of the succeeded response. Null until the job succeeds.
"""

SQL = """
CREATE TABLE IF NOT EXISTS jobs (
  id              INTEGER PRIMARY KEY,
  agent           TEXT NOT NULL,
  input_sha256    TEXT NOT NULL,
  prompt_version  TEXT NOT NULL,
  schema_version  TEXT NOT NULL,
  status          TEXT NOT NULL,
  response_path   TEXT,
  UNIQUE (agent, input_sha256, prompt_version, schema_version)
);
"""
