from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from goodmoneying_shared.portfolio_bot_store import (
    PaperExecutionBlockedError,
    PaperExecutionLeaseLostError,
)
from goodmoneying_worker.paper_execution_worker import (
    PaperExecutionFill,
    PaperExecutionWorker,
)


def test_paper_execution_worker는_claim된_job을_시뮬레이터와_complete로_연결한다() -> None:
    store = FakePaperExecutionStore()
    fill = PaperExecutionFill(
        fill_price=Decimal("100.5"),
        occurred_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
        knowledge_at=datetime(2026, 7, 18, 10, tzinfo=UTC),
        evidence={"source": "unit-test"},
    )
    worker = PaperExecutionWorker(
        store,
        executor=lambda claim: fill,
        worker_id="paper-worker-a",
    )

    processed = worker.run_once()

    assert processed == 1
    assert store.completed == {
        "job_id": 17,
        "worker_id": "paper-worker-a",
        "lease_generation": 3,
        "fill_price": Decimal("100.5"),
        "filled_quantity": None,
        "occurred_at": datetime(2026, 7, 18, 10, tzinfo=UTC),
        "knowledge_at": datetime(2026, 7, 18, 10, tzinfo=UTC),
        "evidence": {"source": "unit-test"},
    }


def test_paper_execution_worker는_lease_lost를_재시도_오류로_기록하지_않는다() -> None:
    store = FakePaperExecutionStore(lease_lost=True)
    worker = PaperExecutionWorker(
        store,
        executor=lambda _claim: PaperExecutionFill(fill_price=Decimal("100")),
        worker_id="paper-worker-a",
    )

    processed = worker.run_once()

    assert processed == 0
    assert store.failed is None


def test_paper_execution_worker는_kill_switch_차단을_재시도_오류로_기록하지_않는다() -> None:
    store = FakePaperExecutionStore(blocked=True)
    worker = PaperExecutionWorker(
        store,
        executor=lambda _claim: PaperExecutionFill(fill_price=Decimal("100")),
        worker_id="paper-worker-a",
    )

    processed = worker.run_once()

    assert processed == 0
    assert store.failed is None


def test_paper_execution_worker는_blocked_반환을_처리완료로_세지_않는다() -> None:
    store = FakePaperExecutionStore(blocked_return=True)
    worker = PaperExecutionWorker(
        store,
        executor=lambda _claim: PaperExecutionFill(fill_price=Decimal("100")),
        worker_id="paper-worker-a",
    )

    processed = worker.run_once()

    assert processed == 0
    assert store.failed is None


def test_paper_execution_worker는_시뮬레이터_예외를_fail로_기록하고_전파한다() -> None:
    store = FakePaperExecutionStore()
    worker = PaperExecutionWorker(
        store,
        executor=lambda _claim: (_ for _ in ()).throw(ValueError("가격 없음")),
        worker_id="paper-worker-a",
    )

    with pytest.raises(ValueError, match="가격 없음"):
        worker.run_once()

    assert store.failed == {
        "job_id": 17,
        "worker_id": "paper-worker-a",
        "lease_generation": 3,
        "error_code": "ValueError",
        "message": "가격 없음",
    }


class FakePaperExecutionStore:
    def __init__(
        self,
        *,
        lease_lost: bool = False,
        blocked: bool = False,
        blocked_return: bool = False,
    ) -> None:
        self.lease_lost = lease_lost
        self.blocked = blocked
        self.blocked_return = blocked_return
        self.completed: dict[str, Any] | None = None
        self.failed: dict[str, Any] | None = None

    def claim_next_paper_execution_job(self, worker_id: str) -> dict[str, Any]:
        assert worker_id == "paper-worker-a"
        return {
            "paperExecutionJobId": 17,
            "orderIntentId": 23,
            "leaseGeneration": 3,
        }

    def complete_claimed_paper_execution_job(self, **arguments: Any) -> dict[str, Any]:
        if self.lease_lost:
            raise PaperExecutionLeaseLostError("임대가 만료됐다.")
        if self.blocked:
            raise PaperExecutionBlockedError("kill switch가 활성화됐다.")
        if self.blocked_return:
            return {"paperExecutionJobId": arguments["job_id"], "status": "retry_wait"}
        self.completed = arguments
        return {"paperExecutionJobId": arguments["job_id"], "status": "succeeded"}

    def fail_claimed_paper_execution_job(self, **arguments: Any) -> dict[str, Any]:
        self.failed = arguments
        return {"paperExecutionJobId": arguments["job_id"], "status": "retry_wait"}
