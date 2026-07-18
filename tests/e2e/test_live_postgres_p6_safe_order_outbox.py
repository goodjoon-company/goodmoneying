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


def test_live_postgres_P6_6_권한_attestation은_출금_권한과_machine_actor를_거부한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 19, tzinfo=UTC)
    exchange_account_id = _insert_exchange_account(repository, key)

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,true,%s,%s,'operator:test','withdraw denied',%s,%s,%s)
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-withdraw-{key}",
                f"p6-6-withdraw-{key}",
            ),
        )

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'CI:runner','case denied',%s,%s,%s)
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-ci-case-{key}",
                f"p6-6-ci-case-{key}",
            ),
        )

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'ai:agent','machine denied',%s,%s,%s)
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-ai-{key}",
                f"p6-6-ai-{key}",
            ),
        )


def test_live_postgres_P6_6_order_outbox는_submit_attempt를_0으로_고정한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 19, tzinfo=UTC)
    exchange_account_id = _insert_exchange_account(repository, key)
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        status="approved",
    )
    identifier = derive_upbit_live_order_identifier(
        exchange_account_stable_id=f"acct-stable-{key}",
        idempotency_key=f"reconciliation-intent-{key}",
    )
    permission_id: int
    live_identifier_id: int

    with repository._connect() as connection:
        permission = connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-6 permissions',%s,%s,%s)
            RETURNING id
            """,
            (
                exchange_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-permission-{key}",
                f"p6-6-permission-{key}",
            ),
        ).fetchone()
        assert permission is not None
        permission_id = int(permission["id"])
        live_identifier = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-6 identifier')
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
        live_identifier_id = int(live_identifier["id"])
        outbox = connection.execute(
            """
            INSERT INTO upbit_order_outbox (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              permission_attestation_id, status, request_payload, request_hash,
              actor_id, reason, request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,'ready',%s,%s,'operator:test','P6-6 outbox',%s,%s)
            RETURNING submit_attempt_count
            """,
            (
                exchange_account_id,
                order_intent_id,
                live_identifier_id,
                permission_id,
                Jsonb({"identifier": identifier, "side": "bid"}),
                "a" * 64,
                f"p6-6-outbox-{key}",
                f"p6-6-outbox-{key}",
            ),
        ).fetchone()
        assert outbox is not None
        assert outbox["submit_attempt_count"] == 0

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_order_outbox (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              permission_attestation_id, status, request_payload, request_hash,
              actor_id, reason, request_id, idempotency_key, submit_attempt_count
            ) VALUES (%s,%s,%s,%s,'ready',%s,%s,'operator:test','attempt denied',%s,%s,1)
            """,
            (
                exchange_account_id,
                order_intent_id,
                live_identifier_id,
                permission_id,
                Jsonb({"identifier": identifier}),
                "b" * 64,
                f"p6-6-attempt-{key}",
                f"p6-6-attempt-{key}",
            ),
        )


def test_live_postgres_P6_6_ready_outbox는_계좌_주문_권한_일치를_강제한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 19, tzinfo=UTC)
    primary_account_id = _insert_exchange_account(repository, f"{key}-primary")
    other_account_id = _insert_exchange_account(repository, f"{key}-other")
    instrument_id = _insert_instrument(repository, key)
    strategy_version_id = _insert_strategy_version(repository, key, now)
    fixture = _insert_paper_bot_fixture(repository, key, now, strategy_version_id)
    order_intent_id = _insert_order_intent(
        repository,
        key,
        fixture["botInstanceId"],
        instrument_id,
        status="approved",
    )
    created_order_intent_id = _insert_order_intent(
        repository,
        f"{key}-created",
        fixture["botInstanceId"],
        instrument_id,
        status="created",
    )
    identifier = derive_upbit_live_order_identifier(
        exchange_account_stable_id=f"acct-stable-{key}-primary",
        idempotency_key=f"reconciliation-intent-{key}",
    )
    created_identifier = derive_upbit_live_order_identifier(
        exchange_account_stable_id=f"acct-stable-{key}-primary",
        idempotency_key=f"reconciliation-intent-{key}-created",
    )
    with repository._connect() as connection:
        permission = connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-6 permissions',%s,%s,%s)
            RETURNING id
            """,
            (
                primary_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-permission-primary-{key}",
                f"p6-6-permission-primary-{key}",
            ),
        ).fetchone()
        assert permission is not None
        other_permission = connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-6 other permissions',%s,%s,%s)
            RETURNING id
            """,
            (
                other_account_id,
                now,
                datetime(2027, 1, 1, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-permission-other-{key}",
                f"p6-6-permission-other-{key}",
            ),
        ).fetchone()
        assert other_permission is not None
        expired_permission = connection.execute(
            """
            INSERT INTO upbit_api_key_permission_attestations (
              exchange_account_id, has_order_permission, has_order_read_permission,
              has_withdraw_permission, attested_at, expires_at, actor_id, reason,
              evidence, request_id, idempotency_key
            ) VALUES (%s,true,true,false,%s,%s,'operator:test','P6-6 expired',%s,%s,%s)
            RETURNING id
            """,
            (
                primary_account_id,
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
                Jsonb({"source": "p6-6"}),
                f"p6-6-expired-permission-{key}",
                f"p6-6-expired-permission-{key}",
            ),
        ).fetchone()
        assert expired_permission is not None
        live_identifier = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-6 identifier')
            RETURNING id
            """,
            (
                primary_account_id,
                order_intent_id,
                f"reconciliation-intent-{key}",
                identifier,
            ),
        ).fetchone()
        assert live_identifier is not None
        created_live_identifier = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-6 created identifier')
            RETURNING id
            """,
            (
                primary_account_id,
                created_order_intent_id,
                f"reconciliation-intent-{key}-created",
                created_identifier,
            ),
        ).fetchone()
        assert created_live_identifier is not None

    _assert_order_outbox_is_rejected(
        repository,
        exchange_account_id=other_account_id,
        order_intent_id=order_intent_id,
        live_order_identifier_id=int(live_identifier["id"]),
        permission_attestation_id=int(permission["id"]),
        key=f"wrong-account-{key}",
        status="ready",
    )
    _assert_order_outbox_is_rejected(
        repository,
        exchange_account_id=primary_account_id,
        order_intent_id=order_intent_id,
        live_order_identifier_id=int(live_identifier["id"]),
        permission_attestation_id=int(expired_permission["id"]),
        key=f"expired-{key}",
        status="ready",
    )
    _assert_order_outbox_is_rejected(
        repository,
        exchange_account_id=primary_account_id,
        order_intent_id=order_intent_id,
        live_order_identifier_id=int(live_identifier["id"]),
        permission_attestation_id=int(other_permission["id"]),
        key=f"blocked-wrong-permission-{key}",
        status="blocked",
        blocked_reason="live_disabled",
    )
    _assert_order_outbox_is_rejected(
        repository,
        exchange_account_id=primary_account_id,
        order_intent_id=created_order_intent_id,
        live_order_identifier_id=int(created_live_identifier["id"]),
        permission_attestation_id=int(permission["id"]),
        key=f"created-intent-{key}",
        status="ready",
    )


def _assert_order_outbox_is_rejected(
    repository: PostgresOperationsRepository,
    *,
    exchange_account_id: int,
    order_intent_id: int,
    live_order_identifier_id: int,
    permission_attestation_id: int,
    key: str,
    status: str,
    blocked_reason: str | None = None,
) -> None:
    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_order_outbox (
              exchange_account_id, order_intent_id, live_order_identifier_id,
              permission_attestation_id, status, request_payload, request_hash,
              blocked_reason, actor_id, reason, request_id, idempotency_key
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'operator:test','P6-6 rejected',%s,%s)
            """,
            (
                exchange_account_id,
                order_intent_id,
                live_order_identifier_id,
                permission_attestation_id,
                status,
                Jsonb({"identifier": key}),
                "c" * 64,
                blocked_reason,
                f"p6-6-rejected-{key}",
                f"p6-6-rejected-{key}",
            ),
        )


def _insert_exchange_account(repository: PostgresOperationsRepository, key: str) -> int:
    with repository._connect() as connection:
        row = connection.execute(
            """
            INSERT INTO exchange_accounts (
              exchange, account_stable_id, label, status, created_by, reason
            ) VALUES ('upbit', %s, %s, 'live_disabled', 'operator:test', 'P6-6 account')
            RETURNING id
            """,
            (f"acct-stable-{key}", f"p6-6-{key}"),
        ).fetchone()
        assert row is not None
        return int(row["id"])


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
