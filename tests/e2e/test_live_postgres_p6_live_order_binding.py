from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb
from test_live_postgres_reconciliation import (
    _insert_instrument,
    _insert_order_intent,
    _insert_paper_bot_fixture,
    _insert_strategy_version,
)

from goodmoneying_shared.live_order_identity import derive_upbit_live_order_identifier
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_P6_7_live_order_uuid와_identifier를_exchange_order에_결합한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)

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
                Jsonb({"source": "p6-7-live"}),
            ),
        ).fetchone()
        assert exchange_order is not None
        binding = connection.execute(
            """
            INSERT INTO upbit_live_exchange_order_bindings (
              exchange_account_id, order_intent_id, exchange_order_id,
              live_order_identifier_id, upbit_order_outbox_id, upbit_order_uuid,
              upbit_identifier, source, observed_at, evidence, actor_id, reason,
              request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'order_submit_response',%s,%s,
              'operator:test','P6-7 binding',%s,%s)
            RETURNING id
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
                Jsonb({"source": "p6-7"}),
                f"p6-7-binding-{key}",
                f"p6-7-binding-{key}",
            ),
        ).fetchone()
        assert binding is not None
        row = connection.execute(
            """
            SELECT live.status AS live_identifier_status,
                   binding.upbit_order_uuid,
                   binding.upbit_identifier
            FROM upbit_live_exchange_order_bindings binding
            JOIN live_order_identifiers live ON live.id = binding.live_order_identifier_id
            WHERE binding.id=%s
            """,
            (binding["id"],),
        ).fetchone()

    assert row is not None
    assert row["live_identifier_status"] == "submitted"
    assert row["upbit_identifier"] == fixture["identifier"]


def test_live_postgres_P6_7_live_exchange_order는_binding_없이_commit될_수_없다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'live',%s,'wait',%s,%s)
            """,
            (
                fixture["orderIntentId"],
                fixture["identifier"],
                now,
                Jsonb({"source": "p6-7-unbound-live"}),
            ),
        )


def test_live_postgres_P6_7_binding은_계좌_의도_identifier_일치를_강제한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)
    other_identifier = derive_upbit_live_order_identifier(
        f"acct-stable-{key}",
        f"reconciliation-intent-{key}-other",
    )

    _assert_binding_is_rejected(
        repository,
        fixture=fixture,
        key=f"wrong-identifier-{key}",
        upbit_identifier=other_identifier,
        expected=psycopg.errors.RaiseException,
    )

    with repository._connect() as connection:
        paper_order = connection.execute(
            """
            INSERT INTO exchange_orders (
              order_intent_id, execution_mode, simulated_order_key,
              status, submitted_at, raw_payload
            ) VALUES (%s,'paper',%s,'done',%s,%s)
            RETURNING id
            """,
            (
                fixture["orderIntentId"],
                f"paper-{key}",
                now,
                Jsonb({"source": "p6-7-paper"}),
            ),
        ).fetchone()
        assert paper_order is not None

    _assert_binding_is_rejected(
        repository,
        fixture={**fixture, "exchangeOrderId": int(paper_order["id"])},
        key=f"paper-order-{key}",
        expected=psycopg.errors.RaiseException,
    )


def test_live_postgres_P6_7_binding은_reserved_live_identifier만_허용한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)

    with repository._connect() as connection:
        connection.execute(
            "UPDATE live_order_identifiers SET status='retired' WHERE id=%s",
            (fixture["liveOrderIdentifierId"],),
        )

    _assert_binding_is_rejected(
        repository,
        fixture=fixture,
        key=f"retired-live-identifier-{key}",
        expected=psycopg.errors.RaiseException,
    )


def test_live_postgres_P6_7_binding_이후_live_exchange_order_key_변경을_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)
    other_identifier = derive_upbit_live_order_identifier(
        f"acct-stable-{key}",
        f"reconciliation-intent-{key}-mutated",
    )

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
                Jsonb({"source": "p6-7-live-mutation"}),
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
              'operator:test','P6-7 binding mutation guard',%s,%s)
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
                Jsonb({"source": "p6-7"}),
                f"p6-7-binding-mutation-{key}",
                f"p6-7-binding-mutation-{key}",
            ),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE exchange_orders SET simulated_order_key=%s WHERE id=%s",
            (other_identifier, int(exchange_order["id"])),
        )


def test_live_postgres_P6_7_binding은_order_test_응답과_machine_actor를_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 18, tzinfo=UTC)
    fixture = _insert_live_binding_fixture(repository, key, now)
    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO upbit_order_test_runs (
              exchange_account_id, request_id, actor_id, reason, requested_at,
              request_parameters, response_status_code, response_uuid, response_body
            ) VALUES (%s,%s,'operator:test','P6-7 order-test collision',%s,%s,201,%s,%s)
            """,
            (
                fixture["exchangeAccountId"],
                f"p6-7-order-test-{key}",
                now,
                Jsonb({"market": "KRW-BTC"}),
                "8f8f1f33-1111-4444-8888-123456789abc",
                Jsonb({"uuid": "8f8f1f33-1111-4444-8888-123456789abc"}),
            ),
        )

    _assert_binding_is_rejected(
        repository,
        fixture=fixture,
        key=f"order-test-uuid-{key}",
        upbit_order_uuid="8f8f1f33-1111-4444-8888-123456789abc",
        expected=psycopg.errors.RaiseException,
    )
    _assert_binding_is_rejected(
        repository,
        fixture=fixture,
        key=f"ci-actor-{key}",
        actor_id="CI:runner",
        expected=psycopg.errors.CheckViolation,
    )


def _insert_live_binding_fixture(
    repository: PostgresOperationsRepository,
    key: str,
    now: datetime,
) -> dict[str, int | str]:
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
    with repository._connect() as connection:
        live_identifier = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-7 live identifier')
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
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-7 permissions',%s,%s,%s)
            RETURNING id
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-7"}),
                f"p6-7-permission-{key}",
                f"p6-7-permission-{key}",
            ),
        ).fetchone()
        assert permission is not None
        outbox = connection.execute(
            """
            INSERT INTO upbit_order_outbox (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              permission_attestation_id, status, request_payload, request_hash,
              actor_id, reason, request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,'ready',%s,%s,'operator:test','P6-7 outbox',%s,%s)
            RETURNING id
            """,
            (
                exchange_account_id,
                order_intent_id,
                int(live_identifier["id"]),
                int(permission["id"]),
                Jsonb({"identifier": identifier}),
                "d" * 64,
                f"p6-7-outbox-{key}",
                f"p6-7-outbox-{key}",
            ),
        ).fetchone()
        assert outbox is not None
    return {
        "exchangeAccountId": exchange_account_id,
        "orderIntentId": order_intent_id,
        "liveOrderIdentifierId": int(live_identifier["id"]),
        "outboxId": int(outbox["id"]),
        "identifier": identifier,
    }


def _assert_binding_is_rejected(
    repository: PostgresOperationsRepository,
    *,
    fixture: dict[str, int | str],
    key: str,
    expected: type[Exception],
    upbit_order_uuid: str | None = None,
    upbit_identifier: str | None = None,
    actor_id: str = "operator:test",
) -> None:
    with (
        pytest.raises(expected),
        repository._connect() as connection,
    ):
        if "exchangeOrderId" in fixture:
            exchange_order_id = fixture["exchangeOrderId"]
        else:
            exchange_order = connection.execute(
                """
                INSERT INTO exchange_orders (
                  order_intent_id, execution_mode, simulated_order_key,
                  status, submitted_at, raw_payload
                ) VALUES (%s,'live',%s,'wait',clock_timestamp(),%s)
                RETURNING id
                """,
                (
                    fixture["orderIntentId"],
                    upbit_identifier or fixture["identifier"],
                    Jsonb({"source": "p6-7-rejected-live"}),
                ),
            ).fetchone()
            assert exchange_order is not None
            exchange_order_id = int(exchange_order["id"])
        connection.execute(
            """
            INSERT INTO upbit_live_exchange_order_bindings (
              exchange_account_id, order_intent_id, exchange_order_id,
              live_order_identifier_id, upbit_order_outbox_id, upbit_order_uuid,
              upbit_identifier, source, observed_at, evidence, actor_id, reason,
              request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,'rest_order_snapshot',clock_timestamp(),%s,
              %s,'P6-7 rejected',%s,%s)
            """,
            (
                fixture["exchangeAccountId"],
                fixture["orderIntentId"],
                exchange_order_id,
                fixture["liveOrderIdentifierId"],
                fixture["outboxId"],
                upbit_order_uuid or str(uuid4()),
                upbit_identifier or fixture["identifier"],
                Jsonb({"source": "p6-7"}),
                actor_id,
                f"p6-7-rejected-{key}",
                f"p6-7-rejected-{key}",
            ),
        )


def _insert_exchange_account(repository: PostgresOperationsRepository, key: str) -> int:
    with repository._connect() as connection:
        account = connection.execute(
            """
            INSERT INTO exchange_accounts (
              exchange, account_stable_id, label, status, created_by, reason
            ) VALUES ('upbit', %s, %s, 'live_disabled', 'operator:test', 'P6-7 account')
            RETURNING id
            """,
            (f"acct-stable-{key}", f"p6-7-{key}"),
        ).fetchone()
        assert account is not None
        return int(account["id"])


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
