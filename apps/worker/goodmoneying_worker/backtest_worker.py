from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import uuid4

from goodmoneying_shared.backtest_engine import BacktestResult
from goodmoneying_shared.backtest_store import BacktestLeaseLostError


@dataclass(frozen=True, slots=True)
class BacktestExecutionOutput:
    result: BacktestResult
    artifacts: Sequence[Mapping[str, object]] = field(default_factory=tuple)


class BacktestStore(Protocol):
    def claim_next_run(self, worker_id: str) -> Mapping[str, object] | None: ...

    def complete_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        result: BacktestResult,
        artifacts: object = (),
    ) -> Mapping[str, object]: ...

    def fail_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        error_code: str,
        message: str,
    ) -> Mapping[str, object]: ...


BacktestExecutor = Callable[[Mapping[str, object]], BacktestExecutionOutput]


class BacktestWorker:
    def __init__(
        self,
        store: BacktestStore,
        executor: BacktestExecutor,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self.worker_id = worker_id or f"backtest-worker-{uuid4().hex}"

    def run_once(self) -> int:
        claim = self._store.claim_next_run(self.worker_id)
        if claim is None:
            return 0

        backtest_run_id = int(cast(int | str, claim["backtestRunId"]))
        lease_generation = int(cast(int | str, claim["leaseGeneration"]))
        try:
            output = self._executor(claim)
            self._store.complete_claimed_run(
                backtest_run_id,
                self.worker_id,
                lease_generation,
                result=output.result,
                artifacts=output.artifacts,
            )
        except BacktestLeaseLostError:
            return 0
        except Exception as exc:
            with suppress(BacktestLeaseLostError):
                self._store.fail_claimed_run(
                    backtest_run_id,
                    self.worker_id,
                    lease_generation,
                    error_code=type(exc).__name__,
                    message=str(exc) or type(exc).__name__,
                )
            raise
        return 1
