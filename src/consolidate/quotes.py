"""Prove a quote against the raw file bytes.

The path must stay inside the corpus after resolution. The quote must be an
exact byte substring of that file. A near miss is a failure. Search does not
use the stored body.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.consolidate.types import QuoteCheck


def check_span(
    corpus: Path,
    relative_path: str,
    quote: str,
    byte_start: int,
    byte_end: int,
    raw_sha256: str,
    transfer_encoding: str | None,
) -> QuoteCheck:
    """Confirm a stored span still matches the raw file and its display text."""
    from src.data_treatment.spans import display_in_raw

    resolved = resolve_corpus_file(corpus, relative_path)
    if resolved is None:
        reason = "path_escape" if _escapes(relative_path) else "missing_file"
        return QuoteCheck(False, None, None, None, reason)
    raw = resolved.read_bytes()
    if byte_start < 0 or byte_end > len(raw) or byte_start >= byte_end:
        return QuoteCheck(False, relative_path, None, None, "bad_span")
    blob = raw[byte_start:byte_end]
    if hashlib.sha256(blob).hexdigest() != raw_sha256:
        return QuoteCheck(False, relative_path, None, None, "hash_mismatch")
    if not display_in_raw(blob, quote, transfer_encoding):
        return QuoteCheck(False, relative_path, None, None, "display_mismatch")
    return QuoteCheck(True, relative_path, byte_start, byte_end, None)


def check_quote(corpus: Path, relative_path: str, quote: str) -> QuoteCheck:
    """Search `quote` in the corpus file at `relative_path`.

    Empty quotes fail. Paths that escape the corpus fail before any read.
    """
    if not quote:
        return QuoteCheck(False, None, None, None, "empty_quote")
    resolved = resolve_corpus_file(corpus, relative_path)
    if resolved is None:
        reason = "path_escape" if _escapes(relative_path) else "missing_file"
        return QuoteCheck(False, None, None, None, reason)
    raw = resolved.read_bytes()
    span = find_quote(raw, quote)
    if span is None:
        return QuoteCheck(False, relative_path, None, None, "not_in_file")
    return QuoteCheck(True, relative_path, span[0], span[1], None)


def resolve_corpus_file(corpus: Path, relative_path: str) -> Path | None:
    """Resolved file under `corpus`, or null when the path leaves it."""
    if not relative_path or _escapes(relative_path):
        return None
    root = corpus.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def find_quote(raw: bytes, quote: str) -> tuple[int, int] | None:
    """Byte offset of `quote` in `raw`, or null when it is absent.

    UTF-8 is tried first, then Latin-1. The match is exact.
    """
    if not quote:
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            needle = quote.encode(encoding)
        except UnicodeEncodeError:
            continue
        index = raw.find(needle)
        if index != -1:
            return index, index + len(needle)
    return None


def highlight_latin1(raw: bytes, quote: str) -> tuple[str, str, str] | None:
    """Split raw Latin-1 text into before, quote, and after.

    Uses the same byte search as quote checks. Null when the quote is absent.
    """
    span = find_quote(raw, quote)
    if span is None:
        return None
    text = raw.decode("latin-1")
    start, end = span
    return text[:start], text[start:end], text[end:]


def _escapes(relative_path: str) -> bool:
    """True when the path is absolute or contains a parent-directory segment."""
    if relative_path.startswith(("/", "\\")):
        return True
    parts = Path(relative_path).parts
    return ".." in parts
