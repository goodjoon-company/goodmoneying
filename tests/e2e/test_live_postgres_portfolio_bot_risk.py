from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_P5_포트폴리오_봇_주문_위험_계약을_보호한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 14, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id, strategy_hash = _insert_strategy_version(repository, key, now)
    dataset_version_id, dataset_hash = _insert_dataset_version(repository, key, now)
    backtest_run_id = _insert_backtest_run(
        repository,
        key,
        now,
        strategy_version_id,
        strategy_hash,
        dataset_version_id,
        dataset_hash,
    )

    with repository._connect() as connection:
        portfolio = connection.execute(
            """
            INSERT INTO portfolios (owner_id, name, created_by, reason)
            VALUES ('operator:test', %s, 'operator:test', 'P5-1 portfolio')
            RETURNING id
            """,
            (f"portfolio-{key}",),
        ).fetchone()
        assert portfolio is not None
        policy = connection.execute(
            """
            INSERT INTO portfolio_policies (
              portfolio_id, version, status, max_gross_exposure, max_single_position_pct,
              cash_reserve_pct, created_by, reason
            ) VALUES (%s, 1, 'published', 1000000, 0.25, 0.1, 'operator:test', 'P5-1 policy')
            RETURNING id
            """,
            (portfolio["id"],),
        ).fetchone()
        assert policy is not None
        connection.execute(
            """
            INSERT INTO capital_allocations (
              portfolio_policy_id, scope_type, scope_key, allocation_pct, max_notional
            ) VALUES (%s, 'global', 'global', 1.0, 1000000)
            """,
            (policy["id"],),
        )
        bot_definition = connection.execute(
            """
            INSERT INTO bot_definitions (
              owner_id, name, strategy_version_id, portfolio_id, created_by, reason
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', 'P5-1 bot')
            RETURNING id
            """,
            (f"bot-{key}", strategy_version_id, portfolio["id"]),
        ).fetchone()
        assert bot_definition is not None
        bot_instance = connection.execute(
            """
            INSERT INTO bot_instances (
              bot_definition_id, strategy_version_id, portfolio_policy_id, backtest_run_id,
              stage, execution_mode, started_at, created_by, reason
            ) VALUES (%s, %s, %s, %s, 'paper', 'paper', %s, 'operator:test', 'P5-1 instance')
            RETURNING id
            """,
            (bot_definition["id"], strategy_version_id, policy["id"], backtest_run_id, now),
        ).fetchone()
        assert bot_instance is not None
        connection.execute(
            """
            INSERT INTO bot_state_transitions (
              bot_instance_id, from_stage, to_stage, request_id, actor_id, reason, occurred_at
            ) VALUES (%s, 'backtest', 'paper', %s, 'operator:test', 'P5-1 transition', %s)
            """,
            (bot_instance["id"], f"transition-{key}", now),
        )
        order_intent = connection.execute(
            """
            INSERT INTO order_intents (
              bot_instance_id, instrument_id, idempotency_key, side, order_type,
              requested_quantity, status, decision_input_hash, risk_policy_version,
              risk_decision_reason, created_by, reason
            ) VALUES (
              %s, %s, %s, 'buy', 'market', 1.5, 'approved', %s, 1,
              'risk approved for paper', 'operator:test', 'P5-1 order intent'
            ) RETURNING id
            """,
            (bot_instance["id"], instrument_id, f"order-{key}", "1" * 64),
        ).fetchone()
        assert order_intent is not None
        exchange_order = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key, status, submitted_at
            ) VALUES (%s, 'paper', %s, 'partially_filled', %s)
            RETURNING id
            """,
            (order_intent["id"], f"paper-order-{key}", now),
        ).fetchone()
        assert exchange_order is not None
        fill = connection.execute(
            """
            INSERT INTO order_fills (
              exchange_order_id, fill_sequence, fill_source, side, filled_quantity,
              fill_price, fee_paid, occurred_at, knowledge_at
            ) VALUES (%s, 1, 'paper_simulator', 'buy', 1.0, 100, 0.1, %s, %s)
            RETURNING id
            """,
            (exchange_order["id"], now, now),
        ).fetchone()
        assert fill is not None
        connection.execute(
            """
            INSERT INTO position_projections (
              portfolio_id, instrument_id, quantity, average_entry_price, realized_pnl,
              source_fill_id
            ) VALUES (%s, %s, 1.0, 100, 0, %s)
            """,
            (portfolio["id"], instrument_id, fill["id"]),
        )
        connection.execute(
            """
            INSERT INTO risk_limits (
              scope_type, scope_key, limit_type, version, limit_value, actor_id, reason
            ) VALUES ('bot', %s, 'max_order_notional', 1, 100000, 'operator:test', 'P5-1 risk')
            """,
            (str(bot_instance["id"]),),
        )
        risk_event = connection.execute(
            """
            INSERT INTO risk_events (
              order_intent_id, bot_instance_id, scope_type, scope_key, event_type,
              severity, fingerprint, risk_policy_version, message
            ) VALUES (
              %s, %s, 'bot', %s, 'policy_approved', 'info', %s, 1,
              'paper order approved'
            ) RETURNING id
            """,
            (order_intent["id"], bot_instance["id"], str(bot_instance["id"]), f"risk-{key}"),
        ).fetchone()
        assert risk_event is not None
        kill_switch = connection.execute(
            """
            INSERT INTO kill_switches (
              scope_type, scope_key, state, sequence, actor_id, reason, open_order_policy
            ) VALUES ('bot', %s, 'armed', 1, 'operator:test', 'P5-1 kill switch', 'leave_open')
            RETURNING id
            """,
            (str(bot_instance["id"]),),
        ).fetchone()
        assert kill_switch is not None

    with (
        pytest.raises(psycopg.errors.RaiseException, match="risk_events is append-only"),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE risk_events SET message='mutated' WHERE fingerprint=%s",
            (f"risk-{key}",),
        )

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO bot_instances (
              bot_definition_id, strategy_version_id, portfolio_policy_id, stage,
              execution_mode, created_by, reason
            ) VALUES (%s, %s, %s, 'paper', 'live', 'operator:test', 'live must fail')
            """,
            (bot_definition["id"], strategy_version_id, policy["id"]),
        )


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _insert_instrument(repository: PostgresOperationsRepository, key: str) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO instruments (
              exchange, market_code, quote_currency, base_asset, display_name, status
            ) VALUES ('UPBIT', %s, 'KRW', %s, %s, 'active')
            RETURNING id
            """,
            (f"KRW-P5{key[:8].upper()}", f"P5{key[:8].upper()}", f"P5 {key[:8]}"),
        ).fetchone()
    assert row is not None
    return int(cast(int, row["id"]))


def _insert_strategy_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> tuple[int, str]:
    graph_hash = "a" * 64
    with repository._connect() as connection:
        strategy = connection.execute(
            """
            INSERT INTO strategy_definitions (
              owner_id, name, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash
            ) VALUES (%s,%s,%s,%s,'operator:test',%s,'P5-1 strategy',%s)
            RETURNING id
            """,
            (
                "operator:test",
                f"p5-1-{key}",
                f"strategy-{key}",
                f"strategy-request-{key}",
                now,
                "4" * 64,
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
              '{"valid":true,"errors":[]}'::jsonb, %s, %s,
              'operator:test', %s, 'P5-1 version', %s, %s
            ) RETURNING id
            """,
            (
                strategy["id"],
                graph_hash,
                f"strategy-version-{key}",
                f"strategy-version-request-{key}",
                now,
                "5" * 64,
                now,
            ),
        ).fetchone()
        assert version is not None
        connection.execute(
            """
            INSERT INTO strategy_graphs (strategy_version_id, graph_json, graph_hash)
            VALUES (%s, %s::jsonb, %s)
            """,
            (
                version["id"],
                '{"schema_version":"strategy-graph-v1","nodes":[],"edges":[],"outputs":[]}',
                graph_hash,
            ),
        )
    return int(cast(int, version["id"])), graph_hash


def _insert_dataset_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> tuple[int, str]:
    content_hash = hashlib.sha256(f"p5-dataset-{key}".encode()).hexdigest()
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO dataset_versions (
              schema_version, as_of, input_start_at, output_start_at, end_at,
              fill_policy, missing_policy, ordering_policy, selection_hash,
              manifest_hash, market_status_hash, coverage_hash, content_hash,
              sealed_at
            ) VALUES (
              'dataset-version-v1', %s, %s, %s, %s, 'none', 'fail',
              'knowledge_at_v1', %s, %s, %s, %s, %s, %s
            ) RETURNING id
            """,
            (
                now,
                now - timedelta(minutes=3),
                now - timedelta(minutes=2),
                now - timedelta(minutes=1),
                hashlib.sha256(f"selection-{key}".encode()).hexdigest(),
                "1" * 64,
                "2" * 64,
                "3" * 64,
                content_hash,
                now,
            ),
        ).fetchone()
    assert row is not None
    return int(cast(int, row["id"])), content_hash


def _insert_backtest_run(
    repository: PostgresOperationsRepository,
    key: str,
    now: datetime,
    strategy_version_id: int,
    strategy_hash: str,
    dataset_version_id: int,
    dataset_hash: str,
) -> int:
    input_hash = hashlib.sha256(f"backtest-input-{key}".encode()).hexdigest()
    result_hash = hashlib.sha256(f"backtest-result-{key}".encode()).hexdigest()
    parameter_hash = hashlib.sha256(f"backtest-parameter-{key}".encode()).hexdigest()
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO backtest_runs (
              strategy_version_id, strategy_graph_hash,
              dataset_version_id, dataset_content_hash,
              engine_version, status, input_hash, result_hash,
              parameter_hash, seed, assumptions, idempotency_key,
              request_id, actor_id, requested_at, reason, request_hash, finished_at
            ) VALUES (
              %s,%s,%s,%s,'backtest-core-v1','succeeded',%s,%s,
              %s,42,'[]'::jsonb,%s,%s,'operator:test',%s,%s,%s,%s
            ) RETURNING id
            """,
            (
                strategy_version_id,
                strategy_hash,
                dataset_version_id,
                dataset_hash,
                input_hash,
                result_hash,
                parameter_hash,
                f"backtest-key-{key}",
                f"backtest-request-{key}",
                now,
                "P5-1 backtest reference",
                "0" * 64,
                now,
            ),
        ).fetchone()
    assert row is not None
    return int(cast(int, row["id"]))
