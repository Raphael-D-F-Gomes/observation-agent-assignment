"""List drafter jobs. The draft itself is written by the drafter agent."""

from __future__ import annotations

import json
from pathlib import Path

from src.drafter.constants import DRAFTER_PROMPT_VERSION
from src.jobs.queue import JobQueue


class Drafter:
    """The drafter queue for the current draft targets."""

    def __init__(self, jobs: JobQueue, output: Path) -> None:
        """Remember the shared job cache and the output directory."""
        self.jobs = jobs
        self.output = output

    def prompt_version(self, override: str | None) -> str:
        """The prompt version for this listing."""
        return override or DRAFTER_PROMPT_VERSION

    def current_inputs(self) -> set[str]:
        """Insight hashes listed in the current draft targets."""
        targets = self.output / "draft_targets.json"
        if not targets.exists():
            return set()
        return {
            item["input_sha256"]
            for item in json.loads(targets.read_text(encoding="utf-8"))["targets"]
        }

    def input_path(self, input_sha256: str) -> str:
        """Path of one insight file."""
        return str(self.output / "insights" / f"{input_sha256}.json")

    def opportunity_id(self, input_sha256: str) -> str | None:
        """Opportunity id stored on an insight, when the file exists."""
        path = self.output / "insights" / f"{input_sha256}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8")).get("opportunity_id")
