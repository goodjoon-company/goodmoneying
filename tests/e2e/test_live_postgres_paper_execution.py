from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_paper_execution_job은_모의주문_체결_position을_기록한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 11, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)

    with repository._connect() as connection:
        portfolio = connection.execute(
            """
            INSERT INTO portfolios (owner_id, name, created_by, reason)
            VALUES ('operator:test', %s, 'operator:test', 'P5-3 portfolio')
            RETURNING id
            """,
            (f"paper-portfolio-{key}",),
        ).fetchone()
        assert portfolio is not None
        policy = connection.execute(
            """
            INSERT INTO portfolio_policies (
              portfolio_id, version, status, max_gross_exposure, max_single_position_pct,
              created_by, reason
            ) VALUES (%s, 1, 'published', 1000000, 0.25, 'operator:test', 'P5-3 policy')
            RETURNING id
            """,
            (portfolio["id"],),
        ).fetchone()
        assert policy is not None
        bot_definition = connection.execute(
            """
            INSERT INTO bot_definitions (
              owner_id, name, strategy_version_id, portfolio_id, created_by, reason
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', 'P5-3 bot')
            RETURNING id
            """,
            (f"paper-bot-{key}", strategy_version_id, portfolio["id"]),
        ).fetchone()
        assert bot_definition is not None
        bot_instance = connection.execute(
            """
            INSERT INTO bot_instances (
              bot_definition_id, strategy_version_id, portfolio_policy_id,
              stage, execution_mode, started_at, created_by, reason
            ) VALUES (%s, %s, %s, 'paper', 'paper', %s, 'operator:test', 'P5-3 instance')
            RETURNING id
            """,
            (bot_definition["id"], strategy_version_id, policy["id"], now),
        ).fetchone()
        assert bot_instance is not None
        order_intent = connection.execute(
            """
            INSERT INTO order_intents (
              bot_instance_id, instrument_id, idempotency_key, side, order_type,
              requested_quantity, limit_price, status, decision_input_hash,
              risk_policy_version, risk_decision_reason, created_by, reason
            ) VALUES (
              %s, %s, %s, 'buy', 'limit', 2.5, 101.25, 'approved', %s,
              1, 'paper approved', 'operator:test', 'P5-3 intent'
            ) RETURNING id
            """,
            (bot_instance["id"], instrument_id, f"paper-intent-{key}", "3" * 64),
        ).fetchone()
        assert order_intent is not None
        job = connection.execute(
            "INSERT INTO paper_execution_jobs (order_intent_id) VALUES (%s) RETURNING id",
            (order_intent["id"],),
        ).fetchone()
        assert job is not None

    claim = store.claim_next_paper_execution_job("paper-worker-live")
    assert claim is not None
    assert claim["orderIntentId"] == order_intent["id"]
    assert claim["portfolioId"] == portfolio["id"]
    assert claim["leaseGeneration"] == 1

    completed = store.complete_claimed_paper_execution_job(
        job_id=claim["paperExecutionJobId"],
        worker_id="paper-worker-live",
        lease_generation=claim["leaseGeneration"],
        fill_price=Decimal("101.25"),
        filled_quantity=None,
        occurred_at=now,
        knowledge_at=now,
        evidence={"source": "live-e2e"},
    )

    assert completed["status"] == "succeeded"
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status AS intent_status,
              exchange.execution_mode,
              exchange.status AS exchange_status,
              fill.fill_source,
              fill.filled_quantity,
              fill.fill_price,
              position.quantity,
              position.average_entry_price
            FROM order_intents intent
            JOIN exchange_orders exchange ON exchange.order_intent_id = intent.id
            JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            WHERE intent.id=%s
            """,
            (portfolio["id"], order_intent["id"]),
        ).fetchone()
        assert row is not None

    assert row["intent_status"] == "paper_filled"
    assert row["execution_mode"] == "paper"
    assert row["exchange_status"] == "done"
    assert row["fill_source"] == "paper_simulator"
    assert row["filled_quantity"] == Decimal("2.500000000000000000")
    assert row["fill_price"] == Decimal("101.250000000000000000")
    assert row["quantity"] == Decimal("2.500000000000000000")
    assert row["average_entry_price"] == Decimal("101.250000000000000000")


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _insert_instrument(repository: PostgresOperationsRepository, key: str) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO instruments (
              exchange, market_code, quote_currency, base_asset, display_name
            )
            VALUES ('UPBIT', %s, 'KRW', %s, %s)
            RETURNING id
            """,
            (f"KRW-PAPER-{key[:8].upper()}", f"PAPER{key[:8].upper()}", f"Paper {key[:8]}"),
        ).fetchone()
        assert row is not None
        return int(row["id"])


def _insert_strategy_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> int:
    graph_hash = "a" * 64
    with repository._connect() as connection:
        strategy = connection.execute(
            """
            INSERT INTO strategy_definitions (
              owner_id, name, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', %s, 'P5-3 strategy', %s)
            RETURNING id
            """,
            (
                f"paper-strategy-{key}",
                f"paper-strategy-key-{key}",
                f"paper-strategy-request-{key}",
                now,
                "b" * 64,
            ),
        ).fetchone()
        assert strategy is not None
        version = connection.execute(
            """
            INSERT INTO strategy_versions (
              strategy_id, version, schema_version, status, graph_hash,
              validation_result, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash, published_at
            ) VALUES (
              %s, 1, 'strategy-graph-v1', 'published', %s,
              %s::jsonb, %s, %s, 'operator:test', %s, 'P5-3 strategy version', %s, %s
            ) RETURNING id
            """,
            (
                strategy["id"],
                graph_hash,
                '{"valid":true,"errors":[],"graphHash":"' + graph_hash + '"}',
                f"paper-strategy-version-key-{key}",
                f"paper-strategy-version-request-{key}",
                now,
                "c" * 64,
                now,
            ),
        ).fetchone()
        assert version is not None
        connection.execute(
            """
            INSERT INTO strategy_graphs (strategy_version_id, graph_json, graph_hash)
            VALUES (%s, %s::jsonb, %s)
            """,
            (version["id"], '{"schema_version":"strategy-graph-v1"}', graph_hash),
        )
        return int(version["id"])
