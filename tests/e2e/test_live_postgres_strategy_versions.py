from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.strategy_store import (
    PostgresStrategyStore,
    StrategyIdempotencyConflictError,
)

pytestmark = pytest.mark.live


def test_live_postgres_전략_definition과_published_version은_불변이다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresStrategyStore(repository)
    key = uuid4().hex
    requested_at = datetime(2026, 7, 18, 8, tzinfo=UTC)
    strategy = store.create_strategy(
        request_id=f"strategy-request-{key}",
        idempotency_key=f"strategy-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P3 live 전략 정의",
        owner_id="operator:local",
        name=f"KRW momentum {key}",
    )
    replay = store.create_strategy(
        request_id=f"strategy-request-{key}",
        idempotency_key=f"strategy-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P3 live 전략 정의",
        owner_id="operator:local",
        name=f"KRW momentum {key}",
    )

    assert strategy["strategyId"] == replay["strategyId"]
    with pytest.raises(StrategyIdempotencyConflictError):
        store.create_strategy(
            request_id=f"strategy-request-{key}",
            idempotency_key=f"strategy-key-{key}",
            actor_id="operator:test",
            requested_at=requested_at,
            reason="같은 키의 다른 전략 정의",
            owner_id="operator:local",
            name=f"KRW momentum changed {key}",
        )

    version = store.publish_version(
        strategy_id=int(strategy["strategyId"]),
        request_id=f"strategy-version-request-{key}",
        idempotency_key=f"strategy-version-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P3 첫 전략 version 게시",
        graph=_graph(),
    )
    fetched = store.get_version(int(version["strategyVersionId"]))
    listed = store.list_versions(strategy_id=int(strategy["strategyId"]), page_size=10, cursor=None)

    assert fetched is not None
    assert fetched["graphHash"] == version["graphHash"]
    assert fetched["validation"]["valid"] is True
    assert listed["items"][0]["strategyVersionId"] == version["strategyVersionId"]

    with (
        pytest.raises(psycopg.errors.RaiseException, match="strategy_graphs is append-only"),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE strategy_graphs SET graph_json = '{}'::jsonb WHERE strategy_version_id=%s",
            (version["strategyVersionId"],),
        )


def test_live_postgres_같은_전략의_동시_version_게시도_순번을_직렬화한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresStrategyStore(repository)
    key = uuid4().hex
    requested_at = datetime(2026, 7, 18, 8, tzinfo=UTC)
    strategy = store.create_strategy(
        request_id=f"strategy-concurrent-request-{key}",
        idempotency_key=f"strategy-concurrent-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P3 동시 게시 순번 검증",
        owner_id="operator:local",
        name=f"concurrent strategy {key}",
    )

    def publish(index: int) -> dict[str, object]:
        return store.publish_version(
            strategy_id=int(strategy["strategyId"]),
            request_id=f"strategy-concurrent-version-request-{key}-{index}",
            idempotency_key=f"strategy-concurrent-version-key-{key}-{index}",
            actor_id="operator:test",
            requested_at=requested_at,
            reason=f"P3 동시 게시 순번 검증 {index}",
            graph=_graph(signal=f"enter_long_{index}"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(publish, [1, 2]))

    assert sorted(int(str(version["version"])) for version in versions) == [1, 2]
    assert len({version["graphHash"] for version in versions}) == 2


def test_live_postgres_strategy_graph_hash는_version_hash와_DB에서_묶인다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    store = PostgresStrategyStore(repository)
    key = uuid4().hex
    requested_at = datetime(2026, 7, 18, 8, tzinfo=UTC)
    strategy = store.create_strategy(
        request_id=f"strategy-fk-request-{key}",
        idempotency_key=f"strategy-fk-key-{key}",
        actor_id="operator:test",
        requested_at=requested_at,
        reason="P3 graph hash FK 검증",
        owner_id="operator:local",
        name=f"hash fk strategy {key}",
    )

    with repository._connect() as connection:
        version = connection.execute(
            """
            INSERT INTO strategy_versions (
              strategy_id, version, schema_version, status, graph_hash,
              validation_result, idempotency_key, request_id, actor_id,
              requested_at, reason, request_hash, published_at
            ) VALUES (
              %s, 1, 'strategy-graph-v1', 'published', %s,
              '{"valid":true,"errors":[],"graphHash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'::jsonb,
              %s, %s, 'operator:test', %s, 'P3 graph hash FK 검증',
              %s, %s
            ) RETURNING id
            """,
            (
                int(strategy["strategyId"]),
                "a" * 64,
                f"strategy-fk-version-key-{key}",
                f"strategy-fk-version-request-{key}",
                requested_at,
                "b" * 64,
                requested_at,
            ),
        ).fetchone()
        assert version is not None

        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                INSERT INTO strategy_graphs (strategy_version_id, graph_json, graph_hash)
                VALUES (%s, %s::jsonb, %s)
                """,
                (
                    version["id"],
                    '{"schema_version":"strategy-graph-v1"}',
                    "c" * 64,
                ),
            )


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _graph(*, signal: str = "enter_long") -> dict[str, object]:
    return {
        "schema_version": "strategy-graph-v1",
        "nodes": [
            {
                "id": "input.close",
                "type": "dataset.candle.close",
                "config": {"missingDataPolicy": "fail"},
                "input_ports": [],
                "output_ports": [
                    {"name": "close", "dataType": "series.decimal", "timeframe": "1m"}
                ],
            },
            {
                "id": "bot.output",
                "type": "bot.signal",
                "config": {"signal": signal},
                "input_ports": [
                    {"name": "condition", "dataType": "series.decimal", "timeframe": "1m"}
                ],
                "output_ports": [
                    {"name": "signal", "dataType": "signal.order_intent", "timeframe": "1m"}
                ],
            },
        ],
        "edges": [
            {
                "from_node": "input.close",
                "from_port": "close",
                "to_node": "bot.output",
                "to_port": "condition",
            }
        ],
        "outputs": [{"node": "bot.output", "port": "signal"}],
    }
