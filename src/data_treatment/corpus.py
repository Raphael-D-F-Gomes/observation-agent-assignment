"""A corpus directory and the path rules for one file inside it."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.consolidate.quotes import QuoteCheck, check_quote, highlight_latin1, resolve_corpus_file


def list_corpus_files(corpus: Path) -> list[Path]:
    """Every file under a corpus directory, sorted by relative path."""
    files = [path for path in corpus.rglob("*") if path.is_file()]
    files.sort(key=lambda path: path.relative_to(corpus).as_posix())
    return files


def quick_fingerprint(corpus: Path) -> str:
    """Fingerprint of relative path, size, and modification time."""
    digest = hashlib.sha256()
    root = corpus.resolve()
    for path in list_corpus_files(root):
        stat = path.stat()
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


class Corpus:
    """One corpus root. A path that leaves the root is rejected."""

    def __init__(self, path: Path) -> None:
        """Resolve `path` as the corpus root."""
        self.path = path.resolve()

    def files(self) -> list[Path]:
        """Every file under the root, sorted by relative path."""
        return list_corpus_files(self.path)

    def check_quote(self, relative_path: str, quote: str) -> QuoteCheck:
        """Exact-byte search for `quote` in one file under this root."""
        return check_quote(self.path, relative_path, quote)

    def resolve(self, relative_path: str) -> Path | None:
        """The file at `relative_path`, or null when it leaves this root."""
        return resolve_corpus_file(self.path, relative_path)

    def highlight(self, relative_path: str, quote: str) -> tuple[str, str, str] | None:
        """Latin-1 text before the quote, the quote, and the text after it."""
        resolved = self.resolve(relative_path)
        if resolved is None or not quote:
            return None
        return highlight_latin1(resolved.read_bytes(), quote)
