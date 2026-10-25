"""From, to, cc, and bcc addresses for one message.

message_path: message this address belongs to.
role: from, to, cc, or bcc.
source: header when taken from the plain header, x-header when that header was absent.
position: order of this address within the same role and source.
raw: address text as written.
addr: address identity. An obvious email is lowercased; any other token stays as written.
"""

SQL = """
CREATE TABLE IF NOT EXISTS participants (
  message_path  TEXT NOT NULL REFERENCES messages(path),
  role          TEXT NOT NULL CHECK (role IN ('from','to','cc','bcc')),
  source        TEXT NOT NULL CHECK (source IN ('header','x-header')),
  position      INTEGER NOT NULL,
  raw           TEXT NOT NULL,
  addr          TEXT,
  PRIMARY KEY (message_path, role, source, position)
);
"""
