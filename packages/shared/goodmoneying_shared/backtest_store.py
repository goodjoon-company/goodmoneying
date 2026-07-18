from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from psycopg import errors
from psycopg.types.json import Jsonb

from goodmoneying_shared.backtest_engine import BacktestEngineSpec, BacktestResult

Row = dict[str, Any]


class BacktestIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 백테스트 저장 요청 본문을 가리킨다."""


class PostgresBacktestStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def persist_completed_run(self, **arguments: object) -> Row:
        return persist_completed_run(self._repository, **arguments)

    def get_run(self, backtest_run_id: int) -> Row | None:
        return get_run(self._repository, backtest_run_id)


def persist_completed_run(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    spec: object,
    result: object,
    started_at: object | None = None,
    completed_at: object | None = None,
    artifacts: object = (),
) -> Row:
    if not isinstance(spec, BacktestEngineSpec):
        raise ValueError("백테스트 spec이 필요하다.")
    if not isinstance(result, BacktestResult):
        raise ValueError("백테스트 result가 필요하다.")
    artifact_rows = _artifact_inputs(artifacts)
    requested_at_dt = _datetime(requested_at, "requestedAt")
    started_at_dt = (
        _datetime(started_at, "startedAt") if started_at is not None else requested_at_dt
    )
    completed_at_dt = (
        _datetime(completed_at, "completedAt") if completed_at is not None else requested_at_dt
    )
    payload = {
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": requested_at_dt.isoformat(),
        "reason": _non_blank(reason, "reason"),
        "inputHash": result.input_hash,
        "resultHash": result.result_hash,
    }
    request_hash = _hash(payload)
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"backtest-run:{payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    "SELECT id, request_hash FROM backtest_runs WHERE idempotency_key=%s",
                    (payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise BacktestIdempotencyConflictError(
                            "멱등 키의 기존 백테스트 저장 요청과 본문이 다르다."
                        )
                    replay = get_run(repository, int(cast(int, existing["id"])))
                    assert replay is not None
                    return replay

                run = connection.execute(
                    """
                    INSERT INTO backtest_runs (
                      strategy_version_id, strategy_graph_hash,
                      dataset_version_id, dataset_content_hash,
                      engine_version, status, input_hash, result_hash,
                      parameter_hash, seed, assumptions, idempotency_key,
                      request_id, actor_id, requested_at, reason, request_hash,
                      started_at, finished_at
                    ) VALUES (
                      %s,%s,%s,%s,%s,'succeeded',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                    ) RETURNING *
                    """,
                    (
                        spec.strategy_version_id,
                        spec.strategy_graph_hash,
                        spec.dataset_version_id,
                        spec.dataset_content_hash,
                        spec.engine_version,
                        result.input_hash,
                        result.result_hash,
                        spec.parameter_hash,
                        spec.seed,
                        Jsonb(list(result.assumptions)),
                        payload["idempotencyKey"],
                        payload["requestId"],
                        payload["actorId"],
                        requested_at_dt,
                        payload["reason"],
                        request_hash,
                        started_at_dt,
                        completed_at_dt,
                    ),
                ).fetchone()
                assert run is not None
                run_id = int(cast(int, run["id"]))
                _insert_trades(connection, run_id, result)
                _insert_equity_points(connection, run_id, result)
                _insert_metrics(connection, run_id, result)
                _insert_artifacts(connection, run_id, artifact_rows)
                saved = _get_run_with_connection(connection, run_id)
                assert saved is not None
                return saved
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("백테스트 저장 재시도 한도를 초과했다.")


def get_run(repository: object, backtest_run_id: int) -> Row | None:
    with _connector(repository)() as connection:
        return _get_run_with_connection(connection, backtest_run_id)


def _get_run_with_connection(connection: Any, backtest_run_id: int) -> Row | None:
    run = connection.execute(
        "SELECT * FROM backtest_runs WHERE id=%s", (backtest_run_id,)
    ).fetchone()
    if run is None:
        return None
    trades = connection.execute(
        "SELECT * FROM backtest_trades WHERE run_id=%s ORDER BY trade_sequence",
        (backtest_run_id,),
    ).fetchall()
    metrics = connection.execute(
        """
        SELECT * FROM backtest_metrics
        WHERE run_id=%s ORDER BY metric_name, scope_key
        """,
        (backtest_run_id,),
    ).fetchall()
    artifacts = connection.execute(
        """
        SELECT * FROM backtest_artifacts
        WHERE run_id=%s ORDER BY artifact_type, id
        """,
        (backtest_run_id,),
    ).fetchall()
    return {
        "backtestRunId": int(cast(int, run["id"])),
        "strategyVersionId": int(cast(int, run["strategy_version_id"])),
        "datasetVersionId": int(cast(int, run["dataset_version_id"])),
        "inputHash": run["input_hash"],
        "resultHash": run["result_hash"],
        "status": run["status"],
        "metrics": [_metric_response(row) for row in metrics],
        "trades": [_trade_response(row) for row in trades],
        "artifacts": [_artifact_response(row) for row in artifacts],
        "request_hash": run["request_hash"],
    }


def _insert_trades(connection: Any, run_id: int, result: BacktestResult) -> None:
    for index, trade in enumerate(result.trades, start=1):
        connection.execute(
            """
            INSERT INTO backtest_trades (
              run_id, trade_sequence, signal_sequence, side, requested_quantity,
              filled_quantity, remaining_quantity, fill_price, fee_paid, status,
              occurred_at, knowledge_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                index,
                index,
                trade.side,
                trade.requested_quantity,
                trade.filled_quantity,
                trade.remaining_quantity,
                trade.fill_price,
                trade.fee_paid,
                trade.status,
                trade.occurred_at,
                trade.knowledge_at,
            ),
        )


def _insert_equity_points(connection: Any, run_id: int, result: BacktestResult) -> None:
    for index, point in enumerate(result.equity_points, start=1):
        connection.execute(
            """
            INSERT INTO backtest_equity_points (
              run_id, point_sequence, occurred_at, knowledge_at,
              cash, base_position, equity
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                index,
                point.occurred_at,
                point.knowledge_at,
                point.cash,
                point.base_position,
                point.equity,
            ),
        )


def _insert_metrics(connection: Any, run_id: int, result: BacktestResult) -> None:
    for name, value in sorted(result.metrics.items()):
        connection.execute(
            """
            INSERT INTO backtest_metrics (run_id, metric_name, scope_key, metric_value)
            VALUES (%s,%s,'run',%s)
            """,
            (run_id, name, value),
        )


def _insert_artifacts(
    connection: Any,
    run_id: int,
    artifacts: Sequence[Mapping[str, object]],
) -> None:
    for artifact in artifacts:
        connection.execute(
            """
            INSERT INTO backtest_artifacts (
              run_id, artifact_type, content_hash, media_type, storage_uri, artifact_json
            ) VALUES (%s,%s,%s,'application/json',%s,%s)
            """,
            (
                run_id,
                artifact["artifactType"],
                artifact["contentHash"],
                artifact.get("storageUri"),
                Jsonb(artifact.get("metadata", {})),
            ),
        )


def _metric_response(row: Mapping[str, object]) -> Row:
    return {
        "metricName": row["metric_name"],
        "scopeKey": row["scope_key"],
        "metricValue": cast(Decimal | None, row["metric_value"]),
        "metricPayload": row["metric_payload"],
    }


def _trade_response(row: Mapping[str, object]) -> Row:
    return {
        "tradeSequence": row["trade_sequence"],
        "side": row["side"],
        "requestedQuantity": cast(Decimal, row["requested_quantity"]),
        "filledQuantity": cast(Decimal, row["filled_quantity"]),
        "remainingQuantity": cast(Decimal, row["remaining_quantity"]),
        "fillPrice": cast(Decimal, row["fill_price"]),
        "feePaid": cast(Decimal, row["fee_paid"]),
        "status": row["status"],
        "occurredAt": row["occurred_at"],
        "knowledgeAt": row["knowledge_at"],
    }


def _artifact_response(row: Mapping[str, object]) -> Row:
    return {
        "artifactType": row["artifact_type"],
        "contentHash": row["content_hash"],
        "mediaType": row["media_type"],
        "storageUri": row["storage_uri"],
        "metadata": row["artifact_json"],
    }


def _artifact_inputs(value: object) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ValueError("백테스트 artifact 목록은 배열이어야 한다.")
    rows: list[Mapping[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("백테스트 artifact는 객체여야 한다.")
        rows.append(item)
    return tuple(rows)


def _connector(repository: object) -> Callable[[], Any]:
    connector = getattr(repository, "_connect", None)
    if connector is None:
        raise RuntimeError("백테스트 저장소는 PostgreSQL 연결이 필요하다.")
    return cast(Callable[[], Any], connector)


def _non_blank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}는 공백일 수 없다.")
    return value.strip()


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field}는 UTC datetime이어야 한다.")
    return value


def _hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
