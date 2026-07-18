from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_reconciliation은_불명_주문을_position으로_대사한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
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
    fill = {
        "fillSequence": 1,
        "side": "buy",
        "filledQuantity": Decimal("2"),
        "fillPrice": Decimal("120"),
        "feePaid": Decimal("0"),
        "occurredAt": now,
        "knowledgeAt": now,
        "evidence": {"source": "p5-5-live"},
    }

    run = store.reconcile_exchange_order(
        exchange_order_id=exchange_order_id,
        run_key=f"reconcile-{key}",
        actor_id="operator:test",
        reason="P5-5 reconciliation",
        observed_status="done",
        fills=[fill],
        evidence={"source": "live-e2e"},
    )
    repeated = store.reconcile_exchange_order(
        exchange_order_id=exchange_order_id,
        run_key=f"reconcile-{key}",
        actor_id="operator:test",
        reason="P5-5 reconciliation",
        observed_status="done",
        fills=[fill],
        evidence={"source": "live-e2e"},
    )

    assert run["status"] == "succeeded"
    assert repeated["reconciliationRunId"] == run["reconciliationRunId"]
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status AS intent_status,
              exchange.status AS exchange_status,
              exchange.reconciled_at IS NOT NULL AS exchange_reconciled,
              COUNT(fill.id) AS fill_count,
              MAX(fill.fill_source) AS fill_source,
              MAX(position.quantity) AS quantity,
              MAX(position.average_entry_price) AS average_entry_price,
              COUNT(run.id) AS run_count
            FROM exchange_orders exchange
            JOIN order_intents intent ON intent.id = exchange.order_intent_id
            LEFT JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            LEFT JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            LEFT JOIN reconciliation_runs run ON run.exchange_order_id = exchange.id
            WHERE exchange.id=%s
            GROUP BY intent.status, exchange.status, exchange.reconciled_at
            """,
            (fixture["portfolioId"], exchange_order_id),
        ).fetchone()
        assert row is not None

    assert row["intent_status"] == "reconciled"
    assert row["exchange_status"] == "reconciled"
    assert row["exchange_reconciled"] is True
    assert row["fill_count"] == 1
    assert row["fill_source"] == "reconciliation"
    assert row["quantity"] == Decimal("2.000000000000000000")
    assert row["average_entry_price"] == Decimal("120.000000000000000000")
    assert row["run_count"] == 1


def test_live_postgres_reconciliation은_fill_mismatch를_기록하고_position을_바꾸지_않는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        status="paper_filled",
    )
    exchange_order_id = _insert_exchange_order(
        repository,
        order_intent_id,
        key,
        status="done",
        now=now,
    )
    with repository._connect() as connection:
        fill = connection.execute(
            """
            INSERT INTO order_fills (
              exchange_order_id, fill_sequence, fill_source, side, filled_quantity,
              fill_price, fee_paid, occurred_at, knowledge_at, evidence
            ) VALUES (%s,1,'paper_simulator','buy',1,100,0,%s,%s,%s)
            RETURNING id
            """,
            (exchange_order_id, now, now, Jsonb({"source": "paper"})),
        ).fetchone()
        assert fill is not None
        connection.execute(
            """
            INSERT INTO position_projections (
              portfolio_id, instrument_id, quantity, average_entry_price,
              realized_pnl, source_fill_id
            ) VALUES (%s,%s,1,100,0,%s)
            """,
            (fixture["portfolioId"], instrument_id, fill["id"]),
        )

    run = store.reconcile_exchange_order(
        exchange_order_id=exchange_order_id,
        run_key=f"reconcile-mismatch-{key}",
        actor_id="operator:test",
        reason="P5-5 mismatch",
        observed_status="done",
        fills=[
            {
                "fillSequence": 1,
                "side": "buy",
                "filledQuantity": Decimal("2"),
                "fillPrice": Decimal("100"),
                "feePaid": Decimal("0"),
                "occurredAt": now,
                "knowledgeAt": now,
                "evidence": {"source": "observed-mismatch"},
            }
        ],
        evidence={"source": "live-e2e"},
    )

    assert run["status"] == "mismatch"
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status AS intent_status,
              exchange.status AS exchange_status,
              COUNT(fill.id) AS fill_count,
              position.quantity,
              position.average_entry_price,
              risk.event_type,
              risk.severity,
              run.status AS run_status
            FROM exchange_orders exchange
            JOIN order_intents intent ON intent.id = exchange.order_intent_id
            JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            LEFT JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            JOIN reconciliation_runs run ON run.exchange_order_id = exchange.id
            JOIN risk_events risk ON risk.order_intent_id = intent.id
            WHERE exchange.id=%s
            GROUP BY
              intent.status, exchange.status, position.quantity,
              position.average_entry_price, risk.event_type, risk.severity, run.status
            """,
            (fixture["portfolioId"], exchange_order_id),
        ).fetchone()
        assert row is not None

    assert row["intent_status"] == "paper_filled"
    assert row["exchange_status"] == "done"
    assert row["fill_count"] == 1
    assert row["quantity"] == Decimal("1.000000000000000000")
    assert row["average_entry_price"] == Decimal("100.000000000000000000")
    assert row["event_type"] == "reconciliation_mismatch"
    assert row["severity"] == "critical"
    assert row["run_status"] == "mismatch"


def test_live_postgres_reconciliation은_mismatch_전에_부분_fill을_반영하지_않는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        status="paper_filled",
    )
    exchange_order_id = _insert_exchange_order(
        repository,
        order_intent_id,
        key,
        status="done",
        now=now,
    )
    with repository._connect() as connection:
        fill = connection.execute(
            """
            INSERT INTO order_fills (
              exchange_order_id, fill_sequence, fill_source, side, filled_quantity,
              fill_price, fee_paid, occurred_at, knowledge_at, evidence
            ) VALUES (%s,2,'paper_simulator','buy',1,100,0,%s,%s,%s)
            RETURNING id
            """,
            (exchange_order_id, now, now, Jsonb({"source": "paper"})),
        ).fetchone()
        assert fill is not None
        connection.execute(
            """
            INSERT INTO position_projections (
              portfolio_id, instrument_id, quantity, average_entry_price,
              realized_pnl, source_fill_id
            ) VALUES (%s,%s,1,100,0,%s)
            """,
            (fixture["portfolioId"], instrument_id, fill["id"]),
        )

    run = store.reconcile_exchange_order(
        exchange_order_id=exchange_order_id,
        run_key=f"reconcile-partial-mismatch-{key}",
        actor_id="operator:test",
        reason="P5-5 partial mismatch",
        observed_status="done",
        fills=[
            {
                "fillSequence": 1,
                "side": "buy",
                "filledQuantity": Decimal("1"),
                "fillPrice": Decimal("90"),
                "feePaid": Decimal("0"),
                "occurredAt": now,
                "knowledgeAt": now,
                "evidence": {"source": "new-before-mismatch"},
            },
            {
                "fillSequence": 2,
                "side": "buy",
                "filledQuantity": Decimal("2"),
                "fillPrice": Decimal("100"),
                "feePaid": Decimal("0"),
                "occurredAt": now,
                "knowledgeAt": now,
                "evidence": {"source": "observed-mismatch"},
            },
        ],
        evidence={"source": "live-e2e"},
    )

    assert run["status"] == "mismatch"
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              COUNT(fill.id) AS fill_count,
              position.quantity,
              position.average_entry_price,
              risk.event_type,
              run.status AS run_status
            FROM exchange_orders exchange
            JOIN order_intents intent ON intent.id = exchange.order_intent_id
            JOIN position_projections position
              ON position.portfolio_id = %s AND position.instrument_id = intent.instrument_id
            LEFT JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            JOIN reconciliation_runs run ON run.exchange_order_id = exchange.id
            JOIN risk_events risk ON risk.order_intent_id = intent.id
            WHERE exchange.id=%s
            GROUP BY position.quantity, position.average_entry_price, risk.event_type, run.status
            """,
            (fixture["portfolioId"], exchange_order_id),
        ).fetchone()
        assert row is not None

    assert row["fill_count"] == 1
    assert row["quantity"] == Decimal("1.000000000000000000")
    assert row["average_entry_price"] == Decimal("100.000000000000000000")
    assert row["event_type"] == "reconciliation_mismatch"
    assert row["run_status"] == "mismatch"


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
            (f"KRW-REC-{key[:8].upper()}", f"REC{key[:8].upper()}", f"Rec {key[:8]}"),
        ).fetchone()
        assert row is not None
        return int(row["id"])


def _insert_strategy_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> int:
    graph_hash = "9" * 64
    with repository._connect() as connection:
        strategy = connection.execute(
            """
            INSERT INTO strategy_definitions (
              owner_id, name, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', %s, 'P5-5 strategy', %s)
            RETURNING id
            """,
            (
                f"reconciliation-strategy-{key}",
                f"reconciliation-strategy-key-{key}",
                f"reconciliation-strategy-request-{key}",
                now,
                "8" * 64,
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
              %s::jsonb, %s, %s, 'operator:test', %s, 'P5-5 strategy version', %s, %s
            ) RETURNING id
            """,
            (
                strategy["id"],
                graph_hash,
                '{"valid":true,"errors":[],"graphHash":"' + graph_hash + '"}',
                f"reconciliation-strategy-version-key-{key}",
                f"reconciliation-strategy-version-request-{key}",
                now,
                "7" * 64,
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


def _insert_paper_bot_fixture(
    repository: PostgresOperationsRepository, key: str, now: datetime, strategy_version_id: int
) -> dict[str, int]:
    with repository._connect() as connection:
        portfolio = connection.execute(
            """
            INSERT INTO portfolios (owner_id, name, created_by, reason)
            VALUES ('operator:test', %s, 'operator:test', 'P5-5 portfolio')
            RETURNING id
            """,
            (f"reconciliation-portfolio-{key}",),
        ).fetchone()
        assert portfolio is not None
        policy = connection.execute(
            """
            INSERT INTO portfolio_policies (
              portfolio_id, version, status, max_gross_exposure, max_single_position_pct,
              created_by, reason
            ) VALUES (%s, 1, 'published', 1000000, 0.25, 'operator:test', 'P5-5 policy')
            RETURNING id
            """,
            (portfolio["id"],),
        ).fetchone()
        assert policy is not None
        bot_definition = connection.execute(
            """
            INSERT INTO bot_definitions (
              owner_id, name, strategy_version_id, portfolio_id, created_by, reason
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', 'P5-5 bot')
            RETURNING id
            """,
            (f"reconciliation-bot-{key}", strategy_version_id, portfolio["id"]),
        ).fetchone()
        assert bot_definition is not None
        bot_instance = connection.execute(
            """
            INSERT INTO bot_instances (
              bot_definition_id, strategy_version_id, portfolio_policy_id,
              stage, execution_mode, started_at, created_by, reason
            ) VALUES (%s, %s, %s, 'paper', 'paper', %s, 'operator:test', 'P5-5 instance')
            RETURNING id
            """,
            (bot_definition["id"], strategy_version_id, policy["id"], now),
        ).fetchone()
        assert bot_instance is not None
        return {
            "portfolioId": int(portfolio["id"]),
            "botInstanceId": int(bot_instance["id"]),
        }


def _insert_order_intent(
    repository: PostgresOperationsRepository,
    key: str,
    bot_instance_id: int,
    instrument_id: int,
    *,
    status: str,
) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO order_intents (
              bot_instance_id, instrument_id, idempotency_key, side, order_type,
              requested_quantity, limit_price, status, decision_input_hash,
              risk_policy_version, risk_decision_reason, created_by, reason
            ) VALUES (
              %s, %s, %s, 'buy', 'limit', 2, 120, %s, %s,
              1, 'P5-5 fixture', 'operator:test', 'P5-5 intent'
            ) RETURNING id
            """,
            (bot_instance_id, instrument_id, f"reconciliation-intent-{key}", status, "6" * 64),
        ).fetchone()
        assert row is not None
        return int(row["id"])


def _insert_exchange_order(
    repository: PostgresOperationsRepository,
    order_intent_id: int,
    key: str,
    *,
    status: str,
    now: datetime,
) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'paper',%s,%s,%s,%s)
            RETURNING id
            """,
            (
                order_intent_id,
                f"reconciliation-order-{key}",
                status,
                now,
                Jsonb({"source": "P5-5 fixture"}),
            ),
        ).fetchone()
        assert row is not None
        return int(row["id"])
