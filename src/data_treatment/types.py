"""Types for reading a corpus and building observer packs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ParticipantRole(StrEnum):
    """From, to, cc, or bcc."""

    FROM = "from"
    TO = "to"
    CC = "cc"
    BCC = "bcc"


class HeaderSource(StrEnum):
    """Whether an address came from the plain header or its X- header."""

    HEADER = "header"
    X_HEADER = "x-header"


class SpanCoverage(StrEnum):
    """How much of a body one raw slice represents."""

    COMPLETE = "complete"
    CHUNK = "chunk"
    ENCODED_CHUNK = "encoded_chunk"


class CoverageDisposition(StrEnum):
    """What prepare decided to do with one corpus file."""

    DUPLICATE = "duplicate"
    CANDIDATE = "candidate"
    CUE_ONLY = "cue_only"
    FALLBACK = "fallback"
    PARSE_FAILURE = "parse_failure"
    INGESTED_UNREVIEWED = "ingested_unreviewed"


class ComponentStatus(StrEnum):
    """Review state stored on a component."""

    PENDING = "pending"
    RECURRING = "recurring"
    INCIDENT = "incident"
    REJECTED = "rejected"


class GroupStatus(StrEnum):
    """Status written into a pack group. Fallback groups are not stored components."""

    PENDING = "pending"
    RECURRING = "recurring"
    INCIDENT = "incident"
    REJECTED = "rejected"
    FALLBACK = "fallback"


class EdgeSignal(StrEnum):
    """Reason two canonical events are linked."""

    SUBJECT = "subject"
    TEMPLATE = "template"
    THREAD = "thread"
    PARTICIPANT_SET = "participant_set"
    CUE = "cue"
    TOKEN_SIMILARITY = "token_similarity"


class PackKind(StrEnum):
    """Which prepare rule filled a pack."""

    COMPONENTS = "components"
    CUES = "cues"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class Participant:
    """One address on a message, in header order."""

    role: ParticipantRole
    source: HeaderSource
    position: int
    raw: str
    addr: str


@dataclass
class ParsedMessage:
    """Parsed fields of one corpus file.

    A parse failure is still a result, with `parse_ok` false and `error` set.
    `sent_at` is UTC or null. `segments` starts with the text before the first
    Original Message separator.
    """

    path: str
    file_sha256: str
    size_bytes: int
    parse_ok: bool
    message_id: str
    date_raw: str | None
    sent_at: str | None
    from_raw: str
    from_addr: str | None
    subject_raw: str
    subject_norm: str
    mailbox: str
    folder: str
    origin_raw: str
    cte: str | None
    body: str
    body_norm_sha256: str
    participants: list[Participant] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    error: str | None = None
    mtime_ns: int | None = None


@dataclass(frozen=True)
class PrepareStats:
    """Counts from one prepare of a corpus directory."""

    file_count: int
    message_count: int
    parse_errors: int
    undated: int
    parse_error_paths: tuple[tuple[str, str], ...]
    component_count: int = 0
    pack_count: int = 0
    projected_seconds: float = 0.0
    gate_ok: bool = True
    files_parsed: int = 0
    cache_hit: bool = False


@dataclass(frozen=True)
class EvidenceSpan:
    """One slice of a raw file and the text shown for it."""

    span_id: str
    path: str
    byte_start: int
    byte_end: int
    raw_sha256: str
    display_text: str
    transfer_encoding: str | None
    coverage: SpanCoverage


@dataclass
class StoredMessage:
    """Headers of one stored message used while packs are built."""

    path: str
    file_sha256: str
    message_id: str
    sent_at: str | None
    date_raw: str | None
    from_raw: str
    from_addr: str | None
    subject_raw: str
    subject_norm: str
    mailbox: str
    folder: str
    origin_raw: str
    body: str
    cte: str | None
    participants: list[str]
    cues: list[str] = field(default_factory=list)
    cue: str | None = None


@dataclass
class Component:
    """One stored component and the messages that belong to it."""

    status: ComponentStatus
    canonical_count: int
    distinct_dates: int
    started_on: str | None
    ended_on: str | None
    component_key: str
    messages: list[StoredMessage]


@dataclass(frozen=True)
class SpanRecord:
    """One raw slice placed in a pack payload."""

    span_id: str
    path: str
    byte_start: int
    byte_end: int
    raw_sha256: str
    display_text: str
    transfer_encoding: str | None
    coverage: SpanCoverage


@dataclass
class RenderedMessage:
    """One message as it appears inside a pack."""

    path: str
    file_sha256: str
    message_id: str
    sent_at: str | None
    date_raw: str | None
    from_raw: str
    from_addr: str | None
    subject_raw: str
    mailbox: str
    origin_raw: str
    participants: list[str]
    cue: str | None
    body: str
    spans: list[SpanRecord]
    body_omitted: bool
    chunk_chars: int


@dataclass(frozen=True)
class ComponentSummary:
    """Paths, dates, and address counts visible in one pack group."""

    paths: list[str]
    dates: list[str]
    participant_counts: dict[str, int]


@dataclass
class RenderedComponent:
    """One component slice written into a component or fallback pack."""

    status: GroupStatus
    canonical_count: int
    distinct_dates: int
    started_on: str | None
    ended_on: str | None
    component_key: str | None
    member_paths: list[str]
    summary: ComponentSummary
    messages: list[RenderedMessage]
    directly_reviewed: bool


@dataclass
class CueGroup:
    """Cue hits gathered into one pack."""

    hits: list[RenderedMessage]
    member_paths: list[str]


@dataclass(frozen=True)
class Pack:
    """One observer input and the id that caches it."""

    pack_id: str
    kind: PackKind
    tokens: int
    payload: dict
    members: tuple[tuple[str, str | None], ...]
    spans: tuple[SpanRecord, ...]
