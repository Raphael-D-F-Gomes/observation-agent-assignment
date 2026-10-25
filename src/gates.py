"""A deterministic stage that must stop the run."""

from __future__ import annotations


class GateError(RuntimeError):
    """One or more required checks failed."""

    def __init__(self, stage: str, reasons: list[str]) -> None:
        """Remember the stage and the reasons the run cannot continue."""
        self.stage = stage
        self.reasons = reasons
        detail = "; ".join(reasons) if reasons else "failed"
        super().__init__(f"{stage}: {detail}")
