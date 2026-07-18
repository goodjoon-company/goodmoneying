from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from psycopg import errors
from psycopg.types.json import Jsonb

Row = dict[str, Any]


class PortfolioIdempotencyConflictError(ValueError):
    """같은 멱등 키가 다른 포트폴리오 명령 본문을 가리킨다."""


class PortfolioCursorMismatchError(ValueError):
    """포트폴리오 cursor가 현재 조회 문맥과 다르다."""


class PaperExecutionLeaseLostError(RuntimeError):
    """paper execution job 임대를 잃어 결과를 기록할 수 없다."""


class PostgresPortfolioBotStore:
    def __init__(self, repository: object) -> None:
        self._repository = repository

    def create_portfolio(self, **arguments: object) -> Row:
        return create_portfolio(self._repository, **arguments)

    def list_portfolios(self, **arguments: object) -> Mapping[str, object]:
        return list_portfolios(self._repository, **arguments)

    def claim_next_paper_execution_job(
        self, worker_id: str
    ) -> Mapping[str, object] | None:
        return claim_next_paper_execution_job(self._repository, worker_id=worker_id)

    def complete_claimed_paper_execution_job(self, **arguments: object) -> Row:
        return complete_claimed_paper_execution_job(self._repository, **arguments)

    def fail_claimed_paper_execution_job(self, **arguments: object) -> Row:
        return fail_claimed_paper_execution_job(self._repository, **arguments)


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


def claim_next_paper_execution_job(
    repository: object, *, worker_id: str, lease_seconds: int = 90
) -> Row | None:
    worker = _non_blank(worker_id, "workerId")
    connector = _connector(repository)
    with connector() as connection:
        job = _claim_next_paper_execution_job(connection, worker, lease_seconds)
        if job is None:
            return None
        return _paper_execution_job_response(connection, int(cast(int, job["id"])))


def _claim_next_paper_execution_job(
    connection: Any, worker_id: str, lease_seconds: int
) -> Row | None:
    candidate = connection.execute(
        """
        SELECT job.id, job.lease_generation
        FROM paper_execution_jobs job
        JOIN order_intents intent ON intent.id = job.order_intent_id
        JOIN bot_instances instance ON instance.id = intent.bot_instance_id
        WHERE (
          job.status='pending'
          OR (job.status='retry_wait' AND job.next_retry_at <= clock_timestamp())
          OR (job.status='running' AND job.lease_expires_at <= clock_timestamp())
        )
        AND intent.status='approved'
        AND instance.stage='paper'
        AND instance.execution_mode='paper'
        ORDER BY job.priority DESC, job.created_at, job.id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """
    ).fetchone()
    if candidate is None:
        return None
    row = connection.execute(
        """
        UPDATE paper_execution_jobs
        SET status='running',
            lease_owner=%s,
            lease_expires_at=clock_timestamp() + (%s * interval '1 second'),
            lease_generation=lease_generation + 1,
            attempt_count=attempt_count + 1,
            updated_at=clock_timestamp(),
            last_error_code=NULL
        WHERE id=%s
          AND lease_generation=%s
          AND attempt_count < max_attempts
        RETURNING id
        """,
        (
            worker_id,
            lease_seconds,
            candidate["id"],
            candidate["lease_generation"],
        ),
    ).fetchone()
    return cast(Row | None, row)


def complete_claimed_paper_execution_job(
    repository: object,
    *,
    job_id: object,
    worker_id: object,
    lease_generation: object,
    fill_price: object,
    filled_quantity: object,
    occurred_at: object,
    knowledge_at: object,
    evidence: object,
) -> Row:
    price = _decimal(fill_price, "fillPrice")
    occurred = _datetime(occurred_at, "occurredAt")
    knowledge = _datetime(knowledge_at, "knowledgeAt")
    if knowledge < occurred:
        raise ValueError("knowledgeAt은 occurredAt보다 빠를 수 없다.")
    evidence_payload = dict(cast(Mapping[str, object], evidence or {}))
    connector = _connector(repository)
    with connector() as connection:
        claim = _locked_claimed_paper_job(
            connection,
            job_id=int(cast(int, job_id)),
            worker_id=_non_blank(worker_id, "workerId"),
            lease_generation=int(cast(int, lease_generation)),
        )
        intent = _paper_intent_row(connection, int(cast(int, claim["order_intent_id"])))
        quantity = _paper_fill_quantity(intent, price, filled_quantity)
        exchange_order = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'paper',%s,'done',%s,%s)
            RETURNING id
            """,
            (
                intent["order_intent_id"],
                f"paper-{claim['id']}-{claim['lease_generation']}",
                occurred,
                Jsonb(
                    {
                        "source": "paper_execution_worker",
                        "paperExecutionJobId": int(cast(int, claim["id"])),
                        "leaseGeneration": int(cast(int, claim["lease_generation"])),
                    }
                ),
            ),
        ).fetchone()
        assert exchange_order is not None
        fill = connection.execute(
            """
            INSERT INTO order_fills (
              exchange_order_id, fill_sequence, fill_source, side, filled_quantity,
              fill_price, fee_paid, occurred_at, knowledge_at, evidence
            ) VALUES (%s,1,'paper_simulator',%s,%s,%s,0,%s,%s,%s)
            RETURNING id
            """,
            (
                exchange_order["id"],
                intent["side"],
                quantity,
                price,
                occurred,
                knowledge,
                Jsonb(evidence_payload),
            ),
        ).fetchone()
        assert fill is not None
        _upsert_position_projection(
            connection,
            portfolio_id=int(cast(int, intent["portfolio_id"])),
            instrument_id=int(cast(int, intent["instrument_id"])),
            side=str(intent["side"]),
            quantity=quantity,
            price=price,
            source_fill_id=int(cast(int, fill["id"])),
        )
        connection.execute(
            "UPDATE order_intents SET status='paper_filled' WHERE id=%s",
            (intent["order_intent_id"],),
        )
        completed = connection.execute(
            """
            UPDATE paper_execution_jobs
            SET status='succeeded',
                lease_owner=NULL,
                lease_expires_at=NULL,
                updated_at=clock_timestamp()
            WHERE id=%s
            RETURNING *
            """,
            (claim["id"],),
        ).fetchone()
        assert completed is not None
        return _paper_execution_job_summary(completed)


def fail_claimed_paper_execution_job(
    repository: object,
    *,
    job_id: object,
    worker_id: object,
    lease_generation: object,
    error_code: object,
    message: object,
) -> Row:
    connector = _connector(repository)
    with connector() as connection:
        claim = _locked_claimed_paper_job(
            connection,
            job_id=int(cast(int, job_id)),
            worker_id=_non_blank(worker_id, "workerId"),
            lease_generation=int(cast(int, lease_generation)),
        )
        next_status = (
            "dead_letter"
            if int(cast(int, claim["attempt_count"])) >= int(cast(int, claim["max_attempts"]))
            else "retry_wait"
        )
        failed = connection.execute(
            """
            UPDATE paper_execution_jobs
            SET status=%s,
                lease_owner=NULL,
                lease_expires_at=NULL,
                next_retry_at=CASE
                  WHEN %s='retry_wait' THEN clock_timestamp() + interval '30 seconds'
                  ELSE next_retry_at
                END,
                last_error_code=%s,
                dead_letter_reason=CASE WHEN %s='dead_letter' THEN %s ELSE dead_letter_reason END,
                updated_at=clock_timestamp()
            WHERE id=%s
            RETURNING *
            """,
            (
                next_status,
                next_status,
                _non_blank(error_code, "errorCode"),
                next_status,
                _non_blank(message, "message"),
                claim["id"],
            ),
        ).fetchone()
        assert failed is not None
        return _paper_execution_job_summary(failed)


def _locked_claimed_paper_job(
    connection: Any, *, job_id: int, worker_id: str, lease_generation: int
) -> Row:
    row = connection.execute(
        """
        SELECT *
        FROM paper_execution_jobs
        WHERE id=%s
          AND status='running'
          AND lease_owner=%s
          AND lease_generation=%s
          AND lease_expires_at > clock_timestamp()
        FOR UPDATE
        """,
        (job_id, worker_id, lease_generation),
    ).fetchone()
    if row is None:
        raise PaperExecutionLeaseLostError("paper execution job 임대를 잃었다.")
    return cast(Row, row)


def _paper_intent_row(connection: Any, order_intent_id: int) -> Row:
    row = connection.execute(
        """
        SELECT
          intent.id AS order_intent_id,
          intent.instrument_id,
          intent.side,
          intent.order_type,
          intent.requested_quantity,
          intent.requested_notional,
          intent.limit_price,
          definition.portfolio_id
        FROM order_intents intent
        JOIN bot_instances instance ON instance.id = intent.bot_instance_id
        JOIN bot_definitions definition ON definition.id = instance.bot_definition_id
        WHERE intent.id=%s
          AND intent.status='approved'
          AND instance.stage='paper'
          AND instance.execution_mode='paper'
        FOR UPDATE OF intent
        """,
        (order_intent_id,),
    ).fetchone()
    if row is None:
        raise PaperExecutionLeaseLostError("paper 실행 가능한 주문 의도가 없다.")
    return cast(Row, row)


def _paper_fill_quantity(
    intent: Mapping[str, object], fill_price: Decimal, filled_quantity: object
) -> Decimal:
    if filled_quantity is not None:
        return _positive_decimal(filled_quantity, "filledQuantity")
    requested_quantity = intent["requested_quantity"]
    if requested_quantity is not None:
        return _positive_decimal(requested_quantity, "requestedQuantity")
    requested_notional = _positive_decimal(intent["requested_notional"], "requestedNotional")
    return requested_notional / fill_price


def _upsert_position_projection(
    connection: Any,
    *,
    portfolio_id: int,
    instrument_id: int,
    side: str,
    quantity: Decimal,
    price: Decimal,
    source_fill_id: int,
) -> None:
    current = connection.execute(
        """
        SELECT * FROM position_projections
        WHERE portfolio_id=%s AND instrument_id=%s
        FOR UPDATE
        """,
        (portfolio_id, instrument_id),
    ).fetchone()
    if current is None:
        signed_quantity = quantity if side == "buy" else -quantity
        average_entry_price = price if signed_quantity > 0 else None
        realized_pnl = Decimal("0")
    else:
        previous_quantity = _decimal(current["quantity"], "quantity")
        previous_average = (
            _decimal(current["average_entry_price"], "averageEntryPrice")
            if current["average_entry_price"] is not None
            else Decimal("0")
        )
        previous_realized = _decimal(current["realized_pnl"], "realizedPnl")
        if side == "buy":
            signed_quantity = previous_quantity + quantity
            cost = previous_quantity * previous_average + quantity * price
            average_entry_price = cost / signed_quantity if signed_quantity > 0 else None
            realized_pnl = previous_realized
        else:
            signed_quantity = previous_quantity - quantity
            realized_pnl = previous_realized + (price - previous_average) * quantity
            average_entry_price = previous_average if signed_quantity > 0 else None
    connection.execute(
        """
        INSERT INTO position_projections (
          portfolio_id, instrument_id, quantity, average_entry_price,
          realized_pnl, source_fill_id
        ) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (portfolio_id, instrument_id)
        DO UPDATE SET
          quantity=EXCLUDED.quantity,
          average_entry_price=EXCLUDED.average_entry_price,
          realized_pnl=EXCLUDED.realized_pnl,
          updated_at=clock_timestamp(),
          source_fill_id=EXCLUDED.source_fill_id
        """,
        (
            portfolio_id,
            instrument_id,
            signed_quantity,
            average_entry_price,
            realized_pnl,
            source_fill_id,
        ),
    )


def _paper_execution_job_response(connection: Any, job_id: int) -> Row:
    row = connection.execute(
        """
        SELECT
          job.*,
          intent.id AS order_intent_id,
          intent.instrument_id,
          intent.side,
          intent.order_type,
          intent.requested_quantity,
          intent.requested_notional,
          intent.limit_price,
          definition.portfolio_id
        FROM paper_execution_jobs job
        JOIN order_intents intent ON intent.id = job.order_intent_id
        JOIN bot_instances instance ON instance.id = intent.bot_instance_id
        JOIN bot_definitions definition ON definition.id = instance.bot_definition_id
        WHERE job.id=%s
        """,
        (job_id,),
    ).fetchone()
    assert row is not None
    return {
        **_paper_execution_job_summary(row),
        "orderIntentId": int(cast(int, row["order_intent_id"])),
        "instrumentId": int(cast(int, row["instrument_id"])),
        "portfolioId": int(cast(int, row["portfolio_id"])),
        "side": row["side"],
        "orderType": row["order_type"],
        "requestedQuantity": row["requested_quantity"],
        "requestedNotional": row["requested_notional"],
        "limitPrice": row["limit_price"],
    }


def _paper_execution_job_summary(row: Mapping[str, object]) -> Row:
    return {
        "paperExecutionJobId": int(cast(int, row["id"])),
        "orderIntentId": int(cast(int, row["order_intent_id"])),
        "status": row["status"],
        "leaseOwner": row["lease_owner"],
        "leaseGeneration": int(cast(int, row["lease_generation"])),
        "attemptCount": int(cast(int, row["attempt_count"])),
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


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field}는 Decimal이어야 한다.") from exc


def _positive_decimal(value: object, field: str) -> Decimal:
    decimal_value = _decimal(value, field)
    if decimal_value <= 0:
        raise ValueError(f"{field}는 0보다 커야 한다.")
    return decimal_value


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
