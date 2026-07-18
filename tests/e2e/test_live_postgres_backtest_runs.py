from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_shared.backtest_engine import (
    BacktestEngineSpec,
    BacktestEquityPoint,
    BacktestResult,
    BacktestTrade,
    ExecutionModel,
)
from goodmoneying_shared.backtest_store import (
    BacktestIdempotencyConflictError,
    PostgresBacktestStore,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_백테스트_run과_결과는_멱등_불변으로_저장된다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresBacktestStore(repository)
    key = uuid4().hex
    now = datetime(2026, 7, 18, 10, tzinfo=UTC)
    dataset_version_id, dataset_hash = _insert_dataset_version(repository, key, now)
    strategy_version_id, strategy_hash = _insert_strategy_version(repository, key, now)
    spec = BacktestEngineSpec(
        dataset_version_id=dataset_version_id,
        dataset_content_hash=dataset_hash,
        strategy_version_id=strategy_version_id,
        strategy_graph_hash=strategy_hash,
        engine_version="backtest-core-v1",
        parameter_hash="b" * 64,
        seed=42,
        initial_cash=Decimal("1000"),
        execution=ExecutionModel(
            fee_rate=Decimal("0.001"),
            slippage_bps=Decimal("10"),
            latency_seconds=60,
            max_participation_rate=Decimal("0.25"),
        ),
    )
    result = _result()

    saved = store.persist_completed_run(
        request_id=f"backtest-request-{key}",
        idempotency_key=f"backtest-key-{key}",
        actor_id="operator:test",
        requested_at=now,
        reason="P4-2 백테스트 영속화 E2E",
        spec=spec,
        result=result,
        started_at=now,
        completed_at=now,
        artifacts=[
            {
                "artifactType": "walk_forward_summary",
                "contentHash": "c" * 64,
                "storageUri": "artifact://p4-2/walk-forward",
                "metadata": {"folds": 3},
            }
        ],
    )
    replay = store.persist_completed_run(
        request_id=f"backtest-request-{key}",
        idempotency_key=f"backtest-key-{key}",
        actor_id="operator:test",
        requested_at=now,
        reason="P4-2 백테스트 영속화 E2E",
        spec=spec,
        result=result,
    )
    fetched = store.get_run(int(saved["backtestRunId"]))

    assert saved["backtestRunId"] == replay["backtestRunId"]
    assert fetched is not None
    assert fetched["inputHash"] == result.input_hash
    assert fetched["resultHash"] == result.result_hash
    assert fetched["metrics"][0]["metricName"] == "finalEquity"
    assert fetched["metrics"][0]["metricValue"] == Decimal("1009.579790")
    assert fetched["trades"][0]["remainingQuantity"] == Decimal("2.00")
    assert fetched["artifacts"][0]["artifactType"] == "walk_forward_summary"

    with pytest.raises(BacktestIdempotencyConflictError):
        store.persist_completed_run(
            request_id=f"backtest-request-{key}",
            idempotency_key=f"backtest-key-{key}",
            actor_id="operator:test",
            requested_at=now,
            reason="같은 키의 다른 결과",
            spec=spec,
            result=_result(result_hash="f" * 64),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="backtest_runs is append-only"),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE backtest_runs SET result_hash=%s WHERE id=%s",
            ("e" * 64, saved["backtestRunId"]),
        )


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _insert_dataset_version(
    repository: PostgresOperationsRepository, key: str, now: datetime
) -> tuple[int, str]:
    content_hash = "d" * 64
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
                f"{key[:64]:0<64}"[:64],
                "1" * 64,
                "2" * 64,
                "3" * 64,
                content_hash,
                now,
            ),
        ).fetchone()
    assert row is not None
    return int(cast(int, row["id"])), content_hash


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
            ) VALUES (%s,%s,%s,%s,'operator:test',%s,'P4-2 전략',%s)
            RETURNING id
            """,
            (
                "operator:test",
                f"p4-2-{key}",
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
              'operator:test', %s, 'P4-2 version', %s, %s
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


def _result(result_hash: str = "f" * 64) -> BacktestResult:
    at = datetime(2026, 7, 18, 0, tzinfo=UTC)
    return BacktestResult(
        status="succeeded",
        input_hash="e" * 64,
        result_hash=result_hash,
        assumptions=(
            "orderbook_absent_uses_candle_close",
            "partial_fill_by_candle_volume_participation",
        ),
        replay_events=(),
        trades=(
            BacktestTrade(
                side="buy",
                requested_quantity=Decimal("3"),
                filled_quantity=Decimal("1.00"),
                remaining_quantity=Decimal("2.00"),
                fill_price=Decimal("100.100"),
                fee_paid=Decimal("0.100100"),
                status="partially_filled",
                occurred_at=at,
                knowledge_at=at,
            ),
        ),
        equity_points=(
            BacktestEquityPoint(
                occurred_at=at,
                knowledge_at=at,
                cash=Decimal("899.799900"),
                base_position=Decimal("1.00"),
                equity=Decimal("1009.579790"),
            ),
        ),
        metrics={"finalEquity": Decimal("1009.579790")},
        golden_replay_signals=(),
    )
