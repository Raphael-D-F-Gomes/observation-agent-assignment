"""Checks for raw-byte quotes.

Scope: a temporary file and paths that try to leave it. No model.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from consolidate.quotes import check_quote


class QuoteTests(unittest.TestCase):
    """Exact quotes pass. Paraphrases and parent-directory paths fail."""

    def test_exact_quote_passes_and_paraphrase_fails(self) -> None:
        """A substring of the file passes. A reworded sentence does not."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            relative = "farmer-d/inbox/1."
            path = corpus / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(b"From: a@enron.com\n\nPlease confirm the volume.\n")
            exact = check_quote(corpus, relative, "Please confirm the volume.")
            self.assertTrue(exact.ok)
            self.assertIsNotNone(exact.byte_start)
            self.assertLess(exact.byte_start, exact.byte_end)
            paraphrase = check_quote(corpus, relative, "Please verify the quantity.")
            self.assertFalse(paraphrase.ok)
            self.assertEqual(paraphrase.fail_reason, "not_in_file")

    def test_parent_directory_path_is_rejected(self) -> None:
        """A path containing `..` is rejected before the file is read."""
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "corpus"
            corpus.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("hidden", encoding="utf-8")
            result = check_quote(corpus, "../secret.txt", "hidden")
            self.assertFalse(result.ok)
            self.assertEqual(result.fail_reason, "path_escape")
            empty = check_quote(corpus, "farmer-d/inbox/1.", "")
            self.assertEqual(empty.fail_reason, "empty_quote")


if __name__ == "__main__":
    unittest.main()
