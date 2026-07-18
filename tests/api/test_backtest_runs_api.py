from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository


def _client(repository: FakeBacktestRepository) -> TestClient:
    return TestClient(
        create_app(
            SQLiteOperationsRepository(),
            backtest_repository=repository,
        )
    )


def test_백테스트_run_GET은_저장된_성과_체결_산출물을_읽기전용으로_반환한다() -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)

    response = client.get("/v1/backtest-runs/21")

    assert response.status_code == 200
    assert response.json() == {
        "backtestRunId": 21,
        "strategyVersionId": 41,
        "datasetVersionId": 12,
        "status": "succeeded",
        "inputHash": "e" * 64,
        "resultHash": "f" * 64,
        "metrics": [
            {
                "metricName": "finalEquity",
                "scopeKey": "run",
                "metricValue": "1009.579790",
                "metricPayload": {},
            }
        ],
        "trades": [
            {
                "tradeSequence": 1,
                "side": "buy",
                "requestedQuantity": "3",
                "filledQuantity": "1.00",
                "remainingQuantity": "2.00",
                "fillPrice": "100.100",
                "feePaid": "0.100100",
                "status": "partially_filled",
                "occurredAt": "2026-07-18T00:00:00Z",
                "knowledgeAt": "2026-07-18T00:00:00Z",
            }
        ],
        "artifacts": [
            {
                "artifactType": "walk_forward_summary",
                "contentHash": "c" * 64,
                "mediaType": "application/json",
                "storageUri": "artifact://p4-3/walk-forward",
                "metadata": {"folds": 3},
            }
        ],
    }
    assert repository.read_count == 1
    assert repository.last_backtest_run_id == 21
    assert repository.mutation_count == 0


def test_없는_백테스트_run은_안정된_404_오류코드를_반환한다() -> None:
    repository = FakeBacktestRepository(not_found=True)
    client = _client(repository)

    response = client.get("/v1/backtest-runs/999")

    assert response.status_code == 404
    assert response.json() == {
        "code": "BACKTEST_RUN_NOT_FOUND",
        "message": "백테스트 실행 결과가 없습니다.",
    }


class FakeBacktestRepository:
    def __init__(self, *, not_found: bool = False) -> None:
        self.not_found = not_found
        self.read_count = 0
        self.mutation_count = 0
        self.last_backtest_run_id: int | None = None

    def get_run(self, backtest_run_id: int) -> Mapping[str, object] | None:
        self.read_count += 1
        self.last_backtest_run_id = backtest_run_id
        if self.not_found:
            return None
        return {
            "backtestRunId": backtest_run_id,
            "strategyVersionId": 41,
            "datasetVersionId": 12,
            "status": "succeeded",
            "inputHash": "e" * 64,
            "resultHash": "f" * 64,
            "metrics": [
                {
                    "metricName": "finalEquity",
                    "scopeKey": "run",
                    "metricValue": Decimal("1009.579790"),
                    "metricPayload": {},
                }
            ],
            "trades": [
                {
                    "tradeSequence": 1,
                    "side": "buy",
                    "requestedQuantity": Decimal("3"),
                    "filledQuantity": Decimal("1.00"),
                    "remainingQuantity": Decimal("2.00"),
                    "fillPrice": Decimal("100.100"),
                    "feePaid": Decimal("0.100100"),
                    "status": "partially_filled",
                    "occurredAt": "2026-07-18T00:00:00Z",
                    "knowledgeAt": "2026-07-18T00:00:00Z",
                }
            ],
            "artifacts": [
                {
                    "artifactType": "walk_forward_summary",
                    "contentHash": "c" * 64,
                    "mediaType": "application/json",
                    "storageUri": "artifact://p4-3/walk-forward",
                    "metadata": {"folds": 3},
                }
            ],
        }
