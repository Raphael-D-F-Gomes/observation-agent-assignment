"""Checks for the job cache.

Scope: a temporary store and canned response files. No model.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from db.connection import connect
from jobs.queue import ensure_job, pending_jobs, submit_response
from jobs.constants import SCHEMA_VERSION


class JobCacheTests(unittest.TestCase):
    """Retries, succeeded jobs, and a prompt change."""

    def test_failed_job_retries_and_succeeded_job_does_not(self) -> None:
        """A failed job is listed again. A succeeded job is not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "observe.sqlite")
            try:
                failed_id, _ = ensure_job(conn, "observer", "aaa", "observer-1", SCHEMA_VERSION)
                succeeded_id, _ = ensure_job(conn, "observer", "bbb", "observer-1", SCHEMA_VERSION)
                good = root / "good.json"
                good.write_text(
                    json.dumps(
                        {
                            "pack_id": "bbb",
                            "results": [
                                {
                                    "reject": True,
                                    "reject_reason": "not repeated work",
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                ok, error = submit_response(conn, succeeded_id, good)
                self.assertTrue(ok, error)
                broken = root / "broken.json"
                broken.write_text("{", encoding="utf-8")
                for _ in range(3):
                    submit_response(conn, failed_id, broken)
                status = conn.execute("SELECT status FROM jobs WHERE id = ?", (failed_id,)).fetchone()
                self.assertEqual(status["status"], "failed")
                inputs = {"aaa", "bbb"}
                retrying = pending_jobs(
                    conn,
                    agent="observer",
                    prompt_version="observer-1",
                    schema_version=SCHEMA_VERSION,
                    input_sha256s=inputs,
                    retry_failed=True,
                )
                self.assertEqual([row["input_sha256"] for row in retrying], ["aaa"])
                quiet = pending_jobs(
                    conn,
                    agent="observer",
                    prompt_version="observer-1",
                    schema_version=SCHEMA_VERSION,
                    input_sha256s=inputs,
                    retry_failed=False,
                )
                self.assertEqual(quiet, [])
            finally:
                conn.close()

    def test_prompt_bump_reprocesses_only_the_bumped_jobs(self) -> None:
        """A new observer prompt leaves a succeeded drafter job untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            conn = connect(root / "observe.sqlite")
            try:
                observer_id, _ = ensure_job(conn, "observer", "pack", "observer-1", SCHEMA_VERSION)
                drafter_id, _ = ensure_job(conn, "drafter", "insight", "drafter-1", SCHEMA_VERSION)
                observer_file = root / "observer.json"
                observer_file.write_text(
                    json.dumps(
                        {
                            "pack_id": "pack",
                            "results": [{"reject": True, "reject_reason": "social"}],
                        }
                    ),
                    encoding="utf-8",
                )
                drafter_file = root / "drafter.json"
                drafter_file.write_text(
                    json.dumps(
                        {
                            "opportunity_id": "opp-1",
                            "kind": "sop",
                            "proposed_language": "Do the step.",
                            "factual_claims": [],
                        }
                    ),
                    encoding="utf-8",
                )
                self.assertTrue(submit_response(conn, observer_id, observer_file)[0])
                self.assertTrue(
                    submit_response(
                        conn, drafter_id, drafter_file, expect_opportunity_id="opp-1"
                    )[0]
                )
                bumped_id, cache = ensure_job(conn, "observer", "pack", "observer-2", SCHEMA_VERSION)
                self.assertEqual(cache, "miss")
                self.assertNotEqual(bumped_id, observer_id)
                bumped = pending_jobs(
                    conn,
                    agent="observer",
                    prompt_version="observer-2",
                    schema_version=SCHEMA_VERSION,
                    input_sha256s={"pack"},
                )
                self.assertEqual([row["id"] for row in bumped], [bumped_id])
                old = pending_jobs(
                    conn,
                    agent="observer",
                    prompt_version="observer-1",
                    schema_version=SCHEMA_VERSION,
                    input_sha256s={"pack"},
                )
                self.assertEqual(old, [])
                drafter = pending_jobs(
                    conn,
                    agent="drafter",
                    prompt_version="drafter-1",
                    schema_version=SCHEMA_VERSION,
                    input_sha256s={"insight"},
                )
                self.assertEqual(drafter, [])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
