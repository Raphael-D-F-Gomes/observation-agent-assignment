"""Cache of agent jobs and their attempts.

A job is unique for an agent, an input hash, a prompt version, and a schema
version. Succeeded jobs stay succeeded. A failed job can be listed again when
retries are allowed. Attempt rows keep timing and the response path.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.drafter.types import DraftKind
from src.jobs.constants import MAX_ATTEMPTS
from src.jobs.types import AgentName, JobStatus
from src.consolidate.constants import FRACTION_LADDER, MINUTE_LADDER


def ensure_job(
    conn: sqlite3.Connection,
    agent: str,
    input_sha256: str,
    prompt_version: str,
    schema_version: str,
) -> tuple[int, str]:
    """Return the job id and whether this input is already succeeded.

    The second value is `hit` when a succeeded row exists, otherwise `miss`.
    A missing row is inserted as pending.
    """
    row = conn.execute(
        """
        SELECT id, status FROM jobs
        WHERE agent = ? AND input_sha256 = ? AND prompt_version = ? AND schema_version = ?
        """,
        (agent, input_sha256, prompt_version, schema_version),
    ).fetchone()
    if row is None:
        cursor = conn.execute(
            """
            INSERT INTO jobs (
              agent, input_sha256, prompt_version, schema_version, status, response_path
            ) VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (agent, input_sha256, prompt_version, schema_version, JobStatus.PENDING),
        )
        return int(cursor.lastrowid), "miss"
    if row["status"] == JobStatus.SUCCEEDED:
        return int(row["id"]), "hit"
    return int(row["id"]), "miss"


def pending_jobs(
    conn: sqlite3.Connection,
    *,
    agent: str,
    prompt_version: str,
    schema_version: str,
    input_sha256s: set[str],
    retry_failed: bool = True,
) -> list[sqlite3.Row]:
    """Jobs for these inputs that are not succeeded.

    Failed jobs are included only when `retry_failed` is set.
    """
    if not input_sha256s:
        return []
    placeholders = ",".join("?" for _ in input_sha256s)
    rows = conn.execute(
        f"""
        SELECT id, agent, input_sha256, prompt_version, schema_version, status, response_path
        FROM jobs
        WHERE agent = ? AND prompt_version = ? AND schema_version = ?
          AND input_sha256 IN ({placeholders})
        ORDER BY input_sha256
        """,
        (agent, prompt_version, schema_version, *sorted(input_sha256s)),
    ).fetchall()
    pending = []
    for row in rows:
        if row["status"] == JobStatus.SUCCEEDED:
            continue
        if row["status"] == JobStatus.FAILED and not retry_failed:
            continue
        pending.append(row)
    return pending


def record_attempt(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str,
    response_path: str | None,
    error: str | None,
    started_at: str,
    ended_at: str,
) -> int:
    """Append one attempt and update the job status. Returns the attempt number.

    A success marks the job succeeded. A failure marks it failed after the
    attempt ceiling, and leaves it pending before that.
    """
    current = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) FROM job_attempts WHERE job_id = ?",
        (job_id,),
    ).fetchone()[0]
    attempt = int(current) + 1
    duration_ms = _duration_ms(started_at, ended_at)
    conn.execute(
        """
        INSERT INTO job_attempts (
          job_id, attempt, started_at, ended_at, duration_ms, status, error, response_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, attempt, started_at, ended_at, duration_ms, status, error, response_path),
    )
    if status == JobStatus.SUCCEEDED:
        job_status = JobStatus.SUCCEEDED
    elif attempt >= MAX_ATTEMPTS:
        job_status = JobStatus.FAILED
    else:
        job_status = JobStatus.PENDING
    conn.execute(
        "UPDATE jobs SET status = ?, response_path = ? WHERE id = ?",
        (job_status, response_path if status == JobStatus.SUCCEEDED else None, job_id),
    )
    return attempt


def submit_response(
    conn: sqlite3.Connection,
    job_id: int,
    response_path: Path,
    *,
    started_at: str | None = None,
    ended_at: str | None = None,
    expect_opportunity_id: str | None = None,
) -> tuple[bool, str]:
    """Record an attempt for a response file. Returns success and an error string.

    Broken JSON and a response that fails the shape check are failed attempts.
    A drafter response must name `expect_opportunity_id` when that is provided.
    """
    started = started_at or _now()
    ended = ended_at or _now()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        return False, "unknown_job"
    try:
        payload = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        record_attempt(
            conn,
            job_id,
            status=JobStatus.FAILED,
            response_path=str(response_path),
            error=f"broken_json: {exc}",
            started_at=started,
            ended_at=ended,
        )
        return False, "broken_json"
    error = precheck_response(job["agent"], job["input_sha256"], payload)
    if error is None and job["agent"] == AgentName.OBSERVER:
        error = validate_observer_scope(conn, job["input_sha256"], payload)
    if (
        error is None
        and job["agent"] == AgentName.DRAFTER
        and expect_opportunity_id is not None
        and payload.get("opportunity_id") != expect_opportunity_id
    ):
        error = "opportunity_mismatch"
    if error:
        record_attempt(
            conn,
            job_id,
            status=JobStatus.FAILED,
            response_path=str(response_path),
            error=error,
            started_at=started,
            ended_at=ended,
        )
        return False, error
    record_attempt(
        conn,
        job_id,
        status=JobStatus.SUCCEEDED,
        response_path=str(response_path),
        error=None,
        started_at=started,
        ended_at=ended,
    )
    return True, ""


def precheck_response(agent: str, input_sha256: str, payload: object) -> str | None:
    """Return an error string when a response has the wrong shape, else null."""
    if not isinstance(payload, dict):
        return "not_an_object"
    if agent == AgentName.OBSERVER:
        return _precheck_observer(input_sha256, payload)
    if agent == AgentName.DRAFTER:
        return _precheck_drafter(payload)
    return "unknown_agent"


def append_cache_log(output_dir: Path, entries: list[dict]) -> None:
    """Append cache hit and miss lines. Prompt bodies are not included."""
    if not entries:
        return
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "cache.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _precheck_observer(pack_id: str, payload: dict) -> str | None:
    """Shape check for an observer response."""
    if payload.get("pack_id") != pack_id:
        return "pack_id_mismatch"
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return "missing_results"
    for result in results:
        if not isinstance(result, dict):
            return "result_not_object"
        if result.get("reject") is True:
            if not isinstance(result.get("reject_reason"), str) or not result["reject_reason"]:
                return "missing_reject_reason"
            continue
        if not isinstance(result.get("process_title"), str) or not result["process_title"]:
            return "missing_title"
        if not isinstance(result.get("member_event_ids"), list) or not result["member_event_ids"]:
            return "missing_members"
        evidence = result.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return "missing_evidence"
        for item in evidence:
            if not isinstance(item, dict) or not item.get("span_id") or not item.get("path"):
                return "bad_evidence"
        span_error = _span_items(result.get("stalls"), "bad_stall")
        if span_error:
            return span_error
        span_error = _span_items(result.get("participant_roles"), "bad_role")
        if span_error:
            return span_error
        cadence = result.get("stated_cadence")
        if cadence is not None and cadence != {}:
            if not isinstance(cadence, dict) or not cadence.get("span_id") or not cadence.get("path"):
                return "bad_cadence"
        if not isinstance(result.get("rationale"), str):
            return "missing_rationale"
        if not isinstance(result.get("minutes_per_occurrence"), (int, float)):
            return "missing_minutes"
        if not isinstance(result.get("automatable_fraction"), (int, float)):
            return "missing_fraction"
        if float(result["minutes_per_occurrence"]) not in MINUTE_LADDER:
            return "minutes_off_ladder"
        if float(result["automatable_fraction"]) not in FRACTION_LADDER:
            return "fraction_off_ladder"
    return None


def _span_items(items: object, error: str) -> str | None:
    """Require a span id and path on every optional citation."""
    if items is None:
        return None
    if not isinstance(items, list):
        return error
    for item in items:
        if not isinstance(item, dict) or not item.get("span_id") or not item.get("path"):
            return error
    return None


def validate_observer_scope(conn: sqlite3.Connection, pack_id: str, payload: dict) -> str | None:
    """Reject members, counterexamples, and spans that this pack did not show."""
    members = {
        row["message_path"]
        for row in conn.execute(
            "SELECT message_path FROM pack_members WHERE pack_id = ?",
            (pack_id,),
        )
    }
    spans = {
        row["span_id"]: row["message_path"]
        for row in conn.execute(
            "SELECT span_id, message_path FROM pack_spans WHERE pack_id = ?",
            (pack_id,),
        )
    }
    for result in payload["results"]:
        cited = list(result.get("member_event_ids") or [])
        cited.extend(result.get("counterexamples") or [])
        for path in cited:
            if path not in members:
                return "member_outside_pack"
        if result.get("reject") is True:
            continue
        citations = list(result.get("evidence") or [])
        citations.extend(result.get("stalls") or [])
        citations.extend(result.get("participant_roles") or [])
        cadence = result.get("stated_cadence")
        if isinstance(cadence, dict) and cadence.get("span_id"):
            citations.append(cadence)
        for item in citations:
            span_id = item.get("span_id")
            if span_id not in spans or spans[span_id] != item.get("path"):
                return "span_outside_pack"
    return None


def _precheck_drafter(payload: dict) -> str | None:
    """Shape check for a drafter response."""
    if not isinstance(payload.get("opportunity_id"), str):
        return "missing_opportunity"
    if payload.get("kind") not in set(DraftKind):
        return "bad_kind"
    if not isinstance(payload.get("proposed_language"), str):
        return "missing_language"
    claims = payload.get("factual_claims")
    if not isinstance(claims, list):
        return "missing_claims"
    for claim in claims:
        if not isinstance(claim, dict):
            return "bad_claim"
        if not claim.get("statement") or not claim.get("quote") or not claim.get("path"):
            return "bad_claim"
    return None


def _duration_ms(started_at: str, ended_at: str) -> int:
    """Milliseconds between two UTC timestamps."""
    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
    return max(0, int((end - start).total_seconds() * 1000))


def _now() -> str:
    """Current UTC time as a stored timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class JobQueue:
    """Open the job cache, list pending work, and record one response."""

    def __init__(self, db_path: Path) -> None:
        """Remember the database file."""
        self.db_path = db_path

    def connect(self):
        """A new connection with the tables present."""
        from src.db.connection import connect

        return connect(self.db_path)

    def ensure(self, conn, agent: str, input_sha256: str, prompt_version: str, schema_version: str):
        """Return the job id and whether this input already succeeded."""
        return ensure_job(conn, agent, input_sha256, prompt_version, schema_version)

    def pending(
        self,
        conn,
        *,
        agent: str,
        prompt_version: str,
        schema_version: str,
        input_sha256s: set[str],
        retry_failed: bool = True,
    ):
        """Jobs for these inputs that are not succeeded."""
        return pending_jobs(
            conn,
            agent=agent,
            prompt_version=prompt_version,
            schema_version=schema_version,
            input_sha256s=input_sha256s,
            retry_failed=retry_failed,
        )

    def submit(
        self,
        conn,
        job_id: int,
        response_path: Path,
        *,
        started_at: str | None = None,
        ended_at: str | None = None,
        expect_opportunity_id: str | None = None,
    ):
        """Record an attempt for a response file."""
        return submit_response(
            conn,
            job_id,
            response_path,
            started_at=started_at,
            ended_at=ended_at,
            expect_opportunity_id=expect_opportunity_id,
        )
