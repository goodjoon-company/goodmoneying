from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from goodmoneying_shared.portfolio_bot_store import (
    PortfolioCursorMismatchError,
    PortfolioIdempotencyConflictError,
    PostgresPortfolioBotStore,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_포트폴리오_생성은_멱등이며_owner별_목록으로_조회된다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    requested_at = datetime(2026, 7, 18, 8, tzinfo=UTC)

    portfolio = store.create_portfolio(
        request_id=f"portfolio-request-{key}",
        idempotency_key=f"portfolio-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P5-2 포트폴리오 생성",
        owner_id="operator:local",
        name=f"KRW 운용 포트폴리오 {key}",
        base_currency="KRW",
    )
    replay = store.create_portfolio(
        request_id=f"portfolio-request-{key}",
        idempotency_key=f"portfolio-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P5-2 포트폴리오 생성",
        owner_id="operator:local",
        name=f"KRW 운용 포트폴리오 {key}",
        base_currency="KRW",
    )
    listing = store.list_portfolios(owner_id="operator:local", page_size=10, cursor=None)

    assert portfolio["portfolioId"] == replay["portfolioId"]
    assert portfolio["ownerId"] == "operator:local"
    assert portfolio["baseCurrency"] == "KRW"
    assert portfolio["status"] == "active"
    items = cast(list[dict[str, Any]], listing["items"])
    assert any(
        item["portfolioId"] == portfolio["portfolioId"]
        for item in items
    )

    with pytest.raises(PortfolioIdempotencyConflictError):
        store.create_portfolio(
            request_id=f"portfolio-request-{key}",
            idempotency_key=f"portfolio-key-{key}",
            actor_id="operator:test",
            requested_at=requested_at,
            reason="같은 키의 다른 포트폴리오 생성",
            owner_id="operator:local",
            name=f"KRW 변경 포트폴리오 {key}",
            base_currency="KRW",
        )


def test_live_postgres_포트폴리오_cursor는_owner_문맥을_검증한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    requested_at = datetime(2026, 7, 18, 9, tzinfo=UTC)
    owner_a = f"operator:portfolio-a-{key}"
    owner_b = f"operator:portfolio-b-{key}"

    for index in range(3):
        store.create_portfolio(
            request_id=f"portfolio-page-request-{key}-{index}",
            idempotency_key=f"portfolio-page-key-{key}-{index}",
            actor_id="operator:test",
            requested_at=requested_at,
            reason="P5-2 포트폴리오 cursor 검증",
            owner_id=owner_a,
            name=f"cursor portfolio {key}-{index}",
            base_currency="KRW",
        )

    first_page = store.list_portfolios(owner_id=owner_a, page_size=2, cursor=None)

    assert first_page["nextCursor"] is not None
    with pytest.raises(PortfolioCursorMismatchError):
        store.list_portfolios(
            owner_id=owner_b,
            page_size=2,
            cursor=str(first_page["nextCursor"]),
        )


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
