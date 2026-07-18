from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import uuid4


class RiskEvaluationStore(Protocol):
    def evaluate_next_order_intent_risk(
        self, worker_id: str
    ) -> Mapping[str, object] | None: ...


class RiskEvaluationWorker:
    def __init__(
        self,
        store: RiskEvaluationStore,
        *,
        worker_id: str | None = None,
    ) -> None:
        self._store = store
        self.worker_id = worker_id or f"risk-evaluation-worker-{uuid4().hex}"

    def run_once(self) -> int:
        evaluated = self._store.evaluate_next_order_intent_risk(self.worker_id)
        if evaluated is None:
            return 0
        return 1
