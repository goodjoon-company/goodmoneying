from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest

from goodmoneying_shared.backtest_engine import BacktestResult
from goodmoneying_shared.backtest_store import BacktestLeaseLostError
from goodmoneying_worker.backtest_worker import BacktestExecutionOutput, BacktestWorker


def test_백테스트_워커는_임대할_run이_없으면_유휴로_종료한다() -> None:
    store = _Store(claim=None)
    worker = BacktestWorker(store, lambda _: pytest.fail("executor는 호출되지 않아야 한다"))

    assert worker.run_once() == 0
    assert store.calls == [("claim", worker.worker_id)]


def test_백테스트_워커는_임대한_run을_실행하고_artifact와_함께_완료한다() -> None:
    result = _result()
    artifact = {
        "artifactType": "worker_summary",
        "contentHash": "a" * 64,
        "metadata": {"rows": 1},
    }
    store = _Store(
        claim={"backtestRunId": 17, "leaseGeneration": 3, "inputHash": result.input_hash}
    )
    executor_calls: list[dict[str, Any]] = []

    def executor(claim: Mapping[str, object]) -> BacktestExecutionOutput:
        claim_dict = dict(claim)
        executor_calls.append(claim_dict)
        return BacktestExecutionOutput(result=result, artifacts=(artifact,))

    worker = BacktestWorker(store, executor, worker_id="backtest-worker-test")

    assert worker.run_once() == 1
    assert executor_calls == [
        {"backtestRunId": 17, "leaseGeneration": 3, "inputHash": result.input_hash}
    ]
    assert store.calls == [
        ("claim", "backtest-worker-test"),
        (
            "complete",
            17,
            "backtest-worker-test",
            3,
            result.result_hash,
            (artifact,),
        ),
    ]


def test_백테스트_워커는_엔진_오류를_현재_임대_generation으로_재시도_기록한다() -> None:
    store = _Store(claim={"backtestRunId": 19, "leaseGeneration": 4, "inputHash": "e" * 64})
    worker = BacktestWorker(
        store,
        lambda _: (_ for _ in ()).throw(ArithmeticError("계산 실패")),
        worker_id="backtest-worker-test",
    )

    with pytest.raises(ArithmeticError, match="계산 실패"):
        worker.run_once()

    assert store.calls == [
        ("claim", "backtest-worker-test"),
        ("fail", 19, "backtest-worker-test", 4, "ArithmeticError", "계산 실패"),
    ]


def test_백테스트_워커는_실패_전이_시점에_임대를_잃어도_원래_엔진_오류를_유지한다() -> None:
    store = _Store(
        claim={"backtestRunId": 29, "leaseGeneration": 6, "inputHash": "e" * 64},
        fail_error=BacktestLeaseLostError("임대가 만료됐다."),
    )
    worker = BacktestWorker(
        store,
        lambda _: (_ for _ in ()).throw(ArithmeticError("계산 실패")),
        worker_id="backtest-worker-test",
    )

    with pytest.raises(ArithmeticError, match="계산 실패"):
        worker.run_once()

    assert store.calls == [
        ("claim", "backtest-worker-test"),
        ("fail", 29, "backtest-worker-test", 6, "ArithmeticError", "계산 실패"),
    ]


def test_백테스트_워커는_완료_시점에_임대를_잃으면_늦은_실패_전이를_쓰지_않는다() -> None:
    store = _Store(
        claim={"backtestRunId": 23, "leaseGeneration": 5, "inputHash": "e" * 64},
        complete_error=BacktestLeaseLostError("임대가 만료됐다."),
    )
    worker = BacktestWorker(
        store,
        lambda _: BacktestExecutionOutput(result=_result()),
        worker_id="backtest-worker-test",
    )

    assert worker.run_once() == 0
    assert store.calls == [
        ("claim", "backtest-worker-test"),
        ("complete", 23, "backtest-worker-test", 5, "f" * 64, ()),
    ]


class _Store:
    def __init__(
        self,
        *,
        claim: dict[str, Any] | None,
        complete_error: Exception | None = None,
        fail_error: Exception | None = None,
    ) -> None:
        self.claim = claim
        self.complete_error = complete_error
        self.fail_error = fail_error
        self.calls: list[tuple[Any, ...]] = []

    def claim_next_run(self, worker_id: str) -> dict[str, Any] | None:
        self.calls.append(("claim", worker_id))
        return self.claim

    def complete_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        result: BacktestResult,
        artifacts: object = (),
    ) -> dict[str, Any]:
        artifacts_tuple = tuple(artifacts) if isinstance(artifacts, tuple) else tuple(artifacts)  # type: ignore[arg-type]
        self.calls.append(
            (
                "complete",
                backtest_run_id,
                worker_id,
                lease_generation,
                result.result_hash,
                artifacts_tuple,
            )
        )
        if self.complete_error is not None:
            raise self.complete_error
        return {"backtestRunId": backtest_run_id}

    def fail_claimed_run(
        self,
        backtest_run_id: int,
        worker_id: str,
        lease_generation: int,
        *,
        error_code: str,
        message: str,
    ) -> dict[str, Any]:
        self.calls.append(
            ("fail", backtest_run_id, worker_id, lease_generation, error_code, message)
        )
        if self.fail_error is not None:
            raise self.fail_error
        return {"backtestRunId": backtest_run_id}


def _result() -> BacktestResult:
    return BacktestResult(
        status="succeeded",
        input_hash="e" * 64,
        result_hash="f" * 64,
        assumptions=("orderbook_absent_uses_candle_close",),
        replay_events=(),
        trades=(),
        equity_points=(),
        metrics={"finalEquity": Decimal("1000")},
        golden_replay_signals=(),
    )
