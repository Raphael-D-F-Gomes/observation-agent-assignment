"""Build the shipped report from the working store.

Numbers in the report are the stored opportunity columns. Narrative fields
come from the formula payload and from quotes that passed. Incidents,
secondary interpretations, and unreviewed files stay out of the dollar total.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from src.consolidate.constants import HOUR_RATE
from src.consolidate.types import Assignment, OpportunityStatus
from src.data_treatment.types import CoverageDisposition
from src.output_builder.fingerprint import stored_corpus_hash


def build_report(conn: sqlite3.Connection) -> dict:
    """The single JSON document that holds the shipped numbers."""
    coverage = _coverage(conn)
    published = _opportunities(conn, OpportunityStatus.PUBLISHED)
    incidents = _opportunities(conn, OpportunityStatus.INCIDENT)
    total_dollars = sum(item["dollars_per_month"] for item in published)
    total_hours = sum(item["hours_per_month"] for item in published)
    dispositions = final_dispositions(conn)
    for item in published + incidents:
        item["provenance"] = _opportunity_provenance(item)
    report = {
        "schema_version": "1",
        "corpus_sha256": stored_corpus_hash(conn),
        "rate_per_hour": HOUR_RATE,
        "scope": (
            "Labor in these four mailboxes, not the whole company. "
            "Minutes and the automatable fraction are assumptions, not evidence."
        ),
        "total_dollars_per_month": total_dollars,
        "total_hours_per_month": total_hours,
        "sensitivity": {
            "dollars_low": sum(item["dollars_low"] for item in published),
            "dollars_high": sum(item["dollars_high"] for item in published),
            "hours_low": sum(item["hours_low"] for item in published),
            "hours_high": sum(item["hours_high"] for item in published),
        },
        "coverage": coverage,
        "final_dispositions": dispositions,
        "opportunities": published,
        "incidents": incidents,
        "secondary": _secondary(conn),
        "rejected": _rejected(conn),
        "complete": False,
    }
    report["provenance"] = _report_provenance(report)
    return report


def render_markdown(report: dict) -> str:
    """Markdown rendering of a report document. It does not recompute numbers."""
    lines = [
        "# Observation report",
        "",
        report["scope"],
        "",
        f"Total: ${report['total_dollars_per_month']:.2f} per month "
        f"({report['total_hours_per_month']:.2f} hours at ${report['rate_per_hour']}/hour).",
        "",
        f"Sensitivity: ${report['sensitivity']['dollars_low']:.2f} to "
        f"${report['sensitivity']['dollars_high']:.2f} per month "
        "at the neighboring minute rungs.",
        "",
        "## Opportunities",
        "",
    ]
    if not report["opportunities"]:
        lines.append("None published.")
        lines.append("")
    for item in report["opportunities"]:
        lines.extend(_markdown_item(item))
    lines.extend(["## Incidents", ""])
    if not report["incidents"]:
        lines.append("None.")
        lines.append("")
    for item in report["incidents"]:
        lines.append(f"- {item['title']} ({item['occurrences']} active days) — excluded from the total")
    lines.extend(["", "## Coverage", ""])
    coverage = report["coverage"]
    lines.append(
        f"Files ingested: {coverage['files']}. "
        f"Semantic review covers {coverage['semantic_review_fraction']:.0%} of canonical events."
    )
    lines.append(
        "Unreviewed ingested files: "
        f"{coverage['dispositions'].get('ingested_unreviewed', 0)}."
    )
    final = report.get("final_dispositions") or {}
    if final:
        lines.append(
            "Final dispositions: "
            + ", ".join(f"{name} {count}" for name, count in sorted(final.items()))
            + "."
        )
    lines.append("")
    return "\n".join(lines)


def _opportunities(conn: sqlite3.Connection, status: OpportunityStatus) -> list[dict]:
    """Opportunity rows of one status, with formula fields and passing quotes."""
    rows = conn.execute(
        """
        SELECT * FROM opportunities
        WHERE status = ?
        ORDER BY dollars_per_month DESC, id
        """,
        (status,),
    ).fetchall()
    items = []
    for row in rows:
        formula = json.loads(row["formula_json"])
        assumptions = json.loads(row["assumptions_json"])
        months = row["months"] or 1
        items.append(
            {
                "id": row["id"],
                "process_id": row["process_id"],
                "title": formula.get("title") or row["id"],
                "status": row["status"],
                "occurrences": row["occurrences"],
                "months": row["months"],
                "occurrences_per_month": row["occurrences"] / months,
                "minutes": row["minutes"],
                "fraction": row["fraction"],
                "hours_per_month": row["hours_per_month"],
                "dollars_per_month": row["dollars_per_month"],
                "hours_low": row["hours_low"],
                "hours_high": row["hours_high"],
                "dollars_low": row["dollars_low"],
                "dollars_high": row["dollars_high"],
                "stated_cadence": row["stated_cadence"],
                "mailboxes": formula.get("mailboxes") or [],
                "participant_counts": formula.get("participant_counts") or {},
                "participants": formula.get("participants") or {},
                "participant_paths": formula.get("participant_paths") or {},
                "occurrence_days": formula.get("occurrence_days") or [],
                "delay": formula.get("delay"),
                "inclusion": formula.get("inclusion") or [],
                "assumptions": assumptions,
                "formula": {
                    "occurrences": formula.get("occurrences"),
                    "months": formula.get("months"),
                    "span_days": formula.get("span_days"),
                    "minutes": formula.get("minutes"),
                    "fraction": formula.get("fraction"),
                    "rate": formula.get("rate"),
                    "expression": formula.get("expression"),
                },
                "quotes": _quotes(conn, row["process_id"]),
                "evidence_status": "quote_present",
            }
        )
    return items


def _quotes(conn: sqlite3.Connection, process_id: str) -> list[dict]:
    """Passing quotes attached to one process."""
    rows = conn.execute(
        """
        SELECT o.obs_type, o.payload_json, e.quote, e.message_path, e.byte_start, e.byte_end
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
                "evidence_status": "quote_present",
                "stall_type": payload.get("type"),
                "label": payload.get("label"),
            }
        )
    return quotes


def _coverage(conn: sqlite3.Connection) -> dict:
    """File count, disposition counts, and the share of canonical events reviewed."""
    rows = conn.execute(
        "SELECT disposition, COUNT(*) AS n FROM coverage GROUP BY disposition"
    ).fetchall()
    dispositions = {row["disposition"]: row["n"] for row in rows}
    files = sum(dispositions.values())
    duplicate = dispositions.get("duplicate", 0)
    reviewed = dispositions.get("candidate", 0) + dispositions.get("cue_only", 0)
    canonical = files - duplicate
    fraction = (reviewed / canonical) if canonical else 0.0
    return {
        "files": files,
        "dispositions": dispositions,
        "semantic_review_fraction": fraction,
    }


def _secondary(conn: sqlite3.Connection) -> list[dict]:
    """Secondary path assignments grouped by process."""
    rows = conn.execute(
        """
        SELECT process_id, message_path FROM process_events
        WHERE assignment = 'secondary'
        ORDER BY process_id, message_path
        """
    ).fetchall()
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["process_id"]].append(row["message_path"])
    return [
        {"process_id": process_id, "paths": paths}
        for process_id, paths in sorted(grouped.items())
    ]


def _rejected(conn: sqlite3.Connection) -> list[dict]:
    """Rejected components and their member paths."""
    rows = conn.execute(
        """
        SELECT c.id, m.message_path
        FROM components AS c
        JOIN component_members AS m ON m.component_id = c.id
        WHERE c.status = 'rejected'
        ORDER BY c.id, m.message_path
        """
    ).fetchall()
    grouped: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["id"]].append(row["message_path"])
    return [
        {"component_id": component_id, "paths": paths}
        for component_id, paths in grouped.items()
    ]


def _markdown_item(item: dict) -> list[str]:
    """Markdown lines for one published opportunity."""
    counts = item["participant_counts"]
    return [
        f"### {item['title']}",
        "",
        f"${item['dollars_per_month']:.2f} per month, "
        f"{item['hours_per_month']:.2f} hours, "
        f"{item['occurrences']} active days over {item['months']:.2f} months.",
        "",
        f"Assumptions: {item['minutes']} minutes, fraction {item['fraction']}.",
        f"Mailboxes: {', '.join(item['mailboxes']) or 'unknown'}. "
        f"Senders: {counts.get('senders', 0)}. Recipients: {counts.get('recipients', 0)}.",
        "",
    ]


def final_dispositions(conn: sqlite3.Connection) -> dict[str, int]:
    """One final label for every stored file."""
    published = _primary_paths(conn, OpportunityStatus.PUBLISHED)
    incidents = _primary_paths(conn, OpportunityStatus.INCIDENT)
    counts: dict[str, int] = defaultdict(int)
    rows = conn.execute("SELECT message_path, disposition FROM coverage").fetchall()
    for row in rows:
        disposition = row["disposition"]
        path = row["message_path"]
        if disposition == CoverageDisposition.DUPLICATE:
            label = CoverageDisposition.DUPLICATE
        elif disposition == CoverageDisposition.PARSE_FAILURE:
            label = CoverageDisposition.PARSE_FAILURE
        elif path in published:
            label = "accepted"
        elif path in incidents:
            label = OpportunityStatus.INCIDENT
        elif disposition == CoverageDisposition.INGESTED_UNREVIEWED:
            label = CoverageDisposition.INGESTED_UNREVIEWED
        else:
            label = "rejected"
        counts[label] += 1
    return dict(counts)


def _primary_paths(conn: sqlite3.Connection, status: OpportunityStatus) -> set[str]:
    """Primary event paths for opportunities of one status."""
    rows = conn.execute(
        """
        SELECT e.message_path
        FROM process_events AS e
        JOIN opportunities AS o ON o.process_id = e.process_id
        WHERE o.status = ? AND e.assignment = ?
        """,
        (status, Assignment.PRIMARY),
    ).fetchall()
    return {row["message_path"] for row in rows}


def _opportunity_provenance(item: dict) -> dict:
    """Links from each displayed number to its messages or formula."""
    paths = _occurrence_paths(item)
    identifier = item["id"]
    return {
        "dollars_per_month": _metric(identifier, "formula", paths),
        "hours_per_month": _metric(identifier, "formula", paths),
        "occurrences": _metric(identifier, "days", paths),
        "participants": _metric(identifier, "participants", paths),
        "cadence": _quote_link(item.get("stated_cadence"), item.get("quotes") or []),
        "delay": _metric(identifier, "delay", _delay_paths(item)),
        "sensitivity": _metric(identifier, "formula", paths),
    }


def _report_provenance(report: dict) -> dict:
    """Links from the totals to the opportunities that produce them."""
    identifiers = [item["id"] for item in report["opportunities"]]
    return {
        "total_dollars_per_month": {"href": "/calculation?metric=total_dollars", "opportunities": identifiers},
        "total_hours_per_month": {"href": "/calculation?metric=total_hours", "opportunities": identifiers},
        "dollars_low": {"href": "/calculation?metric=dollars_low", "opportunities": identifiers},
        "dollars_high": {"href": "/calculation?metric=dollars_high", "opportunities": identifiers},
        "hours_low": {"href": "/calculation?metric=hours_low", "opportunities": identifiers},
        "hours_high": {"href": "/calculation?metric=hours_high", "opportunities": identifiers},
    }


def _metric(identifier: str, focus: str, paths: list[str]) -> dict:
    """One drill-through target and the message paths behind it."""
    return {
        "href": f"/opportunity?id={identifier}&focus={focus}",
        "paths": paths,
    }


def _occurrence_paths(item: dict) -> list[str]:
    """Every path named on an occurrence day."""
    paths = []
    for day in item.get("occurrence_days") or []:
        for path in day.get("paths") or []:
            if path not in paths:
                paths.append(path)
    return paths


def _delay_paths(item: dict) -> list[str]:
    """Paths cited by the median reply gap and its chase count."""
    delay = item.get("delay") or {}
    paths = [cite.get("path") for cite in delay.get("citations") or [] if cite.get("path")]
    for path in delay.get("chase_paths") or []:
        if path not in paths:
            paths.append(path)
    return paths


def _quote_link(cadence: str | None, quotes: list[dict]) -> dict | None:
    """Message link for a stated cadence, when a passing quote carries it."""
    if not cadence:
        return None
    for quote in quotes:
        if quote.get("kind") == "cadence" or quote.get("quote") == cadence:
            return {
                "href": _message_href(quote["path"], quote.get("quote") or "", quote.get("byte_start"), quote.get("byte_end")),
                "paths": [quote["path"]],
            }
    return {"href": None, "paths": []}


def _message_href(path: str, quote: str, start: int | None, end: int | None) -> str:
    """Raw-message URL for one quote and its byte span."""
    from urllib.parse import quote as query

    href = f"/message?path={query(path, safe='')}&quote={query(quote, safe='')}"
    if start is not None and end is not None:
        href += f"&start={start}&end={end}"
    return href

