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


class PaperExecutionBlockedError(RuntimeError):
    """활성 kill switch 때문에 paper execution을 진행할 수 없다."""


class ReconciliationIdempotencyConflictError(ValueError):
    """같은 대사 run key가 다른 관측 payload를 가리킨다."""


class LiveReconciliationApplicationIdempotencyConflictError(ValueError):
    """같은 live 대사 적용 멱등 키가 다른 증적 payload를 가리킨다."""


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

    def evaluate_next_order_intent_risk(
        self, worker_id: str
    ) -> Mapping[str, object] | None:
        return evaluate_next_order_intent_risk(self._repository, worker_id=worker_id)

    def reconcile_exchange_order(self, **arguments: object) -> Row:
        return reconcile_exchange_order(self._repository, **arguments)

    def record_upbit_live_reconciliation_application(
        self, **arguments: object
    ) -> Row:
        return record_upbit_live_reconciliation_application(
            self._repository,
            **arguments,
        )

    def apply_upbit_live_reconciliation_application(
        self, **arguments: object
    ) -> Row:
        return apply_upbit_live_reconciliation_application(
            self._repository,
            **arguments,
        )


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
    _lock_kill_switch_table(connection)
    candidate = connection.execute(
        """
        SELECT job.id, job.lease_generation
        FROM paper_execution_jobs job
        JOIN order_intents intent ON intent.id = job.order_intent_id
        JOIN bot_instances instance ON instance.id = intent.bot_instance_id
        JOIN bot_definitions definition ON definition.id = instance.bot_definition_id
        WHERE (
          job.status='pending'
          OR (job.status='retry_wait' AND job.next_retry_at <= clock_timestamp())
          OR (job.status='running' AND job.lease_expires_at <= clock_timestamp())
        )
        AND intent.status='approved'
        AND instance.stage='paper'
        AND instance.execution_mode='paper'
        AND NOT EXISTS (
          SELECT 1
          FROM (
            VALUES
              ('global', 'global'),
              ('portfolio', definition.portfolio_id::text),
              ('bot', instance.id::text)
          ) AS scope(scope_type, scope_key)
          JOIN LATERAL (
            SELECT *
            FROM kill_switches latest_switch
            WHERE latest_switch.scope_type=scope.scope_type
              AND latest_switch.scope_key=scope.scope_key
            ORDER BY latest_switch.sequence DESC
            LIMIT 1
          ) latest_switch ON true
          WHERE latest_switch.state='armed'
        )
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
        _lock_kill_switch_table(connection)
        active_switch = _active_kill_switch(
            connection,
            _risk_scopes(
                portfolio_id=int(cast(int, intent["portfolio_id"])),
                bot_instance_id=int(cast(int, intent["bot_instance_id"])),
                instrument_id=int(cast(int, intent["instrument_id"])),
                include_instrument=False,
            ),
        )
        if active_switch is not None:
            blocked = _defer_paper_job_for_kill_switch(
                connection, int(cast(int, claim["id"]))
            )
            return _paper_execution_job_summary(blocked)
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


def reconcile_exchange_order(
    repository: object,
    *,
    exchange_order_id: object,
    run_key: object,
    actor_id: object,
    reason: object,
    observed_status: object,
    fills: object,
    evidence: object,
) -> Row:
    order_id = int(cast(int, exchange_order_id))
    key = _non_blank(run_key, "runKey")
    actor = _non_blank(actor_id, "actorId")
    run_reason = _non_blank(reason, "reason")
    observed = _observed_reconciliation_status(observed_status)
    fill_payloads = sorted(
        [
            _reconciliation_fill_payload(fill)
            for fill in cast(list[Mapping[str, object]], fills or [])
        ],
        key=lambda fill: int(cast(int, fill["fillSequence"])),
    )
    evidence_payload = dict(cast(Mapping[str, object], evidence or {}))
    request_hash = _hash(
        {
            "exchangeOrderId": order_id,
            "runKey": key,
            "observedStatus": observed,
            "fills": [_reconciliation_fill_hash_payload(fill) for fill in fill_payloads],
            "evidence": evidence_payload,
        }
    )
    connector = _connector(repository)
    with connector() as connection:
        return _reconcile_exchange_order_in_connection(
            connection,
            order_id=order_id,
            key=key,
            actor=actor,
            run_reason=run_reason,
            observed=observed,
            fill_payloads=fill_payloads,
            evidence_payload=evidence_payload,
            request_hash=request_hash,
        )


def _reconcile_exchange_order_in_connection(
    connection: Any,
    *,
    order_id: int,
    key: str,
    actor: str,
    run_reason: str,
    observed: str,
    fill_payloads: list[Row],
    evidence_payload: Mapping[str, object],
    request_hash: str,
) -> Row:
    _lock_reconciliation_run_key(connection, order_id, key)
    existing_run = connection.execute(
        """
        SELECT *
        FROM reconciliation_runs
        WHERE exchange_order_id=%s AND run_key=%s
        """,
        (order_id, key),
    ).fetchone()
    if existing_run is not None:
        if existing_run["request_hash"] != request_hash:
            raise ReconciliationIdempotencyConflictError(
                "같은 대사 run key가 다른 관측 payload로 재사용됐다."
            )
        return _reconciliation_run_summary(cast(Row, existing_run))
    order = _locked_exchange_order_for_reconciliation(connection, order_id)
    if observed in {"outcome_unknown", "missing"} and not fill_payloads:
        run = _insert_reconciliation_run(
            connection,
            exchange_order_id=order_id,
            run_key=key,
            status="outcome_unknown",
            observed_status=observed,
            observed_fill_count=0,
            request_hash=request_hash,
            actor_id=actor,
            reason=run_reason,
            evidence=evidence_payload,
        )
        connection.execute(
            """
            UPDATE exchange_orders
            SET status='outcome_unknown',
                reconciled_at=clock_timestamp()
            WHERE id=%s
            """,
            (order_id,),
        )
        connection.execute(
            "UPDATE order_intents SET status='outcome_unknown' WHERE id=%s",
            (order["order_intent_id"],),
        )
        _insert_reconciliation_risk_event(
            connection,
            order,
            run,
            event_type="outcome_unknown",
            severity="warning",
            message="대사 결과 주문 결과가 불명확하다.",
        )
        return _reconciliation_run_summary(run)
    mismatch: Row | None = None
    existing_fills = {
        int(cast(int, row["fill_sequence"])): cast(Row, row)
        for row in connection.execute(
            """
            SELECT *
            FROM order_fills
            WHERE exchange_order_id=%s
            FOR UPDATE
            """,
            (order_id,),
        ).fetchall()
    }
    seen_sequences: set[int] = set()
    for fill in fill_payloads:
        fill_sequence = int(cast(int, fill["fillSequence"]))
        if fill_sequence in seen_sequences:
            mismatch = {"fillSequence": fill_sequence, "reason": "duplicate observed fill"}
            break
        seen_sequences.add(fill_sequence)
        existing_fill = existing_fills.get(fill_sequence)
        if existing_fill is not None:
            if not _same_fill(existing_fill, fill):
                mismatch = {
                    "fillSequence": fill_sequence,
                    "existingFillId": int(cast(int, existing_fill["id"])),
                }
                break
            continue
        if any(existing_sequence > fill_sequence for existing_sequence in existing_fills):
            mismatch = {
                "fillSequence": fill_sequence,
                "reason": "late fill before existing higher sequence",
            }
            break
    if mismatch is not None:
        run = _insert_reconciliation_run(
            connection,
            exchange_order_id=order_id,
            run_key=key,
            status="mismatch",
            observed_status=observed,
            observed_fill_count=len(fill_payloads),
            request_hash=request_hash,
            actor_id=actor,
            reason=run_reason,
            evidence={**evidence_payload, "mismatch": mismatch},
        )
        _insert_reconciliation_risk_event(
            connection,
            order,
            run,
            event_type="reconciliation_mismatch",
            severity="critical",
            message="대사 결과 기존 체결과 관측 체결이 불일치한다.",
        )
        return _reconciliation_run_summary(run)
    inserted_fill_count = 0
    for fill in fill_payloads:
        if int(cast(int, fill["fillSequence"])) in existing_fills:
            continue
        inserted = connection.execute(
            """
            INSERT INTO order_fills (
              exchange_order_id, fill_sequence, fill_source, side, filled_quantity,
              fill_price, fee_paid, occurred_at, knowledge_at, evidence
            ) VALUES (%s,%s,'reconciliation',%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                order_id,
                fill["fillSequence"],
                fill["side"],
                fill["filledQuantity"],
                fill["fillPrice"],
                fill["feePaid"],
                fill["occurredAt"],
                fill["knowledgeAt"],
                Jsonb(fill["evidence"]),
            ),
        ).fetchone()
        assert inserted is not None
        _upsert_position_projection(
            connection,
            portfolio_id=int(cast(int, order["portfolio_id"])),
            instrument_id=int(cast(int, order["instrument_id"])),
            side=str(fill["side"]),
            quantity=cast(Decimal, fill["filledQuantity"]),
            price=cast(Decimal, fill["fillPrice"]),
            source_fill_id=int(cast(int, inserted["id"])),
        )
        inserted_fill_count += 1
    connection.execute(
        """
        UPDATE exchange_orders
        SET status='reconciled',
            reconciled_at=clock_timestamp()
        WHERE id=%s
        """,
        (order_id,),
    )
    connection.execute(
        "UPDATE order_intents SET status='reconciled' WHERE id=%s",
        (order["order_intent_id"],),
    )
    run = _insert_reconciliation_run(
        connection,
        exchange_order_id=order_id,
        run_key=key,
        status="succeeded",
        observed_status=observed,
        observed_fill_count=len(fill_payloads),
        request_hash=request_hash,
        actor_id=actor,
        reason=run_reason,
        evidence={**evidence_payload, "insertedFillCount": inserted_fill_count},
    )
    return _reconciliation_run_summary(run)


def record_upbit_live_reconciliation_application(
    repository: object,
    *,
    exchange_account_id: object,
    order_intent_id: object,
    exchange_order_id: object,
    live_exchange_order_binding_id: object,
    reconciliation_run_id: object,
    source: object,
    source_endpoint: object,
    observed_upbit_order_uuid: object,
    observed_upbit_identifier: object,
    observed_state: object,
    applied_at: object,
    can_resubmit: object,
    actual_request_sent: object,
    actual_order_cancel_sent: object,
    evidence: object,
    actor_id: object,
    reason: object,
    request_id: object,
    idempotency_key: object,
) -> Row:
    payload = _live_reconciliation_application_payload(
        exchange_account_id=exchange_account_id,
        order_intent_id=order_intent_id,
        exchange_order_id=exchange_order_id,
        live_exchange_order_binding_id=live_exchange_order_binding_id,
        reconciliation_run_id=reconciliation_run_id,
        source=source,
        source_endpoint=source_endpoint,
        observed_upbit_order_uuid=observed_upbit_order_uuid,
        observed_upbit_identifier=observed_upbit_identifier,
        observed_state=observed_state,
        applied_at=applied_at,
        can_resubmit=can_resubmit,
        actual_request_sent=actual_request_sent,
        actual_order_cancel_sent=actual_order_cancel_sent,
        evidence=evidence,
        actor_id=actor_id,
        reason=reason,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    connector = _connector(repository)
    with connector() as connection:
        return _record_upbit_live_reconciliation_application_in_connection(
            connection,
            payload=payload,
        )


def apply_upbit_live_reconciliation_application(
    repository: object,
    *,
    exchange_account_id: object,
    order_intent_id: object,
    exchange_order_id: object,
    live_exchange_order_binding_id: object,
    run_key: object,
    observed_status: object,
    fills: object,
    reconciliation_evidence: object,
    source: object,
    source_endpoint: object,
    observed_upbit_order_uuid: object,
    observed_upbit_identifier: object,
    observed_state: object,
    applied_at: object,
    can_resubmit: object,
    actual_request_sent: object,
    actual_order_cancel_sent: object,
    application_evidence: object,
    actor_id: object,
    reason: object,
    request_id: object,
    idempotency_key: object,
) -> Row:
    order_id = int(cast(int, exchange_order_id))
    key = _non_blank(run_key, "runKey")
    actor = _non_blank(actor_id, "actorId")
    run_reason = _non_blank(reason, "reason")
    observed = _observed_reconciliation_status(observed_status)
    fill_payloads = sorted(
        [
            _reconciliation_fill_payload(fill)
            for fill in cast(list[Mapping[str, object]], fills or [])
        ],
        key=lambda fill: int(cast(int, fill["fillSequence"])),
    )
    reconciliation_evidence_payload = dict(
        cast(Mapping[str, object], reconciliation_evidence or {})
    )
    reconciliation_request_hash = _hash(
        {
            "exchangeOrderId": order_id,
            "runKey": key,
            "observedStatus": observed,
            "fills": [_reconciliation_fill_hash_payload(fill) for fill in fill_payloads],
            "evidence": reconciliation_evidence_payload,
        }
    )
    connector = _connector(repository)
    with connector() as connection:
        run = _reconcile_exchange_order_in_connection(
            connection,
            order_id=order_id,
            key=key,
            actor=actor,
            run_reason=run_reason,
            observed=observed,
            fill_payloads=fill_payloads,
            evidence_payload=reconciliation_evidence_payload,
            request_hash=reconciliation_request_hash,
        )
        if run["status"] != "succeeded":
            return {
                **run,
                "liveReconciliationApplicationId": None,
                "liveReconciliationApplicationStatus": "not_recorded",
            }
        application_payload = _live_reconciliation_application_payload(
            exchange_account_id=exchange_account_id,
            order_intent_id=order_intent_id,
            exchange_order_id=exchange_order_id,
            live_exchange_order_binding_id=live_exchange_order_binding_id,
            reconciliation_run_id=run["reconciliationRunId"],
            source=source,
            source_endpoint=source_endpoint,
            observed_upbit_order_uuid=observed_upbit_order_uuid,
            observed_upbit_identifier=observed_upbit_identifier,
            observed_state=observed_state,
            applied_at=applied_at,
            can_resubmit=can_resubmit,
            actual_request_sent=actual_request_sent,
            actual_order_cancel_sent=actual_order_cancel_sent,
            evidence=application_evidence,
            actor_id=actor_id,
            reason=reason,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        application = _record_upbit_live_reconciliation_application_in_connection(
            connection,
            payload=application_payload,
        )
        return {
            **run,
            "liveReconciliationApplicationId": application[
                "liveReconciliationApplicationId"
            ],
            "liveReconciliationApplicationStatus": application["status"],
        }


def _live_reconciliation_application_payload(
    *,
    exchange_account_id: object,
    order_intent_id: object,
    exchange_order_id: object,
    live_exchange_order_binding_id: object,
    reconciliation_run_id: object,
    source: object,
    source_endpoint: object,
    observed_upbit_order_uuid: object,
    observed_upbit_identifier: object,
    observed_state: object,
    applied_at: object,
    can_resubmit: object,
    actual_request_sent: object,
    actual_order_cancel_sent: object,
    evidence: object,
    actor_id: object,
    reason: object,
    request_id: object,
    idempotency_key: object,
) -> Row:
    return {
        "exchangeAccountId": int(cast(int, exchange_account_id)),
        "orderIntentId": int(cast(int, order_intent_id)),
        "exchangeOrderId": int(cast(int, exchange_order_id)),
        "liveExchangeOrderBindingId": int(cast(int, live_exchange_order_binding_id)),
        "reconciliationRunId": int(cast(int, reconciliation_run_id)),
        "source": _non_blank(source, "source"),
        "sourceEndpoint": _non_blank(source_endpoint, "sourceEndpoint"),
        "observedUpbitOrderUuid": _non_blank(
            observed_upbit_order_uuid,
            "observedUpbitOrderUuid",
        ),
        "observedUpbitIdentifier": _non_blank(
            observed_upbit_identifier,
            "observedUpbitIdentifier",
        ),
        "observedState": _non_blank(observed_state, "observedState"),
        "appliedAt": _datetime(applied_at, "appliedAt").isoformat(),
        "canResubmit": _must_be_false(can_resubmit, "canResubmit"),
        "actualRequestSent": _must_be_false(
            actual_request_sent,
            "actualRequestSent",
        ),
        "actualOrderCancelSent": _must_be_false(
            actual_order_cancel_sent,
            "actualOrderCancelSent",
        ),
        "evidence": dict(cast(Mapping[str, object], evidence or {})),
        "actorId": _non_blank(actor_id, "actorId"),
        "reason": _non_blank(reason, "reason"),
        "requestId": _non_blank(request_id, "requestId"),
        "idempotencyKey": _non_blank(idempotency_key, "idempotencyKey"),
    }


def _record_upbit_live_reconciliation_application_in_connection(
    connection: Any,
    *,
    payload: Mapping[str, object],
) -> Row:
    request_hash = _hash(payload)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (
            "upbit-live-reconciliation-application:"
            f"{payload['exchangeOrderId']}:{payload['idempotencyKey']}",
        ),
    )
    existing_application = connection.execute(
        """
        SELECT *
        FROM upbit_live_reconciliation_applications
        WHERE idempotency_key=%s
        """,
        (payload["idempotencyKey"],),
    ).fetchone()
    if existing_application is not None:
        if existing_application["request_hash"] != request_hash:
            raise LiveReconciliationApplicationIdempotencyConflictError(
                "같은 live 대사 적용 멱등 키가 다른 payload로 재사용됐다."
            )
        return _live_reconciliation_application_summary(
            cast(Row, existing_application)
        )
    application = connection.execute(
        """
        INSERT INTO upbit_live_reconciliation_applications (
          exchange_account_id, order_intent_id, exchange_order_id,
          live_exchange_order_binding_id, reconciliation_run_id,
          source, source_endpoint, observed_upbit_order_uuid,
          observed_upbit_identifier, observed_state, applied_at,
          request_hash, can_resubmit, actual_request_sent,
          actual_order_cancel_sent, evidence, actor_id, reason,
          request_id, idempotency_key
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,false,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            payload["exchangeAccountId"],
            payload["orderIntentId"],
            payload["exchangeOrderId"],
            payload["liveExchangeOrderBindingId"],
            payload["reconciliationRunId"],
            payload["source"],
            payload["sourceEndpoint"],
            payload["observedUpbitOrderUuid"],
            payload["observedUpbitIdentifier"],
            payload["observedState"],
            datetime.fromisoformat(str(payload["appliedAt"])),
            request_hash,
            Jsonb(payload["evidence"]),
            payload["actorId"],
            payload["reason"],
            payload["requestId"],
            payload["idempotencyKey"],
        ),
    ).fetchone()
    assert application is not None
    return _live_reconciliation_application_summary(cast(Row, application))


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


def _locked_exchange_order_for_reconciliation(connection: Any, exchange_order_id: int) -> Row:
    row = connection.execute(
        """
        SELECT
          exchange.*,
          intent.id AS order_intent_id,
          intent.instrument_id,
          intent.bot_instance_id,
          definition.portfolio_id
        FROM exchange_orders exchange
        JOIN order_intents intent ON intent.id = exchange.order_intent_id
        JOIN bot_instances instance ON instance.id = intent.bot_instance_id
        JOIN bot_definitions definition ON definition.id = instance.bot_definition_id
        WHERE exchange.id=%s
          AND exchange.execution_mode IN ('paper','shadow','live')
        FOR UPDATE OF exchange, intent
        """,
        (exchange_order_id,),
    ).fetchone()
    if row is None:
        raise PaperExecutionLeaseLostError("대사 가능한 exchange order가 없다.")
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
          instance.id AS bot_instance_id,
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


def _defer_paper_job_for_kill_switch(connection: Any, job_id: int) -> Row:
    row = connection.execute(
        """
        UPDATE paper_execution_jobs
        SET status='retry_wait',
            lease_owner=NULL,
            lease_expires_at=NULL,
            next_retry_at=clock_timestamp() + interval '30 seconds',
            attempt_count=GREATEST(attempt_count - 1, 0),
            last_error_code='KILL_SWITCH_ARMED',
            updated_at=clock_timestamp()
        WHERE id=%s
        RETURNING *
        """,
        (job_id,),
    ).fetchone()
    assert row is not None
    return cast(Row, row)


def evaluate_next_order_intent_risk(
    repository: object, *, worker_id: str
) -> Row | None:
    worker = _non_blank(worker_id, "workerId")
    connector = _connector(repository)
    with connector() as connection:
        intent = connection.execute(
            """
            SELECT
              intent.*,
              instance.stage,
              instance.execution_mode,
              instance.id AS bot_instance_id,
              definition.portfolio_id
            FROM order_intents intent
            JOIN bot_instances instance ON instance.id = intent.bot_instance_id
            JOIN bot_definitions definition ON definition.id = instance.bot_definition_id
            WHERE intent.status='created'
              AND instance.stage IN ('paper','shadow')
              AND instance.execution_mode IN ('paper','shadow')
            ORDER BY intent.created_at, intent.id
            FOR UPDATE OF intent SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if intent is None:
            return None
        scopes = _risk_scopes(
            portfolio_id=int(cast(int, intent["portfolio_id"])),
            bot_instance_id=int(cast(int, intent["bot_instance_id"])),
            instrument_id=int(cast(int, intent["instrument_id"])),
            include_instrument=True,
        )
        _lock_kill_switch_table(connection)
        for scope_type, scope_key in scopes:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"risk-scope:{scope_type}:{scope_key}",),
            )
        decision = _risk_decision(connection, cast(Row, intent), scopes)
        connection.execute(
            """
            UPDATE order_intents
            SET status=%s,
                risk_policy_version=%s,
                risk_decision_reason=%s
            WHERE id=%s
            """,
            (
                decision["status"],
                decision["riskPolicyVersion"],
                decision["reason"],
                intent["id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO risk_events (
              order_intent_id, bot_instance_id, scope_type, scope_key, event_type,
              severity, fingerprint, risk_policy_version, message, evidence
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                intent["id"],
                intent["bot_instance_id"],
                decision["scopeType"],
                decision["scopeKey"],
                decision["eventType"],
                decision["severity"],
                f"risk-evaluation-v1:{intent['id']}",
                decision["riskPolicyVersion"],
                decision["message"],
                Jsonb(
                    {
                        "workerId": worker,
                        "decisionInputHash": intent["decision_input_hash"],
                        "orderNotional": str(decision["orderNotional"])
                        if decision["orderNotional"] is not None
                        else None,
                        "riskEvidence": decision["evidence"],
                    }
                ),
            ),
        )
        if (
            decision["status"] == "approved"
            and str(intent["execution_mode"]) == "paper"
        ):
            connection.execute(
                """
                INSERT INTO paper_execution_jobs (order_intent_id)
                VALUES (%s)
                ON CONFLICT (order_intent_id) DO NOTHING
                """,
                (intent["id"],),
            )
        return {
            "orderIntentId": int(cast(int, intent["id"])),
            "status": decision["status"],
            "eventType": decision["eventType"],
            "riskPolicyVersion": decision["riskPolicyVersion"],
            "reason": decision["reason"],
        }


def _lock_kill_switch_table(connection: Any) -> None:
    connection.execute("LOCK TABLE kill_switches IN SHARE MODE")


def _risk_scopes(
    *,
    portfolio_id: int,
    bot_instance_id: int,
    instrument_id: int,
    include_instrument: bool,
) -> list[tuple[str, str]]:
    scopes = [
        ("global", "global"),
        ("portfolio", str(portfolio_id)),
        ("bot", str(bot_instance_id)),
    ]
    if include_instrument:
        scopes.append(("instrument", str(instrument_id)))
    return scopes


def _risk_decision(
    connection: Any, intent: Row, scopes: list[tuple[str, str]]
) -> Row:
    active_switch = _active_kill_switch(connection, scopes[:3])
    limits = _active_risk_limits(connection, scopes)
    policy_version = max(
        [int(cast(int, limit["version"])) for limit in limits],
        default=1,
    )
    order_notional = _order_notional(intent)
    if active_switch is not None:
        scope_type = str(active_switch["scope_type"])
        scope_key = str(active_switch["scope_key"])
        return {
            "status": "risk_rejected",
            "eventType": "kill_switch_rejected",
            "severity": "critical",
            "scopeType": scope_type,
            "scopeKey": scope_key,
            "riskPolicyVersion": policy_version,
            "reason": f"kill switch armed: {scope_type}:{scope_key}",
            "message": "활성 kill switch가 신규 주문 의도를 차단했다.",
            "orderNotional": order_notional,
            "evidence": {
                "killSwitchId": int(cast(int, active_switch["id"])),
                "sequence": int(cast(int, active_switch["sequence"])),
            },
        }
    for limit in limits:
        if limit["limit_type"] != "max_order_notional":
            return _limit_rejected_decision(
                limit,
                policy_version,
                order_notional,
                "P5-4는 해당 위험 한도 계산 증적을 아직 갖고 있지 않다.",
            )
        if order_notional is None:
            return _limit_rejected_decision(
                limit,
                policy_version,
                order_notional,
                "주문 명목 금액을 계산할 수 없다.",
            )
        limit_value = _decimal(limit["limit_value"], "limitValue")
        if order_notional > limit_value:
            return _limit_rejected_decision(
                limit,
                policy_version,
                order_notional,
                "주문 명목 금액이 max_order_notional 한도를 초과했다.",
            )
    execution_mode = str(intent["execution_mode"])
    return {
        "status": "approved",
        "eventType": "policy_approved",
        "severity": "info",
        "scopeType": "bot",
        "scopeKey": str(intent["bot_instance_id"]),
        "riskPolicyVersion": policy_version,
        "reason": f"risk approved for {execution_mode}",
        "message": f"{execution_mode} 주문 의도를 위험 정책이 승인했다.",
        "orderNotional": order_notional,
        "evidence": {
            "limitCount": len(limits),
            "executionMode": execution_mode,
        },
    }


def _limit_rejected_decision(
    limit: Mapping[str, object],
    policy_version: int,
    order_notional: Decimal | None,
    message: str,
) -> Row:
    return {
        "status": "risk_rejected",
        "eventType": "limit_rejected",
        "severity": "warning",
        "scopeType": limit["scope_type"],
        "scopeKey": limit["scope_key"],
        "riskPolicyVersion": policy_version,
        "reason": f"risk limit rejected: {limit['limit_type']}",
        "message": message,
        "orderNotional": order_notional,
        "evidence": {
            "limitId": int(cast(int, limit["id"])),
            "limitType": limit["limit_type"],
            "limitValue": str(limit["limit_value"]),
        },
    }


def _active_kill_switch(
    connection: Any, scopes: list[tuple[str, str]]
) -> Row | None:
    row = connection.execute(
        """
        WITH scope(scope_type, scope_key, priority) AS (
          VALUES (%s,%s,1), (%s,%s,2), (%s,%s,3)
        )
        SELECT latest_switch.*
        FROM scope
        JOIN LATERAL (
          SELECT *
          FROM kill_switches latest_switch
          WHERE latest_switch.scope_type=scope.scope_type
            AND latest_switch.scope_key=scope.scope_key
          ORDER BY latest_switch.sequence DESC
          LIMIT 1
        ) latest_switch ON true
        WHERE latest_switch.state='armed'
        ORDER BY scope.priority
        LIMIT 1
        """,
        (
            scopes[0][0],
            scopes[0][1],
            scopes[1][0],
            scopes[1][1],
            scopes[2][0],
            scopes[2][1],
        ),
    ).fetchone()
    return cast(Row | None, row)


def _active_risk_limits(connection: Any, scopes: list[tuple[str, str]]) -> list[Row]:
    rows = connection.execute(
        """
        WITH scope(scope_type, scope_key, priority) AS (
          VALUES (%s,%s,1), (%s,%s,2), (%s,%s,3), (%s,%s,4)
        )
        SELECT DISTINCT ON (risk_limit.scope_type, risk_limit.scope_key, risk_limit.limit_type)
          risk_limit.*
        FROM scope
        JOIN risk_limits risk_limit
          ON risk_limit.scope_type=scope.scope_type
         AND risk_limit.scope_key=scope.scope_key
        WHERE risk_limit.status='active'
        ORDER BY
          risk_limit.scope_type,
          risk_limit.scope_key,
          risk_limit.limit_type,
          risk_limit.version DESC,
          risk_limit.id DESC
        """,
        (
            scopes[0][0],
            scopes[0][1],
            scopes[1][0],
            scopes[1][1],
            scopes[2][0],
            scopes[2][1],
            scopes[3][0],
            scopes[3][1],
        ),
    ).fetchall()
    return [cast(Row, row) for row in rows]


def _order_notional(intent: Mapping[str, object]) -> Decimal | None:
    if intent["requested_quantity"] is None or intent["limit_price"] is None:
        if intent["requested_notional"] is not None:
            return _positive_decimal(intent["requested_notional"], "requestedNotional")
        return None
    requested_quantity = _positive_decimal(intent["requested_quantity"], "requestedQuantity")
    limit_price = _positive_decimal(intent["limit_price"], "limitPrice")
    computed_notional = requested_quantity * limit_price
    if intent["requested_notional"] is not None:
        requested_notional = _positive_decimal(intent["requested_notional"], "requestedNotional")
        if requested_notional != computed_notional:
            return None
    return computed_notional


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
    _lock_position_projection_scope(connection, portfolio_id, instrument_id)
    current = connection.execute(
        """
        SELECT * FROM position_projections
        WHERE portfolio_id=%s AND instrument_id=%s
        FOR UPDATE
        """,
        (portfolio_id, instrument_id),
    ).fetchone()
    if current is None:
        if side == "sell":
            raise ValueError("보유 position 없는 매도 fill은 projection으로 반영할 수 없다.")
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
            if quantity > previous_quantity:
                raise ValueError(
                    "보유 quantity를 초과한 매도 fill은 projection으로 반영할 수 없다."
                )
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


def _lock_position_projection_scope(
    connection: Any, portfolio_id: int, instrument_id: int
) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"position-projection:{portfolio_id}:{instrument_id}",),
    )


def _lock_reconciliation_run_key(connection: Any, exchange_order_id: int, run_key: str) -> None:
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"reconciliation-run:{exchange_order_id}:{run_key}",),
    )


def _insert_reconciliation_run(
    connection: Any,
    *,
    exchange_order_id: int,
    run_key: str,
    status: str,
    observed_status: str,
    observed_fill_count: int,
    request_hash: str,
    actor_id: str,
    reason: str,
    evidence: Mapping[str, object],
) -> Row:
    row = connection.execute(
        """
        INSERT INTO reconciliation_runs (
          exchange_order_id, run_key, status, observed_status, observed_fill_count,
          request_hash, actor_id, reason, evidence
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            exchange_order_id,
            run_key,
            status,
            observed_status,
            observed_fill_count,
            request_hash,
            actor_id,
            reason,
            Jsonb(dict(evidence)),
        ),
    ).fetchone()
    assert row is not None
    return cast(Row, row)


def _insert_reconciliation_risk_event(
    connection: Any,
    order: Mapping[str, object],
    run: Mapping[str, object],
    *,
    event_type: str,
    severity: str,
    message: str,
) -> None:
    fingerprint = (
        f"reconciliation-v1:{order['id']}:{run['run_key']}:{event_type}"
    )
    connection.execute(
        """
        INSERT INTO risk_events (
          order_intent_id, bot_instance_id, scope_type, scope_key, event_type,
          severity, fingerprint, message, evidence
        ) VALUES (%s,%s,'bot',%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
        """,
        (
            order["order_intent_id"],
            order["bot_instance_id"],
            str(order["bot_instance_id"]),
            event_type,
            severity,
            fingerprint,
            message,
            Jsonb(
                {
                    "reconciliationRunId": int(cast(int, run["id"])),
                    "exchangeOrderId": int(cast(int, order["id"])),
                    "runKey": run["run_key"],
                    "observedStatus": run["observed_status"],
                }
            ),
        ),
    )


def _reconciliation_run_summary(row: Mapping[str, object]) -> Row:
    return {
        "reconciliationRunId": int(cast(int, row["id"])),
        "exchangeOrderId": int(cast(int, row["exchange_order_id"])),
        "runKey": row["run_key"],
        "status": row["status"],
        "observedStatus": row["observed_status"],
        "observedFillCount": int(cast(int, row["observed_fill_count"])),
        "completedAt": row["completed_at"],
    }


def _live_reconciliation_application_summary(row: Mapping[str, object]) -> Row:
    return {
        "liveReconciliationApplicationId": int(cast(int, row["id"])),
        "exchangeAccountId": int(cast(int, row["exchange_account_id"])),
        "orderIntentId": int(cast(int, row["order_intent_id"])),
        "exchangeOrderId": int(cast(int, row["exchange_order_id"])),
        "liveExchangeOrderBindingId": int(
            cast(int, row["live_exchange_order_binding_id"])
        ),
        "reconciliationRunId": int(cast(int, row["reconciliation_run_id"])),
        "status": "recorded",
        "source": row["source"],
        "sourceEndpoint": row["source_endpoint"],
        "observedState": row["observed_state"],
        "observedUpbitOrderUuid": row["observed_upbit_order_uuid"],
        "observedUpbitIdentifier": row["observed_upbit_identifier"],
        "appliedAt": row["applied_at"],
    }


def _observed_reconciliation_status(value: object) -> str:
    status = _non_blank(value, "observedStatus")
    allowed = {"done", "cancel", "prevented", "rejected", "outcome_unknown", "missing"}
    if status not in allowed:
        raise ValueError("observedStatus는 지원하는 대사 상태여야 한다.")
    return status


def _reconciliation_fill_payload(fill: Mapping[str, object]) -> Row:
    fill_sequence = int(cast(int, fill["fillSequence"]))
    if fill_sequence < 1:
        raise ValueError("fillSequence는 1 이상이어야 한다.")
    side = _side(fill["side"])
    occurred = _datetime(fill["occurredAt"], "occurredAt")
    knowledge = _datetime(fill["knowledgeAt"], "knowledgeAt")
    if knowledge < occurred:
        raise ValueError("knowledgeAt은 occurredAt보다 빠를 수 없다.")
    return {
        "fillSequence": fill_sequence,
        "side": side,
        "filledQuantity": _positive_decimal(fill["filledQuantity"], "filledQuantity"),
        "fillPrice": _positive_decimal(fill["fillPrice"], "fillPrice"),
        "feePaid": _non_negative_decimal(fill.get("feePaid", Decimal("0")), "feePaid"),
        "occurredAt": occurred,
        "knowledgeAt": knowledge,
        "evidence": dict(cast(Mapping[str, object], fill.get("evidence", {}))),
    }


def _reconciliation_fill_hash_payload(fill: Mapping[str, object]) -> Row:
    return {
        "fillSequence": fill["fillSequence"],
        "side": fill["side"],
        "filledQuantity": str(fill["filledQuantity"]),
        "fillPrice": str(fill["fillPrice"]),
        "feePaid": str(fill["feePaid"]),
        "occurredAt": cast(datetime, fill["occurredAt"]).isoformat(),
        "knowledgeAt": cast(datetime, fill["knowledgeAt"]).isoformat(),
        "evidence": fill["evidence"],
    }


def _same_fill(existing: Mapping[str, object], observed: Mapping[str, object]) -> bool:
    return (
        str(existing["side"]) == observed["side"]
        and _decimal(existing["filled_quantity"], "filledQuantity")
        == cast(Decimal, observed["filledQuantity"])
        and _decimal(existing["fill_price"], "fillPrice")
        == cast(Decimal, observed["fillPrice"])
        and _decimal(existing["fee_paid"], "feePaid") == cast(Decimal, observed["feePaid"])
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


def _must_be_false(value: object, field: str) -> bool:
    if value is not False:
        raise ValueError(f"{field}는 false여야 한다.")
    return False


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


def _non_negative_decimal(value: object, field: str) -> Decimal:
    decimal_value = _decimal(value, field)
    if decimal_value < 0:
        raise ValueError(f"{field}는 0 이상이어야 한다.")
    return decimal_value


def _side(value: object) -> str:
    side = _non_blank(value, "side")
    if side not in {"buy", "sell"}:
        raise ValueError("side는 buy 또는 sell이어야 한다.")
    return side


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
