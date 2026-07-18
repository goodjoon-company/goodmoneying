from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.portfolio_bot_store import PortfolioIdempotencyConflictError
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository


def _command() -> dict[str, Any]:
    return {
        "requestId": "portfolio-request-1",
        "idempotencyKey": "portfolio-key-1",
        "actorId": "operator:test",
        "requestedAt": "2026-07-18T08:00:00Z",
        "reason": "P5 포트폴리오 API 계약 검증",
    }


def _client(repository: FakePortfolioBotRepository) -> TestClient:
    return TestClient(
        create_app(
            SQLiteOperationsRepository(),
            portfolio_bot_repository=repository,
        )
    )


def test_포트폴리오_생성은_운영토큰과_멱등_명령_필드를_저장소에_전달한다() -> None:
    repository = FakePortfolioBotRepository()
    client = _client(repository)

    unauthorized = client.post("/v1/portfolios", json={**_command(), **_portfolio_body()})
    response = client.post(
        "/v1/portfolios",
        headers={"X-Operator-Token": "local-dev-token"},
        json={**_command(), **_portfolio_body()},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 201
    assert response.json() == _portfolio_response(7)
    assert repository.create_arguments == {
        "request_id": "portfolio-request-1",
        "idempotency_key": "portfolio-key-1",
        "actor_id": "operator:test",
        "requested_at": datetime(2026, 7, 18, 8, tzinfo=UTC),
        "reason": "P5 포트폴리오 API 계약 검증",
        "owner_id": "operator:local",
        "name": "KRW 운용 포트폴리오",
        "base_currency": "KRW",
    }


def test_포트폴리오_목록은_owner_문맥과_cursor를_저장소에_전달한다() -> None:
    repository = FakePortfolioBotRepository()
    client = _client(repository)

    response = client.get(
        "/v1/portfolios",
        params={"ownerId": "operator:local", "pageSize": 25, "cursor": "portfolio-cursor"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [_portfolio_response(8), _portfolio_response(7)],
        "nextCursor": "portfolio-next",
    }
    assert repository.list_arguments == {
        "owner_id": "operator:local",
        "page_size": 25,
        "cursor": "portfolio-cursor",
    }
    assert repository.mutation_count == 0


def test_포트폴리오_멱등키_충돌은_안정된_409_오류코드를_반환한다() -> None:
    repository = FakePortfolioBotRepository(idempotency_conflict=True)
    client = _client(repository)

    response = client.post(
        "/v1/portfolios",
        headers={"X-Operator-Token": "local-dev-token"},
        json={**_command(), **_portfolio_body()},
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "PORTFOLIO_IDEMPOTENCY_CONFLICT",
        "message": "멱등 키의 기존 포트폴리오 생성 요청과 본문이 다르다.",
    }


class FakePortfolioBotRepository:
    def __init__(self, *, idempotency_conflict: bool = False) -> None:
        self.idempotency_conflict = idempotency_conflict
        self.create_arguments: dict[str, Any] = {}
        self.list_arguments: dict[str, Any] = {}
        self.mutation_count = 0

    def create_portfolio(self, **arguments: Any) -> dict[str, Any]:
        if self.idempotency_conflict:
            raise PortfolioIdempotencyConflictError(
                "멱등 키의 기존 포트폴리오 생성 요청과 본문이 다르다."
            )
        self.create_arguments = arguments
        self.mutation_count += 1
        return _portfolio_response(7)

    def list_portfolios(self, **arguments: Any) -> dict[str, Any]:
        self.list_arguments = arguments
        return {
            "items": [_portfolio_response(8), _portfolio_response(7)],
            "nextCursor": "portfolio-next",
        }


def _portfolio_body() -> dict[str, str]:
    return {
        "ownerId": "operator:local",
        "name": "KRW 운용 포트폴리오",
        "baseCurrency": "KRW",
    }


def _portfolio_response(portfolio_id: int) -> dict[str, Any]:
    return {
        "portfolioId": portfolio_id,
        "ownerId": "operator:local",
        "name": "KRW 운용 포트폴리오",
        "baseCurrency": "KRW",
        "status": "active",
        "createdAt": "2026-07-18T08:00:01Z",
    }
