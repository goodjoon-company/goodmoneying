from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
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


def test_live_postgres_P6_order_test_증적과_live_identifier를_분리한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 14, tzinfo=UTC)
    account_stable_id = f"upbit-main-{key[:10]}"
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

    with repository._connect() as connection:
        intent = connection.execute(
            """
            SELECT idempotency_key
            FROM order_intents
            WHERE id = %s
            """,
            (order_intent_id,),
        ).fetchone()
        assert intent is not None
        intent_idempotency_key = str(intent["idempotency_key"])
        identifier = derive_upbit_live_order_identifier(
            account_stable_id,
            intent_idempotency_key,
        )
        account = connection.execute(
            """
            INSERT INTO exchange_accounts (
              exchange, account_stable_id, label, created_by, reason
            ) VALUES ('upbit', %s, %s, 'operator:test', 'P6-2 account')
            RETURNING id
            """,
            (account_stable_id, f"UPBIT {key[:8]}"),
        ).fetchone()
        assert account is not None
        account_id = int(account["id"])
        test_run = connection.execute(
            """
            INSERT INTO upbit_order_test_runs (
              exchange_account_id, request_id, actor_id, reason, requested_at,
              request_parameters, response_status_code, response_uuid,
              response_identifier, response_body
            ) VALUES (%s,%s,'operator:test','P6-2 order-test',%s,%s,201,%s,%s,%s)
            RETURNING id, lookup_allowed, cancel_allowed
            """,
            (
                account_id,
                f"order-test-{key}",
                now,
                Jsonb({"market": "KRW-BTC", "side": "bid", "ord_type": "price"}),
                f"test-uuid-{key}",
                f"test-identifier-{key}",
                Jsonb({"uuid": f"test-uuid-{key}", "identifier": f"test-identifier-{key}"}),
            ),
        ).fetchone()
        assert test_run is not None
        live = connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-2 live identifier')
            RETURNING identifier
            """,
            (account_id, order_intent_id, intent_idempotency_key, identifier),
        ).fetchone()

    assert test_run["lookup_allowed"] is False
    assert test_run["cancel_allowed"] is False
    assert live is not None
    assert live["identifier"] == identifier
    test_run_id = int(test_run["id"])

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE upbit_order_test_runs SET reason = 'mutated' WHERE id = %s",
            (test_run_id,),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            "DELETE FROM upbit_order_test_runs WHERE id = %s",
            (test_run_id,),
        )

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_order_test_runs (
              exchange_account_id, request_id, actor_id, reason, requested_at,
              request_parameters, response_status_code, response_body, lookup_allowed
            ) VALUES (
              (SELECT id FROM exchange_accounts WHERE account_stable_id=%s),
              %s, 'operator:test', 'P6-2 forbidden lookup', %s, '{}'::jsonb, 201,
              '{}'::jsonb, TRUE
            )
            """,
            (account_stable_id, f"forbidden-{key}", now),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (
              (SELECT id FROM exchange_accounts WHERE account_stable_id=%s),
              %s, %s, 'test-order-identifier', 'operator:test', 'P6-2 bad live id'
            )
            """,
            (account_stable_id, order_intent_id, f"bad-{key}"),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO upbit_order_test_runs (
              exchange_account_id, request_id, actor_id, reason, requested_at,
              request_parameters, response_status_code, response_identifier,
              response_body
            ) VALUES (%s,%s,'operator:test','P6-2 live id reuse',%s,%s,201,%s,%s)
            """,
            (
                account_id,
                f"reuse-live-{key}",
                now,
                Jsonb({"market": "KRW-BTC", "side": "bid", "ord_type": "price"}),
                identifier,
                Jsonb({"identifier": identifier}),
            ),
        )

    mismatch_order_intent_id = _insert_order_intent(
        repository,
        f"{key}-mismatch",
        fixture["botInstanceId"],
        instrument_id,
        status="approved",
    )
    blocked_order_intent_id = _insert_order_intent(
        repository,
        f"{key}-blocked",
        fixture["botInstanceId"],
        instrument_id,
        status="approved",
    )
    with repository._connect() as connection:
        mismatch_intent = connection.execute(
            "SELECT idempotency_key FROM order_intents WHERE id = %s",
            (mismatch_order_intent_id,),
        ).fetchone()
        blocked_intent = connection.execute(
            "SELECT idempotency_key FROM order_intents WHERE id = %s",
            (blocked_order_intent_id,),
        ).fetchone()
    assert mismatch_intent is not None
    assert blocked_intent is not None
    mismatch_key = str(mismatch_intent["idempotency_key"])
    blocked_key = str(blocked_intent["idempotency_key"])
    mismatch_identifier = derive_upbit_live_order_identifier(
        account_stable_id,
        f"wrong-{key}",
    )
    blocked_identifier = derive_upbit_live_order_identifier(account_stable_id, blocked_key)

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-2 mismatched idempotency')
            """,
            (account_id, mismatch_order_intent_id, f"wrong-{key}", mismatch_identifier),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-2 mismatched identifier')
            """,
            (account_id, mismatch_order_intent_id, mismatch_key, mismatch_identifier),
        )

    with repository._connect() as connection:
        connection.execute(
            """
            INSERT INTO upbit_order_test_runs (
              exchange_account_id, request_id, actor_id, reason, requested_at,
              request_parameters, response_status_code, response_identifier,
              response_body
            ) VALUES (%s,%s,'operator:test','P6-2 blocked before live',%s,%s,201,%s,%s)
            """,
            (
                account_id,
                f"blocked-before-live-{key}",
                now,
                Jsonb({"market": "KRW-BTC", "side": "bid", "ord_type": "price"}),
                blocked_identifier,
                Jsonb({"identifier": blocked_identifier}),
            ),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO live_order_identifiers (
              exchange_account_id, order_intent_id, idempotency_key, identifier,
              created_by, reason
            ) VALUES (%s,%s,%s,%s,'operator:test','P6-2 blocked by test response')
            """,
            (account_id, blocked_order_intent_id, blocked_key, blocked_identifier),
        )


def test_live_postgres_P6_order_identifier_registry가_동시_재사용을_차단한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    key = uuid4().hex
    now = datetime(2026, 7, 18, 14, tzinfo=UTC)
    account_stable_id = f"upbit-main-{key[:10]}"
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

    with repository._connect() as connection:
        intent = connection.execute(
            "SELECT idempotency_key FROM order_intents WHERE id = %s",
            (order_intent_id,),
        ).fetchone()
        assert intent is not None
        intent_idempotency_key = str(intent["idempotency_key"])
        identifier = derive_upbit_live_order_identifier(
            account_stable_id,
            intent_idempotency_key,
        )
        account = connection.execute(
            """
            INSERT INTO exchange_accounts (
              exchange, account_stable_id, label, created_by, reason
            ) VALUES ('upbit', %s, %s, 'operator:test', 'P6-2 concurrent account')
            RETURNING id
            """,
            (account_stable_id, f"UPBIT {key[:8]}"),
        ).fetchone()
        assert account is not None
        account_id = int(account["id"])

    barrier = Barrier(2)

    def insert_live_identifier() -> str:
        try:
            with repository._connect() as connection:
                connection.execute("SET lock_timeout TO '5000ms'")
                barrier.wait(timeout=10)
                connection.execute(
                    """
                    INSERT INTO live_order_identifiers (
                      exchange_account_id, order_intent_id, idempotency_key, identifier,
                      created_by, reason
                    ) VALUES (%s,%s,%s,%s,'operator:test','P6-2 concurrent live')
                    """,
                    (account_id, order_intent_id, intent_idempotency_key, identifier),
                )
            return "live:ok"
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation) as exc:
            return f"live:blocked:{exc.sqlstate}"

    def insert_order_test_response() -> str:
        try:
            with repository._connect() as connection:
                connection.execute("SET lock_timeout TO '5000ms'")
                barrier.wait(timeout=10)
                connection.execute(
                    """
                    INSERT INTO upbit_order_test_runs (
                      exchange_account_id, request_id, actor_id, reason, requested_at,
                      request_parameters, response_status_code, response_identifier,
                      response_body
                    ) VALUES (%s,%s,'operator:test','P6-2 concurrent order-test',%s,%s,201,%s,%s)
                    """,
                    (
                        account_id,
                        f"concurrent-order-test-{key}",
                        now,
                        Jsonb({"market": "KRW-BTC", "side": "bid", "ord_type": "price"}),
                        identifier,
                        Jsonb({"identifier": identifier}),
                    ),
                )
            return "test:ok"
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation) as exc:
            return f"test:blocked:{exc.sqlstate}"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda task: task(),
                (insert_live_identifier, insert_order_test_response),
            )
        )

    assert sum(outcome.endswith(":ok") for outcome in outcomes) == 1
    assert sum(":blocked:" in outcome for outcome in outcomes) == 1

    with repository._connect() as connection:
        reservation = connection.execute(
            """
            SELECT count(*) AS count
            FROM upbit_order_identifier_reservations
            WHERE exchange_account_id = %s
              AND identifier = %s
            """,
            (account_id, identifier),
        ).fetchone()
    assert reservation is not None
    assert reservation["count"] == 1


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
