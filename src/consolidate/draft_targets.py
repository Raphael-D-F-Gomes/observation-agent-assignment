"""Draft targets and the insight file for each one."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from src.drafter.artifacts import select_draft_targets
from src.jobs.queue import append_cache_log, ensure_job
from src.drafter.constants import DRAFT_THRESHOLD_DOLLARS, DRAFTER_PROMPT_VERSION
from src.jobs.constants import SCHEMA_VERSION


def write_draft_targets(conn: sqlite3.Connection, output_dir: Path) -> list[dict]:
    """Choose draft targets and record one drafter job per target."""
    rows = conn.execute("SELECT * FROM opportunities").fetchall()
    chosen = select_draft_targets(list(rows))
    targets = []
    cache_entries = []
    insight_dir = output_dir / "insights"
    insight_dir.mkdir(parents=True, exist_ok=True)
    for child in insight_dir.glob("*.json"):
        child.unlink()
    for row in chosen:
        formula = json.loads(row["formula_json"])
        assumptions = json.loads(row["assumptions_json"])
        quotes = opportunity_quotes(conn, row["process_id"])
        paths = [
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
        stable = {
            "prompt_version": DRAFTER_PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "opportunity_id": row["id"],
            "title": formula["title"],
            "dollars_per_month": row["dollars_per_month"],
            "formula": formula,
            "assumptions": assumptions,
            "quotes": quotes,
            "evidence_paths": paths,
            "sensitivity": {
                "hours_low": row["hours_low"],
                "hours_high": row["hours_high"],
                "dollars_low": row["dollars_low"],
                "dollars_high": row["dollars_high"],
            },
        }
        input_sha = hashlib.sha256(
            json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        insight = dict(stable)
        insight["input_sha256"] = input_sha
        (insight_dir / f"{input_sha}.json").write_text(
            json.dumps(insight, indent=2), encoding="utf-8"
        )
        job_id, cache = ensure_job(
            conn, "drafter", input_sha, DRAFTER_PROMPT_VERSION, SCHEMA_VERSION
        )
        cache_entries.append(
            {
                "agent": "drafter",
                "input_sha256": input_sha,
                "cache": cache,
                "job_id": job_id,
            }
        )
        targets.append(
            {
                "opportunity_id": row["id"],
                "job_id": job_id,
                "input_sha256": input_sha,
                "title": formula["title"],
                "dollars_per_month": row["dollars_per_month"],
                "insight_path": str(insight_dir / f"{input_sha}.json"),
            }
        )
    payload = {"threshold_dollars": DRAFT_THRESHOLD_DOLLARS, "targets": targets}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "draft_targets.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    append_cache_log(output_dir, cache_entries)
    return targets


def opportunity_quotes(conn: sqlite3.Connection, process_id: str) -> list[dict]:
    """Quotes that passed for one process."""
    rows = conn.execute(
        """
        SELECT o.obs_type, o.payload_json, e.quote, e.message_path, e.byte_start, e.byte_end,
               e.raw_sha256, e.display_text
        FROM observations AS o
        JOIN evidence_spans AS e ON e.id = o.evidence_id
        WHERE o.process_id = ? AND e.check_result = 'pass'
        ORDER BY o.id
        """,
        (process_id,),
    ).fetchall()
    quotes = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        quotes.append(
            {
                "kind": row["obs_type"],
                "quote": row["quote"],
                "path": row["message_path"],
                "byte_start": row["byte_start"],
                "byte_end": row["byte_end"],
                "raw_sha256": row["raw_sha256"],
                "display_text": row["display_text"] or row["quote"],
                "detail": payload,
            }
        )
    return quotes
