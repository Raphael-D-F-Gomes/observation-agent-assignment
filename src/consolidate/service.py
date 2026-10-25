"""Validate observer responses, assign each event once, and price survivors.

Quotes are checked against the raw file. A process ships only when every
evidence quote passes. Stalls, role labels, and a stated cadence ship only
with their own passing quote. Each canonical path is primary on at most one
process. Dollars use primary dated events only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from src.data_treatment.corpus import Corpus
from src.consolidate.types import Assignment, Decision, ObservationKind, OpportunityStatus
from src.data_treatment.types import ComponentStatus
from src.consolidate.draft_targets import write_draft_targets
from src.consolidate.failures import log_quote_failures
from src.consolidate.membership import merge_agreed_components
from src.consolidate.ownership import assign_ownership
from src.consolidate.pricing import occurrence_facts, origin_mailbox, price, sent_hours
from src.consolidate.quotes import QuoteCheck, check_span
from src.consolidate.readiness import observer_blockers
from src.consolidate.constants import FRACTION_LADDER, HOUR_RATE, MINUTE_LADDER
from src.gates import GateError
from src.jobs.constants import SCHEMA_VERSION
from src.observer.constants import OBSERVER_PROMPT_VERSION


def consolidate(conn: sqlite3.Connection, corpus: Path, output_dir: Path) -> dict:
    """Rebuild findings from succeeded observer responses and write draft targets."""
    return Consolidator(conn, corpus, output_dir).run()


class Consolidation:
    """Quote-check, assign, and price the current observer responses."""

    def __init__(self, corpus: Path, db_path: Path, output: Path) -> None:
        """Remember the corpus, the store, and the output directory."""
        self.corpus = corpus
        self.db_path = db_path
        self.output = output

    def run(self) -> dict:
        """Validate quotes, assign events, and write draft targets."""
        from src.db.connection import connect

        conn = connect(self.db_path)
        try:
            summary = consolidate(conn, self.corpus, self.output)
            conn.commit()
            return summary
        except GateError:
            conn.rollback()
            raise
        finally:
            conn.close()


class Consolidator:
    """Rebuild findings from succeeded observer responses."""

    def __init__(self, conn: sqlite3.Connection, corpus: Path, output_dir: Path) -> None:
        """Keep the store, the corpus root, and the output directory."""
        self.connection = conn
        self.corpus = Corpus(corpus)
        self.output_dir = output_dir

    def run(self) -> dict:
        """Validate quotes, assign events, price survivors, and write draft targets."""
        reasons = observer_blockers(self.connection, self.output_dir)
        if reasons:
            _write_stamp(self.output_dir, self.connection, "failed", reasons)
            raise GateError("consolidate", reasons)
        _clear_findings(self.connection)
        decisions, failures, rejected_keys = self._collect()
        required = [item for item in failures if item.get("required")]
        log_quote_failures(self.output_dir, failures)
        if required:
            reasons = [f"required evidence failed: {len(required)}"]
            _write_stamp(self.output_dir, self.connection, "failed", reasons)
            raise GateError("consolidate", reasons)
        decisions = merge_agreed_components(self.connection, decisions, rejected_keys)
        for decision in decisions:
            decision.provisional_dollars = price(
                _sent_ats(self.connection, decision.paths),
                decision.minutes,
                decision.fraction,
            ).dollars_per_month
        assign_ownership(decisions)
        _persist(self.connection, decisions)
        targets = write_draft_targets(self.connection, self.output_dir)
        published = self.connection.execute(
            "SELECT COUNT(*) FROM opportunities WHERE status = 'published'"
        ).fetchone()[0]
        summary = {
            "processes": len(decisions),
            "published": int(published),
            "draft_targets": len(targets),
            "quote_failures": len(failures),
        }
        _write_stamp(self.output_dir, self.connection, "succeeded", [])
        return summary

    def _collect(self) -> tuple[list[Decision], list[dict], set[str]]:
        """Accepted decisions, quote failures, and component keys that were rejected."""
        manifest_path = self.output_dir / "packs" / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else []
        )
        pack_ids = {
            item["pack_id"] for item in manifest if item.get("agent", "observer") == "observer"
        }
        jobs = self.connection.execute(
            """
            SELECT id, input_sha256, response_path
            FROM jobs
            WHERE agent = 'observer' AND status = 'succeeded'
              AND prompt_version = ? AND schema_version = ?
            ORDER BY id
            """,
            (OBSERVER_PROMPT_VERSION, SCHEMA_VERSION),
        ).fetchall()
        decisions: list[Decision] = []
        failures: list[dict] = []
        rejected_keys: set[str] = set()
        for job in jobs:
            if job["input_sha256"] not in pack_ids or not job["response_path"]:
                continue
            payload = json.loads(Path(job["response_path"]).read_text(encoding="utf-8"))
            pack_id = job["input_sha256"]
            for index, result in enumerate(payload.get("results") or []):
                if result.get("reject") is True:
                    rejected_keys.update(_rejected_keys(self.connection, pack_id, result))
                    continue
                decision, failed = _accept(
                    self.connection, self.corpus, result, int(job["id"]), index, pack_id
                )
                failures.extend(failed)
                if decision is not None:
                    decisions.append(decision)
        return decisions, failures, rejected_keys


def _clear_findings(conn: sqlite3.Connection) -> None:
    """Remove findings so they can be rebuilt from the current responses."""
    conn.execute("DELETE FROM observations")
    conn.execute("DELETE FROM evidence_spans")
    conn.execute("DELETE FROM process_events")
    conn.execute("DELETE FROM opportunities")


def _accept(
    conn: sqlite3.Connection,
    corpus: Corpus,
    result: dict,
    job_id: int,
    index: int,
    pack_id: str,
) -> tuple[Decision | None, list[dict]]:
    """Turn one observer result into a decision, or drop it when evidence fails."""
    failures: list[dict] = []
    members = _canonical_members(conn, result.get("member_event_ids") or [])
    excluded = set(result.get("counterexamples") or [])
    members = [path for path in members if path not in excluded]
    allowed = {
        row["message_path"]
        for row in conn.execute(
            "SELECT message_path FROM pack_members WHERE pack_id = ?",
            (pack_id,),
        )
    }
    outside = [path for path in members if path not in allowed]
    if outside:
        return None, [
            {
                "path": outside[0],
                "quote": "",
                "fail_reason": "member_outside_pack",
                "required": True,
            }
        ]
    component_id = _component_for(conn, members)
    if component_id is not None:
        member_set = {
            row["message_path"]
            for row in conn.execute(
                "SELECT message_path FROM component_members WHERE component_id = ?",
                (component_id,),
            )
        }
        members = [path for path in members if path in member_set]
    evidence, evidence_failures = _checked_quotes(
        conn, corpus, result.get("evidence") or [], pack_id, required=True
    )
    failures.extend(evidence_failures)
    if not members or not evidence or evidence_failures:
        if component_id is not None:
            conn.execute(
                "UPDATE components SET status = ? WHERE id = ?",
                (ComponentStatus.REJECTED, component_id),
            )
        return None, failures
    stalls, stall_failures = _checked_quotes(
        conn, corpus, result.get("stalls") or [], pack_id, typed=True
    )
    roles, role_failures = _checked_quotes(
        conn, corpus, result.get("participant_roles") or [], pack_id, labeled=True
    )
    failures.extend(stall_failures)
    failures.extend(role_failures)
    cadence = None
    raw_cadence = result.get("stated_cadence")
    if isinstance(raw_cadence, dict) and raw_cadence.get("quote") and raw_cadence.get("path"):
        checked, cadence_failures = _checked_quotes(conn, corpus, [raw_cadence], pack_id)
        failures.extend(cadence_failures)
        cadence = checked[0] if checked else None
    sent_ats = _sent_ats(conn, members)
    provisional = price(
        sent_ats,
        float(result["minutes_per_occurrence"]),
        float(result["automatable_fraction"]),
    )
    return (
        Decision(
            title=result["process_title"],
            component_id=component_id,
            job_id=job_id,
            paths=members,
            evidence=evidence,
            stalls=stalls,
            roles=roles,
            cadence=cadence,
            minutes=float(result["minutes_per_occurrence"]),
            fraction=float(result["automatable_fraction"]),
            rationale=result.get("rationale") or "",
            ordinal=index,
            provisional_dollars=provisional.dollars_per_month,
            pack_id=pack_id,
            component_key=_component_key_for(conn, pack_id, members),
        ),
        failures,
    )


def _persist(conn: sqlite3.Connection, decisions: list[Decision]) -> None:
    """Write process events, opportunities, and the quotes that passed."""
    for decision in decisions:
        process_id = _process_id(decision)
        for path in decision.primary:
            conn.execute(
                """
                INSERT INTO process_events (process_id, message_path, assignment)
                VALUES (?, ?, ?)
                """,
                (process_id, path, Assignment.PRIMARY),
            )
        for path in decision.secondary:
            conn.execute(
                """
                INSERT OR REPLACE INTO process_events (process_id, message_path, assignment)
                VALUES (?, ?, ?)
                """,
                (process_id, path, Assignment.SECONDARY),
            )
        if not decision.primary:
            _insert_observation(
                conn,
                process_id,
                decision.component_id,
                decision.job_id,
                ObservationKind.SECONDARY,
                {"title": decision.title, "paths": decision.secondary},
                None,
            )
            continue
        status, sent_ats = _status_and_times(conn, decision.primary)
        if decision.component_id is not None:
            component_status = {
                OpportunityStatus.PUBLISHED: ComponentStatus.RECURRING,
                OpportunityStatus.INCIDENT: ComponentStatus.INCIDENT,
                OpportunityStatus.REJECTED: ComponentStatus.REJECTED,
            }[status]
            conn.execute(
                "UPDATE components SET status = ? WHERE id = ?",
                (component_status, decision.component_id),
            )
        if status == OpportunityStatus.REJECTED:
            continue
        priced = price(sent_ats, decision.minutes, decision.fraction)
        days = _days(conn, decision.primary)
        census = _census(conn, decision.primary)
        mailboxes = sorted(
            {
                origin_mailbox(row["origin_raw"])
                for row in conn.execute(
                    f"""
                    SELECT origin_raw FROM messages
                    WHERE path IN ({",".join("?" for _ in decision.primary)})
                    """,
                    decision.primary,
                )
                if row["origin_raw"]
            }
        )
        delay = _delay(conn, decision.primary)
        opportunity_id = "opp-" + process_id.removeprefix("proc-")
        assumptions = {
            "label": "assumption",
            "minutes": priced.minutes,
            "fraction": priced.fraction,
            "minutes_proposed": decision.minutes,
            "fraction_proposed": decision.fraction,
            "rationale": decision.rationale,
            "ladder_minutes": list(MINUTE_LADDER),
            "ladder_fraction": list(FRACTION_LADDER),
        }
        formula = {
            "title": decision.title,
            "occurrences": priced.occurrences,
            "months": priced.months,
            "span_days": priced.span_days,
            "minutes": priced.minutes,
            "fraction": priced.fraction,
            "rate": HOUR_RATE,
            "occurrence_days": days,
            "mailboxes": mailboxes,
            "participant_counts": {
                "senders": census["senders"],
                "recipients": census["recipients"],
            },
            "participants": census["by_role"],
            "participant_paths": census["paths"],
            "delay": delay,
            "rationale": decision.rationale,
            "expression": "occurrences / months * minutes / 60 * fraction * rate",
            "inclusion": [
                {"path": path, "reason": decision.inclusion_rule} for path in decision.primary
            ],
        }
        conn.execute(
            """
            INSERT INTO opportunities (
              id, process_id, status, occurrences, months, minutes, fraction,
              hours_per_month, dollars_per_month, hours_low, hours_high,
              dollars_low, dollars_high, stated_cadence, assumptions_json, formula_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                opportunity_id,
                process_id,
                status,
                priced.occurrences,
                priced.months,
                priced.minutes,
                priced.fraction,
                priced.hours_per_month,
                priced.dollars_per_month,
                priced.hours_low,
                priced.hours_high,
                priced.dollars_low,
                priced.dollars_high,
                decision.cadence["quote"] if decision.cadence else None,
                json.dumps(assumptions),
                json.dumps(formula),
            ),
        )
        _link_quotes(conn, decision, process_id, ObservationKind.EVIDENCE, decision.evidence)
        _link_quotes(conn, decision, process_id, ObservationKind.STALL, decision.stalls)
        _link_quotes(conn, decision, process_id, ObservationKind.ROLE, decision.roles)
        if decision.cadence:
            _link_quotes(conn, decision, process_id, ObservationKind.CADENCE, [decision.cadence])
        if delay:
            _insert_observation(
                conn,
                process_id,
                decision.component_id,
                decision.job_id,
                ObservationKind.DELAY,
                delay,
                None,
            )


def _link_quotes(
    conn: sqlite3.Connection,
    decision: Decision,
    process_id: str,
    obs_type: ObservationKind,
    items: list[dict],
) -> None:
    """Store each passing quote and an observation that points at it."""
    for item in items:
        evidence_id = _insert_span(conn, item, "pass", None)
        _insert_observation(
            conn,
            process_id,
            decision.component_id,
            decision.job_id,
            obs_type,
            item,
            evidence_id,
        )


def _insert_span(conn: sqlite3.Connection, item: dict, result: str, reason: str | None) -> int:
    """Insert one evidence span and return its row id."""
    cursor = conn.execute(
        """
        INSERT INTO evidence_spans (
          message_path, quote, byte_start, byte_end, raw_sha256, display_text,
          check_result, fail_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["path"],
            item["quote"],
            item.get("byte_start"),
            item.get("byte_end"),
            item.get("raw_sha256"),
            item.get("display_text") or item["quote"],
            result,
            reason,
        ),
    )
    return int(cursor.lastrowid)


def _insert_observation(
    conn: sqlite3.Connection,
    process_id: str | None,
    component_id: int | None,
    job_id: int,
    obs_type: ObservationKind,
    payload: dict,
    evidence_id: int | None,
) -> None:
    """Insert one observation row."""
    conn.execute(
        """
        INSERT INTO observations (
          process_id, component_id, job_id, obs_type, payload_json, evidence_id
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (process_id, component_id, job_id, obs_type, json.dumps(payload), evidence_id),
    )


def _checked_quotes(
    conn: sqlite3.Connection,
    corpus: Corpus,
    items: list[dict],
    pack_id: str,
    *,
    typed: bool = False,
    labeled: bool = False,
    required: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Check span ids against the pack and the raw file."""
    passed = []
    failed = []
    for item in items:
        span_id = item.get("span_id") or ""
        path = item.get("path") or ""
        row = conn.execute("SELECT * FROM pack_spans WHERE span_id = ?", (span_id,)).fetchone()
        if row is None or row["pack_id"] != pack_id or row["message_path"] != path:
            record = {"quote": item.get("quote") or "", "path": path, "span_id": span_id}
            failed.append({**record, "fail_reason": "span_outside_pack", "required": required})
            continue
        result = check_span(
            corpus.path,
            path,
            row["display_text"],
            int(row["byte_start"]),
            int(row["byte_end"]),
            row["raw_sha256"],
            row["transfer_encoding"],
        )
        record = {
            "quote": row["display_text"],
            "path": path,
            "byte_start": result.byte_start,
            "byte_end": result.byte_end,
            "raw_sha256": row["raw_sha256"],
            "display_text": row["display_text"],
            "span_id": span_id,
        }
        if typed:
            record["type"] = item.get("type") or "waiting"
        if labeled:
            record["label"] = item.get("label") or ""
        if result.ok:
            passed.append(record)
        else:
            failed.append({**record, "fail_reason": result.fail_reason, "required": required})
            _insert_span(conn, record, "fail", result.fail_reason)
    return passed, failed


def _with_encoding(conn: sqlite3.Connection, path: str, result: QuoteCheck) -> QuoteCheck:
    """Replace a plain miss with the transfer encoding when the body was encoded."""
    if result.ok or result.fail_reason != "not_in_file":
        return result
    row = conn.execute("SELECT cte FROM messages WHERE path = ?", (path,)).fetchone()
    cte = (row["cte"] or "").lower() if row else ""
    if cte in {"quoted-printable", "base64"}:
        return QuoteCheck(False, result.path, None, None, cte)
    return result


def _canonical_members(conn: sqlite3.Connection, paths: list[str]) -> list[str]:
    """Canonical paths named by an observer, in the order given."""
    canonical = {
        row["canonical_path"]
        for row in conn.execute("SELECT canonical_path FROM duplicate_groups")
    }
    seen = []
    for path in paths:
        if path in canonical and path not in seen:
            seen.append(path)
    return seen


def _component_for(conn: sqlite3.Connection, paths: list[str]) -> int | None:
    """The component that contains the most of these paths."""
    counts: Counter[int] = Counter()
    for path in paths:
        row = conn.execute(
            "SELECT component_id FROM component_members WHERE message_path = ?",
            (path,),
        ).fetchone()
        if row:
            counts[int(row["component_id"])] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _component_key_for(conn: sqlite3.Connection, pack_id: str, paths: list[str]) -> str | None:
    """Component key shared by these paths in the pack that proposed them."""
    if not paths:
        return None
    rows = conn.execute(
        f"""
        SELECT component_key, COUNT(*) AS n
        FROM pack_members
        WHERE pack_id = ? AND message_path IN ({",".join("?" for _ in paths)})
          AND component_key IS NOT NULL
        GROUP BY component_key
        ORDER BY n DESC, component_key
        """,
        (pack_id, *paths),
    ).fetchall()
    if not rows:
        return None
    return rows[0]["component_key"]


def _rejected_keys(conn: sqlite3.Connection, pack_id: str, result: dict) -> set[str]:
    """Component keys covered by a rejection in this pack."""
    paths = list(result.get("member_event_ids") or [])
    if paths:
        rows = conn.execute(
            f"""
            SELECT DISTINCT component_key FROM pack_members
            WHERE pack_id = ? AND component_key IS NOT NULL
              AND message_path IN ({",".join("?" for _ in paths)})
            """,
            (pack_id, *paths),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT DISTINCT component_key FROM pack_members
            WHERE pack_id = ? AND component_key IS NOT NULL
            """,
            (pack_id,),
        ).fetchall()
    return {row["component_key"] for row in rows}


def _write_stamp(output_dir: Path, conn: sqlite3.Connection, status: str, reasons: list[str]) -> None:
    """Record whether consolidation matches the stored corpus."""
    from src.output_builder.fingerprint import stored_corpus_hash

    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "corpus_sha256": stored_corpus_hash(conn),
        "reasons": reasons,
    }
    (log_dir / "consolidate.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _mark_rejected(conn: sqlite3.Connection, result: dict) -> None:
    """Mark the component behind a rejected result."""
    members = _canonical_members(conn, result.get("member_event_ids") or [])
    component_id = _component_for(conn, members)
    if component_id is not None:
        conn.execute(
            "UPDATE components SET status = ? WHERE id = ?",
            (ComponentStatus.REJECTED, component_id),
        )


def _status_and_times(
    conn: sqlite3.Connection, paths: list[str]
) -> tuple[OpportunityStatus, list[str | None]]:
    """Published, incident, or rejected, plus the sent times used for pricing."""
    rows = conn.execute(
        f"SELECT sent_at FROM messages WHERE path IN ({','.join('?' for _ in paths)})",
        paths,
    ).fetchall()
    sent_ats = [row["sent_at"] for row in rows]
    occurrences, _months, _span, _days = occurrence_facts(sent_ats)
    if len(paths) >= 3 and occurrences >= 3:
        return OpportunityStatus.PUBLISHED, sent_ats
    if len(paths) >= 3 and occurrences < 3:
        return OpportunityStatus.INCIDENT, sent_ats
    return OpportunityStatus.REJECTED, sent_ats


def _sent_ats(conn: sqlite3.Connection, paths: list[str]) -> list[str | None]:
    """Sent times for these paths, including nulls."""
    if not paths:
        return []
    rows = conn.execute(
        f"SELECT sent_at FROM messages WHERE path IN ({','.join('?' for _ in paths)})",
        paths,
    )
    return [row["sent_at"] for row in rows]


def _days(conn: sqlite3.Connection, paths: list[str]) -> list[dict]:
    """Active days and the paths that fall on each day."""
    grouped: dict[str, list[str]] = defaultdict(list)
    rows = conn.execute(
        f"SELECT path, sent_at FROM messages WHERE path IN ({','.join('?' for _ in paths)})",
        paths,
    )
    for row in rows:
        if row["sent_at"]:
            grouped[row["sent_at"][:10]].append(row["path"])
    return [
        {"date": day, "paths": sorted(grouped[day])}
        for day in sorted(grouped)
    ]


def _census(conn: sqlite3.Connection, paths: list[str]) -> dict:
    """Distinct senders, distinct recipients, and address counts by header role."""
    placeholders = ",".join("?" for _ in paths)
    rows = conn.execute(
        f"""
        SELECT role, addr, message_path FROM participants
        WHERE message_path IN ({placeholders}) AND addr IS NOT NULL
        """,
        paths,
    ).fetchall()
    by_role: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    path_map: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in rows:
        by_role[row["role"]][row["addr"]] += 1
        path_map[row["role"]][row["addr"]].add(row["message_path"])
    senders = set(by_role.get("from", {}))
    recipients: set[str] = set()
    for role in ("to", "cc", "bcc"):
        recipients.update(by_role.get(role, {}))
    return {
        "senders": len(senders),
        "recipients": len(recipients),
        "by_role": {role: dict(sorted(counts.items())) for role, counts in sorted(by_role.items())},
        "paths": {
            role: {addr: sorted(path_set) for addr, path_set in sorted(addresses.items())}
            for role, addresses in sorted(path_map.items())
        },
    }


def _delay(conn: sqlite3.Connection, paths: list[str]) -> dict | None:
    """Median reply gap for these paths, with the raw dates of that pair."""
    placeholders = ",".join("?" for _ in paths)
    rows = conn.execute(
        f"""
        SELECT path, sent_at, date_raw, subject_raw, thread_id
        FROM messages
        WHERE path IN ({placeholders}) AND sent_at IS NOT NULL
        ORDER BY thread_id, sent_at, path
        """,
        paths,
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[row["thread_id"] or row["path"]].append(row)
    gaps = []
    chases = []
    for thread in grouped.values():
        for left, right in zip(thread, thread[1:]):
            gaps.append(
                (
                    sent_hours(left["sent_at"], right["sent_at"]),
                    left,
                    right,
                )
            )
            if _is_follow_up(right["subject_raw"]):
                chases.append(right)
    if not gaps:
        return None
    gaps.sort(key=lambda item: (item[0], item[1]["path"], item[2]["path"]))
    median = gaps[(len(gaps) - 1) // 2]
    _hours, left, right = median
    return {
        "median_reply_hours": median[0],
        "citations": [
            {"path": left["path"], "date_raw": left["date_raw"]},
            {"path": right["path"], "date_raw": right["date_raw"]},
        ],
        "chase_count": len(chases),
        "chase_paths": [row["path"] for row in chases],
    }


def _is_follow_up(subject: str) -> bool:
    """True when the subject starts with a reply or forward prefix."""
    folded = subject.strip().lower()
    return folded.startswith(("re:", "fw:", "fwd:", "re :", "fw :", "fwd :"))


def _process_id(decision: Decision) -> str:
    """Stable process id from the title, result ordinal, paths, and job."""
    basis = "\n".join(
        [
            decision.title,
            str(decision.ordinal),
            *sorted(decision.primary or decision.paths),
            str(decision.job_id),
        ]
    )
    return "proc-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


