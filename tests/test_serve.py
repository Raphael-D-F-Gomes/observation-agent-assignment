"""Checks for the local dashboard.

Scope: a temporary report and corpus. Requests stay on localhost.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from output_builder.dashboard import make_server


class ServeTests(unittest.TestCase):
    """Escaping, corpus confinement, highlight, and the bind address."""

    def test_script_tags_are_escaped_and_paths_stay_in_the_corpus(self) -> None:
        """A script in the file is escaped, the quote is marked, and `..` is refused."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            relative = "farmer-d/inbox/script."
            path = corpus / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"From: a@enron.com\n\n<script>alert(1)</script>\nPlease review.\n")
            report = {
                "scope": "Four mailboxes. <script>alert(1)</script>",
                "total_dollars_per_month": 10.0,
                "total_hours_per_month": 1.0,
                "rate_per_hour": 85,
                "sensitivity": {
                    "dollars_low": 5.0,
                    "dollars_high": 15.0,
                    "hours_low": 0.5,
                    "hours_high": 1.5,
                },
                "coverage": {"files": 1, "dispositions": {"ingested_unreviewed": 1}},
                "opportunities": [
                    {
                        "id": "opp-1",
                        "title": "Desk <script>alert(1)</script>",
                        "occurrences_per_month": 1.0,
                        "hours_per_month": 1.0,
                        "dollars_per_month": 10.0,
                        "mailboxes": ["farmer-d"],
                        "participant_counts": {"senders": 1, "recipients": 1},
                    }
                ],
                "incidents": [],
                "secondary": [],
            }
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaises(ValueError):
                make_server(report_path, corpus, host="0.0.0.0", port=0)
            server = make_server(report_path, corpus, port=0)
            self.assertEqual(server.server_address[0], "127.0.0.1")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_address[1]}"
                dashboard = _get(base + "/")
                self.assertNotIn("<script>", dashboard)
                self.assertIn("&lt;script&gt;", dashboard)
                raw = _get(base + "/message?path=" + _q(relative))
                self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", raw)
                self.assertNotIn("<script>", raw)
                marked = _get(
                    base + "/message?path=" + _q(relative) + "&quote=" + _q("Please review.")
                )
                self.assertIn("<mark>Please review.</mark>", marked)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _get(base + "/message?path=" + _q("../secret.txt"))
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


def _get(url: str) -> str:
    """Response body from a localhost GET."""
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8")


def _q(value: str) -> str:
    """Query-string encoding for one parameter value."""
    from urllib.parse import quote

    return quote(value, safe="")


if __name__ == "__main__":
    unittest.main()
