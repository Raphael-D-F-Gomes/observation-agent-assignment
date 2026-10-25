"""Full working-store schema, assembled from one file per table."""

from src.db.tables.body_segments import SQL as BODY_SEGMENTS
from src.db.tables.candidate_edges import SQL as CANDIDATE_EDGES
from src.db.tables.component_members import SQL as COMPONENT_MEMBERS
from src.db.tables.components import SQL as COMPONENTS
from src.db.tables.coverage import SQL as COVERAGE
from src.db.tables.duplicate_groups import SQL as DUPLICATE_GROUPS
from src.db.tables.duplicate_paths import SQL as DUPLICATE_PATHS
from src.db.tables.evidence_spans import SQL as EVIDENCE_SPANS
from src.db.tables.job_attempts import SQL as JOB_ATTEMPTS
from src.db.tables.jobs import SQL as JOBS
from src.db.tables.message_refs import SQL as MESSAGE_REFS
from src.db.tables.messages import SQL as MESSAGES
from src.db.tables.observations import SQL as OBSERVATIONS
from src.db.tables.opportunities import SQL as OPPORTUNITIES
from src.db.tables.pack_members import SQL as PACK_MEMBERS
from src.db.tables.pack_spans import SQL as PACK_SPANS
from src.db.tables.participants import SQL as PARTICIPANTS
from src.db.tables.process_events import SQL as PROCESS_EVENTS
from src.db.tables.raw_files import SQL as RAW_FILES
from src.db.tables.stage_meta import SQL as STAGE_META

SCHEMA = "\n".join(
    (
        RAW_FILES,
        MESSAGES,
        PARTICIPANTS,
        BODY_SEGMENTS,
        MESSAGE_REFS,
        DUPLICATE_GROUPS,
        DUPLICATE_PATHS,
        CANDIDATE_EDGES,
        COMPONENTS,
        COMPONENT_MEMBERS,
        COVERAGE,
        PROCESS_EVENTS,
        EVIDENCE_SPANS,
        OBSERVATIONS,
        OPPORTUNITIES,
        JOBS,
        JOB_ATTEMPTS,
        PACK_MEMBERS,
        PACK_SPANS,
        STAGE_META,
    )
)

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_participants_path ON participants(message_path);
CREATE INDEX IF NOT EXISTS idx_segments_path ON body_segments(message_path);
CREATE INDEX IF NOT EXISTS idx_members_component ON component_members(component_id);
CREATE INDEX IF NOT EXISTS idx_edges_left ON candidate_edges(left_path);
CREATE INDEX IF NOT EXISTS idx_edges_signal ON candidate_edges(signal);
"""
