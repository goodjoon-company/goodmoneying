from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

import psycopg
import pytest

from goodmoneying_shared.live_capability import (
    evaluate_live_capability,
    fetch_global_live_capability_record,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_live_postgres_P6_live_capability는_폐쇄형_gate를_강제한다() -> None:
    repository = PostgresOperationsRepository(_database_url())
    now = datetime(2026, 7, 18, 15, tzinfo=UTC)
    sha = "a" * 40

    with repository._connect() as connection:
        assert (
            evaluate_live_capability(
                fetch_global_live_capability_record(connection),
                deployment_sha=sha,
                now=now,
            ).reason
            == "authority_missing"
        )

        row = connection.execute(
            """
            INSERT INTO trading_capabilities (
              scope_type, scope_key, state, deployment_sha, approved_at, expires_at,
              actor_id, reason, evidence, request_id, idempotency_key
            ) VALUES (
              'global', 'global', 'live_enabled', %s, %s, %s,
              'operator:goodjoon', 'P6-3 live readiness gate test', '{}'::jsonb,
              'p6-3-live-enable', 'p6-3-live-enable'
            )
            RETURNING id
            """,
            (sha, now, now + timedelta(minutes=5)),
        ).fetchone()
        assert row is not None

        enabled = evaluate_live_capability(
            fetch_global_live_capability_record(connection),
            deployment_sha=sha,
            now=now,
        )
        assert enabled.state == "live_enabled"
        assert (
            evaluate_live_capability(
                fetch_global_live_capability_record(connection),
                deployment_sha="b" * 40,
                now=now,
            ).reason
            == "deployment_sha_mismatch"
        )
        assert (
            evaluate_live_capability(
                fetch_global_live_capability_record(connection),
                deployment_sha=sha,
                now=now + timedelta(minutes=6),
            ).reason
            == "approval_expired"
        )

        connection.execute(
            """
            INSERT INTO trading_capabilities (
              scope_type, scope_key, state, deployment_sha, approved_at, expires_at,
              actor_id, reason, evidence, request_id, idempotency_key
            ) VALUES (
              'global', 'global', 'live_disabled', %s, %s, %s,
              'operator:goodjoon', 'operator disabled live after test', '{}'::jsonb,
              'p6-3-live-disable', 'p6-3-live-disable'
            )
            """,
            (sha, now, now + timedelta(minutes=5)),
        )
        explicitly_disabled = evaluate_live_capability(
            fetch_global_live_capability_record(connection),
            deployment_sha=sha,
            now=now,
        )
        assert explicitly_disabled.state == "live_disabled"
        assert explicitly_disabled.reason == "explicitly_disabled"

    _assert_actor_prefix_is_rejected("ci")
    _assert_actor_prefix_is_rejected("ai")
    _assert_actor_prefix_is_rejected("service")

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            "DELETE FROM trading_capabilities WHERE request_id = 'p6-3-live-enable'",
        )

    with (
        pytest.raises(psycopg.errors.RaiseException),
        repository._connect() as connection,
    ):
        connection.execute(
            "UPDATE trading_capabilities SET reason = 'mutated' WHERE deployment_sha = %s",
            (sha,),
        )


def _assert_actor_prefix_is_rejected(prefix: Literal["ci", "ai", "service"]) -> None:
    repository = PostgresOperationsRepository(_database_url())
    now = datetime(2026, 7, 18, 15, tzinfo=UTC)
    sha = "a" * 40

    with (
        pytest.raises(psycopg.errors.CheckViolation),
        repository._connect() as connection,
    ):
        connection.execute(
            """
            INSERT INTO trading_capabilities (
              scope_type, scope_key, state, deployment_sha, approved_at, expires_at,
              actor_id, reason, evidence, request_id, idempotency_key
            ) VALUES (
              'global', 'global', 'live_enabled', %s, %s, %s,
              %s, 'machine actor must not enable live', '{}'::jsonb,
              %s, %s
            )
            """,
            (
                sha,
                now,
                now + timedelta(minutes=5),
                f"{prefix}:automation",
                f"p6-3-{prefix}-rejected",
                f"p6-3-{prefix}-rejected",
            ),
        )


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]
