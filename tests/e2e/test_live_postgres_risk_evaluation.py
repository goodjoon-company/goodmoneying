from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from goodmoneying_shared.portfolio_bot_store import PostgresPortfolioBotStore
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_risk_evaluation은_한도_내_주문을_승인하고_paper_job을_만든다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 16, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        requested_quantity=Decimal("2"),
        limit_price=Decimal("100"),
    )
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO risk_limits (
              scope_type, scope_key, limit_type, version, limit_value, actor_id, reason
            ) VALUES ('bot', %s, 'max_order_notional', 3, 1000, 'operator:test', 'P5-4 risk')
            """,
            (str(fixture["botInstanceId"]),),
        )

    evaluated = store.evaluate_next_order_intent_risk("risk-worker-live")

    assert evaluated is not None
    assert evaluated["orderIntentId"] == order_intent_id
    assert evaluated["status"] == "approved"
    assert evaluated["riskPolicyVersion"] == 3
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT
              intent.status,
              intent.risk_policy_version,
              intent.risk_decision_reason,
              event.event_type,
              event.severity,
              job.status AS paper_job_status
            FROM order_intents intent
            JOIN risk_events event ON event.order_intent_id = intent.id
            LEFT JOIN paper_execution_jobs job ON job.order_intent_id = intent.id
            WHERE intent.id=%s
            """,
            (order_intent_id,),
        ).fetchone()
        assert row is not None

    assert row["status"] == "approved"
    assert row["risk_policy_version"] == 3
    assert row["risk_decision_reason"] == "risk approved for paper"
    assert row["event_type"] == "policy_approved"
    assert row["severity"] == "info"
    assert row["paper_job_status"] == "pending"
    claim = store.claim_next_paper_execution_job("paper-worker-before-switch")
    assert claim is not None
    assert claim["orderIntentId"] == order_intent_id
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO kill_switches (
              scope_type, scope_key, state, sequence, actor_id, reason, open_order_policy
            ) VALUES (
              'bot', %s, 'armed', 1, 'operator:test', 'P5-4 paper claim block',
              'leave_open'
            )
            """,
            (str(fixture["botInstanceId"]),),
        )

    assert store.claim_next_paper_execution_job("paper-worker-blocked") is None
    blocked = store.complete_claimed_paper_execution_job(
        job_id=claim["paperExecutionJobId"],
        worker_id="paper-worker-before-switch",
        lease_generation=claim["leaseGeneration"],
        fill_price=Decimal("100"),
        filled_quantity=None,
        occurred_at=now,
        knowledge_at=now,
        evidence={"source": "blocked-after-claim"},
    )
    assert blocked["status"] == "retry_wait"
    with repository._connect() as connection:
        blocked_row = connection.execute(
            """
            SELECT
              job.status,
              job.last_error_code,
              COUNT(exchange.id) AS exchange_count,
              COUNT(fill.id) AS fill_count
            FROM paper_execution_jobs job
            LEFT JOIN exchange_orders exchange ON exchange.order_intent_id = job.order_intent_id
            LEFT JOIN order_fills fill ON fill.exchange_order_id = exchange.id
            WHERE job.order_intent_id=%s
            GROUP BY job.status, job.last_error_code
            """,
            (order_intent_id,),
        ).fetchone()
        assert blocked_row is not None
    assert blocked_row["status"] == "retry_wait"
    assert blocked_row["last_error_code"] == "KILL_SWITCH_ARMED"
    assert blocked_row["exchange_count"] == 0
    assert blocked_row["fill_count"] == 0


def test_live_postgres_risk_evaluation은_한도_초과와_kill_switch를_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 16, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    limit_rejected_id = _insert_order_intent(
        repository,
        f"{key}-limit",
        fixture["botInstanceId"],
        instrument_id,
        requested_quantity=Decimal("20"),
        limit_price=Decimal("100"),
    )
    kill_rejected_id = _insert_order_intent(
        repository,
        f"{key}-kill",
        fixture["botInstanceId"],
        instrument_id,
        requested_quantity=Decimal("1"),
        limit_price=Decimal("100"),
    )
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO risk_limits (
              scope_type, scope_key, limit_type, version, limit_value, actor_id, reason
            ) VALUES ('bot', %s, 'max_order_notional', 2, 1000, 'operator:test', 'P5-4 risk')
            """,
            (str(fixture["botInstanceId"]),),
        )

    limit_result = store.evaluate_next_order_intent_risk("risk-worker-live")
    assert limit_result is not None
    assert limit_result["orderIntentId"] == limit_rejected_id
    assert limit_result["status"] == "risk_rejected"
    assert limit_result["eventType"] == "limit_rejected"

    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO kill_switches (
              scope_type, scope_key, state, sequence, actor_id, reason, open_order_policy
            ) VALUES ('bot', %s, 'armed', 1, 'operator:test', 'P5-4 kill switch', 'leave_open')
            """,
            (str(fixture["botInstanceId"]),),
        )

    kill_result = store.evaluate_next_order_intent_risk("risk-worker-live")

    assert kill_result is not None
    assert kill_result["orderIntentId"] == kill_rejected_id
    assert kill_result["status"] == "risk_rejected"
    assert kill_result["eventType"] == "kill_switch_rejected"
    with repository._connect() as connection:
        rows = connection.execute(
            """
            SELECT intent.id, intent.status, event.event_type, job.id AS paper_job_id
            FROM order_intents intent
            JOIN risk_events event ON event.order_intent_id = intent.id
            LEFT JOIN paper_execution_jobs job ON job.order_intent_id = intent.id
            WHERE intent.id IN (%s,%s)
            ORDER BY intent.id
            """,
            (limit_rejected_id, kill_rejected_id),
        ).fetchall()

    assert [row["status"] for row in rows] == ["risk_rejected", "risk_rejected"]
    assert [row["event_type"] for row in rows] == [
        "limit_rejected",
        "kill_switch_rejected",
    ]
    assert [row["paper_job_id"] for row in rows] == [None, None]


def test_live_postgres_risk_evaluation은_모순된_명목금액을_실패_폐쇄로_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 16, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        requested_quantity=Decimal("100"),
        limit_price=Decimal("100"),
        requested_notional=Decimal("1"),
    )
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO risk_limits (
              scope_type, scope_key, limit_type, version, limit_value, actor_id, reason
            ) VALUES ('bot', %s, 'max_order_notional', 1, 1000, 'operator:test', 'P5-4 risk')
            """,
            (str(fixture["botInstanceId"]),),
        )

    result = store.evaluate_next_order_intent_risk("risk-worker-live")

    assert result is not None
    assert result["orderIntentId"] == order_intent_id
    assert result["status"] == "risk_rejected"
    assert result["eventType"] == "limit_rejected"
    with repository._connect() as connection:
        row = connection.execute(
            """
            SELECT intent.status, event.event_type, job.id AS paper_job_id
            FROM order_intents intent
            JOIN risk_events event ON event.order_intent_id = intent.id
            LEFT JOIN paper_execution_jobs job ON job.order_intent_id = intent.id
            WHERE intent.id=%s
            """,
            (order_intent_id,),
        ).fetchone()
        assert row is not None
    assert row["status"] == "risk_rejected"
    assert row["event_type"] == "limit_rejected"
    assert row["paper_job_id"] is None


def test_live_postgres_risk_evaluation은_kill_switch_차단으로_attempt를_소모하지_않는다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresPortfolioBotStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 16, tzinfo=UTC)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        requested_quantity=Decimal("1"),
        limit_price=Decimal("100"),
    )
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO risk_limits (
              scope_type, scope_key, limit_type, version, limit_value, actor_id, reason
            ) VALUES ('bot', %s, 'max_order_notional', 1, 1000, 'operator:test', 'P5-4 risk')
            """,
            (str(fixture["botInstanceId"]),),
        )

    evaluated = store.evaluate_next_order_intent_risk("risk-worker-live")
    assert evaluated is not None and evaluated["status"] == "approved"
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE paper_execution_jobs
            SET max_attempts=1
            WHERE order_intent_id=%s
            """,
            (order_intent_id,),
        )
    claim = store.claim_next_paper_execution_job("paper-worker-before-switch")
    assert claim is not None
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO kill_switches (
              scope_type, scope_key, state, sequence, actor_id, reason, open_order_policy
            ) VALUES ('bot', %s, 'armed', 1, 'operator:test', 'P5-4 block', 'leave_open')
            """,
            (str(fixture["botInstanceId"]),),
        )

    blocked = store.complete_claimed_paper_execution_job(
        job_id=claim["paperExecutionJobId"],
        worker_id="paper-worker-before-switch",
        lease_generation=claim["leaseGeneration"],
        fill_price=Decimal("100"),
        filled_quantity=None,
        occurred_at=now,
        knowledge_at=now,
        evidence={"source": "blocked-after-claim"},
    )
    assert blocked["status"] == "retry_wait"
    with repository._connect() as connection:
        blocked_row = connection.execute(
            """
            SELECT attempt_count, max_attempts, status, last_error_code
            FROM paper_execution_jobs
            WHERE order_intent_id=%s
            """,
            (order_intent_id,),
        ).fetchone()
        assert blocked_row is not None
        connection.execute(
            """
            INSERT INTO kill_switches (
              scope_type, scope_key, state, sequence, actor_id, reason, open_order_policy
            ) VALUES ('bot', %s, 'released', 2, 'operator:test', 'P5-4 unblock', 'leave_open')
            """,
            (str(fixture["botInstanceId"]),),
        )
        connection.execute(
            """
            UPDATE paper_execution_jobs
            SET next_retry_at=clock_timestamp() - interval '1 second'
            WHERE order_intent_id=%s
            """,
            (order_intent_id,),
        )

    assert blocked_row["attempt_count"] == 0
    assert blocked_row["max_attempts"] == 1
    assert blocked_row["status"] == "retry_wait"
    assert blocked_row["last_error_code"] == "KILL_SWITCH_ARMED"
    retried = store.claim_next_paper_execution_job("paper-worker-after-switch")
    assert retried is not None
    assert retried["orderIntentId"] == order_intent_id


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
            (f"KRW-RISK-{key[:8].upper()}", f"RISK{key[:8].upper()}", f"Risk {key[:8]}"),
        ).fetchone()
        assert row is not None
        return int(row["id"])


def _insert_strategy_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> int:
    graph_hash = "d" * 64
    with repository._connect() as connection:
        strategy = connection.execute(
            """
            INSERT INTO strategy_definitions (
              owner_id, name, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', %s, 'P5-4 strategy', %s)
            RETURNING id
            """,
            (
                f"risk-strategy-{key}",
                f"risk-strategy-key-{key}",
                f"risk-strategy-request-{key}",
                now,
                "e" * 64,
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
              %s::jsonb, %s, %s, 'operator:test', %s, 'P5-4 strategy version', %s, %s
            ) RETURNING id
            """,
            (
                strategy["id"],
                graph_hash,
                '{"valid":true,"errors":[],"graphHash":"' + graph_hash + '"}',
                f"risk-strategy-version-key-{key}",
                f"risk-strategy-version-request-{key}",
                now,
                "f" * 64,
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
            VALUES ('operator:test', %s, 'operator:test', 'P5-4 portfolio')
            RETURNING id
            """,
            (f"risk-portfolio-{key}",),
        ).fetchone()
        assert portfolio is not None
        policy = connection.execute(
            """
            INSERT INTO portfolio_policies (
              portfolio_id, version, status, max_gross_exposure, max_single_position_pct,
              created_by, reason
            ) VALUES (%s, 1, 'published', 1000000, 0.25, 'operator:test', 'P5-4 policy')
            RETURNING id
            """,
            (portfolio["id"],),
        ).fetchone()
        assert policy is not None
        bot_definition = connection.execute(
            """
            INSERT INTO bot_definitions (
              owner_id, name, strategy_version_id, portfolio_id, created_by, reason
            ) VALUES ('operator:test', %s, %s, %s, 'operator:test', 'P5-4 bot')
            RETURNING id
            """,
            (f"risk-bot-{key}", strategy_version_id, portfolio["id"]),
        ).fetchone()
        assert bot_definition is not None
        bot_instance = connection.execute(
            """
            INSERT INTO bot_instances (
              bot_definition_id, strategy_version_id, portfolio_policy_id,
              stage, execution_mode, started_at, created_by, reason
            ) VALUES (%s, %s, %s, 'paper', 'paper', %s, 'operator:test', 'P5-4 instance')
            RETURNING id
            """,
            (bot_definition["id"], strategy_version_id, policy["id"], now),
        ).fetchone()
        assert bot_instance is not None
        return {
            "portfolioId": int(portfolio["id"]),
            "policyId": int(policy["id"]),
            "botDefinitionId": int(bot_definition["id"]),
            "botInstanceId": int(bot_instance["id"]),
        }


def _insert_order_intent(
    repository: PostgresOperationsRepository,
    key: str,
    bot_instance_id: int,
    instrument_id: int,
    *,
    requested_quantity: Decimal,
    limit_price: Decimal,
    requested_notional: Decimal | None = None,
) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO order_intents (
              bot_instance_id, instrument_id, idempotency_key, side, order_type,
              requested_quantity, requested_notional, limit_price, status, decision_input_hash,
              created_by, reason
            ) VALUES (
              %s, %s, %s, 'buy', 'limit', %s, %s, %s, 'created', %s,
              'operator:test', 'P5-4 intent'
            ) RETURNING id
            """,
            (
                bot_instance_id,
                instrument_id,
                f"risk-intent-{key}",
                requested_quantity,
                requested_notional,
                limit_price,
                "4" * 64,
            ),
        ).fetchone()
        assert row is not None
        return int(row["id"])
