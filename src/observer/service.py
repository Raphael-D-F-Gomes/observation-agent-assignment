"""Record and list observer jobs. The prompt itself is not in this module."""

from __future__ import annotations

import json
from pathlib import Path

from src.db.connection import Store
from src.jobs.constants import SCHEMA_VERSION
from src.jobs.queue import JobQueue, append_cache_log, ensure_job
from src.jobs.types import AgentName
from src.observer.constants import OBSERVER_PROMPT_VERSION


class Observer:
    """The observer queue: one job per pack, and the packs still waiting."""

    def __init__(self, jobs: JobQueue, output: Path) -> None:
        """Remember the shared job cache and the output directory."""
        self.jobs = jobs
        self.output = output

    def record_packs(self, store: Store, packs) -> None:
        """Ensure one job for each pack and append the cache result."""
        entries = []
        for pack in packs.results:
            job_id, cache = ensure_job(
                store.connection,
                AgentName.OBSERVER,
                pack.pack_id,
                OBSERVER_PROMPT_VERSION,
                SCHEMA_VERSION,
            )
            entries.append(
                {
                    "agent": AgentName.OBSERVER,
                    "input_sha256": pack.pack_id,
                    "cache": cache,
                    "job_id": job_id,
                }
            )
        append_cache_log(self.output, entries)

    def prompt_version(self, override: str | None) -> str:
        """The prompt version for this listing."""
        return override or OBSERVER_PROMPT_VERSION

    def current_inputs(self) -> set[str]:
        """Pack ids written by the latest prepare."""
        manifest = self.output / "packs" / "manifest.json"
        if not manifest.exists():
            return set()
        return {item["pack_id"] for item in json.loads(manifest.read_text(encoding="utf-8"))}

    def input_path(self, input_sha256: str) -> str:
        """Path of one pack file."""
        return str(self.output / "packs" / f"{input_sha256}.json")
