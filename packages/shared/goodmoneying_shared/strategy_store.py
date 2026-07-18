from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, cast

from psycopg import errors
from psycopg.types.json import Jsonb

from goodmoneying_shared.strategy_graph import validate_strategy_graph

Row = dict[str, Any]


class StrategyIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 전략 명령 본문을 가리킨다."""


class StrategyCursorMismatchError(ValueError):
    """전략 cursor가 현재 조회 문맥과 다르다."""


class PostgresStrategyStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def validate_graph(self, *, graph: Mapping[str, object]) -> Row:
        return validate_strategy_graph(graph).to_api()

    def create_strategy(self, **arguments: object) -> Row:
        return create_strategy(self._repository, **arguments)

    def publish_version(self, **arguments: object) -> Row:
        return publish_version(self._repository, **arguments)

    def get_version(self, strategy_version_id: int) -> Row | None:
        return get_version(self._repository, strategy_version_id)

    def list_versions(self, **arguments: object) -> Row:
        return list_versions(self._repository, **arguments)


def create_strategy(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    owner_id: object,
    name: object,
) -> Row:
    payload = {
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": _datetime(requested_at, "requestedAt").isoformat(),
        "reason": _non_blank(reason, "reason"),
        "ownerId": _non_blank(owner_id, "ownerId"),
        "name": _non_blank(name, "name"),
    }
    request_hash = _hash(payload)
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"strategy-definition:{payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    """
                    SELECT * FROM strategy_definitions
                    WHERE idempotency_key=%s
                    """,
                    (payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise StrategyIdempotencyConflictError(
                            "멱등 키의 기존 전략 정의 요청과 본문이 다르다."
                        )
                    return _strategy_response(existing)
                row = connection.execute(
                    """
                    INSERT INTO strategy_definitions (
                      owner_id, name, idempotency_key, request_id, actor_id,
                      requested_at, reason, request_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        payload["ownerId"],
                        payload["name"],
                        payload["idempotencyKey"],
                        payload["requestId"],
                        payload["actorId"],
                        _datetime(requested_at, "requestedAt"),
                        payload["reason"],
                        request_hash,
                    ),
                ).fetchone()
                assert row is not None
                return _strategy_response(row)
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("전략 생성 재시도 한도를 초과했다.")


def publish_version(
    repository: object,
    *,
    strategy_id: object,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    graph: object,
) -> Row:
    if not isinstance(graph, Mapping):
        raise ValueError("전략 graph는 객체여야 한다.")
    validation = validate_strategy_graph(graph)
    if not validation.valid:
        codes = ",".join(error.code for error in validation.errors)
        raise ValueError(codes)
    payload = {
        "strategyId": int(cast(int, strategy_id)),
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": _datetime(requested_at, "requestedAt").isoformat(),
        "reason": _non_blank(reason, "reason"),
        "graphHash": validation.graph_hash,
    }
    request_hash = _hash(payload)
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"strategy-version:{payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    """
                    SELECT version.id
                    FROM strategy_versions version
                    WHERE version.idempotency_key=%s
                    """,
                    (payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    row = _version_row(connection, int(existing["id"]))
                    assert row is not None
                    if row["request_hash"] != request_hash:
                        raise StrategyIdempotencyConflictError(
                            "멱등 키의 기존 전략 버전 요청과 본문이 다르다."
                        )
                    return _version_response(connection, row)
                strategy = connection.execute(
                    "SELECT id FROM strategy_definitions WHERE id=%s FOR KEY SHARE",
                    (payload["strategyId"],),
                ).fetchone()
                if strategy is None:
                    raise ValueError("전략 정의가 없다.")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"strategy-version-sequence:{payload['strategyId']}",),
                )
                next_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                    FROM strategy_versions WHERE strategy_id=%s
                    """,
                    (payload["strategyId"],),
                ).fetchone()
                version_number = int(next_row["next_version"])
                version = connection.execute(
                    """
                    INSERT INTO strategy_versions (
                      strategy_id, version, schema_version, status, graph_hash,
                      validation_result, idempotency_key, request_id, actor_id,
                      requested_at, reason, request_hash, published_at
                    ) VALUES (
                      %s,%s,'strategy-graph-v1','published',%s,%s,%s,%s,%s,%s,%s,%s,%s
                    ) RETURNING *
                    """,
                    (
                        payload["strategyId"],
                        version_number,
                        validation.graph_hash,
                        Jsonb(validation.to_api()),
                        payload["idempotencyKey"],
                        payload["requestId"],
                        payload["actorId"],
                        _datetime(requested_at, "requestedAt"),
                        payload["reason"],
                        request_hash,
                        _datetime(requested_at, "requestedAt"),
                    ),
                ).fetchone()
                assert version is not None
                connection.execute(
                    """
                    INSERT INTO strategy_graphs (strategy_version_id, graph_json, graph_hash)
                    VALUES (%s,%s,%s)
                    """,
                    (version["id"], Jsonb(dict(graph)), validation.graph_hash),
                )
                return _version_response(connection, version)
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("전략 버전 게시 재시도 한도를 초과했다.")


def get_version(repository: object, strategy_version_id: int) -> Row | None:
    with _connector(repository)() as connection:
        row = _version_row(connection, strategy_version_id)
        if row is None:
            return None
        return _version_response(connection, row)


def list_versions(
    repository: object, *, strategy_id: object, page_size: object, cursor: object
) -> Row:
    decoded = _decode_cursor(cast(str | None, cursor))
    limit = int(cast(int, page_size))
    requested_strategy_id = int(cast(int, strategy_id))
    if decoded is not None and int(cast(int, decoded["strategyId"])) != requested_strategy_id:
        raise StrategyCursorMismatchError("전략 버전 cursor 문맥이 다르다.")
    with _connector(repository)() as connection:
        if decoded is None:
            ceiling_row = connection.execute(
                "SELECT COALESCE(MAX(id),0) AS id FROM strategy_versions WHERE strategy_id=%s",
                (requested_strategy_id,),
            ).fetchone()
            ceiling = int(ceiling_row["id"])
            last_id = ceiling + 1
        else:
            ceiling = int(cast(int, decoded["ceiling"]))
            last_id = int(cast(int, decoded["lastId"]))
        rows = connection.execute(
            """
            SELECT * FROM strategy_versions
            WHERE strategy_id=%s AND id <= %s AND id < %s
            ORDER BY id DESC LIMIT %s
            """,
            (requested_strategy_id, ceiling, last_id, limit + 1),
        ).fetchall()
        page = rows[:limit]
        items = [_version_response(connection, row) for row in page]
    return {
        "items": items,
        "nextCursor": (
            _encode_cursor(requested_strategy_id, ceiling, int(page[-1]["id"]))
            if len(rows) > limit and page
            else None
        ),
    }


def _version_row(connection: Any, strategy_version_id: int) -> Row | None:
    row = connection.execute(
        "SELECT * FROM strategy_versions WHERE id=%s", (strategy_version_id,)
    ).fetchone()
    return cast(Row | None, row)


def _version_response(connection: Any, row: Mapping[str, object]) -> Row:
    graph = connection.execute(
        "SELECT graph_json FROM strategy_graphs WHERE strategy_version_id=%s",
        (row["id"],),
    ).fetchone()
    return {
        "strategyVersionId": int(cast(int, row["id"])),
        "strategyId": int(cast(int, row["strategy_id"])),
        "version": int(cast(int, row["version"])),
        "schemaVersion": row["schema_version"],
        "status": row["status"],
        "graphHash": row["graph_hash"],
        "validation": row["validation_result"],
        "graph": graph["graph_json"] if graph is not None else {},
        "createdAt": row["created_at"],
        "publishedAt": row["published_at"],
        "request_hash": row["request_hash"],
    }


def _strategy_response(row: Mapping[str, object]) -> Row:
    return {
        "strategyId": int(cast(int, row["id"])),
        "ownerId": row["owner_id"],
        "name": row["name"],
        "createdAt": row["created_at"],
    }


def _connector(repository: object) -> Callable[[], Any]:
    connector = getattr(repository, "_connect", None)
    if connector is None:
        raise RuntimeError("전략 저장소는 PostgreSQL 연결이 필요하다.")
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


def _encode_cursor(strategy_id: int, ceiling: int, last_id: int) -> str:
    raw = json.dumps(
        {
            "context": "strategy-version-list-v1",
            "strategyId": strategy_id,
            "ceiling": ceiling,
            "lastId": last_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str | None) -> Mapping[str, object] | None:
    if cursor is None:
        return None
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:
        raise StrategyCursorMismatchError("전략 버전 cursor를 해석할 수 없다.") from exc
    if raw.get("context") != "strategy-version-list-v1":
        raise StrategyCursorMismatchError("전략 버전 cursor 문맥이 다르다.")
    return cast(Mapping[str, object], raw)
