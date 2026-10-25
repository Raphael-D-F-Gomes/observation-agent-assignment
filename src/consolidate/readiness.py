"""Checks that must pass before consolidation starts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src.jobs.constants import SCHEMA_VERSION
from src.observer.constants import OBSERVER_PROMPT_VERSION


def observer_blockers(conn: sqlite3.Connection, output_dir: Path) -> list[str]:
    """Reasons the current observer jobs cannot be priced."""
    reasons = []
    manifest_path = output_dir / "packs" / "manifest.json"
    if not manifest_path.exists():
        return ["observer manifest is missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_ids = {item["pack_id"] for item in manifest if item.get("agent", "observer") == "observer"}
    stored = {row["pack_id"] for row in conn.execute("SELECT DISTINCT pack_id FROM pack_members")}
    if pack_ids != stored:
        reasons.append("pack manifest does not match stored pack membership")
    rows = conn.execute(
        """
        SELECT input_sha256, status FROM jobs
        WHERE agent = 'observer' AND prompt_version = ? AND schema_version = ?
        """,
        (OBSERVER_PROMPT_VERSION, SCHEMA_VERSION),
    ).fetchall()
    status = {row["input_sha256"]: row["status"] for row in rows}
    not_ready = [pack_id for pack_id in sorted(pack_ids) if status.get(pack_id) != "succeeded"]
    failed = [pack_id for pack_id in not_ready if status.get(pack_id) == "failed"]
    if not_ready:
        reasons.append(
            f"observer jobs not succeeded: {len(not_ready)} outstanding, {len(failed)} failed"
        )
    unreviewed = conn.execute(
        "SELECT COUNT(*) FROM coverage WHERE disposition = 'ingested_unreviewed'"
    ).fetchone()[0]
    if int(unreviewed):
        reasons.append(f"ingested_unreviewed={int(unreviewed)}")
    missing = conn.execute(
        """
        SELECT COUNT(*) FROM coverage AS c
        WHERE c.disposition IN ('candidate', 'cue_only', 'fallback')
          AND NOT EXISTS (
            SELECT 1 FROM pack_members AS p WHERE p.message_path = c.message_path
          )
        """
    ).fetchone()[0]
    if int(missing):
        reasons.append(f"{int(missing)} canonical events are not in a pack")
    return reasons
