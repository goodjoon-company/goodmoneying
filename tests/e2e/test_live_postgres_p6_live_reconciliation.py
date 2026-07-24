from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_live_postgres_p6_live_order_binding import _insert_live_binding_fixture

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.upbit_live_reconciliation import (
    apply_upbit_live_rest_order_snapshot,
)

pytestmark = pytest.mark.live


def test_live_postgres_P6_9_live_REST_terminal_snapshot은_binding_검증후_원장에_적용된다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 20, tzinfo=UTC)
    fixture = _insert_live_exchange_order_with_binding(repository, key, now)

    result = apply_upbit_live_rest_order_snapshot(
        store,
        live_binding=fixture,
        exchange_order_id=fixture["exchangeOrderId"],
        run_key=f"p6-live-rest-{key}",
        actor_id="operator:test",
        reason="P6-9 live REST reconciliation",
        snapshot_payload={
            "market": "KRW-BTC",
            "uuid": fixture["upbitOrderUuid"],
            "identifier": fixture["identifier"],
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
    assert isinstance(result["liveReconciliationApplicationId"], int)
    live_reconciliation_application_id = result["liveReconciliationApplicationId"]
    assert live_reconciliation_application_id > 0
    assert result["canResubmit"] is False
    assert result["actualRequestSent"] is False
    assert result["actualOrderCancelSent"] is False

    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status AS intent_status,
              exchange.status AS exchange_status,
              fill.fill_source,
              fill.filled_quantity,
              fill.fill_price,
              fill.evidence->>'orderUuid' AS fill_order_uuid,
              position.quantity,
              run.status AS run_status,
              run.evidence->>'identifier' AS run_identifier,
              application.source,
              application.source_endpoint,
              application.observed_state,
              application.observed_upbit_order_uuid,
              application.observed_upbit_identifier,
              application.can_resubmit,
              application.actual_request_sent,
              application.actual_order_cancel_sent
            FROM upbit_live_reconciliation_applications application
            JOIN reconciliation_runs run ON run.id = application.reconciliation_run_id
            JOIN exchange_orders exchange ON exchange.id = application.exchange_order_id
            JOIN order_intents intent ON intent.id = exchange.order_intent_id
            JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            WHERE application.id=%s
            """,
            (fixture["portfolioId"], live_reconciliation_application_id),
        ).fetchone()
        assert row is not None

    assert row["intent_status"] == "reconciled"
    assert row["exchange_status"] == "reconciled"
    assert row["fill_source"] == "reconciliation"
    assert row["filled_quantity"] == Decimal("2.000000000000000000")
    assert row["fill_price"] == Decimal("120.000000000000000000")
    assert row["fill_order_uuid"] == fixture["upbitOrderUuid"]
    assert row["quantity"] == Decimal("2.000000000000000000")
    assert row["run_status"] == "succeeded"
    assert row["run_identifier"] == fixture["identifier"]
    assert row["source"] == "rest_order_snapshot"
    assert row["source_endpoint"] == "GET /v1/order"
    assert row["observed_state"] == "done"
    assert row["observed_upbit_order_uuid"] == fixture["upbitOrderUuid"]
    assert row["observed_upbit_identifier"] == fixture["identifier"]
    assert row["can_resubmit"] is False
    assert row["actual_request_sent"] is False
    assert row["actual_order_cancel_sent"] is False


def test_live_postgres_P6_9_application은_binding과_snapshot_UUID_불일치를_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 20, tzinfo=UTC)
    fixture = _insert_live_exchange_order_with_binding(repository, key, now)

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        run = connection.execute(
            """
            INSERT INTO reconciliation_runs (
              exchange_order_id, run_key, status, observed_status,
              observed_fill_count, request_hash, actor_id, reason, evidence
            ) VALUES (%s,%s,'succeeded','done',0,%s,'operator:test',
              'P6-9 DB mismatch seed',%s)
            RETURNING id
            """,
            (
                fixture["exchangeOrderId"],
                f"p6-live-mismatch-{key}",
                "f" * 64,
                Jsonb(
                    {
                        "source": "upbit-rest-order-snapshot",
                        "sourceEndpoint": "GET /v1/order",
                        "orderUuid": fixture["upbitOrderUuid"],
                        "identifier": fixture["identifier"],
                        "state": "done",
                        "canResubmit": False,
                    }
                ),
            ),
        ).fetchone()
        assert run is not None
        connection.execute(
            """
            INSERT INTO upbit_live_reconciliation_applications (
              exchange_account_id, order_intent_id, exchange_order_id,
              live_exchange_order_binding_id, reconciliation_run_id,
              source, source_endpoint, observed_upbit_order_uuid,
              observed_upbit_identifier, observed_state, applied_at,
              request_hash, evidence, actor_id, reason, request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,'rest_order_snapshot','GET /v1/order',%s,%s,
              'done',%s,%s,%s,'operator:test','P6-9 mismatch',%s,%s)
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                fixture["exchangeOrderId"],
                fixture["liveBindingId"],
                int(run["id"]),
                str(uuid4()),
                fixture["identifier"],
                now,
                "e" * 64,
                Jsonb({"source": "p6-9-mismatch"}),
                f"p6-9-mismatch-{key}",
                f"p6-9-mismatch-{key}",
            ),
        )


def test_live_postgres_P6_9_live_reconciliation_run은_application없이_커밋될수없다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 20, tzinfo=UTC)
    fixture = _insert_live_exchange_order_with_binding(repository, key, now)

    with pytest.raises(psycopg.errors.RaiseException):
        store.reconcile_exchange_order(
            exchange_order_id=fixture["exchangeOrderId"],
            run_key=f"p6-live-bypass-{key}",
            actor_id="operator:test",
            reason="P6-9 direct live reconciliation bypass",
            observed_status="done",
            fills=[],
            evidence={
                "source": "upbit-rest-order-snapshot",
                "sourceEndpoint": "GET /v1/order",
                "orderUuid": fixture["upbitOrderUuid"],
                "identifier": fixture["identifier"],
                "state": "done",
                "canResubmit": False,
            },
        )


def _insert_live_exchange_order_with_binding(
    repository: PostgresOperationsRepository,
    key: str,
    now: datetime,
) -> dict[str, int | str]:
    fixture = _insert_live_binding_fixture(repository, key, now)
    upbit_order_uuid = str(uuid4())
    with repository._connect() as connection:
        bot = connection.execute(
            """
            SELECT policy.portfolio_id
            FROM order_intents intent
            JOIN bot_instances bot ON bot.id = intent.bot_instance_id
            JOIN portfolio_policies policy ON policy.id = bot.portfolio_policy_id
            WHERE intent.id=%s
            """,
            (fixture["orderIntentId"],),
        ).fetchone()
        assert bot is not None
        exchange_order = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'live',%s,'wait',%s,%s)
            RETURNING id
            """,
            (
                fixture["orderIntentId"],
                fixture["identifier"],
                now,
                Jsonb({"source": "p6-9-live"}),
            ),
        ).fetchone()
        assert exchange_order is not None
        binding = connection.execute(
            """
            INSERT INTO upbit_live_exchange_order_bindings (
              exchange_account_id, order_intent_id, exchange_order_id,
              live_order_identifier_id, upbit_order_outbox_id, upbit_order_uuid,
              upbit_identifier, source, observed_at, evidence, actor_id, reason,
              request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'order_submit_response',%s,%s,
              'operator:test','P6-9 binding',%s,%s)
            RETURNING id
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                int(exchange_order["id"]),
                fixture["liveOrderIdentifierId"],
                fixture["outboxId"],
                upbit_order_uuid,
                fixture["identifier"],
                now,
                Jsonb({"source": "p6-9"}),
                f"p6-9-binding-{key}",
                f"p6-9-binding-{key}",
            ),
        ).fetchone()
        assert binding is not None
    return {
        **fixture,
        "portfolioId": int(bot["portfolio_id"]),
        "exchangeOrderId": int(exchange_order["id"]),
        "liveBindingId": int(binding["id"]),
        "upbitOrderUuid": upbit_order_uuid,
    }


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
