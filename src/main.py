"""Entry object for the deterministic stages.

The observer and drafter agents run between these calls. This module lists
their jobs and records their responses.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.consolidate.service import Consolidation
from src.data_treatment.budget import BudgetError
from src.data_treatment.ingest import CoverageError
from src.data_treatment.treatment import DataTreatment
from src.drafter.service import Drafter
from src.gates import GateError
from src.jobs.constants import ATTEMPT_TIMEOUT_S, SCHEMA_VERSION
from src.jobs.queue import JobQueue
from src.jobs.types import AgentName, JobStatus
from src.observer.service import Observer
from src.output_builder.builder import OutputBuilder


class Main:
    """The services for one corpus, store, and output directory."""

    def __init__(
        self,
        corpus: Path = Path("corpus"),
        db: Path = Path("output/observe.sqlite"),
        output: Path = Path("output"),
        port: int = 8765,
    ) -> None:
        """Build each service from the same paths."""
        self.corpus = Path(corpus)
        self.db = Path(db)
        self.output = Path(output)
        self.port = port
        self.jobs = JobQueue(self.db)
        self.observer = Observer(self.jobs, self.output)
        self.drafter = Drafter(self.jobs, self.output)
        self.data_treatment = DataTreatment(self.corpus, self.db, self.output, self.observer)
        self.consolidation = Consolidation(self.corpus, self.db, self.output)
        self.output_builder = OutputBuilder(self.corpus, self.db, self.output, self.port)

    def prepare(self) -> int:
        """Parse the corpus, build packs, and print the budget."""
        try:
            stats = self.data_treatment.run()
        except FileNotFoundError as exc:
            print(f"prepare: corpus not found: {exc}", file=sys.stderr)
            return 1
        except CoverageError as exc:
            print(f"prepare: {exc}", file=sys.stderr)
            return 1
        except BudgetError as exc:
            print(f"prepare: {exc}", file=sys.stderr)
            return 1
        for path, reason in stats.parse_error_paths:
            detail = f": {reason}" if reason else ""
            print(f"parse error: {path}{detail}", file=sys.stderr)
        print(
            f"prepare: ingested {stats.file_count} files; "
            f"messages={stats.message_count}; "
            f"parse_errors={stats.parse_errors}; "
            f"undated={stats.undated}; "
            f"components={stats.component_count}; "
            f"packs={stats.pack_count}; "
            f"projected_seconds={stats.projected_seconds:.1f}"
        )
        if not stats.gate_ok:
            print("prepare: projected run exceeds 25 minutes", file=sys.stderr)
            return 1
        return 0

    def pending(self, agent: str = "observer", retry_failed: bool = True, prompt_version: str | None = None) -> int:
        """Print jobs that still need a response."""
        service = self.observer if agent == AgentName.OBSERVER else self.drafter
        conn = self.jobs.connect()
        try:
            rows = self.jobs.pending(
                conn,
                agent=agent,
                prompt_version=service.prompt_version(prompt_version),
                schema_version=SCHEMA_VERSION,
                input_sha256s=service.current_inputs(),
                retry_failed=retry_failed,
            )
            payload = [
                {
                    "job_id": row["id"],
                    "agent": row["agent"],
                    "input_sha256": row["input_sha256"],
                    "status": row["status"],
                    "path": service.input_path(row["input_sha256"]),
                }
                for row in rows
            ]
        finally:
            conn.close()
        print(json.dumps(payload, indent=2))
        return 0

    def submit(
        self,
        job_id: int,
        response: Path,
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> int:
        """Record one agent response."""
        conn = self.jobs.connect()
        try:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                print("submit: unknown job", file=sys.stderr)
                return 1
            expected = None
            if job["agent"] == AgentName.DRAFTER:
                expected = self.drafter.opportunity_id(job["input_sha256"])
            ok, error = self.jobs.submit(
                conn,
                job_id,
                Path(response),
                started_at=started_at,
                ended_at=ended_at,
                expect_opportunity_id=expected,
            )
            conn.commit()
        finally:
            conn.close()
        if not ok:
            print(f"submit: {error}", file=sys.stderr)
            return 1
        print(f"submit: job {job_id} succeeded")
        return 0

    def consolidate(self) -> int:
        """Validate quotes, assign events, and price opportunities."""
        try:
            summary = self.consolidation.run()
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(
            f"consolidate: processes={summary['processes']}; "
            f"published={summary['published']}; "
            f"draft_targets={summary['draft_targets']}; "
            f"quote_failures={summary['quote_failures']}"
        )
        return 0

    def finalize(self) -> int:
        """Check drafts and write the report."""
        try:
            summary = self.output_builder.finalize()
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(
            f"finalize: opportunities={summary['opportunities']}; "
            f"artifacts={summary['artifacts']}; "
            f"total_dollars_per_month={summary['total_dollars_per_month']:.2f}"
        )
        return 0

    def serve(self) -> int:
        """Serve the dashboard on localhost."""
        try:
            self.output_builder.serve()
        except GateError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    def status(self, agent: str, retry_failed: bool, prompt_version: str | None, require_ready: bool) -> int:
        """Print job counts for the current inputs."""
        service = self.observer if agent == AgentName.OBSERVER else self.drafter
        conn = self.jobs.connect()
        try:
            rows = conn.execute(
                """
                SELECT input_sha256, status FROM jobs
                WHERE agent = ? AND prompt_version = ? AND schema_version = ?
                """,
                (agent, service.prompt_version(prompt_version), SCHEMA_VERSION),
            ).fetchall()
            current = service.current_inputs()
            by_input = {row["input_sha256"]: row["status"] for row in rows}
            succeeded = sum(1 for item in current if by_input.get(item) == JobStatus.SUCCEEDED)
            failed = sum(1 for item in current if by_input.get(item) == JobStatus.FAILED)
            pending = len(current) - succeeded - failed
            if not retry_failed:
                pending = sum(
                    1
                    for item in current
                    if by_input.get(item) not in {JobStatus.SUCCEEDED, JobStatus.FAILED}
                )
            payload = {
                "agent": agent,
                "expected": len(current),
                "succeeded": succeeded,
                "pending": pending,
                "failed": failed,
                "ready": succeeded == len(current) and failed == 0,
            }
        finally:
            conn.close()
        print(json.dumps(payload, indent=2))
        if require_ready and not payload["ready"]:
            return 1
        return 0

    def timeout(self, job_id: int, elapsed_seconds: float) -> int:
        """Record a failed attempt when an agent exceeds the attempt budget."""
        if elapsed_seconds < ATTEMPT_TIMEOUT_S:
            print("timeout: attempt is still inside the budget", file=sys.stderr)
            return 1
        conn = self.jobs.connect()
        try:
            from src.jobs.queue import record_attempt

            job = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                print("timeout: unknown job", file=sys.stderr)
                return 1
            record_attempt(
                conn,
                job_id,
                status=JobStatus.FAILED,
                response_path=None,
                error="timeout",
                started_at=_stamp(elapsed_seconds),
                ended_at=_stamp(0),
            )
            conn.commit()
        finally:
            conn.close()
        print(f"timeout: job {job_id} failed")
        return 0


def main(argv: list[str] | None = None) -> int:
    """Call one Main method from a short argument list."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return 2
    command, rest = args[0], args[1:]
    if command == "jobs":
        if not rest:
            return 2
        action, rest = rest[0], rest[1:]
        options = _options(rest)
        app = Main(options["corpus"], options["db"], options["output"], options["port"])
        if action == "pending":
            return app.pending(options["agent"], options["retry_failed"], options["prompt_version"])
        if action == "status":
            return app.status(
                options["agent"],
                options["retry_failed"],
                options["prompt_version"],
                options["require_ready"],
            )
        if action == "timeout":
            return app.timeout(options["job_id"], float(options["elapsed_seconds"]))
        if action == "submit":
            return app.submit(options["job_id"], options["response"], options["started_at"], options["ended_at"])
        print(f"unknown command: jobs {action}", file=sys.stderr)
        return 2
    options = _options(rest)
    app = Main(options["corpus"], options["db"], options["output"], options["port"])
    if options.get("report"):
        app.output_builder.report = options["report"]
    handler = {
        "prepare": app.prepare,
        "consolidate": app.consolidate,
        "finalize": app.finalize,
        "serve": app.serve,
    }.get(command)
    if handler is None:
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    return handler()


def _options(argv: list[str]) -> dict:
    """Read --flag value pairs into the paths Main expects."""
    options = {
        "corpus": Path("corpus"),
        "db": Path("output/observe.sqlite"),
        "output": Path("output"),
        "port": 8765,
        "agent": "observer",
        "retry_failed": True,
        "require_ready": False,
        "prompt_version": None,
        "job_id": None,
        "response": None,
        "started_at": None,
        "ended_at": None,
        "elapsed_seconds": None,
    }
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag == "--no-retry-failed":
            options["retry_failed"] = False
            index += 1
            continue
        if flag == "--require-ready":
            options["require_ready"] = True
            index += 1
            continue
        key = flag[2:].replace("-", "_")
        value = argv[index + 1]
        if key in {"corpus", "db", "output", "response", "report"}:
            options[key] = Path(value)
        elif key in {"port", "job_id"}:
            options[key] = int(value)
        elif key == "elapsed_seconds":
            options[key] = float(value)
        else:
            options[key] = value
        index += 2
    return options


def _stamp(elapsed_seconds: float) -> str:
    """UTC timestamp used for a timeout attempt."""
    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(seconds=elapsed_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    sys.exit(main())
