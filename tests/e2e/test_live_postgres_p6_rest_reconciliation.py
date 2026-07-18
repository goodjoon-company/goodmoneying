from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from test_live_postgres_reconciliation import (
    _insert_exchange_order,
    _insert_instrument,
    _insert_order_intent,
    _insert_paper_bot_fixture,
    _insert_strategy_version,
)

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.upbit_rest_reconciliation import apply_upbit_rest_order_snapshot

pytestmark = pytest.mark.live


def test_live_postgres_P6_5_Upbit_REST_snapshot은_내부_원장_fill로_반영된다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 12, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        status="outcome_unknown",
    )
    exchange_order_id = _insert_exchange_order(
        repository,
        order_intent_id,
        key,
        status="outcome_unknown",
        now=now,
    )
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE exchange_orders
            SET raw_payload = raw_payload || %s
            WHERE id=%s
            """,
            (
                Jsonb(
                    {
                        "upbit": {
                            "uuid": f"upbit-rest-{key}",
                            "identifier": (
                                "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr"
                            ),
                        }
                    }
                ),
                exchange_order_id,
            ),
        )

    result = apply_upbit_rest_order_snapshot(
        store,
        exchange_order_id=exchange_order_id,
        run_key=f"p6-rest-{key}",
        actor_id="operator:test",
        reason="P6-5 Upbit REST snapshot reconciliation",
        snapshot_payload={
            "market": "KRW-BTC",
            "uuid": f"upbit-rest-{key}",
            "identifier": "gm1_abcdefghijklmnopqrstuvwxyz234567abcdefghijklmnopqr",
            "side": "bid",
            "state": "done",
            "created_at": "2026-07-18T20:59:59+09:00",
            "volume": "2",
            "remaining_volume": "0",
            "executed_volume": "2",
            "paid_fee": "24",
            "smp_type": "cancel_taker",
            "prevented_volume": "0",
            "prevented_locked": "0",
            "trades_count": 1,
            "trades": [
                {
                    "uuid": f"trade-{key}",
                    "price": "120",
                    "volume": "2",
                    "funds": "240",
                    "side": "bid",
                    "created_at": "2026-07-18T21:00:00+09:00",
                }
            ],
        },
        knowledge_at=now,
        source_endpoint="GET /v1/order",
    )

    assert result["status"] == "succeeded"
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status AS intent_status,
              exchange.status AS exchange_status,
              fill.fill_source,
              fill.filled_quantity,
              fill.fill_price,
              fill.fee_paid,
              fill.evidence->>'sourceEndpoint' AS source_endpoint,
              fill.evidence->>'state' AS fill_state,
              fill.evidence->>'paidFee' AS fill_paid_fee,
              fill.evidence->>'smpType' AS fill_smp_type,
              fill.evidence->>'tradesCount' AS fill_trades_count,
              position.quantity,
              position.average_entry_price,
              run.evidence->>'sourceEndpoint' AS run_source_endpoint,
              run.evidence->>'smpType' AS run_smp_type
            FROM exchange_orders exchange
            JOIN order_intents intent ON intent.id = exchange.order_intent_id
            JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            JOIN reconciliation_runs run ON run.exchange_order_id = exchange.id
            WHERE exchange.id=%s
            """,
            (fixture["portfolioId"], exchange_order_id),
        ).fetchone()
        assert row is not None

    assert row["intent_status"] == "reconciled"
    assert row["exchange_status"] == "reconciled"
    assert row["fill_source"] == "reconciliation"
    assert row["filled_quantity"] == Decimal("2.000000000000000000")
    assert row["fill_price"] == Decimal("120.000000000000000000")
    assert row["fee_paid"] == Decimal("24.000000000000000000")
    assert row["source_endpoint"] == "GET /v1/order"
    assert row["fill_state"] == "done"
    assert row["fill_paid_fee"] == "24"
    assert row["fill_smp_type"] == "cancel_taker"
    assert row["fill_trades_count"] == "1"
    assert row["quantity"] == Decimal("2.000000000000000000")
    assert row["average_entry_price"] == Decimal("120.000000000000000000")
    assert row["run_source_endpoint"] == "GET /v1/order"
    assert row["run_smp_type"] == "cancel_taker"


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
