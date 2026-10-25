"""Validate drafted artifacts and write the shipped report.

Factual claims that fail the raw-byte check keep the draft out of the report.
A missing required artifact, observer gap, or stale consolidation fails the
stage. The markdown report is rendered from the JSON document.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from src.data_treatment.budget import record_duration
from src.drafter.artifacts import validate_draft
from src.drafter.constants import DRAFTER_PROMPT_VERSION
from src.gates import GateError
from src.jobs.constants import SCHEMA_VERSION
from src.output_builder.fingerprint import stored_corpus_hash
from src.output_builder.report import build_report, final_dispositions, render_markdown



def finalize(conn: sqlite3.Connection, corpus: Path, output_dir: Path) -> dict:
    """Write artifacts that pass and write report.json plus report.md.

    The report is written either way. `complete` is false, and this function
    raises, when a required gate fails.
    """
    started = time.perf_counter()
    reasons = _finalize_reasons(conn, output_dir)
    artifacts, failures = _artifacts(conn, corpus, output_dir)
    if failures:
        reasons.append(f"artifact validation failed: {len(failures)}")
    report = build_report(conn)
    for item in report["opportunities"]:
        item["artifact"] = artifacts.get(item["id"])
    for item in report["incidents"]:
        item["artifact"] = None
    report["complete"] = not reasons
    report["failure_reasons"] = reasons
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "opportunities": len(report["opportunities"]),
        "incidents": len(report["incidents"]),
        "artifacts": len(artifacts),
        "artifact_failures": len(failures),
        "total_dollars_per_month": report["total_dollars_per_month"],
        "complete": report["complete"],
        "failure_reasons": reasons,
        "final_dispositions": report["final_dispositions"],
    }
    (log_dir / "finalize.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if failures:
        with (log_dir / "artifact_failures.jsonl").open("w", encoding="utf-8") as handle:
            for failure in failures:
                handle.write(json.dumps(failure, sort_keys=True) + "\n")
    record_duration(output_dir, "finalize", time.perf_counter() - started)
    if reasons:
        raise GateError("finalize", reasons)
    return summary


def _artifacts(
    conn: sqlite3.Connection, corpus: Path, output_dir: Path
) -> tuple[dict[str, dict], list[dict]]:
    """Artifacts whose factual claims passed, and the failures."""
    targets_path = output_dir / "draft_targets.json"
    if not targets_path.exists():
        return {}, []
    targets = json.loads(targets_path.read_text(encoding="utf-8"))["targets"]
    by_job = {item["job_id"]: item for item in targets}
    jobs = conn.execute(
        """
        SELECT id, response_path, input_sha256
        FROM jobs
        WHERE agent = 'drafter' AND status = 'succeeded'
          AND prompt_version = ? AND schema_version = ?
        """,
        (DRAFTER_PROMPT_VERSION, SCHEMA_VERSION),
    ).fetchall()
    shipped: dict[str, dict] = {}
    failures: list[dict] = []
    artifact_dir = output_dir / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        target = by_job.get(job["id"])
        if target is None or not job["response_path"]:
            continue
        draft = json.loads(Path(job["response_path"]).read_text(encoding="utf-8"))
        if draft.get("opportunity_id") != target["opportunity_id"]:
            failures.append({"opportunity_id": target["opportunity_id"], "reason": "opportunity_mismatch"})
            continue
        insight = json.loads(Path(target["insight_path"]).read_text(encoding="utf-8"))
        allowed = set(insight.get("evidence_paths") or [])
        ok, reason = validate_draft(corpus, allowed, draft, insight.get("quotes") or [])
        if not ok:
            failures.append(
                {"opportunity_id": target["opportunity_id"], "reason": reason}
            )
            continue
        path = artifact_dir / f"{target['opportunity_id']}.md"
        path.write_text(_render_artifact(insight["title"], draft), encoding="utf-8")
        shipped[target["opportunity_id"]] = {
            "kind": draft["kind"],
            "path": str(path),
            "proposed_language": draft.get("proposed_language") or "",
            "factual_claims": draft.get("factual_claims") or [],
        }
    return shipped, failures


def _render_artifact(title: str, draft: dict) -> str:
    """Markdown for one shipped draft."""
    lines = [
        f"# {title}",
        "",
        f"Kind: {draft['kind']}",
        "",
        "## Proposed language",
        "",
        draft.get("proposed_language") or "",
        "",
        "## Factual claims",
        "",
    ]
    claims = draft.get("factual_claims") or []
    if not claims:
        lines.append("None.")
    for claim in claims:
        lines.extend(
            [
                f"- {claim['statement']}",
                "",
                f"  > {claim['quote']}",
                "",
                f"  Source: `{claim['path']}`",
                "",
            ]
        )
    return "\n".join(lines)


def _finalize_reasons(conn: sqlite3.Connection, output_dir: Path) -> list[str]:
    """Consolidation, coverage, and drafter gates for this output."""
    reasons = []
    stamp_path = output_dir / "logs" / "consolidate.json"
    if not stamp_path.exists():
        reasons.append("consolidation has not completed")
    else:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        if stamp.get("status") != "succeeded":
            reasons.append("consolidation failed")
        elif stamp.get("corpus_sha256") != stored_corpus_hash(conn):
            reasons.append("consolidation is stale for this corpus")
    dispositions = final_dispositions(conn)
    if dispositions.get("ingested_unreviewed", 0):
        reasons.append(f"ingested_unreviewed={dispositions['ingested_unreviewed']}")
    files = int(conn.execute("SELECT COUNT(*) FROM coverage").fetchone()[0])
    if sum(dispositions.values()) != files:
        reasons.append("final dispositions do not cover every file")
    reasons.extend(_drafter_reasons(conn, output_dir))
    return reasons


def _drafter_reasons(conn: sqlite3.Connection, output_dir: Path) -> list[str]:
    """Selected draft jobs that have not succeeded."""
    targets_path = output_dir / "draft_targets.json"
    if not targets_path.exists():
        return ["draft targets are missing"]
    targets = json.loads(targets_path.read_text(encoding="utf-8"))["targets"]
    if not targets:
        return []
    rows = conn.execute(
        """
        SELECT id, status FROM jobs
        WHERE agent = 'drafter' AND prompt_version = ? AND schema_version = ?
        """,
        (DRAFTER_PROMPT_VERSION, SCHEMA_VERSION),
    ).fetchall()
    status = {int(row["id"]): row["status"] for row in rows}
    outstanding = [item["job_id"] for item in targets if status.get(int(item["job_id"])) != "succeeded"]
    if outstanding:
        return [f"drafter jobs not succeeded: {len(outstanding)}"]
    return []
