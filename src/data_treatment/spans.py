"""Evidence spans taken from raw message bytes.

A span records the raw slice an observer was shown and the display text for
that slice. Quoted-printable and base64 text is mapped back to the encoded
bytes when the decoded text is not itself present in the file.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import quopri

from src.data_treatment.constants import CHUNK_CHARS, CHUNK_OVERLAP
from src.data_treatment.types import EvidenceSpan, SpanCoverage


def chunk_ranges(length: int, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Cover `length` with windows of `size` and `overlap`.

    A short text is one window. The last window always reaches the end.
    """
    if length <= 0:
        return [(0, 0)]
    if length <= size:
        return [(0, length)]
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(length, start + size)
        ranges.append((start, end))
        if end == length:
            break
        start = max(start + 1, end - overlap)
    return ranges


def spans_for_message(
    path: str,
    raw: bytes,
    display_text: str,
    transfer_encoding: str | None,
) -> list[EvidenceSpan]:
    """Build spans whose display text, joined with overlap, covers `display_text`."""
    text = display_text or ""
    ranges = chunk_ranges(len(text))
    spans: list[EvidenceSpan] = []
    search_from = 0
    for index, (start, end) in enumerate(ranges):
        chunk = text[start:end]
        found = find_loose(raw, chunk, search_from) if chunk else None
        if found is not None:
            raw_start, raw_end = found
            search_from = raw_start + 1
            coverage = SpanCoverage.COMPLETE if len(ranges) == 1 else SpanCoverage.CHUNK
            spans.append(_make(path, raw, raw_start, raw_end, chunk, transfer_encoding, coverage, index))
            continue
        body_start, body_end = body_bounds(raw)
        spans.append(
            _make(path, raw, body_start, body_end, chunk, transfer_encoding, SpanCoverage.ENCODED_CHUNK, index)
        )
    return spans


def find_loose(raw: bytes, display: str, start_at: int = 0) -> tuple[int, int] | None:
    """Byte span of `display` in `raw`, treating CRLF and LF as the same break."""
    if display == "":
        return None
    haystack = raw.decode("latin-1")
    normalized, starts, widths = _normalize_newlines(haystack)
    needle = display.replace("\r\n", "\n").replace("\r", "\n")
    origin = _normalized_index(starts, start_at)
    found = normalized.find(needle, origin)
    if found < 0:
        return None
    last = found + len(needle) - 1
    return starts[found], starts[last] + widths[last]


def body_bounds(raw: bytes) -> tuple[int, int]:
    """Byte range of the body, after the header block."""
    for separator in (b"\r\n\r\n", b"\n\n"):
        index = raw.find(separator)
        if index != -1:
            return index + len(separator), len(raw)
    return 0, len(raw)


def display_in_raw(blob: bytes, display: str, transfer_encoding: str | None) -> bool:
    """Whether `display` is the raw slice or part of its decoded body."""
    if display == "":
        return False
    if find_loose(blob, display) is not None:
        return True
    decoded = _decode_transfer(blob, transfer_encoding)
    if decoded is None:
        return False
    needle = display.replace("\r\n", "\n").replace("\r", "\n")
    haystack = decoded.replace("\r\n", "\n").replace("\r", "\n")
    return needle in haystack


def _make(
    path: str,
    raw: bytes,
    start: int,
    end: int,
    display: str,
    transfer_encoding: str | None,
    coverage: SpanCoverage,
    index: int,
) -> EvidenceSpan:
    """One span id and the hash of its raw slice."""
    start = max(0, min(start, len(raw)))
    end = max(start, min(end, len(raw)))
    blob = raw[start:end]
    raw_sha = hashlib.sha256(blob).hexdigest()
    identity = "\0".join(
        (
            path,
            str(start),
            str(end),
            raw_sha,
            str(index),
            hashlib.sha256(display.encode("utf-8")).hexdigest(),
            coverage,
        )
    )
    span_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return EvidenceSpan(
        span_id=span_id,
        path=path,
        byte_start=start,
        byte_end=end,
        raw_sha256=raw_sha,
        display_text=display,
        transfer_encoding=transfer_encoding,
        coverage=coverage,
    )


def _normalized_index(starts: list[int], raw_index: int) -> int:
    """Normalized cursor at or after a raw byte index."""
    for index, start in enumerate(starts):
        if start >= raw_index:
            return index
    return len(starts)


def _normalize_newlines(text: str) -> tuple[str, list[int], list[int]]:
    """Return newline-normalized text, raw starts, and raw widths."""
    chars: list[str] = []
    starts: list[int] = []
    widths: list[int] = []
    index = 0
    while index < len(text):
        if text[index] == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            chars.append("\n")
            starts.append(index)
            widths.append(2)
            index += 2
            continue
        chars.append(text[index])
        starts.append(index)
        widths.append(1)
        index += 1
    return "".join(chars), starts, widths


def _decode_transfer(blob: bytes, transfer_encoding: str | None) -> str | None:
    """Decode a transfer-encoded body to text. Malformed input returns null."""
    encoding = (transfer_encoding or "").lower()
    try:
        if encoding == "quoted-printable":
            decoded = quopri.decodestring(blob)
        elif encoding == "base64":
            decoded = base64.b64decode(blob, validate=False)
        else:
            decoded = blob
    except (binascii.Error, ValueError, TypeError):
        return None
    for charset in ("utf-8", "latin-1"):
        try:
            return decoded.decode(charset)
        except UnicodeDecodeError:
            continue
    return None
