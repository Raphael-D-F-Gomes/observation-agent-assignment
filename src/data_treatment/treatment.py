"""Run every prepare stage and return the pack and budget counts."""

from __future__ import annotations

import json
import time
from pathlib import Path

from src.data_treatment.budget import gate_ok, load_timings, planned_drafter_jobs, project_seconds, record_duration
from src.data_treatment.candidates import Candidates
from src.data_treatment.components import Components
from src.data_treatment.constants import CUE_LIST_VERSION, PIPELINE_VERSION
from src.data_treatment.corpus import Corpus, quick_fingerprint
from src.data_treatment.dedupe import Dedupe
from src.data_treatment.ingest import CoverageCheck, Ingest
from src.data_treatment.types import PrepareStats
from src.data_treatment.packs import Packs
from src.data_treatment.threads import Threads
from src.db.connection import Store
from src.drafter.constants import DRAFTER_CONCURRENCY, P95_DRAFTER_S
from src.jobs.constants import ATTEMPT_TIMEOUT_S, SCHEMA_VERSION
from src.observer.constants import OBSERVER_CONCURRENCY, OBSERVER_PROMPT_VERSION, P95_OBSERVER_S
from src.observer.service import Observer


class DataTreatment:
    """Parse a corpus, then build packs and observer jobs for it."""

    def __init__(
        self,
        corpus: Path,
        db_path: Path,
        output: Path,
        observer: Observer,
        token_budget: int | None = None,
    ) -> None:
        """Keep the corpus, the store path, and the observer queue."""
        self.corpus = corpus
        self.db_path = db_path
        self.output = output
        self.observer = observer
        self.token_budget = token_budget

    def run(self) -> PrepareStats:
        """Store one row per file and return the pack and budget counts.

        The message count must equal the number of files on disk. A parse
        failure is stored. An unchanged corpus reuses parsed rows and packs.
        """
        resolved = self.corpus.resolve()
        if not resolved.is_dir():
            raise FileNotFoundError(resolved)
        self.output.mkdir(parents=True, exist_ok=True)
        store = Store(self.db_path)
        try:
            return self._run(Corpus(resolved), store)
        finally:
            store.close()

    def _run(self, corpus: Corpus, store: Store) -> PrepareStats:
        """Sync files, reuse a matching prepare, or rebuild packs."""
        ingest = Ingest(corpus)
        started = time.perf_counter()
        with store.connection:
            ingest.run(store)
            fingerprint = quick_fingerprint(corpus.path)
            if self._can_reuse(store, fingerprint, ingest):
                stats = self._stats(store, ingest, cache_hit=True, prepare_s=None)
                cache = "hit"
            else:
                store.clear_derived()
                coverage = CoverageCheck(corpus)
                packs = Packs(self.output, corpus.path, self.token_budget)
                for stage in (Dedupe(), Threads(), Candidates(), Components(), coverage, packs):
                    stage.run(store)
                self.observer.record_packs(store, packs)
                prepare_s = time.perf_counter() - started
                self._write_meta(store, fingerprint, prepare_s)
                stats = self._stats(
                    store,
                    ingest,
                    cache_hit=False,
                    prepare_s=prepare_s,
                    coverage=coverage,
                    pack_count=len(packs.results),
                )
                cache = "miss"
        elapsed = time.perf_counter() - started
        self._write_log(stats, cache, elapsed)
        if cache == "miss":
            record_duration(self.output, "prepare", elapsed)
        return stats

    def _can_reuse(self, store: Store, fingerprint: str, ingest: Ingest) -> bool:
        """True when this corpus and pipeline already have packs."""
        if ingest.files_parsed or ingest.files_removed:
            return False
        expected = {
            "fingerprint": fingerprint,
            "pipeline_version": PIPELINE_VERSION,
            "token_budget": _budget_key(self.token_budget),
            "prompt_version": OBSERVER_PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "cue_list_version": CUE_LIST_VERSION,
        }
        for key, value in expected.items():
            if _meta(store, key) != value:
                return False
        return (self.output / "packs" / "manifest.json").exists()

    def _stats(
        self,
        store: Store,
        ingest: Ingest,
        *,
        cache_hit: bool,
        prepare_s: float | None,
        coverage: CoverageCheck | None = None,
        pack_count: int | None = None,
    ) -> PrepareStats:
        """Counts for this prepare, including a reused run."""
        if prepare_s is None:
            stored = _meta(store, "prepare_seconds")
            prepare_s = float(stored) if stored else 0.0
        if pack_count is None:
            pack_count = store.count("SELECT COUNT(DISTINCT pack_id) FROM pack_members")
        projected = project_seconds(
            prepare_s,
            pack_count,
            planned_drafter_jobs(pack_count),
            load_timings(self.output),
        )
        file_count = coverage.file_count if coverage is not None else store.count(
            "SELECT COUNT(*) FROM raw_files"
        )
        return PrepareStats(
            file_count=file_count,
            message_count=store.count("SELECT COUNT(*) FROM messages"),
            parse_errors=store.count("SELECT COUNT(*) FROM raw_files WHERE parse_ok = 0"),
            undated=store.count("SELECT COUNT(*) FROM messages WHERE sent_at IS NULL"),
            parse_error_paths=tuple(ingest.parse_error_paths),
            component_count=store.count("SELECT COUNT(*) FROM components"),
            pack_count=pack_count,
            projected_seconds=projected,
            gate_ok=gate_ok(projected),
            files_parsed=ingest.files_parsed,
            cache_hit=cache_hit,
        )

    def _write_meta(self, store: Store, fingerprint: str, prepare_s: float) -> None:
        """Record the inputs that make this prepare reusable."""
        values = {
            "fingerprint": fingerprint,
            "pipeline_version": PIPELINE_VERSION,
            "token_budget": _budget_key(self.token_budget),
            "prompt_version": OBSERVER_PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "cue_list_version": CUE_LIST_VERSION,
            "prepare_seconds": f"{prepare_s:.6f}",
        }
        for key, value in values.items():
            store.connection.execute(
                """
                INSERT INTO stage_meta (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def _write_log(self, stats: PrepareStats, cache: str, elapsed: float) -> None:
        """Write the pack count and the projected timeline."""
        log_dir = self.output / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "file_count": stats.file_count,
            "message_count": stats.message_count,
            "parse_errors": stats.parse_errors,
            "undated": stats.undated,
            "component_count": stats.component_count,
            "pack_count": stats.pack_count,
            "observer_jobs": stats.pack_count,
            "drafter_jobs_cap": planned_drafter_jobs(stats.pack_count),
            "elapsed_seconds": round(elapsed, 3),
            "projected_seconds": round(stats.projected_seconds, 3),
            "gate_ok": stats.gate_ok,
            "cache": cache,
            "files_parsed": stats.files_parsed,
            "observer_concurrency": OBSERVER_CONCURRENCY,
            "drafter_concurrency": DRAFTER_CONCURRENCY,
            "attempt_timeout_seconds": ATTEMPT_TIMEOUT_S,
            "p95_observer_seconds": P95_OBSERVER_S,
            "p95_drafter_seconds": P95_DRAFTER_S,
            "retry_wave_reserve": 1,
        }
        (log_dir / "prepare.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _budget_key(token_budget: int | None) -> str:
    """Stable meta value for the optional token budget."""
    return "default" if token_budget is None else str(token_budget)


def _meta(store: Store, key: str) -> str | None:
    """One stage_meta value, or null when it has not been written."""
    row = store.connection.execute("SELECT value FROM stage_meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row[0])
