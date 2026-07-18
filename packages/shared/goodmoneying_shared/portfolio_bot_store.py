from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, cast

from psycopg import errors

Row = dict[str, Any]


class PortfolioIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 포트폴리오 명령 본문을 가리킨다."""


class PortfolioCursorMismatchError(ValueError):
    """포트폴리오 cursor가 현재 조회 문맥과 다르다."""


class PostgresPortfolioBotStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def create_portfolio(self, **arguments: object) -> Row:
        return create_portfolio(self._repository, **arguments)

    def list_portfolios(self, **arguments: object) -> Mapping[str, object]:
        return list_portfolios(self._repository, **arguments)


def create_portfolio(
    repository: object,
    *,
    request_id: object,
    idempotency_key: object,
    actor_id: object,
    requested_at: object,
    reason: object,
    owner_id: object,
    name: object,
    base_currency: object,
) -> Row:
    payload = {
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
        "actorId": _non_blank(actor_id, "actorId"),
        "requestedAt": _datetime(requested_at, "requestedAt").isoformat(),
        "reason": _non_blank(reason, "reason"),
        "ownerId": _non_blank(owner_id, "ownerId"),
        "name": _non_blank(name, "name"),
        "baseCurrency": _base_currency(base_currency),
    }
    request_hash = _hash(payload)
    connector = _connector(repository)
    for attempt in range(3):
        try:
            with connector() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"portfolio:{payload['idempotencyKey']}",),
                )
                existing = connection.execute(
                    """
                    SELECT * FROM portfolios
                    WHERE idempotency_key=%s
                    """,
                    (payload["idempotencyKey"],),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise PortfolioIdempotencyConflictError(
                            "멱등 키의 기존 포트폴리오 생성 요청과 본문이 다르다."
                        )
                    return _portfolio_response(existing)
                row = connection.execute(
                    """
                    INSERT INTO portfolios (
                      owner_id, name, base_currency, created_by, reason,
                      request_id, idempotency_key, requested_at, request_hash
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (
                        payload["ownerId"],
                        payload["name"],
                        payload["baseCurrency"],
                        payload["actorId"],
                        payload["reason"],
                        payload["requestId"],
                        payload["idempotencyKey"],
                        _datetime(requested_at, "requestedAt"),
                        request_hash,
                    ),
                ).fetchone()
                assert row is not None
                return _portfolio_response(row)
        except errors.UniqueViolation as exc:
            raise ValueError("같은 owner와 이름의 포트폴리오가 이미 있다.") from exc
        except errors.SerializationFailure:
            if attempt == 2:
                raise
    raise RuntimeError("포트폴리오 생성 재시도 한도를 초과했다.")


def list_portfolios(
    repository: object, *, owner_id: object, page_size: object, cursor: object
) -> Row:
    decoded = _decode_cursor(cast(str | None, cursor))
    requested_owner_id = _non_blank(owner_id, "ownerId")
    limit = int(cast(int, page_size))
    if decoded is not None and decoded["ownerId"] != requested_owner_id:
        raise PortfolioCursorMismatchError("포트폴리오 cursor owner 문맥이 다르다.")
    with _connector(repository)() as connection:
        if decoded is None:
            ceiling_row = connection.execute(
                "SELECT COALESCE(MAX(id),0) AS id FROM portfolios WHERE owner_id=%s",
                (requested_owner_id,),
            ).fetchone()
            ceiling = int(ceiling_row["id"])
            last_id = ceiling + 1
        else:
            ceiling = int(cast(int, decoded["ceiling"]))
            last_id = int(cast(int, decoded["lastId"]))
        rows = connection.execute(
            """
            SELECT * FROM portfolios
            WHERE owner_id=%s AND id <= %s AND id < %s
            ORDER BY id DESC LIMIT %s
            """,
            (requested_owner_id, ceiling, last_id, limit + 1),
        ).fetchall()
        page = rows[:limit]
        items = [_portfolio_response(row) for row in page]
    return {
        "items": items,
        "nextCursor": (
            _encode_cursor(requested_owner_id, ceiling, int(page[-1]["id"]))
            if len(rows) > limit and page
            else None
        ),
    }


def _portfolio_response(row: Mapping[str, object]) -> Row:
    return {
        "portfolioId": int(cast(int, row["id"])),
        "ownerId": row["owner_id"],
        "name": row["name"],
        "baseCurrency": row["base_currency"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def _connector(repository: object) -> Callable[[], Any]:
    connector = getattr(repository, "_connect", None)
    if connector is None:
        raise RuntimeError("포트폴리오/봇 저장소는 PostgreSQL 연결이 필요하다.")
    return cast(Callable[[], Any], connector)


def _non_blank(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field}는 공백일 수 없다.")
    return value.strip()


def _datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field}는 UTC datetime이어야 한다.")
    return value


def _base_currency(value: object) -> str:
    currency = _non_blank(value, "baseCurrency")
    if currency not in {"KRW", "BTC", "USDT"}:
        raise ValueError("baseCurrency는 KRW, BTC, USDT 중 하나여야 한다.")
    return currency


def _hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _encode_cursor(owner_id: str, ceiling: int, last_id: int) -> str:
    raw = json.dumps(
        {
            "context": "portfolio-list-v1",
            "ownerId": owner_id,
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
        raise PortfolioCursorMismatchError("포트폴리오 cursor를 해석할 수 없다.") from exc
    if raw.get("context") != "portfolio-list-v1":
        raise PortfolioCursorMismatchError("포트폴리오 cursor 문맥이 다르다.")
    return cast(Mapping[str, object], raw)
