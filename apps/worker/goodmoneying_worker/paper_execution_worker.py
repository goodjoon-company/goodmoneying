from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, cast
from uuid import uuid4

from goodmoneying_shared.portfolio_bot_store import PaperExecutionLeaseLostError


@dataclass(frozen=True, slots=True)
class PaperExecutionFill:
    fill_price: Decimal
    filled_quantity: Decimal | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    knowledge_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence: Mapping[str, object] = field(default_factory=dict)


class PaperExecutionStore(Protocol):
    def claim_next_paper_execution_job(
        self, worker_id: str
    ) -> Mapping[str, object] | None: ...

    def complete_claimed_paper_execution_job(
        self,
        *,
        job_id: int,
        worker_id: str,
        lease_generation: int,
        fill_price: Decimal,
        filled_quantity: Decimal | None,
        occurred_at: datetime,
        knowledge_at: datetime,
        evidence: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    def fail_claimed_paper_execution_job(
        self,
        *,
        job_id: int,
        worker_id: str,
        lease_generation: int,
        error_code: str,
        message: str,
    ) -> Mapping[str, object]: ...


PaperExecutionExecutor = Callable[[Mapping[str, object]], PaperExecutionFill]


class PaperExecutionWorker:
    def __init__(
        self,
        store: PaperExecutionStore,
        executor: PaperExecutionExecutor,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self.worker_id = worker_id or f"paper-execution-worker-{uuid4().hex}"

    def run_once(self) -> int:
        claim = self._store.claim_next_paper_execution_job(self.worker_id)
        if claim is None:
            return 0

        job_id = int(cast(int | str, claim["paperExecutionJobId"]))
        lease_generation = int(cast(int | str, claim["leaseGeneration"]))
        try:
            fill = self._executor(claim)
            self._store.complete_claimed_paper_execution_job(
                job_id=job_id,
                worker_id=self.worker_id,
                lease_generation=lease_generation,
                fill_price=fill.fill_price,
                filled_quantity=fill.filled_quantity,
                occurred_at=fill.occurred_at,
                knowledge_at=fill.knowledge_at,
                evidence=fill.evidence,
            )
        except PaperExecutionLeaseLostError:
            return 0
        except Exception as exc:
            with suppress(PaperExecutionLeaseLostError):
                self._store.fail_claimed_paper_execution_job(
                    job_id=job_id,
                    worker_id=self.worker_id,
                    lease_generation=lease_generation,
                    error_code=type(exc).__name__,
                    message=str(exc) or type(exc).__name__,
                )
            raise
        return 1
