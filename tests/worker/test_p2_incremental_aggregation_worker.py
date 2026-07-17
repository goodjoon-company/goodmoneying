from types import SimpleNamespace
from typing import Any

import pytest

from goodmoneying_worker.aggregation_worker import CandleAggregationWorker


def test_집계_워커는_증분_재계산_작업을_레거시_전체_작업보다_먼저_처리한다() -> None:
    calls: list[tuple[Any, ...]] = []

    class Repository:
        def claim_next_candle_rollup_recompute_job(self, worker_id: str) -> object:
            calls.append(("claim", worker_id))
            return SimpleNamespace(id=7)

        def run_candle_rollup_recompute_job(self, job_id: int, worker_id: str) -> int:
            calls.append(("run", job_id, worker_id))
            return 1

    worker = CandleAggregationWorker(Repository(), worker_id="aggregation-test")  # type: ignore[arg-type]

    assert worker.run_once() == 1
    assert calls == [("claim", "aggregation-test"), ("run", 7, "aggregation-test")]


def test_증분_재계산_실패는_현재_임대_소유자로_재시도_상태를_기록한다() -> None:
    failures: list[tuple[int, str, str]] = []

    class Repository:
        def claim_next_candle_rollup_recompute_job(self, worker_id: str) -> object:
            return SimpleNamespace(id=9)

        def run_candle_rollup_recompute_job(self, job_id: int, worker_id: str) -> int:
            raise ArithmeticError("계산 실패")

        def fail_candle_rollup_recompute_job(
            self, job_id: int, worker_id: str, error_code: str
        ) -> None:
            failures.append((job_id, worker_id, error_code))

    worker = CandleAggregationWorker(Repository(), worker_id="aggregation-test")  # type: ignore[arg-type]

    with pytest.raises(ArithmeticError, match="계산 실패"):
        worker.run_once()

    assert failures == [(9, "aggregation-test", "ArithmeticError")]
