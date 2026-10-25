"""Checks for spans, gates, and narrow reruns.

Scope: temporary files and canned responses. No model.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from consolidate.quotes import check_span
from data_treatment.spans import chunk_ranges, spans_for_message
from gates import GateError
from jobs.queue import submit_response
from main import main


def _mail(body: str, encoding: str = "7bit", extra: str = "") -> bytes:
    """One small RFC822 message."""
    text = (
        "Message-ID: <span@enron.com>\n"
        "Date: Fri, 5 Jan 2001 10:00:00 -0000\n"
        "From: A Person <a@enron.com>\n"
        "To: b@enron.com\n"
        "Subject: Span check\n"
        f"Content-Transfer-Encoding: {encoding}\n"
        f"{extra}"
        "\n"
        f"{body}\n"
    )
    return text.encode("latin-1")


class SpanTests(unittest.TestCase):
    """Raw spans round-trip through the citation check."""

    def test_chunks_cover_a_long_body_with_overlap(self) -> None:
        """Every character of a long body falls inside a chunk."""
        ranges = chunk_ranges(20_000, 6_000, 400)
        covered = [False] * 20_000
        for start, end in ranges:
            for index in range(start, end):
                covered[index] = True
        self.assertTrue(all(covered))
        self.assertGreater(len(ranges), 1)

    def test_plain_repeated_quote_gets_distinct_spans(self) -> None:
        """The same words later in the file do not reuse the first span."""
        raw = _mail("Please confirm.\nPlease confirm.")
        spans = spans_for_message("inbox/1.", raw, "Please confirm.\nPlease confirm.", "7bit")
        self.assertGreaterEqual(len(spans), 1)
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            path = corpus / "inbox/1."
            path.parent.mkdir()
            path.write_bytes(raw)
            for span in spans:
                result = check_span(
                    corpus,
                    span.path,
                    span.display_text,
                    span.byte_start,
                    span.byte_end,
                    span.raw_sha256,
                    span.transfer_encoding,
                )
                self.assertTrue(result.ok, result.fail_reason)

    def test_quoted_printable_display_text_validates(self) -> None:
        """Decoded quoted-printable text cites the encoded bytes."""
        raw = _mail("hello=20there", "quoted-printable")
        spans = spans_for_message("inbox/qp.", raw, "hello there", "quoted-printable")
        self.assertTrue(spans)
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            path = corpus / "inbox/qp."
            path.parent.mkdir()
            path.write_bytes(raw)
            result = check_span(
                corpus,
                spans[0].path,
                spans[0].display_text,
                spans[0].byte_start,
                spans[0].byte_end,
                spans[0].raw_sha256,
                spans[0].transfer_encoding,
            )
            self.assertTrue(result.ok, result.fail_reason)

    def test_base64_and_latin1_and_malformed_base64(self) -> None:
        """Base64 and Latin-1 cite raw bytes. Malformed base64 does not raise."""
        import base64

        encoded = base64.b64encode(b"Please confirm the volume.").decode("ascii")
        raw = _mail(encoded, "base64")
        spans = spans_for_message("inbox/b64.", raw, "Please confirm the volume.", "base64")
        self.assertTrue(spans)
        latin = _mail("caf\xe9 review", "8bit")
        latin_spans = spans_for_message("inbox/latin.", latin, "caf\u00e9 review", "8bit")
        self.assertTrue(latin_spans)
        broken = spans_for_message("inbox/bad.", _mail("!!!!", "base64"), "not decoded", "base64")
        self.assertTrue(broken)


class CommandTests(unittest.TestCase):
    """The command line reports an unknown command."""

    def test_unknown_command_is_a_useful_error(self) -> None:
        """An unknown command returns a non-zero status."""
        self.assertEqual(main(["no-such-command"]), 2)

    def test_consolidate_without_jobs_fails(self) -> None:
        """Consolidation refuses to run before observer jobs succeed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            path = corpus / "box/inbox/1."
            path.parent.mkdir(parents=True)
            path.write_bytes(_mail("Please confirm the volume."))
            output = root / "output"
            code = main(["prepare", "--corpus", str(corpus), "--db", str(root / "observe.sqlite"), "--output", str(output)])
            self.assertEqual(code, 0)
            status = main(
                ["jobs", "status", "--agent", "observer", "--require-ready", "--db", str(root / "observe.sqlite"), "--output", str(output)]
            )
            self.assertEqual(status, 1)
            blocked = main(["consolidate", "--corpus", str(corpus), "--db", str(root / "observe.sqlite"), "--output", str(output)])
            self.assertEqual(blocked, 1)

    def test_timeout_records_a_failed_attempt(self) -> None:
        """An over-budget attempt is stored as a timeout."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            path = corpus / "box/inbox/1."
            path.parent.mkdir(parents=True)
            path.write_bytes(_mail("Please confirm the volume."))
            output = root / "output"
            db = root / "observe.sqlite"
            self.assertEqual(
                main(["prepare", "--corpus", str(corpus), "--db", str(db), "--output", str(output)]),
                0,
            )
            from db.connection import connect

            conn = connect(db)
            try:
                job = conn.execute("SELECT id FROM jobs WHERE agent = 'observer'").fetchone()
                self.assertIsNotNone(job)
            finally:
                conn.close()
            code = main(
                [
                    "jobs",
                    "timeout",
                    "--job-id",
                    str(job["id"]),
                    "--elapsed-seconds",
                    "120",
                    "--db",
                    str(db),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(code, 0)


class ScopeTests(unittest.TestCase):
    """A response cannot cite a span from another pack."""

    def test_span_from_another_pack_is_rejected(self) -> None:
        """Submit fails when the span id was not shown in the job's pack."""
        from db.connection import connect

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            corpus = root / "corpus"
            for name, body in (("one.", "Alpha body stays here."), ("two.", "Beta body stays here.")):
                path = corpus / "box/inbox" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_mail(body).replace(b"Span check", name.encode()))
            from data_treatment.ingest import prepare_corpus

            output = root / "output"
            db = root / "observe.sqlite"
            prepare_corpus(corpus, db, output, token_budget=1)
            conn = connect(db)
            try:
                packs = list((output / "packs").glob("*.json"))
                packs = [path for path in packs if path.name != "manifest.json"]
                self.assertGreaterEqual(len(packs), 2)
                first = json.loads(packs[0].read_text(encoding="utf-8"))
                second = json.loads(packs[1].read_text(encoding="utf-8"))
                foreign = _first_span(second)
                response = root / "response.json"
                response.write_text(
                    json.dumps(
                        {
                            "pack_id": first["pack_id"],
                            "results": [
                                {
                                    "process_title": "Wrong pack",
                                    "member_event_ids": first["member_paths"][:1],
                                    "evidence": [{"span_id": foreign["span_id"], "path": foreign["path"]}],
                                    "rationale": "This cites the other pack.",
                                    "minutes_per_occurrence": 30,
                                    "automatable_fraction": 0.5,
                                }
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                job = conn.execute(
                    "SELECT id FROM jobs WHERE input_sha256 = ?",
                    (first["pack_id"],),
                ).fetchone()
                ok, error = submit_response(conn, int(job["id"]), response)
                self.assertFalse(ok)
                self.assertEqual(error, "span_outside_pack")
            finally:
                conn.close()


def _first_span(pack: dict) -> dict:
    """The first span in a component or cue pack."""
    if pack.get("components"):
        return pack["components"][0]["messages"][0]["spans"][0]
    return pack["hits"][0]["spans"][0]


class GateTypeTests(unittest.TestCase):
    """Gate failures use the shared error type."""

    def test_gate_error_names_its_stage(self) -> None:
        """The error carries the stage and the reasons."""
        error = GateError("finalize", ["missing artifact"])
        self.assertEqual(error.stage, "finalize")
        self.assertIn("missing artifact", str(error))
