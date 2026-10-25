"""Quote-failure log for one consolidation."""

from __future__ import annotations

import json
from pathlib import Path


def log_quote_failures(output_dir: Path, failures: list[dict]) -> None:
    """Overwrite the quote-failure log with this run's failures."""
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "quote_failures.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for failure in failures:
            handle.write(json.dumps(failure, sort_keys=True) + "\n")
