from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.backtest_store import BacktestCursorMismatchError
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


def test_백테스트_run_목록은_저장된_run을_최신순_cursor_페이지로_반환한다() -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)

    response = client.get(
        "/v1/backtest-runs",
        params={"pageSize": 25, "cursor": "backtest-run-list-cursor"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [repository.summary(22), repository.summary(21)],
        "nextCursor": "backtest-run-list-next",
    }
    assert repository.list_count == 1
    assert repository.last_list_arguments == {
        "page_size": 25,
        "cursor": "backtest-run-list-cursor",
    }
    assert repository.mutation_count == 0


def test_pending_백테스트_run은_아직_result_hash가_없어도_API_계약을_통과한다() -> None:
    repository = FakeBacktestRepository(pending=True)
    client = _client(repository)

    detail = client.get("/v1/backtest-runs/21")
    listing = client.get("/v1/backtest-runs")

    assert detail.status_code == 200
    assert listing.status_code == 200
    assert detail.json()["status"] == "pending"
    assert detail.json()["resultHash"] is None
    assert listing.json()["items"][0]["resultHash"] is None


def test_백테스트_run_목록_cursor_문맥이_다르면_안정된_409_오류코드를_반환한다() -> None:
    repository = FakeBacktestRepository(cursor_mismatch=True)
    client = _client(repository)

    response = client.get("/v1/backtest-runs", params={"cursor": "wrong-context"})

    assert response.status_code == 409
    assert response.json() == {
        "code": "BACKTEST_CURSOR_CONTEXT_MISMATCH",
        "message": "백테스트 run 목록 cursor가 현재 조회 문맥과 다릅니다.",
    }


def test_백테스트_trade_페이지는_저장된_체결을_cursor로_반환한다() -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)

    response = client.get(
        "/v1/backtest-runs/21/trades",
        params={"pageSize": 100, "cursor": "trade-cursor"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "backtestRunId": 21,
        "items": [
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
        "nextCursor": "trade-next",
    }
    assert repository.last_trade_arguments == {
        "backtest_run_id": 21,
        "page_size": 100,
        "cursor": "trade-cursor",
    }
    assert repository.mutation_count == 0


def test_백테스트_equity_point_페이지는_저장된_자산곡선을_cursor로_반환한다() -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)

    response = client.get(
        "/v1/backtest-runs/21/equity-points",
        params={"pageSize": 100, "cursor": "equity-cursor"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "backtestRunId": 21,
        "items": [
            {
                "pointSequence": 1,
                "occurredAt": "2026-07-18T00:00:00Z",
                "knowledgeAt": "2026-07-18T00:00:00Z",
                "cash": "899.799900",
                "basePosition": "1.00",
                "equity": "1009.579790",
            }
        ],
        "nextCursor": "equity-next",
    }
    assert repository.last_equity_arguments == {
        "backtest_run_id": 21,
        "page_size": 100,
        "cursor": "equity-cursor",
    }
    assert repository.mutation_count == 0


def test_백테스트_결과_cursor_문맥이_다르면_안정된_409_오류코드를_반환한다() -> None:
    repository = FakeBacktestRepository(cursor_mismatch=True)
    client = _client(repository)

    response = client.get("/v1/backtest-runs/21/trades", params={"cursor": "wrong"})

    assert response.status_code == 409
    assert response.json() == {
        "code": "BACKTEST_RESULT_CURSOR_CONTEXT_MISMATCH",
        "message": "백테스트 결과 cursor가 현재 조회 문맥과 다릅니다.",
    }


def test_백테스트_결과_페이지의_없는_run은_안정된_404_오류코드를_반환한다() -> None:
    repository = FakeBacktestRepository(not_found=True)
    client = _client(repository)

    response = client.get("/v1/backtest-runs/999/trades")

    assert response.status_code == 404
    assert response.json() == {
        "code": "BACKTEST_RUN_NOT_FOUND",
        "message": "백테스트 실행 결과가 없습니다.",
    }


class FakeBacktestRepository:
    def __init__(
        self,
        *,
        not_found: bool = False,
        cursor_mismatch: bool = False,
        pending: bool = False,
    ) -> None:
        self.not_found = not_found
        self.cursor_mismatch = cursor_mismatch
        self.pending = pending
        self.read_count = 0
        self.list_count = 0
        self.mutation_count = 0
        self.last_backtest_run_id: int | None = None
        self.last_list_arguments: dict[str, object] | None = None
        self.last_trade_arguments: dict[str, object] | None = None
        self.last_equity_arguments: dict[str, object] | None = None

    def list_runs(self, **arguments: object) -> Mapping[str, object]:
        self.list_count += 1
        self.last_list_arguments = dict(arguments)
        if self.cursor_mismatch:
            raise BacktestCursorMismatchError("목록 cursor 문맥이 다르다.")
        return {
            "items": [self.summary(22), self.summary(21)],
            "nextCursor": "backtest-run-list-next",
        }

    def get_run(self, backtest_run_id: int) -> Mapping[str, object] | None:
        self.read_count += 1
        self.last_backtest_run_id = backtest_run_id
        if self.not_found:
            return None
        return self.run(backtest_run_id)

    def list_run_trades(self, **arguments: object) -> Mapping[str, object] | None:
        self.last_trade_arguments = dict(arguments)
        if self.cursor_mismatch:
            raise BacktestCursorMismatchError("결과 cursor 문맥이 다르다.")
        if self.not_found:
            return None
        return {
            "backtestRunId": arguments["backtest_run_id"],
            "items": self.run(21)["trades"],
            "nextCursor": "trade-next",
        }

    def list_run_equity_points(self, **arguments: object) -> Mapping[str, object] | None:
        self.last_equity_arguments = dict(arguments)
        if self.cursor_mismatch:
            raise BacktestCursorMismatchError("결과 cursor 문맥이 다르다.")
        if self.not_found:
            return None
        return {
            "backtestRunId": arguments["backtest_run_id"],
            "items": [
                {
                    "pointSequence": 1,
                    "occurredAt": "2026-07-18T00:00:00Z",
                    "knowledgeAt": "2026-07-18T00:00:00Z",
                    "cash": Decimal("899.799900"),
                    "basePosition": Decimal("1.00"),
                    "equity": Decimal("1009.579790"),
                }
            ],
            "nextCursor": "equity-next",
        }

    def run(self, backtest_run_id: int) -> Mapping[str, object]:
        return {
            "backtestRunId": backtest_run_id,
            "strategyVersionId": 41,
            "datasetVersionId": 12,
            "status": "pending" if self.pending else "succeeded",
            "inputHash": "e" * 64,
            "resultHash": None if self.pending else "f" * 64,
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

    def summary(self, backtest_run_id: int) -> Mapping[str, object]:
        return {
            "backtestRunId": backtest_run_id,
            "strategyVersionId": 41,
            "datasetVersionId": 12,
            "engineVersion": "backtest-core-v1",
            "status": "pending" if self.pending else "succeeded",
            "inputHash": "e" * 64,
            "resultHash": None if self.pending else "f" * 64,
            "requestedAt": "2026-07-18T00:00:00Z",
            "startedAt": "2026-07-18T00:00:00Z",
            "finishedAt": "2026-07-18T00:00:00Z",
        }
