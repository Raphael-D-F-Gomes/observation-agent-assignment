"""Checks for corpus ingest.

Scope: temporary fixtures and the assignment corpus. No model and no network.
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

from main import main
from data_treatment.ingest import CoverageError, prepare_corpus, require_full_coverage
from data_treatment.parse import (
    normalize_subject,
    parse_address_list,
    parse_sent_at,
    sha256_bytes,
    split_original_message,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"

WRAPPED = """\
Message-ID: <wrapped@enron.com>\r
Date: Fri, 14 Dec 2001 08:11:23 -0800 (PST)\r
From: Jayant <jayant.krishnaswamy@enron.com>\r
To: mark.mcclure@enron.com, tammy.jaquet@enron.com\r
Subject: RE: Hello\r
\tWorld  Today\r
Cc: matt.pena@enron.com,\r
\tj..farmer@enron.com, Dfarmer@enron.com\r
X-To: should.not.be.used@enron.com\r
X-Origin: Farmer-D\r
Content-Transfer-Encoding: 7bit\r
\r
Please review.\r
\r
 -----Original Message-----\r
From: b@enron.com\r
\r
older text\r
"""

MISSING_DATE = """\
Message-ID: <nodate@enron.com>
From: darron.giron@enron.com
Subject:\r
X-From: Giron, Darron </O=ENRON/OU=NA/CN=RECIPIENTS/CN=Dgiron>
X-To: Mary.Poorman@enron.com
X-Origin: GIRON-D
Content-Transfer-Encoding: 7bit

Confirmed on my end.
"""

INVALID_DATE = """\
Message-ID: <baddate@enron.com>
Date: Tues, 32 Febuary 2001 25:61:00
From: clem.cernosek@enron.com
To: bill.williams@enron.com
Subject: Meter recap
X-Origin: WILLIAMS-W3

Bill, the meter recap is attached.
"""

QUOTED_PRINTABLE = """\
Message-ID: <qp@enron.com>
Date: Wed, 12 Sep 2001 11:52:22 -0700 (PDT)
From: carlos.giron@psiusa.com
To: c..giron@enron.com
Subject: Fwd: TRIBUTE
Mime-Version: 1.0
Content-Type: text/plain; charset=us-ascii
Content-Transfer-Encoding: quoted-printable
X-Origin: GIRON-D

hello=20there
"""

NOTES_TO = """\
Message-ID: <notesto@enron.com>
Date: Fri, 1 Jun 2001 07:50:54 -0700 (PDT)
From: rebecca.griffin@enron.com
Subject: Mobil Beaumont
X-To: Daren J Farmer/HOU/ECT@ENRON
X-Origin: FARMER-D

Please confirm.
"""

NOT_MAIL = b"This is not an email\nno headers really\n\nbody text stays\n"


def write_mail(root: Path, relative: str, content: str | bytes) -> Path:
    """Write one fixture message under a corpus root and return its path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("ascii") if isinstance(content, str) else content
    path.write_bytes(data)
    return path


class AddressAndDateTests(unittest.TestCase):
    """Address identity, subject text, dates, and body segmentation."""

    def test_obvious_emails_are_lowercased_and_not_merged(self) -> None:
        """Two different emails stay distinct, and the mixed-case one is lowercased."""
        identities = parse_address_list(
            "j..farmer@enron.com, Dfarmer@enron.com"
        )
        self.assertEqual(
            [addr for _, addr in identities],
            ["j..farmer@enron.com", "dfarmer@enron.com"],
        )

    def test_x500_display_name_stays_raw(self) -> None:
        """An X.500 address with a comma in the display name stays one raw string."""
        raw = "Farmer, Daren J. </O=ENRON/OU=NA/CN=RECIPIENTS/CN=Dfarmer>"
        identities = parse_address_list(raw)
        self.assertEqual(identities, [(raw, raw)])
        self.assertNotIn("dfarmer@enron.com", [addr for _, addr in identities])

    def test_x500_list_stays_two_people(self) -> None:
        """A comma-separated X.500 list stays two raw identities."""
        raw = (
            "Pena, Matt </O=ENRON/OU=NA/CN=RECIPIENTS/CN=Mpena2>, "
            "Superty, Robert </O=ENRON/OU=NA/CN=RECIPIENTS/CN=Rsupert>"
        )
        identities = parse_address_list(raw)
        self.assertEqual(len(identities), 2)
        self.assertTrue(all("@" not in addr for _, addr in identities))
        self.assertIn("Mpena2", identities[0][1])
        self.assertIn("Rsupert", identities[1][1])

    def test_notes_address_without_a_domain_dot_stays_raw(self) -> None:
        """A Notes path that is not an email stays the raw string."""
        raw = "Daren J Farmer/HOU/ECT@ENRON"
        self.assertEqual(parse_address_list(raw), [(raw, raw)])

    def test_duplicate_angle_address_is_one_identity(self) -> None:
        """The same email written twice in one token is one identity."""
        identities = parse_address_list("<'rlloyd@enron.com'> <rlloyd@enron.com>")
        self.assertEqual([addr for _, addr in identities], ["rlloyd@enron.com"])

    def test_subject_norm_strips_prefixes_and_collapses_space(self) -> None:
        """Leading reply prefixes are removed, whitespace collapses, and empty stays empty."""
        self.assertEqual(
            normalize_subject("RE: Hello\tWorld  Today"),
            "hello world today",
        )
        self.assertEqual(normalize_subject("Re: Fw: Fwd: Nomination"), "nomination")
        self.assertEqual(normalize_subject(""), "")
        self.assertEqual(normalize_subject("   "), "")

    def test_unparseable_date_is_null_not_a_timestamp(self) -> None:
        """A missing or impossible Date is null. A zoned Date becomes UTC."""
        self.assertIsNone(parse_sent_at(None))
        self.assertIsNone(parse_sent_at("Tues, 32 Febuary 2001 25:61:00"))
        self.assertEqual(
            parse_sent_at("Fri, 14 Dec 2001 08:11:23 -0800 (PST)"),
            "2001-12-14T16:11:23Z",
        )

    def test_leading_space_before_original_message_is_position_zero(self) -> None:
        """Text before a spaced Original Message line is segment 0."""
        body = (
            "Please review.\n\n"
            " -----Original Message-----\n"
            "From: b@enron.com\n\n"
            "older text\n"
        )
        segments = split_original_message(body)
        self.assertEqual(segments[0], "Please review.")
        self.assertIn("older text", segments[1])
        self.assertTrue(all("Original Message" not in segment for segment in segments))

    def test_forwarded_by_is_not_an_original_message_split(self) -> None:
        """A Forwarded-by line does not start a new segment."""
        body = "Note\n\n---------------------- Forwarded by Darron C Giron/HOU/ECT\n\nrest"
        segments = split_original_message(body)
        self.assertEqual(len(segments), 1)
        self.assertIn("Forwarded by", segments[0])


class PrepareTests(unittest.TestCase):
    """Round-trip of a small corpus through the working store."""

    def setUp(self) -> None:
        """Create an empty temporary corpus and database path."""
        import tempfile

        self._hold = tempfile.TemporaryDirectory()
        self.root = Path(self._hold.name)
        self.corpus = self.root / "corpus"
        self.db = self.root / "observe.sqlite"
        self.corpus.mkdir()

    def tearDown(self) -> None:
        """Remove the temporary corpus and database."""
        self._hold.cleanup()

    def _fixture(self) -> None:
        """Write the wrapped, undated, invalid, encoded, Notes, and broken fixtures."""
        write_mail(self.corpus, "farmer-d/logistics/1.", WRAPPED)
        write_mail(self.corpus, "giron-d/sent/795.", MISSING_DATE)
        write_mail(self.corpus, "williams-w3/inbox/142.", INVALID_DATE)
        write_mail(self.corpus, "giron-d/inbox/222.", QUOTED_PRINTABLE)
        write_mail(self.corpus, "farmer-d/logistics/120.", NOTES_TO)
        write_mail(self.corpus, "badbox/inbox/not-mail", NOT_MAIL)

    def test_fixture_round_trip(self) -> None:
        """Headers, fallbacks, segments, and a kept parse error survive prepare.

        The undated file's modification time is set and must not become sent_at.
        """
        self._fixture()
        missing = self.corpus / "giron-d/sent/795."
        os.utime(missing, (1_600_000_000, 1_600_000_000))

        stats = prepare_corpus(self.corpus, self.db)
        self.assertEqual(stats.file_count, 6)
        self.assertEqual(stats.message_count, 6)
        self.assertEqual(stats.parse_errors, 1)
        self.assertEqual(stats.parse_error_paths[0][0], "badbox/inbox/not-mail")

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            self.assertEqual(_count(conn, "SELECT COUNT(*) FROM raw_files"), 6)
            self.assertEqual(_count(conn, "SELECT COUNT(*) FROM messages"), 6)

            wrapped = conn.execute(
                "SELECT * FROM messages WHERE path = ?",
                ("farmer-d/logistics/1.",),
            ).fetchone()
            self.assertEqual(wrapped["date_raw"], "Fri, 14 Dec 2001 08:11:23 -0800 (PST)")
            self.assertEqual(wrapped["sent_at"], "2001-12-14T16:11:23Z")
            self.assertEqual(wrapped["subject_raw"], "RE: Hello\tWorld  Today")
            self.assertEqual(wrapped["subject_norm"], "hello world today")
            self.assertEqual(wrapped["mailbox"], "farmer-d")
            self.assertEqual(wrapped["folder"], "logistics")
            self.assertEqual(wrapped["origin_raw"], "Farmer-D")
            self.assertEqual(wrapped["cte"], "7bit")
            self.assertEqual(wrapped["from_addr"], "jayant.krishnaswamy@enron.com")
            self.assertIn("Please review.", wrapped["body"])
            self.assertIn("Original Message", wrapped["body"])
            self.assertEqual(
                wrapped["file_sha256"],
                sha256_bytes((self.corpus / "farmer-d/logistics/1.").read_bytes()),
            )

            cc = conn.execute(
                """
                SELECT addr FROM participants
                WHERE message_path = ? AND role = 'cc'
                ORDER BY position
                """,
                ("farmer-d/logistics/1.",),
            ).fetchall()
            self.assertEqual(
                [row["addr"] for row in cc],
                [
                    "matt.pena@enron.com",
                    "j..farmer@enron.com",
                    "dfarmer@enron.com",
                ],
            )
            to_sources = conn.execute(
                """
                SELECT source, addr FROM participants
                WHERE message_path = ? AND role = 'to'
                ORDER BY position
                """,
                ("farmer-d/logistics/1.",),
            ).fetchall()
            self.assertEqual(
                [(row["source"], row["addr"]) for row in to_sources],
                [
                    ("header", "mark.mcclure@enron.com"),
                    ("header", "tammy.jaquet@enron.com"),
                ],
            )

            segments = conn.execute(
                """
                SELECT position, body FROM body_segments
                WHERE message_path = ?
                ORDER BY position
                """,
                ("farmer-d/logistics/1.",),
            ).fetchall()
            self.assertEqual(segments[0]["position"], 0)
            self.assertEqual(segments[0]["body"], "Please review.")
            self.assertIn("older text", segments[1]["body"])

            undated = conn.execute(
                "SELECT * FROM messages WHERE path = ?",
                ("giron-d/sent/795.",),
            ).fetchone()
            self.assertIsNone(undated["date_raw"])
            self.assertIsNone(undated["sent_at"])
            self.assertEqual(undated["subject_raw"], "")
            self.assertEqual(undated["subject_norm"], "")
            fallback = conn.execute(
                """
                SELECT role, source, addr, raw FROM participants
                WHERE message_path = ? AND role = 'to'
                """,
                ("giron-d/sent/795.",),
            ).fetchone()
            self.assertEqual(fallback["source"], "x-header")
            self.assertEqual(fallback["addr"], "mary.poorman@enron.com")
            sender = conn.execute(
                """
                SELECT source, addr FROM participants
                WHERE message_path = ? AND role = 'from'
                """,
                ("giron-d/sent/795.",),
            ).fetchone()
            self.assertEqual(sender["source"], "header")
            self.assertEqual(sender["addr"], "darron.giron@enron.com")

            invalid = conn.execute(
                "SELECT date_raw, sent_at, parse_ok FROM messages "
                "JOIN raw_files USING (path) WHERE path = ?",
                ("williams-w3/inbox/142.",),
            ).fetchone()
            self.assertEqual(invalid["date_raw"], "Tues, 32 Febuary 2001 25:61:00")
            self.assertIsNone(invalid["sent_at"])
            self.assertEqual(invalid["parse_ok"], 1)

            notes = conn.execute(
                """
                SELECT source, raw, addr FROM participants
                WHERE message_path = ? AND role = 'to'
                """,
                ("farmer-d/logistics/120.",),
            ).fetchone()
            self.assertEqual(notes["source"], "x-header")
            self.assertEqual(notes["raw"], "Daren J Farmer/HOU/ECT@ENRON")
            self.assertEqual(notes["addr"], "Daren J Farmer/HOU/ECT@ENRON")

            decoded = conn.execute(
                "SELECT cte, body FROM messages WHERE path = ?",
                ("giron-d/inbox/222.",),
            ).fetchone()
            self.assertEqual(decoded["cte"], "quoted-printable")
            self.assertIn("hello there", decoded["body"])
            self.assertNotIn("=20", decoded["body"])

            broken = conn.execute(
                """
                SELECT m.message_id, m.body, r.parse_ok
                FROM messages AS m
                JOIN raw_files AS r ON r.path = m.path
                WHERE m.path = ?
                """,
                ("badbox/inbox/not-mail",),
            ).fetchone()
            self.assertEqual(broken["parse_ok"], 0)
            self.assertIn("body text stays", broken["body"])
            self.assertTrue(broken["message_id"].startswith("<unparsed-"))
        finally:
            conn.close()

    def test_second_prepare_does_not_duplicate_rows(self) -> None:
        """A second prepare of the same files keeps one row per file."""
        self._fixture()
        prepare_corpus(self.corpus, self.db)
        stats = prepare_corpus(self.corpus, self.db)
        self.assertEqual(stats.message_count, 6)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(_count(conn, "SELECT COUNT(*) FROM messages"), 6)
            self.assertEqual(_count(conn, "SELECT COUNT(*) FROM raw_files"), 6)
        finally:
            conn.close()

    def test_coverage_check_rejects_a_short_table(self) -> None:
        """A message table shorter than the file count is rejected."""
        self._fixture()
        prepare_corpus(self.corpus, self.db)
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            for table in (
                "observations",
                "evidence_spans",
                "process_events",
                "opportunities",
                "component_members",
                "components",
                "coverage",
                "candidate_edges",
                "duplicate_paths",
                "duplicate_groups",
                "message_refs",
                "body_segments",
                "participants",
            ):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM messages WHERE path = ?", ("badbox/inbox/not-mail",))
            conn.execute("DELETE FROM raw_files WHERE path = ?", ("badbox/inbox/not-mail",))
            conn.commit()
            with self.assertRaises(CoverageError):
                require_full_coverage(conn, 6)
        finally:
            conn.close()

    def test_cli_prepare_and_missing_corpus(self) -> None:
        """The prepare command succeeds on a fixture and fails when the corpus is missing."""
        self._fixture()
        code = main(
            [
                "prepare",
                "--corpus",
                str(self.corpus),
                "--db",
                str(self.db),
                "--output",
                str(self.root / "cli-output"),
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            main(["prepare", "--corpus", str(self.root / "missing"), "--db", str(self.db)]),
            1,
        )

    def test_package_does_not_hardcode_the_archive_size(self) -> None:
        """The package source does not embed this corpus's file count."""
        source = "\n".join(path.read_text() for path in (ROOT / "src").rglob("*.py"))
        self.assertNotIn("3240", source)


class RealCorpusTests(unittest.TestCase):
    """Ingest of the assignment corpus."""

    def test_one_row_per_file(self) -> None:
        """Every file on disk has one message, including the known date and header edges."""
        if not CORPUS.is_dir():
            self.skipTest("corpus is not present")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            stats = prepare_corpus(CORPUS, Path(tmp) / "observe.sqlite")
            on_disk = sum(1 for path in CORPUS.rglob("*") if path.is_file())
            self.assertEqual(stats.file_count, on_disk)
            self.assertEqual(stats.message_count, on_disk)
            self.assertEqual(stats.parse_errors, 0)
            self.assertEqual(stats.undated, 2)

            conn = sqlite3.connect(Path(tmp) / "observe.sqlite")
            conn.row_factory = sqlite3.Row
            try:
                undated = conn.execute(
                    "SELECT path, date_raw, sent_at FROM messages WHERE sent_at IS NULL ORDER BY path"
                ).fetchall()
                self.assertEqual(
                    [row["path"] for row in undated],
                    ["giron-d/sent/795.", "williams-w3/inbox/142."],
                )
                self.assertIsNone(undated[0]["date_raw"])
                self.assertEqual(
                    undated[1]["date_raw"], "Tues, 32 Febuary 2001 25:61:00"
                )

                empty = conn.execute(
                    "SELECT subject_raw, subject_norm FROM messages WHERE path = ?",
                    ("farmer-d/logistics/1053.",),
                ).fetchone()
                self.assertEqual(empty["subject_raw"], "")
                self.assertEqual(empty["subject_norm"], "")

                sample = conn.execute(
                    "SELECT * FROM messages WHERE path = ?",
                    ("farmer-d/logistics/1.",),
                ).fetchone()
                self.assertEqual(
                    sample["date_raw"], "Fri, 14 Dec 2001 08:11:23 -0800 (PST)"
                )
                self.assertEqual(sample["sent_at"], "2001-12-14T16:11:23Z")
                self.assertEqual(sample["origin_raw"], "FARMER-D")
                self.assertEqual(sample["mailbox"], "farmer-d")
                self.assertEqual(sample["folder"], "logistics")
                self.assertIn("66 physical deals", sample["body"])
                on_disk_hash = sha256_bytes((CORPUS / "farmer-d/logistics/1.").read_bytes())
                self.assertEqual(sample["file_sha256"], on_disk_hash)

                segment = conn.execute(
                    """
                    SELECT body FROM body_segments
                    WHERE message_path = ? AND position = 0
                    """,
                    ("farmer-d/logistics/1.",),
                ).fetchone()
                self.assertIn("66 physical deals", segment["body"])
                self.assertNotIn("Let's not assume", segment["body"])

                cc = [
                    row["addr"]
                    for row in conn.execute(
                        """
                        SELECT addr FROM participants
                        WHERE message_path = ? AND role = 'cc'
                        """,
                        ("farmer-d/logistics/1.",),
                    )
                ]
                self.assertIn("j..farmer@enron.com", cc)
                self.assertNotIn("dfarmer@enron.com", cc)

                notes = conn.execute(
                    """
                    SELECT source, addr FROM participants
                    WHERE message_path = ? AND role = 'to'
                    """,
                    ("farmer-d/logistics/120.",),
                ).fetchone()
                self.assertEqual(notes["source"], "x-header")
                self.assertEqual(notes["addr"], "Daren J Farmer/HOU/ECT@ENRON")

                misplaced = conn.execute(
                    "SELECT mailbox, folder, origin_raw FROM messages WHERE path = ?",
                    ("williams-w3/inbox/143.",),
                ).fetchone()
                self.assertEqual(misplaced["mailbox"], "williams-w3")
                self.assertEqual(misplaced["folder"], "inbox")
                self.assertEqual(misplaced["origin_raw"], "Farmer-D")

                huge = conn.execute(
                    "SELECT length(body) AS n FROM messages WHERE path = ?",
                    ("farmer-d/logistics/3221.",),
                ).fetchone()
                self.assertGreater(huge["n"], 200_000)

                self.assertEqual(
                    _count(conn, "SELECT COUNT(*) FROM messages"),
                    _count(conn, "SELECT COUNT(*) FROM body_segments WHERE position = 0"),
                )
                self.assertEqual(
                    _count(conn, "SELECT COUNT(*) FROM coverage"),
                    on_disk,
                )
                dispositions = {
                    row["disposition"]: row["n"]
                    for row in conn.execute(
                        "SELECT disposition, COUNT(*) AS n FROM coverage GROUP BY disposition"
                    )
                }
                self.assertEqual(sum(dispositions.values()), on_disk)
                self.assertEqual(dispositions.get("ingested_unreviewed", 0), 0)
                self.assertGreater(dispositions.get("fallback", 0), 0)
                reviewed = (
                    dispositions.get("candidate", 0)
                    + dispositions.get("cue_only", 0)
                    + dispositions.get("fallback", 0)
                )
                self.assertEqual(
                    reviewed,
                    _count(conn, "SELECT COUNT(DISTINCT message_path) FROM pack_members"),
                )
                huge_spans = conn.execute(
                    """
                    SELECT display_text FROM pack_spans
                    WHERE message_path = 'farmer-d/logistics/3221.'
                    ORDER BY byte_start, span_id
                    """
                ).fetchall()
                texts = [row["display_text"] for row in huge_spans]
                huge_body = conn.execute(
                    "SELECT body FROM messages WHERE path = 'farmer-d/logistics/3221.'"
                ).fetchone()["body"]
                self.assertGreater(len(texts), 1)
                self.assertTrue(any(huge_body[:80] in text for text in texts))
                self.assertTrue(any(huge_body[-80:] in text for text in texts))
                self.assertGreaterEqual(sum(len(text) for text in texts), len(huge_body))

                cirque = conn.execute(
                    """
                    SELECT g.copy_count, g.canonical_path
                    FROM duplicate_groups AS g
                    JOIN duplicate_paths AS p ON p.group_id = g.id
                    WHERE p.path = 'lokay-m/sent_items/1.'
                    """
                ).fetchone()
                self.assertEqual(cirque["copy_count"], 2)
                self.assertEqual(cirque["canonical_path"], "lokay-m/inbox/6.")

                calpine_hash = conn.execute(
                    """
                    SELECT body_norm_sha256, COUNT(*) AS n
                    FROM messages
                    WHERE from_addr = 'rickya@calpine.com'
                    GROUP BY body_norm_sha256
                    ORDER BY n DESC
                    LIMIT 1
                    """
                ).fetchone()
                calpine_paths = [
                    row["path"]
                    for row in conn.execute(
                        """
                        SELECT path FROM messages
                        WHERE body_norm_sha256 = ?
                        ORDER BY path
                        """,
                        (calpine_hash["body_norm_sha256"],),
                    )
                ]
                self.assertGreaterEqual(len(calpine_paths), 2)
                dates = {
                    row["sent_at"][:10]
                    for row in conn.execute(
                        f"""
                        SELECT sent_at FROM messages
                        WHERE path IN ({",".join("?" for _ in calpine_paths)})
                        """,
                        calpine_paths,
                    )
                    if row["sent_at"]
                }
                self.assertEqual(len(dates), len(calpine_paths))
                components = {
                    row["component_id"]
                    for row in conn.execute(
                        f"""
                        SELECT component_id FROM component_members
                        WHERE message_path IN ({",".join("?" for _ in calpine_paths)})
                        """,
                        calpine_paths,
                    )
                }
                self.assertEqual(len(components), 1)
                signals = {
                    row["signal"]
                    for row in conn.execute(
                        f"""
                        SELECT signal FROM candidate_edges
                        WHERE left_path IN ({",".join("?" for _ in calpine_paths)})
                          AND right_path IN ({",".join("?" for _ in calpine_paths)})
                        """,
                        [*calpine_paths, *calpine_paths],
                    )
                }
                self.assertIn("subject", signals)
                self.assertIn("template", signals)
                self.assertTrue(stats.gate_ok)
                self.assertGreater(stats.pack_count, 0)
                self.assertGreater(stats.component_count, 0)
            finally:
                conn.close()


def _count(conn: sqlite3.Connection, sql: str) -> int:
    """Integer result of a single-value query."""
    return int(conn.execute(sql).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
