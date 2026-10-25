"""Token-bounded observer packs.

Every canonical event is either a parse failure or a member of a pack. Large
components are split into subpacks whose messages are fully visible. Bodies
that exceed the chunk size are split into overlapping raw-byte spans instead
of being truncated.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

from src.data_treatment.constants import (
    CHUNK_CHARS,
    CUE_LIST_VERSION,
    CUE_PACK_SIZE,
    SMALL_COMPONENT_MAX,
    TOKEN_BUDGET,
)
from src.data_treatment.spans import spans_for_message
from src.data_treatment.types import (
    Component,
    ComponentStatus,
    ComponentSummary,
    CoverageDisposition,
    CueGroup,
    GroupStatus,
    Pack,
    PackKind,
    RenderedComponent,
    RenderedMessage,
    SpanRecord,
    StoredMessage,
)
from src.db.connection import Store
from src.jobs.constants import SCHEMA_VERSION
from src.jobs.types import AgentName
from src.observer.constants import OBSERVER_PROMPT_VERSION


def build_packs(
    conn: sqlite3.Connection,
    corpus: Path,
    *,
    token_budget: int | None = None,
    cue_pack_size: int | None = None,
) -> list[Pack]:
    """Build component, cue, and fallback packs for the current store.

    `token_budget` is the reading budget for one pack. It is not raised to
    force the pack count down.
    """
    budget = TOKEN_BUDGET if token_budget is None else token_budget
    cue_size = cue_pack_size or CUE_PACK_SIZE
    components = _load_components(conn)
    cues = _load_disposition(conn, CoverageDisposition.CUE_ONLY)
    fallback = _load_disposition(conn, CoverageDisposition.FALLBACK)
    packs: list[Pack] = []
    units: list[RenderedComponent] = []
    for component in components:
        units.extend(_component_units(component, corpus, budget))
    packs.extend(_batch(units, PackKind.COMPONENTS, budget))
    packs.extend(_batch(_cue_units(cues, corpus), PackKind.CUES, budget, cue_size))
    packs.extend(_batch(_fallback_units(fallback, corpus), PackKind.FALLBACK, budget))
    return packs


def write_packs(output_dir: Path, packs: list[Pack]) -> Path:
    """Write pack files that changed and remove packs that are gone."""
    pack_dir = output_dir / "packs"
    pack_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    names = {"manifest.json"}
    for pack in packs:
        path = pack_dir / f"{pack.pack_id}.json"
        text = json.dumps(pack.payload, indent=2, sort_keys=True)
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
        names.add(path.name)
        manifest.append(
            {
                "pack_id": pack.pack_id,
                "path": str(path),
                "kind": pack.kind,
                "tokens": pack.tokens,
                "agent": AgentName.OBSERVER,
            }
        )
    for child in pack_dir.glob("*.json"):
        if child.name not in names:
            child.unlink()
    manifest_path = pack_dir / "manifest.json"
    manifest_text = json.dumps(manifest, indent=2)
    if not manifest_path.exists() or manifest_path.read_text(encoding="utf-8") != manifest_text:
        manifest_path.write_text(manifest_text, encoding="utf-8")
    return manifest_path


def estimate_tokens(text: str) -> int:
    """Rough token count from character length."""
    return max(1, math.ceil(len(text) / 4))


def store_pack_rows(conn: sqlite3.Connection, packs: list[Pack]) -> None:
    """Replace pack membership and span rows with `packs`."""
    conn.execute("DELETE FROM pack_spans")
    conn.execute("DELETE FROM pack_members")
    member_rows = []
    span_rows = []
    seen_members: set[tuple[str, str]] = set()
    seen_spans: set[str] = set()
    for pack in packs:
        for path, component_key in pack.members:
            member_key = (pack.pack_id, path)
            if member_key in seen_members:
                continue
            seen_members.add(member_key)
            member_rows.append((pack.pack_id, path, component_key))
        for span in pack.spans:
            if span.span_id in seen_spans:
                continue
            seen_spans.add(span.span_id)
            span_rows.append(
                (
                    span.span_id,
                    pack.pack_id,
                    span.path,
                    span.byte_start,
                    span.byte_end,
                    span.raw_sha256,
                    span.display_text,
                    span.transfer_encoding,
                    span.coverage,
                )
            )
    conn.executemany(
        """
        INSERT INTO pack_members (pack_id, message_path, component_key)
        VALUES (?, ?, ?)
        """,
        member_rows,
    )
    conn.executemany(
        """
        INSERT INTO pack_spans (
          span_id, pack_id, message_path, byte_start, byte_end, raw_sha256,
          display_text, transfer_encoding, coverage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        span_rows,
    )


def _component_units(component: Component, corpus: Path, token_budget: int) -> list[RenderedComponent]:
    """Split one component into visible groups that fit the budget."""
    units = []
    messages = component.messages
    for offset in range(0, len(messages), SMALL_COMPONENT_MAX):
        group = messages[offset : offset + SMALL_COMPONENT_MAX]
        rendered = _render_group(
            GroupStatus(component.status),
            component.component_key,
            component.started_on,
            component.ended_on,
            group,
            corpus,
        )
        units.extend(_fit(rendered, token_budget))
    return units


def _cue_units(messages: list[StoredMessage], corpus: Path) -> list[CueGroup]:
    """One cue hit per unit, with the phrase that selected it."""
    units = []
    for message in messages:
        rendered = _render_message(message, corpus)
        units.append(CueGroup(hits=[rendered], member_paths=[message.path]))
    return units


def _fallback_units(messages: list[StoredMessage], corpus: Path) -> list[RenderedComponent]:
    """Group remaining events by subject, or by folder and month."""
    groups: dict[str, list[StoredMessage]] = defaultdict(list)
    for message in messages:
        if message.subject_norm:
            key = "subject:" + message.subject_norm
        else:
            month = (message.sent_at or "")[:7] or "undated"
            key = f"folder:{message.mailbox}/{message.folder}:{month}"
        groups[key].append(message)
    units = []
    for key in sorted(groups):
        rows = groups[key]
        dates = {row.sent_at[:10] for row in rows if row.sent_at}
        units.append(
            _render_group(
                GroupStatus.FALLBACK,
                None,
                None,
                None,
                rows,
                corpus,
                distinct_dates=len(dates),
            )
        )
    return units


def _batch(
    rendered: list[RenderedComponent] | list[CueGroup],
    kind: PackKind,
    token_budget: int,
    count_limit: int | None = None,
) -> list[Pack]:
    """Pack rendered units until a pack reaches the token or count limit."""
    packs: list[Pack] = []
    current: list[RenderedComponent | CueGroup] = []
    current_tokens = 0
    for item in rendered:
        pieces = _fit(item, token_budget) if kind != PackKind.CUES else [item]
        for piece in pieces:
            tokens = estimate_tokens(json.dumps(asdict(piece), sort_keys=True))
            over_count = count_limit is not None and len(current) >= count_limit
            if current and (current_tokens + tokens > token_budget or over_count):
                packs.append(_pack(kind, current))
                current = []
                current_tokens = 0
            current.append(piece)
            current_tokens += tokens
    if current:
        packs.append(_pack(kind, current))
    return packs


def _fit(item: RenderedComponent | CueGroup, token_budget: int) -> list[RenderedComponent | CueGroup]:
    """Split a component until it fits, or until only one span remains."""
    if not isinstance(item, RenderedComponent):
        return [item]
    if estimate_tokens(json.dumps(asdict(item), sort_keys=True)) <= token_budget:
        return [item]
    messages = item.messages
    if len(messages) > 1:
        middle = len(messages) // 2
        return _fit(_with_messages(item, messages[:middle]), token_budget) + _fit(
            _with_messages(item, messages[middle:]), token_budget
        )
    spans = messages[0].spans
    if len(spans) <= 1:
        return [item]
    middle = len(spans) // 2
    return _fit(_with_spans(item, spans[:middle]), token_budget) + _fit(
        _with_spans(item, spans[middle:]), token_budget
    )


def _with_messages(item: RenderedComponent, messages: list[RenderedMessage]) -> RenderedComponent:
    """Copy a component onto a subset of its messages."""
    return replace(
        item,
        messages=messages,
        member_paths=[message.path for message in messages],
        canonical_count=len(messages),
        summary=_summary(messages),
    )


def _with_spans(item: RenderedComponent, spans: list[SpanRecord]) -> RenderedComponent:
    """Copy a one-message component onto a subset of its spans."""
    message = replace(
        item.messages[0],
        spans=spans,
        body="\n".join(span.display_text for span in spans),
    )
    return _with_messages(item, [message])


def _pack(kind: PackKind, units: list[RenderedComponent | CueGroup]) -> Pack:
    """One pack and the id of its stable payload."""
    members: list[tuple[str, str | None]] = []
    span_rows: list[SpanRecord] = []
    if kind == PackKind.CUES:
        hits: list[RenderedMessage] = []
        for unit in units:
            if isinstance(unit, CueGroup):
                hits.extend(unit.hits)
        for hit in hits:
            members.append((hit.path, None))
            span_rows.extend(hit.spans)
        stable = _stable(kind, hits=hits, member_paths=sorted({path for path, _ in members}))
    else:
        components = [unit for unit in units if isinstance(unit, RenderedComponent)]
        for component in components:
            for path in component.member_paths:
                members.append((path, component.component_key))
            for message in component.messages:
                span_rows.extend(message.spans)
        stable = _stable(
            kind,
            components=components,
            member_paths=sorted({path for path, _ in members}),
        )
    pack_id = _digest(stable)
    payload = dict(stable)
    payload["pack_id"] = pack_id
    return Pack(
        pack_id=pack_id,
        kind=kind,
        tokens=estimate_tokens(json.dumps(stable, sort_keys=True)),
        payload=payload,
        members=tuple(members),
        spans=tuple(span_rows),
    )


def _stable(
    kind: PackKind,
    member_paths: list[str],
    components: list[RenderedComponent] | None = None,
    hits: list[RenderedMessage] | None = None,
) -> dict:
    """Payload fields that participate in the pack id."""
    stable: dict = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": OBSERVER_PROMPT_VERSION,
        "cue_list_version": CUE_LIST_VERSION,
        "kind": kind,
        "member_paths": member_paths,
    }
    if hits is not None:
        stable["hits"] = [asdict(hit) for hit in hits]
    else:
        stable["components"] = [asdict(component) for component in (components or [])]
    return stable


def _load_components(conn: sqlite3.Connection) -> list[Component]:
    """Components with their messages, ordered by sent time."""
    participants = _participant_map(conn)
    rows = conn.execute(
        """
        SELECT id, status, canonical_count, distinct_dates, started_on, ended_on
        FROM components
        ORDER BY id
        """
    ).fetchall()
    membership = conn.execute(
        """
        SELECT cm.component_id, m.path, m.message_id, m.date_raw, m.sent_at, m.from_raw,
               m.from_addr, m.subject_raw, m.subject_norm, m.mailbox, m.folder,
               m.origin_raw, m.body, m.file_sha256, m.cte
        FROM component_members AS cm
        JOIN messages AS m ON m.path = cm.message_path
        ORDER BY cm.component_id, m.sent_at IS NULL, m.sent_at, m.path
        """
    ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in membership:
        grouped[int(row["component_id"])].append(row)
    components = []
    for row in rows:
        messages = [_message_record(member, participants) for member in grouped[int(row["id"])]]
        if not messages:
            continue
        components.append(
            Component(
                status=ComponentStatus(row["status"]),
                canonical_count=row["canonical_count"],
                distinct_dates=row["distinct_dates"],
                started_on=row["started_on"],
                ended_on=row["ended_on"],
                component_key=_component_key(message.path for message in messages),
                messages=messages,
            )
        )
    components.sort(key=lambda item: min(message.path for message in item.messages))
    return components


def _load_disposition(conn: sqlite3.Connection, disposition: CoverageDisposition) -> list[StoredMessage]:
    """Messages with one coverage disposition."""
    participants = _participant_map(conn)
    rows = conn.execute(
        """
        SELECT m.path, m.message_id, m.date_raw, m.sent_at, m.from_raw, m.from_addr,
               m.subject_raw, m.subject_norm, m.mailbox, m.folder, m.origin_raw, m.body,
               m.file_sha256, m.cte, e.detail AS cue
        FROM coverage AS c
        JOIN messages AS m ON m.path = c.message_path
        LEFT JOIN candidate_edges AS e
          ON e.left_path = m.path AND e.right_path = m.path AND e.signal = 'cue'
        WHERE c.disposition = ?
        ORDER BY m.path, e.detail
        """,
        (disposition,),
    ).fetchall()
    messages: list[StoredMessage] = []
    seen: set[str] = set()
    for row in rows:
        if row["path"] in seen:
            current = messages[-1]
            if row["cue"] and row["cue"] not in current.cues:
                current.cues.append(row["cue"])
            continue
        seen.add(row["path"])
        record = _message_record(row, participants)
        record.cues = [row["cue"]] if row["cue"] else []
        record.cue = row["cue"]
        messages.append(record)
    return messages


def _participant_map(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Addresses for every message, in header order."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in conn.execute(
        """
        SELECT message_path, addr FROM participants
        WHERE addr IS NOT NULL
        ORDER BY message_path, role, position
        """
    ):
        if row["addr"]:
            grouped[row["message_path"]].append(row["addr"])
    return grouped


def _render_group(
    status: GroupStatus,
    component_key: str | None,
    started_on: str | None,
    ended_on: str | None,
    messages: list[StoredMessage],
    corpus: Path,
    distinct_dates: int | None = None,
) -> RenderedComponent:
    """Component payload containing every selected message."""
    rendered = [_render_message(message, corpus) for message in messages]
    dates = sorted({message.sent_at[:10] for message in messages if message.sent_at})
    return RenderedComponent(
        status=status,
        canonical_count=len(rendered),
        distinct_dates=len(dates) if distinct_dates is None else distinct_dates,
        started_on=dates[0] if dates else started_on,
        ended_on=dates[-1] if dates else ended_on,
        component_key=component_key,
        member_paths=[message.path for message in rendered],
        summary=_summary(rendered),
        messages=rendered,
        directly_reviewed=True,
    )


def _render_message(message: StoredMessage, corpus: Path) -> RenderedMessage:
    """Headers plus raw-byte spans for one message. Nothing is dropped."""
    raw_path = corpus / message.path
    raw = raw_path.read_bytes() if raw_path.is_file() else message.body.encode("utf-8")
    spans = spans_for_message(message.path, raw, message.body, message.cte)
    rendered_spans = [
        SpanRecord(
            span_id=span.span_id,
            path=span.path,
            byte_start=span.byte_start,
            byte_end=span.byte_end,
            raw_sha256=span.raw_sha256,
            display_text=span.display_text,
            transfer_encoding=span.transfer_encoding,
            coverage=span.coverage,
        )
        for span in spans
    ]
    return RenderedMessage(
        path=message.path,
        file_sha256=message.file_sha256,
        message_id=message.message_id,
        sent_at=message.sent_at,
        date_raw=message.date_raw,
        from_raw=message.from_raw,
        from_addr=message.from_addr,
        subject_raw=message.subject_raw,
        mailbox=message.mailbox,
        origin_raw=message.origin_raw,
        participants=message.participants,
        cue=message.cue,
        body="\n".join(span.display_text for span in spans),
        spans=rendered_spans,
        body_omitted=False,
        chunk_chars=CHUNK_CHARS,
    )


def _summary(messages: list[RenderedMessage]) -> ComponentSummary:
    """Visible paths, dates, and address counts."""
    dates = sorted({message.sent_at[:10] for message in messages if message.sent_at})
    people: dict[str, int] = {}
    for message in messages:
        for addr in message.participants:
            people[addr] = people.get(addr, 0) + 1
    return ComponentSummary(
        paths=[message.path for message in messages],
        dates=dates,
        participant_counts=dict(sorted(people.items())),
    )


def _message_record(row: sqlite3.Row, participants: dict[str, list[str]]) -> StoredMessage:
    """Headers and participants for one stored message."""
    return StoredMessage(
        path=row["path"],
        file_sha256=row["file_sha256"],
        message_id=row["message_id"],
        sent_at=row["sent_at"],
        date_raw=row["date_raw"],
        from_raw=row["from_raw"],
        from_addr=row["from_addr"],
        subject_raw=row["subject_raw"],
        subject_norm=row["subject_norm"],
        mailbox=row["mailbox"],
        folder=row["folder"],
        origin_raw=row["origin_raw"],
        body=row["body"],
        cte=row["cte"],
        participants=participants.get(row["path"], []),
    )


def _component_key(paths) -> str:
    """Stable id for one component, from its member paths."""
    encoded = "\n".join(sorted(paths)).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _digest(payload: dict) -> str:
    """SHA-256 of a stable JSON payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class Packs:
    """Build observer packs and write them under an output directory."""

    def __init__(self, output: Path, corpus: Path, token_budget: int | None = None) -> None:
        """Remember where packs are written and the corpus they cite."""
        self.output = output
        self.corpus = corpus
        self.token_budget = token_budget
        self.results: list[Pack] = []

    def run(self, store: Store) -> None:
        """Build the packs for the current store and write their files."""
        self.results = build_packs(
            store.connection, self.corpus, token_budget=self.token_budget
        )
        store_pack_rows(store.connection, self.results)
        write_packs(self.output, self.results)
