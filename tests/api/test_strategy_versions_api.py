from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from goodmoneying_api.main import create_app
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository

HASH_A = "a" * 64


def _graph() -> dict[str, Any]:
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
                "config": {"signal": "enter_long"},
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


def _command() -> dict[str, Any]:
    return {
        "requestId": "strategy-request-1",
        "idempotencyKey": "strategy-key-1",
        "actorId": "operator:test",
        "requestedAt": "2026-07-18T08:00:00Z",
        "reason": "P3 전략 버전 계약 검증",
    }


def _client(repository: FakeStrategyRepository) -> TestClient:
    return TestClient(
        create_app(
            SQLiteOperationsRepository(),
            strategy_repository=repository,
        )
    )


def test_graph_validate는_운영토큰을_요구하고_오류와_hash를_반환한다() -> None:
    repository = FakeStrategyRepository()
    client = _client(repository)

    unauthorized = client.post("/v1/strategy-graphs/validate", json={"graph": _graph()})
    response = client.post(
        "/v1/strategy-graphs/validate",
        headers={"X-Operator-Token": "local-dev-token"},
        json={"graph": _graph()},
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["graphHash"] == HASH_A
    assert repository.validate_arguments == {"graph": _graph()}


def test_strategy와_version_게시명령은_멱등성과_불변_hash를_저장소에_전달한다() -> None:
    repository = FakeStrategyRepository()
    client = _client(repository)
    headers = {"X-Operator-Token": "local-dev-token"}

    strategy = client.post(
        "/v1/strategies",
        headers=headers,
        json={
            **_command(),
            "ownerId": "operator:local",
            "name": "KRW momentum v1",
        },
    )
    version = client.post(
        "/v1/strategies/7/versions",
        headers=headers,
        json={**_command(), "graph": _graph()},
    )
    read = client.get("/v1/strategy-versions/11")
    listed = client.get("/v1/strategies/7/versions", params={"pageSize": 20})

    assert strategy.status_code == 201
    assert version.status_code == 201
    assert read.status_code == listed.status_code == 200
    assert version.json()["status"] == "published"
    assert version.json()["graphHash"] == HASH_A
    assert repository.create_arguments["requested_at"] == datetime(
        2026, 7, 18, 8, tzinfo=UTC
    )
    assert repository.publish_arguments["strategy_id"] == 7
    assert repository.publish_arguments["graph"] == _graph()
    assert repository.read_count == 2


def test_실행전_검증오류는_version_게시를_422로_차단한다() -> None:
    repository = FakeStrategyRepository(validation_error=True)
    client = _client(repository)

    response = client.post(
        "/v1/strategies/7/versions",
        headers={"X-Operator-Token": "local-dev-token"},
        json={**_command(), "graph": _graph()},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_STRATEGY_GRAPH"
    assert repository.mutation_count == 0


class FakeStrategyRepository:
    def __init__(self, *, validation_error: bool = False) -> None:
        self.validation_error = validation_error
        self.validate_arguments: dict[str, Any] | None = None
        self.create_arguments: dict[str, Any] = {}
        self.publish_arguments: dict[str, Any] = {}
        self.mutation_count = 0
        self.read_count = 0

    def validate_graph(self, *, graph: Mapping[str, object]) -> dict[str, Any]:
        self.validate_arguments = {"graph": graph}
        return {"valid": True, "errors": [], "graphHash": HASH_A}

    def create_strategy(self, **arguments: Any) -> dict[str, Any]:
        self.create_arguments = arguments
        self.mutation_count += 1
        return {
            "strategyId": 7,
            "ownerId": arguments["owner_id"],
            "name": arguments["name"],
            "createdAt": "2026-07-18T08:00:01Z",
        }

    def publish_version(self, **arguments: Any) -> dict[str, Any]:
        if self.validation_error:
            raise ValueError("missing_output")
        self.publish_arguments = arguments
        self.mutation_count += 1
        return _version(arguments["strategy_id"])

    def get_version(self, strategy_version_id: int) -> dict[str, Any] | None:
        self.read_count += 1
        return _version(7, strategy_version_id)

    def list_versions(self, **arguments: Any) -> dict[str, Any]:
        self.read_count += 1
        return {"items": [_version(arguments["strategy_id"])], "nextCursor": None}


def _version(strategy_id: int, strategy_version_id: int = 11) -> dict[str, Any]:
    return {
        "strategyVersionId": strategy_version_id,
        "strategyId": strategy_id,
        "version": 1,
        "schemaVersion": "strategy-graph-v1",
        "status": "published",
        "graphHash": HASH_A,
        "validation": {"valid": True, "errors": [], "graphHash": HASH_A},
        "graph": _graph(),
        "createdAt": "2026-07-18T08:00:02Z",
        "publishedAt": "2026-07-18T08:00:02Z",
    }
