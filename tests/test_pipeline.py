"""Checks for duplicates, threads, components, pricing, and drafts.

Scope: a temporary corpus and canned JSON responses. No model and no network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from drafter.artifacts import validate_draft
from consolidate.service import consolidate
from db.connection import connect
from output_builder.finalize import finalize
from data_treatment.ingest import prepare_corpus
from jobs.queue import pending_jobs, submit_response
from data_treatment.parse import split_original_message
from consolidate.constants import HOUR_RATE
from drafter.constants import DRAFTER_PROMPT_VERSION
from jobs.constants import SCHEMA_VERSION
from observer.constants import OBSERVER_PROMPT_VERSION


def _mail(
    message_id: str,
    subject: str,
    body: str,
    *,
    date: str | None = "Fri, 5 Jan 2001 10:00:00 -0000",
    sender: str = "A Person <a@enron.com>",
    to: str = "b@enron.com",
    cc: str = "c@enron.com",
    origin: str = "Farmer-D",
    reply_to: str | None = None,
) -> str:
    """One RFC822 message with the headers the pipeline reads."""
    lines = [f"Message-ID: {message_id}"]
    if date:
        lines.append(f"Date: {date}")
    lines.append(f"From: {sender}")
    lines.append(f"To: {to}")
    lines.append(f"Cc: {cc}")
    lines.append(f"Subject: {subject}")
    if reply_to:
        lines.append(f"In-Reply-To: {reply_to}")
    lines.append(f"X-Origin: {origin}")
    lines.append("Content-Transfer-Encoding: 7bit")
    lines.append("")
    lines.append(body)
    return "\n".join(lines) + "\n"


class PipelineTests(unittest.TestCase):
    """Temporary mail through prepare, canned observation, and finalize."""

    def test_candidates_prices_and_warm_cache(self) -> None:
        """Copies, threads, components, quotes, money, drafts, and a warm rerun."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            output = root / "output"
            db_path = root / "observe.sqlite"
            self._write_corpus(corpus)
            stats = prepare_corpus(corpus, db_path, output, token_budget=1)
            self.assertEqual(stats.file_count, stats.message_count)
            self.assertTrue(stats.gate_ok)

            conn = connect(db_path)
            try:
                self._assert_copies_and_threads(conn)
                self._assert_edges_and_coverage(conn, stats.file_count)
                self._submit_observers(conn, output)
                summary = consolidate(conn, corpus, output)
                conn.commit()
                self.assertGreaterEqual(summary["published"], 1)
                self._assert_priced(conn)
                self._submit_drafters(conn, corpus, output)
                conn.commit()
            finally:
                conn.close()

            conn = connect(db_path)
            try:
                with self.assertRaises(Exception) as caught:
                    finalize(conn, corpus, output)
                self.assertEqual(type(caught.exception).__name__, "GateError")
            finally:
                conn.close()
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["complete"])
            self.assertIn("artifact validation failed", " ".join(report["failure_reasons"]))
            markdown = (output / "report.md").read_text(encoding="utf-8")
            self.assertIn(f"${report['total_dollars_per_month']:.2f}", markdown)
            titles = {item["title"] for item in report["opportunities"]}
            self.assertIn("Confirm nomination volume", titles)
            self.assertNotIn("Burst check", titles)
            incident_titles = {item["title"] for item in report["incidents"]}
            self.assertIn("Burst check", incident_titles)
            published_dollars = sum(item["dollars_per_month"] for item in report["opportunities"])
            self.assertAlmostEqual(published_dollars, report["total_dollars_per_month"])
            nomin = next(item for item in report["opportunities"] if item["title"] == "Confirm nomination volume")
            self.assertIsNotNone(nomin["artifact"])
            desk = next(item for item in report["opportunities"] if item["title"] == "Desk follow up")
            self.assertIsNone(desk["artifact"])

            warm = prepare_corpus(corpus, db_path, output, token_budget=1)
            self.assertEqual(warm.files_parsed, 0)
            self.assertTrue(warm.cache_hit)
            self.assertEqual(warm.file_count, stats.file_count)
            self.assertEqual(self._pending(db_path, output, "observer", OBSERVER_PROMPT_VERSION), [])
            self.assertEqual(self._pending(db_path, output, "drafter", DRAFTER_PROMPT_VERSION), [])

            desk_path = corpus / "farmer-d/inbox/desk-3."
            desk_path.write_bytes(desk_path.read_bytes().replace(b"DESK-THIRD-TOKEN", b"DESK-THIRD-TOKEN-EDIT"))
            prepare_corpus(corpus, db_path, output, token_budget=1)
            changed = self._pending(db_path, output, "observer", OBSERVER_PROMPT_VERSION)
            self.assertGreaterEqual(len(changed), 1)
            for pack_id in changed:
                payload = json.loads((output / "packs" / f"{pack_id}.json").read_text(encoding="utf-8"))
                self.assertIn("farmer-d/inbox/desk-3.", payload["member_paths"])
                self.assertFalse(any("nomin" in path for path in payload["member_paths"]))

    def _write_corpus(self, corpus: Path) -> None:
        """Write the fixture files the assertions name."""
        files = {
            "farmer-d/inbox/nomin-1.": _mail(
                "<nomin-1@enron.com>",
                "Daily nomination",
                "Please confirm the volume.",
                date="Fri, 5 Jan 2001 10:00:00 -0000",
            ),
            "farmer-d/inbox/nomin-2.": _mail(
                "<nomin-2@enron.com>",
                "RE: Daily nomination",
                "Please confirm the volume.",
                date="Fri, 12 Jan 2001 10:00:00 -0000",
                reply_to="<nomin-1@enron.com>",
            ),
            "farmer-d/inbox/nomin-3.": _mail(
                "<nomin-3@enron.com>",
                "RE: Daily nomination",
                "Please confirm the volume.",
                date="Fri, 19 Jan 2001 10:00:00 -0000",
                reply_to="<nomin-2@enron.com>",
            ),
            "farmer-d/inbox/nomin-extra.": _mail(
                "<nomin-extra@enron.com>",
                "Daily nomination",
                "Please confirm the extra volume.",
                date="Fri, 5 Jan 2001 11:00:00 -0000",
            ),
            "farmer-d/inbox/burst-1.": _mail(
                "<burst-1@enron.com>",
                "Burst check",
                "Burst one is the note.",
                date="Mon, 2 Apr 2001 09:00:00 -0000",
            ),
            "farmer-d/inbox/burst-2.": _mail(
                "<burst-2@enron.com>",
                "Burst check",
                "Burst two is the note.",
                date="Mon, 2 Apr 2001 12:00:00 -0000",
            ),
            "farmer-d/inbox/burst-3.": _mail(
                "<burst-3@enron.com>",
                "Burst check",
                "Burst three is the note.",
                date="Mon, 2 Apr 2001 15:00:00 -0000",
            ),
            "farmer-d/inbox/desk-1.": _mail(
                "<desk-1@enron.com>",
                "Desk follow up",
                "Waiting on the desk.",
                date="Thu, 1 Feb 2001 10:00:00 -0000",
            ),
            "farmer-d/inbox/desk-2.": _mail(
                "<desk-2@enron.com>",
                "RE: Desk follow up",
                "Waiting on the desk still.",
                date="Fri, 2 Feb 2001 10:00:00 -0000",
                reply_to="<desk-1@enron.com>",
            ),
            "farmer-d/inbox/desk-3.": _mail(
                "<desk-3@enron.com>",
                "RE: Desk follow up",
                "Waiting on the desk. DESK-THIRD-TOKEN",
                date="Sat, 3 Feb 2001 10:00:00 -0000",
                reply_to="<desk-2@enron.com>",
            ),
            "farmer-d/inbox/cirque-a.": _mail(
                "<cirque-a@enron.com>",
                "Cirque note",
                "Cirque note text.",
                date="Mon, 29 Oct 2001 11:52:40 -0800",
                sender="Michelle <michelle.lokay@enron.com>",
            ),
            "farmer-d/inbox/cirque-b.": _mail(
                "<cirque-b@enron.com>",
                "Cirque note copy",
                "Cirque note text.",
                date="Mon, 29 Oct 2001 11:52:40 -0800",
                sender="Michelle <michelle.lokay@enron.com>",
            ),
            "farmer-d/inbox/calpine-1.": _mail(
                "<calpine-1@enron.com>",
                "Calpine daily gas nomination",
                "Calpine nomination volume 100.",
                date="Tue, 1 Aug 2000 08:00:00 -0000",
                sender="Ricky <rickya@calpine.com>",
            ),
            "farmer-d/inbox/calpine-2.": _mail(
                "<calpine-2@enron.com>",
                "Calpine daily gas nomination",
                "Calpine nomination volume 100.",
                date="Wed, 2 Aug 2000 08:00:00 -0000",
                sender="Ricky <rickya@calpine.com>",
            ),
            "farmer-d/inbox/undated.": _mail(
                "<undated@enron.com>",
                "RE: Daily nomination",
                "Not part of the thread.",
                date=None,
            ),
            "farmer-d/inbox/cue.": _mail(
                "<cue@enron.com>",
                "Valve question",
                "Please advise on the valve.",
                date="Mon, 9 Jul 2001 09:00:00 -0000",
            ),
            "farmer-d/inbox/sameid-1.": _mail(
                "<shared@enron.com>",
                "Alpha note",
                "Alpha body stays.",
                date="Mon, 4 Jun 2001 09:00:00 -0000",
            ),
            "farmer-d/inbox/sameid-2.": _mail(
                "<shared@enron.com>",
                "Beta note",
                "Beta body stays.",
                date="Tue, 5 Jun 2001 09:00:00 -0000",
            ),
            "farmer-d/inbox/split.": _mail(
                "<split@enron.com>",
                "Forwarded note unique",
                "New text stays first.\n\n -----Original Message-----\nFrom: z@enron.com\n\nolder text\n",
                date="Wed, 6 Jun 2001 09:00:00 -0000",
            ),
            "farmer-d/inbox/plain.": _mail(
                "<plain@enron.com>",
                "One off note",
                "Nothing repeated here.",
                date="Thu, 7 Jun 2001 09:00:00 -0000",
            ),
        }
        for relative, text in files.items():
            path = corpus / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def _assert_copies_and_threads(self, conn) -> None:
        """Cirque counts once, Calpine dates stay apart, and the undated reply stays out."""
        cirque = conn.execute(
            """
            SELECT copy_count, canonical_path FROM duplicate_groups
            WHERE canonical_path = 'farmer-d/inbox/cirque-a.'
            """
        ).fetchone()
        self.assertEqual(cirque["copy_count"], 2)
        self.assertEqual(
            conn.execute(
                """
                SELECT COUNT(*) FROM duplicate_groups AS g
                JOIN messages AS m ON m.path = g.canonical_path
                WHERE m.body LIKE 'Calpine nomination volume 100.%'
                """
            ).fetchone()[0],
            2,
        )
        shared = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE message_id = '<shared@enron.com>'"
        ).fetchone()[0]
        self.assertEqual(shared, 2)
        nomin_thread = conn.execute(
            "SELECT thread_id FROM messages WHERE path = 'farmer-d/inbox/nomin-1.'"
        ).fetchone()["thread_id"]
        undated_thread = conn.execute(
            "SELECT thread_id FROM messages WHERE path = 'farmer-d/inbox/undated.'"
        ).fetchone()["thread_id"]
        self.assertNotEqual(nomin_thread, undated_thread)
        segment = conn.execute(
            """
            SELECT body FROM body_segments
            WHERE message_path = 'farmer-d/inbox/split.' AND position = 0
            """
        ).fetchone()["body"]
        self.assertIn("New text stays first.", segment)
        self.assertNotIn("older text", segment)
        pieces = split_original_message("New text stays first.\n\n -----Original Message-----\nolder text\n")
        self.assertIn("New text stays first.", pieces[0])
        self.assertNotIn("older text", pieces[0])

    def _assert_edges_and_coverage(self, conn, file_count: int) -> None:
        """Subject and template edges share one component, and coverage sums to the files."""
        nomin = [
            "farmer-d/inbox/nomin-1.",
            "farmer-d/inbox/nomin-2.",
            "farmer-d/inbox/nomin-3.",
            "farmer-d/inbox/nomin-extra.",
        ]
        placeholders = ",".join("?" for _ in nomin)
        signals = {
            row["signal"]
            for row in conn.execute(
                f"""
                SELECT signal FROM candidate_edges
                WHERE left_path IN ({placeholders}) AND right_path IN ({placeholders})
                """,
                [*nomin, *nomin],
            )
        }
        self.assertIn("subject", signals)
        self.assertIn("template", signals)
        components = {
            row["component_id"]
            for row in conn.execute(
                f"SELECT component_id FROM component_members WHERE message_path IN ({placeholders})",
                nomin,
            )
        }
        self.assertEqual(len(components), 1)
        burst = conn.execute(
            """
            SELECT status, distinct_dates FROM components
            WHERE id = (
              SELECT component_id FROM component_members
              WHERE message_path = 'farmer-d/inbox/burst-1.'
            )
            """
        ).fetchone()
        self.assertEqual(burst["status"], "incident")
        self.assertEqual(burst["distinct_dates"], 1)
        dispositions = {
            row["disposition"]: row["n"]
            for row in conn.execute(
                "SELECT disposition, COUNT(*) AS n FROM coverage GROUP BY disposition"
            )
        }
        self.assertEqual(sum(dispositions.values()), file_count)
        self.assertEqual(dispositions.get("ingested_unreviewed", 0), 0)
        self.assertGreater(dispositions.get("fallback", 0), 0)
        self.assertGreater(dispositions.get("candidate", 0), 0)
        self.assertGreater(dispositions.get("cue_only", 0), 0)
        self.assertGreater(dispositions.get("duplicate", 0), 0)

    def _submit_observers(self, conn, output: Path) -> None:
        """Write one canned observer response per pack and record it."""
        manifest = json.loads((output / "packs" / "manifest.json").read_text(encoding="utf-8"))
        response_dir = output / "logs" / "responses"
        response_dir.mkdir(parents=True, exist_ok=True)
        for item in manifest:
            pack = json.loads(Path(item["path"]).read_text(encoding="utf-8"))
            payload = {"pack_id": pack["pack_id"], "results": _results(pack)}
            path = response_dir / f"{pack['pack_id']}.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            job = conn.execute(
                "SELECT id FROM jobs WHERE agent = 'observer' AND input_sha256 = ?",
                (pack["pack_id"],),
            ).fetchone()
            ok, error = submit_response(conn, int(job["id"]), path)
            self.assertTrue(ok, error)
        conn.commit()

    def _assert_priced(self, conn) -> None:
        """One nomination price, a same-day incident, people, and a cited delay."""
        nomin = conn.execute(
            "SELECT * FROM opportunities WHERE formula_json LIKE '%Confirm nomination volume%'"
        ).fetchall()
        published = [row for row in nomin if row["status"] == "published"]
        self.assertEqual(len(published), 1)
        row = published[0]
        formula = json.loads(row["formula_json"])
        self.assertEqual(row["occurrences"], 3)
        self.assertEqual(formula["occurrence_days"][0]["date"], "2001-01-05")
        self.assertEqual(len(formula["occurrence_days"][0]["paths"]), 2)
        self.assertEqual(row["minutes"], 60)
        self.assertEqual(row["fraction"], 1.0)
        self.assertEqual(row["months"], 1.0)
        expected = 3 / 1.0 * 60 / 60 * 1.0 * HOUR_RATE
        self.assertAlmostEqual(row["dollars_per_month"], expected)
        self.assertEqual(formula["participant_counts"]["senders"], 1)
        self.assertEqual(formula["participant_counts"]["recipients"], 2)
        self.assertEqual(formula["participants"]["from"]["a@enron.com"], 4)
        self.assertEqual(formula["participants"]["to"]["b@enron.com"], 4)
        self.assertEqual(formula["participants"]["cc"]["c@enron.com"], 4)
        owners = [
            item["message_path"]
            for item in conn.execute(
                """
                SELECT message_path FROM process_events
                WHERE process_id = ? AND assignment = 'primary'
                ORDER BY message_path
                """,
                (row["process_id"],),
            )
        ]
        self.assertEqual(
            owners,
            [
                "farmer-d/inbox/nomin-1.",
                "farmer-d/inbox/nomin-2.",
                "farmer-d/inbox/nomin-3.",
                "farmer-d/inbox/nomin-extra.",
            ],
        )
        secondary = conn.execute(
            """
            SELECT COUNT(*) FROM process_events
            WHERE assignment = 'secondary'
              AND message_path = 'farmer-d/inbox/nomin-1.'
            """
        ).fetchone()[0]
        self.assertEqual(secondary, 1)
        burst = conn.execute(
            "SELECT * FROM opportunities WHERE formula_json LIKE '%Burst check%'"
        ).fetchone()
        self.assertEqual(burst["status"], "incident")
        self.assertEqual(burst["occurrences"], 1)
        desk = conn.execute(
            "SELECT * FROM opportunities WHERE formula_json LIKE '%Desk follow up%'"
        ).fetchone()
        delay = json.loads(desk["formula_json"])["delay"]
        self.assertEqual(len(delay["citations"]), 2)
        for cite in delay["citations"]:
            stored = conn.execute(
                "SELECT date_raw FROM messages WHERE path = ?",
                (cite["path"],),
            ).fetchone()
            self.assertEqual(cite["date_raw"], stored["date_raw"])
            self.assertTrue(cite["path"].startswith("farmer-d/inbox/desk-"))

    def _submit_drafters(self, conn, corpus: Path, output: Path) -> None:
        """Ship language with no claims, and reject a claim whose quote is absent."""
        targets = json.loads((output / "draft_targets.json").read_text(encoding="utf-8"))["targets"]
        self.assertGreaterEqual(len(targets), 2)
        response_dir = output / "logs" / "drafts"
        response_dir.mkdir(parents=True, exist_ok=True)
        for target in targets:
            insight = json.loads(Path(target["insight_path"]).read_text(encoding="utf-8"))
            if insight["title"] == "Confirm nomination volume":
                draft = {
                    "opportunity_id": target["opportunity_id"],
                    "kind": "sop",
                    "proposed_language": "Confirm the volume before noon.",
                    "factual_claims": [],
                }
                self.assertTrue(validate_draft(corpus, set(insight["evidence_paths"]), draft)[0])
            else:
                draft = {
                    "opportunity_id": target["opportunity_id"],
                    "kind": "email",
                    "proposed_language": "Follow up with the desk.",
                    "factual_claims": [
                        {
                            "statement": "The desk was waiting.",
                            "quote": "This sentence was never in the file.",
                            "path": insight["evidence_paths"][0],
                        }
                    ],
                }
                ok, reason = validate_draft(corpus, set(insight["evidence_paths"]), draft)
                self.assertFalse(ok)
                self.assertEqual(reason, "not_in_file")
            path = response_dir / f"{target['opportunity_id']}.json"
            path.write_text(json.dumps(draft), encoding="utf-8")
            ok, error = submit_response(
                conn,
                int(target["job_id"]),
                path,
                expect_opportunity_id=target["opportunity_id"],
            )
            self.assertTrue(ok, error)

    def _pending(self, db_path: Path, output: Path, agent: str, prompt: str) -> list[str]:
        """Input hashes that still need a response after the latest prepare."""
        if agent == "observer":
            manifest = json.loads((output / "packs" / "manifest.json").read_text(encoding="utf-8"))
            current = {item["pack_id"] for item in manifest}
        else:
            targets = json.loads((output / "draft_targets.json").read_text(encoding="utf-8"))
            current = {item["input_sha256"] for item in targets["targets"]}
        conn = connect(db_path)
        try:
            rows = pending_jobs(
                conn,
                agent=agent,
                prompt_version=prompt,
                schema_version=SCHEMA_VERSION,
                input_sha256s=current,
            )
            return [row["input_sha256"] for row in rows]
        finally:
            conn.close()


def _results(pack: dict) -> list[dict]:
    """Canned observer results for one pack."""
    if pack["kind"] == "cues":
        return [{"reject": True, "reject_reason": "A cue mention is not a repeated process."}]
    results = []
    for component in pack["components"]:
        message = component["messages"][0]
        quote = message["spans"][0]
        base = {
            "process_title": "Unclassified work",
            "member_event_ids": component["member_paths"],
            "evidence": [{"span_id": quote["span_id"], "path": message["path"]}],
            "rationale": "Assumption only.",
            "minutes_per_occurrence": 30,
            "automatable_fraction": 0.5,
        }
        paths = " ".join(component["member_paths"])
        if "nomin" in paths:
            high = dict(base)
            high["process_title"] = "Confirm nomination volume"
            high["minutes_per_occurrence"] = 60
            high["automatable_fraction"] = 1.0
            low = dict(base)
            low["process_title"] = "Confirm nomination volume"
            low["minutes_per_occurrence"] = 15
            low["automatable_fraction"] = 0.25
            results.extend([high, low])
        elif "burst" in paths:
            base["process_title"] = "Burst check"
            results.append(base)
        elif "desk" in paths:
            base["process_title"] = "Desk follow up"
            results.append(base)
        else:
            results.append(base)
    return results


if __name__ == "__main__":
    unittest.main()
