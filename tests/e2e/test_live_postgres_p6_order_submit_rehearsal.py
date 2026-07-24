from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TypedDict
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_live_postgres_p6_live_order_binding import _insert_exchange_account
from test_live_postgres_reconciliation import (
    _insert_instrument,
    _insert_order_intent,
    _insert_paper_bot_fixture,
    _insert_strategy_version,
)

from goodmoneying_shared.live_order_identity import derive_upbit_live_order_identifier
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.upbit_order_submit_rehearsal import (
    build_upbit_order_submit_rehearsal,
)

pytestmark = pytest.mark.live


class RehearsalFixture(TypedDict):
    exchangeAccountId: int
    orderIntentId: int
    liveOrderIdentifierId: int
    outboxId: int
    identifier: str
    queryString: str
    queryHash: str


def test_live_postgres_P6_8_rehearsal은_주문_outbox를_실제_제출없이_검증한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_rehearsal_fixture(repository, key, now)

    with repository._connect() as connection:
        outbox = _outbox(connection, fixture["outboxId"])
        rehearsal = connection.execute(
            """
            INSERT INTO upbit_order_submit_rehearsals (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              upbit_order_outbox_id, permission_attestation_id,
              rehearsal_status, endpoint_key, http_method, request_path,
              request_payload, request_hash, query_string, query_hash,
              actual_request_sent, would_submit, can_bind_response,
              rehearsed_at, evidence, actor_id, reason, request_id, idempotency_key
            ) VALUES (
              %s,%s,%s,%s,%s,
              'passed','rest.new-order','POST','/v1/orders',
              %s,%s,%s,%s,
              false,false,false,
              %s,%s,'operator:test','P6-8 rehearsal',%s,%s
            )
            RETURNING id, query_string, query_hash
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                fixture["liveOrderIdentifierId"],
                fixture["outboxId"],
                _permission_attestation_id(outbox),
                Jsonb(outbox["request_payload"]),
                outbox["request_hash"],
                fixture["queryString"],
                fixture["queryHash"],
                now,
                Jsonb({"source": "p6-8"}),
                f"p6-8-rehearsal-{key}",
                f"p6-8-rehearsal-{key}",
            ),
        ).fetchone()
        assert rehearsal is not None
        state = connection.execute(
            """
            SELECT live.status AS live_identifier_status,
                   outbox.submit_attempt_count,
                   (SELECT count(*) FROM upbit_live_exchange_order_bindings
                    WHERE upbit_order_outbox_id = outbox.id) AS binding_count
            FROM upbit_order_outbox outbox
            JOIN live_order_identifiers live ON live.id = outbox.live_order_identifier_id
            JOIN upbit_order_submit_rehearsals rehearsal
              ON rehearsal.upbit_order_outbox_id = outbox.id
            WHERE outbox.id=%s
            """,
            (fixture["outboxId"],),
        ).fetchone()

    assert state is not None
    assert state["live_identifier_status"] == "reserved"
    assert state["submit_attempt_count"] == 0
    assert state["binding_count"] == 0
    assert rehearsal["query_hash"] == fixture["queryHash"]
    assert rehearsal["query_string"] == fixture["queryString"]


def test_live_postgres_P6_8_rehearsal은_응답_UUID와_실제_전송_flag를_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_rehearsal_fixture(repository, key, now)

    _assert_rehearsal_is_rejected(
        repository,
        fixture=fixture,
        key=f"actual-request-{key}",
        actual_request_sent=True,
        expected=psycopg.errors.CheckViolation,
    )
    _assert_rehearsal_is_rejected(
        repository,
        fixture=fixture,
        key=f"response-uuid-{key}",
        response_uuid=str(uuid4()),
        expected=psycopg.errors.CheckViolation,
    )


def test_live_postgres_P6_8_rehearsal은_outbox와_identifier_일치를_강제한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_rehearsal_fixture(repository, key, now)

    _assert_rehearsal_is_rejected(
        repository,
        fixture=fixture,
        key=f"request-hash-{key}",
        request_hash="b" * 64,
        expected=psycopg.errors.RaiseException,
    )
    _assert_rehearsal_is_rejected(
        repository,
        fixture=fixture,
        key=f"ci-actor-{key}",
        actor_id="service:worker",
        expected=psycopg.errors.CheckViolation,
    )


def test_live_postgres_P6_8_rehearsal은_live_binding_이후_재제출_리허설을_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_rehearsal_fixture(repository, key, now)

    with repository._connect() as connection:
        exchange_order = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'live',%s,'wait',%s,%s)
            RETURNING id
            """,
            (
                fixture["orderIntentId"],
                fixture["identifier"],
                now,
                Jsonb({"source": "p6-8-existing-live"}),
            ),
        ).fetchone()
        assert exchange_order is not None
        connection.execute(
            """
            INSERT INTO upbit_live_exchange_order_bindings (
              exchange_account_id, order_intent_id, exchange_order_id,
              live_order_identifier_id, upbit_order_outbox_id, upbit_order_uuid,
              upbit_identifier, source, observed_at, evidence, actor_id, reason,
              request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'order_submit_response',%s,%s,
              'operator:test','P6-8 existing live binding',%s,%s)
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                int(exchange_order["id"]),
                fixture["liveOrderIdentifierId"],
                fixture["outboxId"],
                str(uuid4()),
                fixture["identifier"],
                now,
                Jsonb({"source": "p6-8"}),
                f"p6-8-binding-{key}",
                f"p6-8-binding-{key}",
            ),
        )

    _assert_rehearsal_is_rejected(
        repository,
        fixture=fixture,
        key=f"existing-binding-{key}",
        expected=psycopg.errors.RaiseException,
    )


def _assert_rehearsal_is_rejected(
    repository: PostgresOperationsRepository,
    *,
    fixture: RehearsalFixture,
    key: str,
    expected: type[Exception],
    actual_request_sent: bool = False,
    response_uuid: str | None = None,
    request_hash: str | None = None,
    actor_id: str = "operator:test",
) -> None:
    with (
        pytest.raises(expected),
        repository._connect() as connection,
    ):
        outbox = _outbox(connection, fixture["outboxId"])
        connection.execute(
            """
            INSERT INTO upbit_order_submit_rehearsals (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              upbit_order_outbox_id, permission_attestation_id,
              rehearsal_status, endpoint_key, http_method, request_path,
              request_payload, request_hash, query_string, query_hash,
              actual_request_sent, would_submit, can_bind_response,
              response_uuid, rehearsed_at, evidence, actor_id, reason,
              request_id, idempotency_key
            ) VALUES (
              %s,%s,%s,%s,%s,
              'passed','rest.new-order','POST','/v1/orders',
              %s,%s,%s,%s,
              %s,false,false,
              %s,clock_timestamp(),%s,%s,'P6-8 rejected',
              %s,%s
            )
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                fixture["liveOrderIdentifierId"],
                fixture["outboxId"],
                _permission_attestation_id(outbox),
                Jsonb(outbox["request_payload"]),
                request_hash or outbox["request_hash"],
                fixture["queryString"],
                fixture["queryHash"],
                actual_request_sent,
                response_uuid,
                Jsonb({"source": "p6-8"}),
                actor_id,
                f"p6-8-rejected-{key}",
                f"p6-8-rejected-{key}",
            ),
        )


def _insert_rehearsal_fixture(
    repository: PostgresOperationsRepository,
    key: str,
    now: datetime,
) -> RehearsalFixture:
    exchange_account_id = _insert_exchange_account(repository, key)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    bot = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        int(bot["botInstanceId"]),
        instrument_id,
        status="approved",
    )
    identifier = derive_upbit_live_order_identifier(
        f"acct-stable-{key}",
        f"reconciliation-intent-{key}",
    )
    rehearsal = build_upbit_order_submit_rehearsal(
        {
            "market": "KRW-BTC",
            "side": "bid",
            "ord_type": "limit",
            "volume": "0.1",
            "price": "1000",
            "identifier": identifier,
            "time_in_force": "post_only",
        },
        rehearsed_at=now,
    )
    with repository._connect() as connection:
        live_identifier = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-8 live identifier')
            RETURNING id
            """,
            (
                exchange_account_id,
                order_intent_id,
                f"reconciliation-intent-{key}",
                identifier,
            ),
        ).fetchone()
        assert live_identifier is not None
        permission = connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-8 permissions',%s,%s,%s)
            RETURNING id
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-8"}),
                f"p6-8-permission-{key}",
                f"p6-8-permission-{key}",
            ),
        ).fetchone()
        assert permission is not None
        outbox = connection.execute(
            """
            INSERT INTO upbit_order_outbox (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              permission_attestation_id, status, request_payload, request_hash,
              actor_id, reason, request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,'ready',%s,%s,'operator:test','P6-8 outbox',%s,%s)
            RETURNING id
            """,
            (
                exchange_account_id,
                order_intent_id,
                int(live_identifier["id"]),
                int(permission["id"]),
                Jsonb(rehearsal.canonical_payload),
                rehearsal.request_hash,
                f"p6-8-outbox-{key}",
                f"p6-8-outbox-{key}",
            ),
        ).fetchone()
        assert outbox is not None
    return {
        "exchangeAccountId": exchange_account_id,
        "orderIntentId": order_intent_id,
        "liveOrderIdentifierId": int(live_identifier["id"]),
        "outboxId": int(outbox["id"]),
        "identifier": identifier,
        "queryString": rehearsal.query_string,
        "queryHash": rehearsal.query_hash,
    }


def _outbox(connection: psycopg.Connection, outbox_id: int) -> dict[str, object]:
    outbox = connection.execute(
        """
        SELECT permission_attestation_id, request_payload, request_hash
        FROM upbit_order_outbox
        WHERE id=%s
        """,
        (outbox_id,),
    ).fetchone()
    assert outbox is not None
    return dict(outbox)


def _permission_attestation_id(outbox: dict[str, object]) -> int:
    value = outbox["permission_attestation_id"]
    assert isinstance(value, int)
    return value


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
