from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.backtest_store import (
    BacktestCursorMismatchError,
    BacktestIdempotencyConflictError,
    BacktestInputNotReadyError,
)
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


def test_백테스트_run_생성은_운영토큰을_요구하고_queued_run을_202로_반환한다() -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)

    unauthorized = client.post("/v1/backtest-runs", json=_create_request())
    accepted = client.post(
        "/v1/backtest-runs",
        headers={"X-Operator-Token": "local-dev-token"},
        json=_create_request(),
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json() == repository.summary(23)
    assert repository.mutation_count == 1
    assert repository.last_create_arguments == {
        "request_id": "backtest-request-1",
        "idempotency_key": "backtest-key-1",
        "actor_id": "operator:test",
        "requested_at": datetime(2026, 7, 18, 8, tzinfo=UTC),
        "reason": "P4-7 백테스트 실행 생성",
        "strategy_version_id": 41,
        "dataset_version_id": 12,
        "engine_version": "backtest-core-v1",
        "parameters": {"entryQuantity": "0.1"},
        "seed": 42,
        "initial_cash": Decimal("1000000"),
        "execution": {
            "feeRate": Decimal("0.0005"),
            "slippageBps": Decimal("5"),
            "latencySeconds": 60,
            "maxParticipationRate": Decimal("0.25"),
        },
        "max_attempts": 3,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("feeRate", "-0.0001"),
        ("slippageBps", "-1"),
        ("maxParticipationRate", "0"),
        ("maxParticipationRate", "1.1"),
    ),
)
def test_백테스트_run_생성은_체결_모델_범위를_검증한다(
    field: str,
    value: str,
) -> None:
    repository = FakeBacktestRepository()
    client = _client(repository)
    request = _create_request()
    execution = request["execution"]
    assert isinstance(execution, dict)
    execution[field] = value

    response = client.post(
        "/v1/backtest-runs",
        headers={"X-Operator-Token": "local-dev-token"},
        json=request,
    )

    assert response.status_code == 422
    assert repository.mutation_count == 0


def test_백테스트_run_생성은_같은_멱등키와_본문을_재생하고_다른_본문은_409로_거부한다() -> None:
    repository = FakeBacktestRepository(idempotency_conflict=True)
    client = _client(repository)
    headers = {"X-Operator-Token": "local-dev-token"}

    first = client.post("/v1/backtest-runs", headers=headers, json=_create_request())
    replay = client.post("/v1/backtest-runs", headers=headers, json=_create_request())
    changed = _create_request()
    changed["reason"] = "다른 백테스트 요청"
    conflict = client.post("/v1/backtest-runs", headers=headers, json=changed)

    assert first.status_code == replay.status_code == 202
    assert first.json()["backtestRunId"] == replay.json()["backtestRunId"]
    assert conflict.status_code == 409
    assert conflict.json() == {
        "code": "BACKTEST_IDEMPOTENCY_CONFLICT",
        "message": "멱등 키의 기존 백테스트 실행 요청과 본문이 다르다.",
    }


def test_백테스트_run_생성은_published_strategy와_sealed_dataset만_허용한다() -> None:
    repository = FakeBacktestRepository(input_not_ready=True)
    client = _client(repository)

    response = client.post(
        "/v1/backtest-runs",
        headers={"X-Operator-Token": "local-dev-token"},
        json=_create_request(),
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "BACKTEST_INPUT_NOT_READY",
        "message": (
            "published 전략 version과 sealed 데이터셋 version만 "
            "백테스트 실행에 사용할 수 있다."
        ),
    }


def test_백테스트_progress_websocket은_현재_run_진행_snapshot을_전송한다() -> None:
    repository = FakeBacktestRepository(pending=True)
    client = _client(repository)

    with client.websocket_connect("/v1/backtest-runs/21/progress") as websocket:
        message = websocket.receive_json()

    assert message == {
        "version": "1",
        "type": "backtest.progress",
        "backtestRunId": 21,
        "status": "pending",
        "progressPercent": "0",
        "isTerminal": False,
        "inputHash": "e" * 64,
        "resultHash": None,
        "requestedAt": "2026-07-18T00:00:00Z",
        "startedAt": None,
        "finishedAt": None,
    }
    assert repository.summary_read_count == 1
    assert repository.last_backtest_run_id == 21


def test_백테스트_progress_websocket은_없는_run을_안정된_오류로_전송한다() -> None:
    repository = FakeBacktestRepository(not_found=True)
    client = _client(repository)

    with client.websocket_connect("/v1/backtest-runs/999/progress") as websocket:
        message = websocket.receive_json()

    assert message == {
        "version": "1",
        "type": "backtest.error",
        "code": "BACKTEST_RUN_NOT_FOUND",
        "message": "백테스트 실행 결과가 없습니다.",
        "backtestRunId": 999,
    }


class FakeBacktestRepository:
    def __init__(
        self,
        *,
        not_found: bool = False,
        cursor_mismatch: bool = False,
        pending: bool = False,
        idempotency_conflict: bool = False,
        input_not_ready: bool = False,
    ) -> None:
        self.not_found = not_found
        self.cursor_mismatch = cursor_mismatch
        self.pending = pending
        self.idempotency_conflict = idempotency_conflict
        self.input_not_ready = input_not_ready
        self.read_count = 0
        self.summary_read_count = 0
        self.list_count = 0
        self.mutation_count = 0
        self.last_backtest_run_id: int | None = None
        self.last_list_arguments: dict[str, object] | None = None
        self.last_trade_arguments: dict[str, object] | None = None
        self.last_equity_arguments: dict[str, object] | None = None
        self.last_create_arguments: dict[str, object] | None = None

    def list_runs(self, **arguments: object) -> Mapping[str, object]:
        self.list_count += 1
        self.last_list_arguments = dict(arguments)
        if self.cursor_mismatch:
            raise BacktestCursorMismatchError("목록 cursor 문맥이 다르다.")
        return {
            "items": [self.summary(22), self.summary(21)],
            "nextCursor": "backtest-run-list-next",
        }

    def create_run(self, **arguments: object) -> Mapping[str, object]:
        if self.input_not_ready:
            raise BacktestInputNotReadyError(
                "published 전략 version과 sealed 데이터셋 version만 백테스트 실행에 사용할 수 있다."
            )
        if self.idempotency_conflict and self.mutation_count >= 2:
            raise BacktestIdempotencyConflictError(
                "멱등 키의 기존 백테스트 실행 요청과 본문이 다르다."
            )
        self.mutation_count += 1
        self.last_create_arguments = dict(arguments)
        return self.summary(23)

    def get_run(self, backtest_run_id: int) -> Mapping[str, object] | None:
        self.read_count += 1
        self.last_backtest_run_id = backtest_run_id
        if self.not_found:
            return None
        return self.run(backtest_run_id)

    def get_run_summary(self, backtest_run_id: int) -> Mapping[str, object] | None:
        self.summary_read_count += 1
        self.last_backtest_run_id = backtest_run_id
        if self.not_found:
            return None
        return self.summary(backtest_run_id)

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
            "startedAt": None if self.pending else "2026-07-18T00:00:00Z",
            "finishedAt": None if self.pending else "2026-07-18T00:00:00Z",
        }


def _create_request() -> dict[str, object]:
    return {
        "requestId": "backtest-request-1",
        "idempotencyKey": "backtest-key-1",
        "actorId": "operator:test",
        "requestedAt": "2026-07-18T08:00:00Z",
        "reason": "P4-7 백테스트 실행 생성",
        "strategyVersionId": 41,
        "datasetVersionId": 12,
        "engineVersion": "backtest-core-v1",
        "parameters": {"entryQuantity": "0.1"},
        "seed": 42,
        "initialCash": "1000000",
        "execution": {
            "feeRate": "0.0005",
            "slippageBps": "5",
            "latencySeconds": 60,
            "maxParticipationRate": "0.25",
        },
        "maxAttempts": 3,
    }
