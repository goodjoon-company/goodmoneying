from __future__ import annotations

from typing import Any

from goodmoneying_worker.risk_evaluation_worker import RiskEvaluationWorker


def test_risk_evaluation_worker는_다음_주문_의도를_평가한다() -> None:
    store = FakeRiskEvaluationStore(result={"orderIntentId": 17, "status": "approved"})
    worker = RiskEvaluationWorker(store, worker_id="risk-worker-a")

    processed = worker.run_once()

    assert processed == 1
    assert store.worker_id == "risk-worker-a"


def test_risk_evaluation_worker는_평가할_주문이_없으면_0을_반환한다() -> None:
    store = FakeRiskEvaluationStore(result=None)
    worker = RiskEvaluationWorker(store, worker_id="risk-worker-a")

    processed = worker.run_once()

    assert processed == 0
    assert store.worker_id == "risk-worker-a"


class FakeRiskEvaluationStore:
    def __init__(self, *, result: dict[str, Any] | None) -> None:
        self.result = result
        self.worker_id: str | None = None

    def evaluate_next_order_intent_risk(self, worker_id: str) -> dict[str, Any] | None:
        self.worker_id = worker_id
        return self.result
