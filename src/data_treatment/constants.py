"""Pack size, the subject window, and the prepare-time gate."""

CUE_LIST_VERSION = "cues-2"
# A pack stays at or below 64,000 estimated tokens so the prompt, the pack,
# and the JSON response fit in one model context. This budget is fixed. It is
# not raised to force the pack count down to a target.
TOKEN_BUDGET = 64_000
SMALL_COMPONENT_MAX = 12
CUE_PACK_SIZE = 40
CHUNK_CHARS = 6_000
CHUNK_OVERLAP = 400
SUBJECT_FALLBACK_DAYS = 60
PIPELINE_VERSION = "2"

BUDGET_LIMIT_S = 25 * 60
CONSOLIDATE_ALLOWANCE_S = 30
FINALIZE_ALLOWANCE_S = 15
DASHBOARD_START_S = 2
# One extra wave, not a second and third pass of every job.
RETRY_WAVE_RESERVE = 1
