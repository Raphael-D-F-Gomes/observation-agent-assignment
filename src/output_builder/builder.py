"""Write the shipped report and serve the dashboard."""

from __future__ import annotations

from pathlib import Path

from src.output_builder.finalize import finalize
from src.output_builder.dashboard import serve


class OutputBuilder:
    """Finalize artifacts and serve the report."""

    def __init__(
        self,
        corpus: Path,
        db_path: Path,
        output: Path,
        port: int = 8765,
        report: Path | None = None,
    ) -> None:
        """Remember the corpus, the store, the output directory, and the port."""
        self.corpus = corpus
        self.db_path = db_path
        self.output = output
        self.port = port
        self.report = report or (output / "report.json")

    def finalize(self):
        """Check drafts and write report.json and report.md."""
        from src.db.connection import connect

        conn = connect(self.db_path)
        try:
            return finalize(conn, self.corpus, self.output)
        finally:
            conn.close()

    def serve(self) -> None:
        """Serve the dashboard until the process is stopped."""
        serve(self.report, self.corpus, port=self.port)
