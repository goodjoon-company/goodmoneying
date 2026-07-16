from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from goodmoneying_shared.data_foundation import (
    MarketCatalogItem,
    MarketCollectionPolicySettings,
)
from goodmoneying_shared.data_foundation_repository import (
    PostgresDataFoundationRepository,
)
from goodmoneying_shared.models import FetchEvidence
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository

pytestmark = pytest.mark.live


def test_market_sync_is_idempotent_and_creates_default_krw_work() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, tzinfo=UTC)
    catalog = [
        _market("KRW-BTC", "비트코인"),
        _market("KRW-ETH", "이더리움"),
        _market("USDT-BTC", "비트코인"),
    ]

    first = repository.sync_market_catalog(catalog, observed_at=observed_at)
    second = repository.sync_market_catalog(
        catalog,
        observed_at=observed_at + timedelta(minutes=5),
    )
    overview = repository.overview()

    assert first.market_count == 3
    assert first.new_history_count == 3
    assert first.default_target_count == 8
    assert first.created_backfill_job_count == 2
    assert second.new_history_count == 0
    assert second.default_target_count == 8
    assert second.created_backfill_job_count == 0
    assert overview.market_count == 3
    assert overview.krw_market_count == 2
    assert overview.active_target_count == 8
    assert overview.pending_backfill_job_count == 2
    assert overview.policy_start_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert overview.coverage_counts == {
        "available": 0,
        "no_trade": 0,
        "missing": 0,
        "unavailable": 6,
        "unverified": 2,
    }
    btc = next(item for item in overview.markets if item.market_code == "KRW-BTC")
    assert btc.coverage_counts["unavailable"] == 3
    assert btc.coverage_counts["unverified"] == 1


def test_market_state_change_closes_previous_point_in_time_revision() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    repository = PostgresDataFoundationRepository(os.environ["GOODMONEYING_DATABASE_URL"])
    changed_at = datetime(2026, 7, 17, 1, tzinfo=UTC)

    result = repository.sync_market_catalog(
        [
            MarketCatalogItem(
                market_code="KRW-BTC",
                korean_name="비트코인",
                english_name="Bitcoin",
                market_warning="CAUTION",
                tradable=False,
            ),
            _market("KRW-ETH", "이더리움"),
            _market("USDT-BTC", "비트코인"),
        ],
        observed_at=changed_at,
    )
    history = repository.market_history("KRW-BTC")

    assert result.new_history_count == 1
    assert len(history) == 2
    assert history[0].valid_to == changed_at
    assert history[1].valid_from == changed_at
    assert history[1].market_warning == "CAUTION"
    assert history[1].trading_status == "inactive"


def test_explicit_exclusion_survives_catalog_resync() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    repository = PostgresDataFoundationRepository(os.environ["GOODMONEYING_DATABASE_URL"])
    observed_at = datetime(2026, 7, 17, 2, tzinfo=UTC)

    repository.exclude_market(
        "KRW-ETH",
        actor="operator:e2e",
        reason="E2E 제외 유지 검증",
        changed_at=observed_at,
    )
    repository.sync_market_catalog(
        [
            _market("KRW-BTC", "비트코인"),
            _market("KRW-ETH", "이더리움"),
            _market("USDT-BTC", "비트코인"),
        ],
        observed_at=observed_at + timedelta(minutes=5),
    )

    eth = next(item for item in repository.list_markets() if item.market_code == "KRW-ETH")
    assert eth.target_status == "excluded"
    assert eth.active_data_type_count == 0


def test_job_claim_uses_lease_and_recovers_only_after_expiry() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    repository = PostgresDataFoundationRepository(os.environ["GOODMONEYING_DATABASE_URL"])
    now = datetime(2026, 7, 17, 3, tzinfo=UTC)

    first = repository.claim_backfill_job(
        worker_id="worker-a",
        now=now,
        lease_seconds=30,
    )
    second = repository.claim_backfill_job(
        worker_id="worker-b",
        now=now,
        lease_seconds=30,
    )
    none_left = repository.claim_backfill_job(
        worker_id="worker-c",
        now=now,
        lease_seconds=30,
    )
    reclaimed = repository.claim_backfill_job(
        worker_id="worker-c",
        now=now + timedelta(seconds=31),
        lease_seconds=30,
    )

    assert first is not None
    assert second is None
    assert none_left is None
    assert reclaimed is not None
    assert reclaimed.id == first.id
    assert reclaimed.lease_owner == "worker-c"
    assert reclaimed.attempt_count == 2


def test_subscription_apply_updates_only_the_exact_planned_generation() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    operations_repository = PostgresOperationsRepository(database_url)
    desires = operations_repository.load_collection_subscription_desires()
    assert desires
    target = desires[0]

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE collection_subscription_desires
            SET generation = generation + 1, applied_generation = NULL
            WHERE target_spec_id = %s
            """,
            (target.target_spec_id,),
        )

    operations_repository.mark_collection_subscription_desires_applied(
        ((target.target_spec_id, target.generation),),
        connection_id="stale-connection",
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        stale_result = connection.execute(
            """
            SELECT applied_generation
            FROM collection_subscription_desires
            WHERE target_spec_id = %s
            """,
            (target.target_spec_id,),
        ).fetchone()
    assert stale_result is not None
    assert stale_result[0] is None

    current = next(
        desire
        for desire in operations_repository.load_collection_subscription_desires()
        if desire.target_spec_id == target.target_spec_id
    )
    operations_repository.mark_collection_subscription_desires_applied(
        ((current.target_spec_id, current.generation),),
        connection_id="current-connection",
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        current_result = connection.execute(
            """
            SELECT applied_generation, connection_id
            FROM collection_subscription_desires
            WHERE target_spec_id = %s
            """,
            (target.target_spec_id,),
        ).fetchone()
    assert current_result == (current.generation, "current-connection")


def test_market_policy_update_is_idempotent_and_advances_exact_realtime_generations() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    changed_at = datetime(2026, 7, 17, 4, tzinfo=UTC)
    market_code = "KRW-POLICY-REBALANCE"
    repository.sync_market_catalog(
        [_market(market_code, "정책재조정")],
        observed_at=changed_at - timedelta(minutes=1),
    )
    policy = MarketCollectionPolicySettings(
        start_at=datetime(2025, 1, 1, tzinfo=UTC),
        data_types=("source_candle", "trade_event"),
        candle_unit="1m",
        retention_days=365,
        priority=321,
        continuous=False,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        baseline_generations: dict[str, int] = dict(
            connection.execute(
                """
                SELECT spec.data_type, desire.generation
                FROM collection_target_specs spec
                JOIN collection_subscription_desires desire
                  ON desire.target_spec_id = spec.id
                JOIN markets market ON market.id = spec.market_id
                WHERE market.market_code = %s
                """,
                (market_code,),
            ).fetchall()
        )

    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="P1 거래쌍별 정책 변경",
        changed_at=changed_at,
        policy=policy,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        first_desires = connection.execute(
            """
            SELECT spec.data_type, desire.desired_state, desire.generation
            FROM collection_target_specs spec
            JOIN collection_subscription_desires desire
              ON desire.target_spec_id = spec.id
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = %s
            ORDER BY spec.data_type
            """,
            (market_code,),
        ).fetchall()
        first_job_count = connection.execute(
            """
            SELECT count(*)
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.idempotency_key LIKE 'p1:policy:%%'
            """,
            (market_code,),
        ).fetchone()
        policy_job = connection.execute(
            """
            SELECT job.status, target.status, job.target_start_at, job.target_end_at,
                   job.priority, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.idempotency_key LIKE 'p1:policy:%%'
            """,
            (market_code,),
        ).fetchone()
        superseded_default = connection.execute(
            """
            SELECT job.status, target.status, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.idempotency_key LIKE 'p1:default:%%'
            ORDER BY job.id
            LIMIT 1
            """,
            (market_code,),
        ).fetchone()

    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="동일 정책 재요청",
        changed_at=changed_at + timedelta(minutes=1),
        policy=policy,
    )
    repository.sync_market_catalog(
        [_market(market_code, "정책재조정")],
        observed_at=changed_at + timedelta(minutes=2),
    )
    overview = repository.overview()
    btc = next(item for item in overview.markets if item.market_code == market_code)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        second_desires = connection.execute(
            """
            SELECT spec.data_type, desire.desired_state, desire.generation
            FROM collection_target_specs spec
            JOIN collection_subscription_desires desire
              ON desire.target_spec_id = spec.id
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = %s
            ORDER BY spec.data_type
            """,
            (market_code,),
        ).fetchall()
        second_job_count = connection.execute(
            """
            SELECT count(*)
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.idempotency_key LIKE 'p1:policy:%%'
            """,
            (market_code,),
        ).fetchone()
        source_spec_count = connection.execute(
            """
            SELECT count(*)
            FROM collection_target_specs spec
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = %s
              AND spec.data_type = 'source_candle'
            """,
            (market_code,),
        ).fetchone()

    assert btc.collection_policy == policy
    assert [(item[0], item[1]) for item in first_desires] == [
        ("orderbook_snapshot", "unsubscribed"),
        ("source_candle", "unsubscribed"),
        ("ticker_snapshot", "unsubscribed"),
        ("trade_event", "unsubscribed"),
    ]
    assert {data_type: generation for data_type, _state, generation in first_desires} == {
        data_type: baseline_generations[data_type] + 1 for data_type in baseline_generations
    }
    assert second_desires == first_desires
    assert first_job_count == (1,)
    assert second_job_count == first_job_count
    assert policy_job == (
        "pending",
        "pending",
        policy.start_at,
        changed_at,
        policy.priority,
        None,
        None,
    )
    assert superseded_default == ("cancelled", "stopped", None, None)
    assert source_spec_count == (1,)


def test_policy_without_source_candle_does_not_create_backfill_job() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    changed_at = datetime(2026, 7, 17, 5, tzinfo=UTC)
    market_code = "KRW-POLICY-NO-SOURCE"
    repository.sync_market_catalog(
        [_market(market_code, "원천캔들미선택")],
        observed_at=changed_at - timedelta(minutes=1),
    )

    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="실시간 체결만 수집",
        changed_at=changed_at,
        policy=MarketCollectionPolicySettings(
            start_at=datetime(2025, 1, 1, tzinfo=UTC),
            data_types=("trade_event",),
            candle_unit="1m",
            retention_days=30,
            priority=200,
            continuous=True,
        ),
    )

    eth = next(item for item in repository.list_markets() if item.market_code == market_code)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        policy_backfills = connection.execute(
            """
            SELECT count(*)
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.idempotency_key LIKE 'p1:policy:%%'
            """,
            (market_code,),
        ).fetchone()
        superseded_source_work = connection.execute(
            """
            SELECT job.status, target.status, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.data_type = 'source_candle'
            ORDER BY job.id
            """,
            (market_code,),
        ).fetchall()

    assert eth.collection_policy is not None
    assert eth.collection_policy.data_types == ("trade_event",)
    assert policy_backfills == (0,)
    assert superseded_source_work
    assert all(row == ("cancelled", "stopped", None, None) for row in superseded_source_work)

    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="동일 원천 정책 재선택",
        changed_at=changed_at + timedelta(minutes=1),
        policy=MarketCollectionPolicySettings(
            start_at=datetime(2025, 1, 1, tzinfo=UTC),
            data_types=("source_candle",),
            candle_unit="1m",
            retention_days=30,
            priority=200,
            continuous=True,
        ),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        reselected = connection.execute(
            """
            SELECT job.status, target.status, job.plan ->> 'planId'
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE target.instrument_id = (
              SELECT id FROM instruments WHERE market_code = %s
            ) AND job.idempotency_key LIKE 'p1:policy:%%'
            ORDER BY job.id DESC LIMIT 1
            """,
            (market_code,),
        ).fetchone()
    assert reselected is not None
    assert reselected[0:2] == ("pending", "pending")
    assert str(reselected[2]).startswith("p1:policy:")


def test_policy_rebalance_stops_manual_target_without_invalidating_other_target_lease() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    operations = PostgresOperationsRepository(database_url)
    changed_at = datetime(2026, 7, 17, 5, 4, tzinfo=UTC)
    market_a = "KRW-POLICY-MANUAL-A"
    market_b = "KRW-POLICY-MANUAL-B"
    repository.sync_market_catalog(
        [_market(market_a, "수동작업A"), _market(market_b, "수동작업B")],
        observed_at=changed_at - timedelta(minutes=2),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        instruments = connection.execute(
            "SELECT id, market_code FROM instruments WHERE market_code IN (%s, %s)",
            (market_a, market_b),
        ).fetchall()
        instrument_ids = {str(row[1]): int(row[0]) for row in instruments}
        connection.execute(
            """
            UPDATE backfill_jobs SET status = 'cancelled'
            WHERE EXISTS (
              SELECT 1 FROM backfill_job_targets target
              WHERE target.backfill_job_id = backfill_jobs.id
                AND target.instrument_id = ANY(%s)
            )
            """,
            (list(instrument_ids.values()),),
        )
        manual_job = connection.execute(
            """
            INSERT INTO backfill_jobs (
              status, data_type, plan, target_start_at, target_end_at,
              estimated_request_count, estimated_row_count, estimated_storage_bytes,
              restart_mode, created_by, idempotency_key, priority
            ) VALUES (
              'pending', 'source_candle', %s, %s, %s, 1, 2, 512,
              'safe_restart', 'operator:e2e', 'manual:multi-target:policy-rebalance', 10
            ) RETURNING id
            """,
            (
                Jsonb({"targets": list(instrument_ids.values())}),
                changed_at - timedelta(days=1),
                changed_at,
            ),
        ).fetchone()
        assert manual_job is not None
        manual_job_id = int(manual_job[0])
        for instrument_id in instrument_ids.values():
            connection.execute(
                """
                INSERT INTO backfill_job_targets (backfill_job_id, instrument_id, status)
                VALUES (%s, %s, 'pending')
                """,
                (manual_job_id, instrument_id),
            )

    claimed = operations.claim_next_backfill_job()
    assert claimed is not None and claimed.id == manual_job_id
    repository.set_market_target_state(
        market_a,
        state="active",
        actor="operator:e2e",
        reason="정책 원자 재조정",
        changed_at=changed_at,
        policy=MarketCollectionPolicySettings(
            start_at=changed_at - timedelta(hours=12),
            data_types=("source_candle",),
            candle_unit="1m",
            retention_days=30,
            priority=300,
            continuous=True,
        ),
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        states = connection.execute(
            """
            SELECT target.instrument_id, target.status, job.status,
                   job.lease_owner IS NOT NULL, job.lease_expires_at IS NOT NULL
            FROM backfill_job_targets target
            JOIN backfill_jobs job ON job.id = target.backfill_job_id
            WHERE job.id = %s ORDER BY target.instrument_id
            """,
            (manual_job_id,),
        ).fetchall()
        policy_job_count = connection.execute(
            """
            SELECT count(*) FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE target.instrument_id = %s AND job.idempotency_key LIKE 'p1:policy:%%'
            """,
            (instrument_ids[market_a],),
        ).fetchone()

    state_by_instrument = {int(row[0]): tuple(row[1:]) for row in states}
    assert state_by_instrument[instrument_ids[market_a]] == (
        "stopped",
        "running",
        True,
        True,
    )
    assert state_by_instrument[instrument_ids[market_b]] == (
        "pending",
        "running",
        True,
        True,
    )
    assert policy_job_count == (1,)
    with pytest.raises(RuntimeError, match="대상"):
        operations.record_backfill_target_progress(
            manual_job_id, instrument_ids[market_a], 1, 1, 1, changed_at
        )
    operations.mark_backfill_target(
        manual_job_id,
        instrument_ids[market_b],
        status="succeeded",
        last_completed_at=changed_at,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        terminal = connection.execute(
            """
            SELECT status, lease_owner, lease_expires_at
            FROM backfill_jobs WHERE id = %s
            """,
            (manual_job_id,),
        ).fetchone()
    assert terminal == ("succeeded", None, None)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        reclaimable = connection.execute(
            """
            SELECT count(*) FROM backfill_jobs
            WHERE id = %s AND status IN ('pending', 'retry_wait', 'running')
            """,
            (manual_job_id,),
        ).fetchone()
    assert reclaimable == (0,)


def test_market_change_command_is_idempotent_under_concurrent_retries() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    changed_at = datetime(2026, 7, 17, 5, 4, 30, tzinfo=UTC)
    market_code = "KRW-CONCURRENT-IDEMPOTENCY"
    repository.sync_market_catalog(
        [_market(market_code, "동시멱등")], observed_at=changed_at - timedelta(minutes=1)
    )

    def update_once(_attempt: int) -> datetime:
        return repository.set_market_target_state(
            market_code,
            state="paused",
            actor="operator:e2e",
            reason="동일 명령 동시 재전송",
            changed_at=changed_at,
            request_id="request-concurrent-idempotency",
            idempotency_key="command-concurrent-idempotency",
            requested_at=changed_at,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(update_once, range(8)))

    assert results == [changed_at] * 8
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        command_count = connection.execute(
            """
            SELECT count(*) FROM command_idempotency_records
            WHERE scope = 'market_target_state'
              AND idempotency_key = 'command-concurrent-idempotency'
            """
        ).fetchone()
        audit_count = connection.execute(
            """
            SELECT count(*) FROM audit_logs
            WHERE target_type = 'market' AND target_id = %s
              AND action = 'market_target_state_changed'
            """,
            (market_code,),
        ).fetchone()
    assert command_count == (1,)
    assert audit_count == (1,)


def test_market_catalog_manifest_preserves_raw_response_and_status_history_link() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 5, tzinfo=UTC)
    raw_payload = [
        {
            "market": "KRW-MANIFEST-RAW",
            "korean_name": "원문증적",
            "english_name": "Raw Evidence",
            "market_warning": "NONE",
            "market_event": {
                "trading_suspended": False,
                "withdrawal_suspended": True,
            },
            "future_field": {"preserved": True},
        }
    ]
    evidence = FetchEvidence(
        endpoint="/v1/market/all",
        request_parameters={"is_details": "true"},
        requested_at=observed_at - timedelta(milliseconds=20),
        responded_at=observed_at - timedelta(milliseconds=5),
        response_status=200,
        response_payload=raw_payload,
    )

    repository.sync_market_catalog(
        [_market("KRW-MANIFEST-RAW", "원문증적")],
        observed_at=observed_at,
        fetch_evidence=evidence,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        row = connection.execute(
            """
            SELECT manifest.endpoint, manifest.request_parameters,
                   manifest.requested_at, manifest.responded_at,
                   manifest.response_status, manifest.response_payload,
                   manifest.response_checksum, manifest.collector_version,
                   manifest.schema_version, manifest.outcome,
                   history.fetch_manifest_id = manifest.id
            FROM fetch_manifests manifest
            JOIN market_status_history history
              ON history.fetch_manifest_id = manifest.id
            JOIN markets market ON market.id = history.market_id
            WHERE market.market_code = 'KRW-MANIFEST-RAW'
            """
        ).fetchone()

    assert row is not None
    assert row[:6] == (
        "/v1/market/all",
        {"is_details": "true"},
        evidence.requested_at,
        evidence.responded_at,
        200,
        raw_payload,
    )
    assert row[6]
    assert row[7:] == (
        "market-sync-worker-v1",
        "upbit-market-catalog-v1",
        "succeeded",
        True,
    )

    omitted_at = observed_at + timedelta(minutes=1)
    omitted_payload = [
        {
            "market": "KRW-MANIFEST-KEEP",
            "korean_name": "유지",
            "english_name": "Keep",
            "market_warning": "NONE",
            "market_event": {"trading_suspended": False},
        }
    ]
    repository.sync_market_catalog(
        [_market("KRW-MANIFEST-KEEP", "유지")],
        observed_at=omitted_at,
        fetch_evidence=FetchEvidence(
            endpoint="/v1/market/all",
            request_parameters={"is_details": "true"},
            requested_at=omitted_at - timedelta(milliseconds=20),
            responded_at=omitted_at - timedelta(milliseconds=5),
            response_status=200,
            response_payload=omitted_payload,
        ),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        omission = connection.execute(
            """
            SELECT history.trading_status, manifest.response_payload
            FROM market_status_history history
            JOIN markets market ON market.id = history.market_id
            JOIN fetch_manifests manifest ON manifest.id = history.fetch_manifest_id
            WHERE market.market_code = 'KRW-MANIFEST-RAW'
              AND history.valid_from = %s
            """,
            (omitted_at,),
        ).fetchone()
    assert omission == ("inactive", omitted_payload)


def test_empty_market_catalog_response_records_failed_manifest_without_state_change() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    requested_at = datetime(2026, 7, 17, 5, 6, tzinfo=UTC)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        before = connection.execute("SELECT count(*) FROM market_status_history").fetchone()
    assert before is not None

    repository.record_market_catalog_fetch_failure(
        FetchEvidence(
            endpoint="/v1/market/all",
            request_parameters={"is_details": "true"},
            requested_at=requested_at,
            responded_at=requested_at + timedelta(milliseconds=10),
            response_status=200,
            response_payload=[],
        )
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        after = connection.execute("SELECT count(*) FROM market_status_history").fetchone()
        manifest = connection.execute(
            """
            SELECT response_status, response_payload, outcome, error_code, error_message
            FROM fetch_manifests
            WHERE endpoint = '/v1/market/all' AND requested_at = %s
            """,
            (requested_at,),
        ).fetchone()

    assert after == before
    assert manifest == (
        200,
        [],
        "failed",
        "EMPTY_RESPONSE",
        "업비트 시장 목록 성공 응답이 비어 있다.",
    )


def test_backfill_safety_gate_is_fail_closed_for_every_required_condition() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    instrument_id = _insert_bare_instrument(database_url, "KRW-SAFETY-GATE")
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'paused', lease_owner = NULL, lease_expires_at = NULL
            WHERE status IN ('pending', 'leased', 'running', 'retry_wait')
            """
        )
        job = connection.execute(
            """
            INSERT INTO backfill_jobs (
              status, data_type, plan, target_start_at, target_end_at,
              estimated_request_count, estimated_row_count, estimated_storage_bytes,
              restart_mode, created_by, approved_by, approved_at,
              idempotency_key, priority
            ) VALUES (
              'pending', 'source_candle', %s, now() - interval '1 hour', now(),
              1, 60, 15360, 'safe_restart', 'system', 'operator:e2e', now(),
              'e2e:safety-gate', 1000
            ) RETURNING id
            """,
            (psycopg.types.json.Jsonb({"targets": [instrument_id]}),),
        ).fetchone()
        assert job is not None
        connection.execute(
            """
            INSERT INTO backfill_job_targets (backfill_job_id, instrument_id, status)
            VALUES (%s, %s, 'pending')
            """,
            (job[0], instrument_id),
        )
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET enabled = false, backup_verified_at = NULL,
                free_capacity_bytes = 0, required_capacity_bytes = 0,
                approved_sha = NULL, approved_by = NULL, approved_at = NULL
            WHERE singleton
            """
        )

    repository = PostgresOperationsRepository(
        database_url,
        enforce_backfill_safety_gate=True,
        release_sha="release-exact",
    )

    def assert_gate_closed(expected_reason: str) -> str:
        reason = repository.backfill_claim_gate_reason()
        assert reason is not None
        assert expected_reason in reason
        assert repository.claim_next_backfill_job() is None
        with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
            state = connection.execute(
                """
                SELECT job.status, job.attempt_count, job.lease_owner,
                       job.lease_expires_at, target.status
                FROM backfill_jobs job
                JOIN backfill_job_targets target ON target.backfill_job_id = job.id
                WHERE job.id = %s
                """,
                (job[0],),
            ).fetchone()
        assert state == ("pending", 0, None, None, "pending")
        return reason

    default_reason = assert_gate_closed("기본 닫힘")
    repository.record_collection_worker_heartbeat("backfill_collection", "gated", default_reason)
    runtime = repository.collection_worker_runtime_status("backfill_collection")
    assert runtime.status == "gated"
    assert runtime.status_label == "승인 대기"
    assert runtime.status_detail == default_reason
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET enabled = true, backup_verified_at = now() - interval '25 hours',
                free_capacity_bytes = 200, required_capacity_bytes = 100,
                approved_sha = 'release-exact', approved_by = 'operator:e2e',
                approved_at = now()
            WHERE singleton
            """
        )
    assert_gate_closed("백업 검증")
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET backup_verified_at = now() + interval '1 hour'
            WHERE singleton
            """
        )
    assert_gate_closed("백업 검증")
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(database_url, options="-c timezone=UTC") as connection,
    ):
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET backup_verified_at = now(), free_capacity_bytes = 0,
                required_capacity_bytes = 0
            WHERE singleton
            """
        )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET backup_verified_at = now(), free_capacity_bytes = 99
            WHERE singleton
            """
        )
    assert_gate_closed("여유 용량")
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET free_capacity_bytes = 100, approved_sha = 'different-release'
            WHERE singleton
            """
        )
    assert_gate_closed("승인 SHA")
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_safety_gate
            SET approved_sha = 'release-exact'
            WHERE singleton
            """
        )

    claimed = repository.claim_next_backfill_job()
    assert claimed is not None
    assert claimed.id == job[0]


def test_market_state_transition_updates_backfill_jobs_targets_and_leases_atomically() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 10, tzinfo=UTC)
    repository.sync_market_catalog(
        [_market("KRW-STATE", "상태검증")],
        observed_at=observed_at,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        job_id = connection.execute(
            """
            SELECT job.id
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = 'KRW-STATE'
            """
        ).fetchone()
        assert job_id is not None
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'running', lease_owner = 'worker-e2e',
                lease_expires_at = %s
            WHERE id = %s
            """,
            (observed_at + timedelta(minutes=10), job_id[0]),
        )
        connection.execute(
            """
            UPDATE backfill_job_targets SET status = 'running'
            WHERE backfill_job_id = %s
            """,
            (job_id[0],),
        )

    repository.set_market_target_state(
        "KRW-STATE",
        state="paused",
        actor="operator:e2e",
        reason="일시정지 전이 검증",
        changed_at=observed_at + timedelta(minutes=1),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        paused = connection.execute(
            """
            SELECT job.status, target.status, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s
            """,
            (job_id[0],),
        ).fetchone()
    assert paused == ("paused", "paused", None, None)

    repository.set_market_target_state(
        "KRW-STATE",
        state="active",
        actor="operator:e2e",
        reason="재개 전이 검증",
        changed_at=observed_at + timedelta(minutes=2),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        resumed = connection.execute(
            """
            SELECT job.status, target.status
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s
            """,
            (job_id[0],),
        ).fetchone()
    assert resumed == ("pending", "pending")

    repository.set_market_target_state(
        "KRW-STATE",
        state="excluded",
        actor="operator:e2e",
        reason="제외 전이 검증",
        changed_at=observed_at + timedelta(minutes=3),
    )
    repository.set_market_target_state(
        "KRW-STATE",
        state="active",
        actor="operator:e2e",
        reason="취소 작업 비복구 검증",
        changed_at=observed_at + timedelta(minutes=4),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        cancelled = connection.execute(
            """
            SELECT job.status, target.status, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s
            """,
            (job_id[0],),
        ).fetchone()
    assert cancelled == ("cancelled", "stopped", None, None)


def test_completed_default_backfill_creates_one_idempotent_tail_job() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    first_observed_at = datetime(2026, 7, 17, 5, 20, tzinfo=UTC)
    second_observed_at = first_observed_at + timedelta(minutes=30)
    catalog = [_market("KRW-TAIL", "꼬리검증")]
    repository.sync_market_catalog(catalog, observed_at=first_observed_at)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        first_job = connection.execute(
            """
            SELECT job.id, job.target_end_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = 'KRW-TAIL'
            """
        ).fetchone()
        assert first_job is not None
        connection.execute(
            "UPDATE backfill_jobs SET status = 'succeeded' WHERE id = %s",
            (first_job[0],),
        )
        connection.execute(
            "UPDATE backfill_job_targets SET status = 'succeeded' WHERE backfill_job_id = %s",
            (first_job[0],),
        )

    first_resync = repository.sync_market_catalog(catalog, observed_at=second_observed_at)
    second_resync = repository.sync_market_catalog(catalog, observed_at=second_observed_at)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        jobs = connection.execute(
            """
            SELECT job.target_start_at, job.target_end_at, job.idempotency_key
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = 'KRW-TAIL'
            ORDER BY job.target_start_at
            """
        ).fetchall()

    assert first_resync.created_backfill_job_count == 1
    assert second_resync.created_backfill_job_count == 0
    assert len(jobs) == 2
    assert jobs[1][0] == first_job[1]
    assert jobs[1][1] == second_observed_at
    assert jobs[0][2] != jobs[1][2]


def test_postgres_manual_backfill_plan_persists_deterministic_non_null_key() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    instrument_id = _insert_bare_instrument(database_url, "KRW-MANUAL")
    operations_repository = PostgresOperationsRepository(database_url)
    plan = operations_repository.create_backfill_plan(
        "source_candle",
        datetime(2026, 7, 17, 4, 50, tzinfo=UTC),
        datetime(2026, 7, 17, 5, 50, tzinfo=UTC),
        [instrument_id],
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        key = connection.execute(
            "SELECT idempotency_key FROM backfill_jobs WHERE plan ->> 'planId' = %s",
            (plan.plan_id,),
        ).fetchone()

    assert key == (f"manual:{plan.plan_id}",)


def test_continuous_source_candle_creates_subscribed_desire() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    repository.sync_market_catalog(
        [_market("KRW-CANDLE-DESIRE", "캔들구독")],
        observed_at=datetime(2026, 7, 17, 5, 51, tzinfo=UTC),
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        desire = connection.execute(
            """
            SELECT spec.data_type, spec.continuous, desire.desired_state
            FROM collection_target_specs spec
            JOIN collection_subscription_desires desire
              ON desire.target_spec_id = spec.id
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = 'KRW-CANDLE-DESIRE'
              AND spec.data_type = 'source_candle'
            """
        ).fetchone()

    assert desire == ("source_candle", True, "subscribed")


def test_automatic_job_blocks_manual_plan_creation_for_same_instrument() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 52, tzinfo=UTC)
    repository.sync_market_catalog(
        [_market("KRW-AUTO-FIRST", "자동우선")],
        observed_at=observed_at,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        instrument_id = connection.execute(
            "SELECT id FROM instruments WHERE market_code = 'KRW-AUTO-FIRST'"
        ).fetchone()
    assert instrument_id is not None

    with pytest.raises(ValueError, match="활성 백필 작업"):
        PostgresOperationsRepository(database_url).create_backfill_plan(
            "source_candle",
            observed_at - timedelta(hours=1),
            observed_at,
            [instrument_id[0]],
        )


def test_planned_manual_job_blocks_automatic_job_and_can_be_approved() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    instrument_id = _insert_bare_instrument(database_url, "KRW-MANUAL-FIRST")
    operations_repository = PostgresOperationsRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 53, tzinfo=UTC)
    plan = operations_repository.create_backfill_plan(
        "source_candle",
        observed_at - timedelta(hours=1),
        observed_at,
        [instrument_id],
    )

    sync_result = PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [_market("KRW-MANUAL-FIRST", "수동우선")],
        observed_at=observed_at,
    )
    approved = operations_repository.approve_backfill_job(plan.plan_id)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        jobs = connection.execute(
            """
            SELECT job.status, count(target.instrument_id)
            FROM backfill_jobs job
            LEFT JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.plan -> 'targets' @> %s
              AND job.status IN ('planned', 'pending', 'running', 'retry_wait', 'paused')
            GROUP BY job.id, job.status
            """,
            (psycopg.types.json.Jsonb([instrument_id]),),
        ).fetchall()

    assert sync_result.created_backfill_job_count == 0
    assert approved.status == "pending"
    assert jobs == [("pending", 1)]


def test_manual_approval_rechecks_competing_active_job() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    instrument_id = _insert_bare_instrument(database_url, "KRW-APPROVE-RACE")
    operations_repository = PostgresOperationsRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 54, tzinfo=UTC)
    plan = operations_repository.create_backfill_plan(
        "source_candle",
        observed_at - timedelta(hours=1),
        observed_at,
        [instrument_id],
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        competing = connection.execute(
            """
            INSERT INTO backfill_jobs (
              status, data_type, plan, target_start_at, target_end_at,
              estimated_request_count, estimated_row_count, estimated_storage_bytes,
              restart_mode, created_by, idempotency_key
            ) VALUES (
              'pending', 'source_candle', %s, %s, %s, 1, 60, 15360,
              'safe_restart', 'system', %s
            ) RETURNING id
            """,
            (
                psycopg.types.json.Jsonb({"targets": [instrument_id]}),
                observed_at - timedelta(hours=1),
                observed_at,
                f"e2e:approval-race:{instrument_id}",
            ),
        ).fetchone()
        assert competing is not None
        connection.execute(
            """
            INSERT INTO backfill_job_targets (backfill_job_id, instrument_id, status)
            VALUES (%s, %s, 'pending')
            """,
            (competing[0], instrument_id),
        )

    with pytest.raises(ValueError, match="활성 백필 작업"):
        operations_repository.approve_backfill_job(plan.plan_id)


def test_catalog_omission_pauses_backfill_targets_and_clears_lease_atomically() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 55, tzinfo=UTC)
    repository.sync_market_catalog(
        [_market("KRW-MISSING", "누락시장"), _market("KRW-KEEP", "유지시장")],
        observed_at=observed_at,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        job = connection.execute(
            """
            SELECT job.id
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = 'KRW-MISSING'
              AND job.status = 'pending'
            """
        ).fetchone()
        assert job is not None
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'running', lease_owner = 'missing-worker',
                lease_expires_at = %s
            WHERE id = %s
            """,
            (observed_at + timedelta(minutes=5), job[0]),
        )
        connection.execute(
            "UPDATE backfill_job_targets SET status = 'running' WHERE backfill_job_id = %s",
            (job[0],),
        )

    repository.sync_market_catalog(
        [_market("KRW-KEEP", "유지시장")],
        observed_at=observed_at + timedelta(seconds=10),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        state = connection.execute(
            """
            SELECT job.status, target.status, job.lease_owner, job.lease_expires_at
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s
            """,
            (job[0],),
        ).fetchone()

    assert state == ("paused", "paused", None, None)

    repository.sync_market_catalog(
        [_market("KRW-MISSING", "누락시장"), _market("KRW-KEEP", "유지시장")],
        observed_at=observed_at + timedelta(seconds=20),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        restored = connection.execute(
            """
            SELECT job.status, target.status
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s
            """,
            (job[0],),
        ).fetchone()

    assert restored == ("pending", "pending")


def test_multi_target_job_changes_only_the_affected_market_target() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    first_id = _insert_bare_instrument(database_url, "KRW-MULTI-A")
    second_id = _insert_bare_instrument(database_url, "KRW-MULTI-B")
    operations_repository = PostgresOperationsRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 56, tzinfo=UTC)
    plan = operations_repository.create_backfill_plan(
        "source_candle",
        observed_at - timedelta(hours=1),
        observed_at,
        [first_id, second_id],
    )
    repository = PostgresDataFoundationRepository(database_url)
    repository.sync_market_catalog(
        [_market("KRW-MULTI-A", "다중A"), _market("KRW-MULTI-B", "다중B")],
        observed_at=observed_at,
    )
    job = operations_repository.approve_backfill_job(plan.plan_id)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'running', lease_owner = 'multi-worker',
                lease_expires_at = %s
            WHERE id = %s
            """,
            (observed_at + timedelta(minutes=5), job.id),
        )
        connection.execute(
            "UPDATE backfill_job_targets SET status = 'running' WHERE backfill_job_id = %s",
            (job.id,),
        )

    repository.set_market_target_state(
        "KRW-MULTI-A",
        state="paused",
        actor="operator:e2e",
        reason="다중 작업 전체 일시정지",
        changed_at=observed_at + timedelta(seconds=1),
    )
    assert _job_target_states(database_url, job.id) == (
        "running",
        [("KRW-MULTI-A", "paused"), ("KRW-MULTI-B", "running")],
        "multi-worker",
    )

    repository.set_market_target_state(
        "KRW-MULTI-B",
        state="paused",
        actor="operator:e2e",
        reason="두 번째 시장도 일시정지",
        changed_at=observed_at + timedelta(seconds=2),
    )

    repository.set_market_target_state(
        "KRW-MULTI-A",
        state="active",
        actor="operator:e2e",
        reason="첫 시장만 준비",
        changed_at=observed_at + timedelta(seconds=3),
    )
    assert _job_target_states(database_url, job.id) == (
        "pending",
        [("KRW-MULTI-A", "pending"), ("KRW-MULTI-B", "paused")],
        None,
    )

    repository.set_market_target_state(
        "KRW-MULTI-B",
        state="active",
        actor="operator:e2e",
        reason="모든 시장 준비",
        changed_at=observed_at + timedelta(seconds=4),
    )
    assert _job_target_states(database_url, job.id) == (
        "pending",
        [("KRW-MULTI-A", "pending"), ("KRW-MULTI-B", "pending")],
        None,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'running', lease_owner = 'multi-worker', lease_expires_at = %s
            WHERE id = %s
            """,
            (observed_at + timedelta(minutes=10), job.id),
        )
        connection.execute(
            "UPDATE backfill_job_targets SET status = 'running' WHERE backfill_job_id = %s",
            (job.id,),
        )
    repository.set_market_target_state(
        "KRW-MULTI-A",
        state="excluded",
        actor="operator:e2e",
        reason="다중 작업 전체 취소",
        changed_at=observed_at + timedelta(seconds=5),
    )
    assert _job_target_states(database_url, job.id) == (
        "running",
        [("KRW-MULTI-A", "stopped"), ("KRW-MULTI-B", "running")],
        "multi-worker",
    )


def test_operator_managed_targets_restore_after_catalog_reappearance() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 57, tzinfo=UTC)
    market_code = "KRW-OPERATOR-RESTORE"
    catalog = [_market(market_code, "운영자복구"), _market("KRW-RESTORE-KEEP", "유지")]
    repository.sync_market_catalog(catalog, observed_at=observed_at)
    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="운영자 전체 타입 정책",
        changed_at=observed_at + timedelta(seconds=1),
        policy=MarketCollectionPolicySettings(
            start_at=datetime(2025, 1, 1, tzinfo=UTC),
            data_types=(
                "source_candle",
                "trade_event",
                "orderbook_snapshot",
                "ticker_snapshot",
            ),
            candle_unit="1m",
            retention_days=365,
            priority=350,
            continuous=True,
        ),
    )
    repository.sync_market_catalog(
        [_market("KRW-RESTORE-KEEP", "유지")],
        observed_at=observed_at + timedelta(seconds=2),
    )
    missing_states = _target_spec_states(database_url, market_code)
    repository.sync_market_catalog(
        catalog,
        observed_at=observed_at + timedelta(seconds=3),
    )

    specs = _target_spec_states(database_url, market_code)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        job_state = connection.execute(
            """
            SELECT job.status, target.status
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE instrument.market_code = %s
              AND job.status IN ('pending', 'running', 'retry_wait', 'paused')
            """,
            (market_code,),
        ).fetchone()

    assert missing_states == _expected_target_spec_states(
        status="paused",
        auto_managed=False,
        state_reason="catalog_missing",
        desired_state="unsubscribed",
    )
    assert specs == _expected_target_spec_states(
        status="active",
        auto_managed=False,
        state_reason=None,
        desired_state="subscribed",
    )
    assert job_state == ("pending", "pending")


def test_operator_paused_targets_survive_catalog_sync_and_reappearance() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 57, 10, tzinfo=UTC)
    market_code = "KRW-OPERATOR-PAUSED"
    catalog = [_market(market_code, "운영자일시정지"), _market("KRW-PAUSED-KEEP", "유지")]
    repository.sync_market_catalog(catalog, observed_at=observed_at)
    repository.set_market_target_state(
        market_code,
        state="paused",
        actor="operator:e2e",
        reason="운영자 명시적 일시정지",
        changed_at=observed_at + timedelta(seconds=1),
    )

    repository.sync_market_catalog(
        catalog,
        observed_at=observed_at + timedelta(seconds=2),
    )
    after_regular_sync = _target_spec_states(database_url, market_code)
    repository.sync_market_catalog(
        [_market("KRW-PAUSED-KEEP", "유지")],
        observed_at=observed_at + timedelta(seconds=3),
    )
    repository.sync_market_catalog(
        catalog,
        observed_at=observed_at + timedelta(seconds=4),
    )
    after_reappearance = _target_spec_states(database_url, market_code)

    expected = _expected_target_spec_states(
        status="paused",
        auto_managed=False,
        state_reason="operator_paused",
        desired_state="unsubscribed",
    )
    assert after_regular_sync == expected
    assert after_reappearance == expected


def test_auto_managed_targets_restore_only_catalog_missing_pause() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 57, 20, tzinfo=UTC)
    market_code = "KRW-AUTO-RESTORE"
    catalog = [_market(market_code, "자동복구"), _market("KRW-AUTO-KEEP", "유지")]
    repository.sync_market_catalog(catalog, observed_at=observed_at)
    repository.sync_market_catalog(
        [_market("KRW-AUTO-KEEP", "유지")],
        observed_at=observed_at + timedelta(seconds=1),
    )
    missing_states = _target_spec_states(database_url, market_code)
    repository.sync_market_catalog(
        catalog,
        observed_at=observed_at + timedelta(seconds=2),
    )

    assert missing_states == _expected_target_spec_states(
        status="paused",
        auto_managed=True,
        state_reason="catalog_missing",
        desired_state="unsubscribed",
    )
    assert _target_spec_states(
        database_url,
        market_code,
    ) == _expected_target_spec_states(
        status="active",
        auto_managed=True,
        state_reason=None,
        desired_state="subscribed",
    )


def test_catalog_missing_creates_unavailable_until_market_reappears() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    first_seen_at = datetime(2026, 7, 17, 6, tzinfo=UTC)
    missing_at = first_seen_at + timedelta(minutes=1)
    resumed_at = missing_at + timedelta(minutes=1)
    market_code = "KRW-COVERAGE-CATALOG-MISSING"
    keep = _market("KRW-COVERAGE-CATALOG-KEEP", "유지")
    market = _market(market_code, "카탈로그누락")
    repository.sync_market_catalog([market, keep], observed_at=first_seen_at)

    repository.sync_market_catalog([keep], observed_at=missing_at)

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        unavailable = connection.execute(
            """
                SELECT coverage.range_start_at, coverage.range_end_at,
                   coverage.status, coverage.evidence ->> 'reasonCode',
                   event.previous_status, event.new_status
            FROM coverage_intervals coverage
            JOIN collection_target_specs spec ON spec.id = coverage.target_spec_id
            JOIN markets market ON market.id = spec.market_id
            LEFT JOIN data_quality_events event
              ON event.target_spec_id = coverage.target_spec_id
             AND event.event_type = 'catalog_missing_unavailable'
            WHERE market.market_code = %s
              AND spec.data_type = 'source_candle'
              AND coverage.range_start_at = %s
            """,
            (market_code, missing_at),
        ).fetchone()
    assert unavailable == (
        missing_at,
        datetime(9999, 1, 1, tzinfo=UTC),
        "unavailable",
        "catalog_missing",
        None,
        "unavailable",
    )

    repository.sync_market_catalog([market, keep], observed_at=resumed_at)

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        resumed = connection.execute(
            """
            SELECT coverage.range_start_at, coverage.range_end_at,
                   coverage.status,
                   coverage.evidence ->> 'reasonCode'
            FROM coverage_intervals coverage
            JOIN collection_target_specs spec ON spec.id = coverage.target_spec_id
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = %s
              AND spec.data_type = 'source_candle'
              AND coverage.range_end_at > %s
            ORDER BY coverage.range_start_at
            """,
            (market_code, missing_at),
        ).fetchall()
    assert resumed == [
        (missing_at, resumed_at, "unavailable", "catalog_missing"),
        (
            resumed_at,
            datetime(9999, 1, 1, tzinfo=UTC),
            "unverified",
            "market_trading_resumed",
        ),
    ]


def test_explicit_market_inactive_creates_unavailable_coverage() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    repository = PostgresDataFoundationRepository(database_url)
    active_at = datetime(2026, 7, 17, 6, 10, tzinfo=UTC)
    inactive_at = active_at + timedelta(minutes=1)
    market_code = "KRW-COVERAGE-INACTIVE"
    repository.sync_market_catalog([_market(market_code, "거래종료")], observed_at=active_at)

    repository.sync_market_catalog(
        [
            MarketCatalogItem(
                market_code=market_code,
                korean_name="거래종료",
                english_name="Inactive",
                market_warning="NONE",
                tradable=False,
            )
        ],
        observed_at=inactive_at,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        unavailable = connection.execute(
            """
                SELECT coverage.range_start_at, coverage.range_end_at,
                   coverage.status, coverage.evidence ->> 'reasonCode',
                   event.event_type
            FROM coverage_intervals coverage
            JOIN collection_target_specs spec ON spec.id = coverage.target_spec_id
            JOIN markets market ON market.id = spec.market_id
            LEFT JOIN data_quality_events event
              ON event.target_spec_id = coverage.target_spec_id
             AND event.event_type = 'market_inactive_unavailable'
            WHERE market.market_code = %s
              AND spec.data_type = 'source_candle'
              AND coverage.range_start_at = %s
            """,
            (market_code, inactive_at),
        ).fetchone()
    assert unavailable == (
        inactive_at,
        datetime(9999, 1, 1, tzinfo=UTC),
        "unavailable",
        "market_inactive",
        "market_inactive_unavailable",
    )


def test_paused_target_requires_explicit_state_reason() -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [_market("KRW-STATE-REASON-CONTRACT", "상태원인계약")],
        observed_at=datetime(2026, 7, 17, 5, 57, 30, tzinfo=UTC),
    )

    with (
        psycopg.connect(database_url, options="-c timezone=UTC") as connection,
        pytest.raises(psycopg.errors.CheckViolation),
    ):
        connection.execute(
            """
            UPDATE collection_target_specs spec
            SET status = 'paused', state_reason = NULL
            FROM markets market
            WHERE market.id = spec.market_id
              AND market.market_code = 'KRW-STATE-REASON-CONTRACT'
            """
        )


@pytest.mark.parametrize("inactive_state", ["paused", "excluded"])
def test_targetless_manual_plan_stays_planned_and_approval_checks_market_state(
    inactive_state: str,
) -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    market_code = f"KRW-PLANNED-{inactive_state.upper()}"
    instrument_id = _insert_bare_instrument(database_url, market_code)
    operations_repository = PostgresOperationsRepository(database_url)
    observed_at = datetime(2026, 7, 17, 5, 58, tzinfo=UTC) + (
        timedelta(seconds=10) if inactive_state == "excluded" else timedelta()
    )
    plan = operations_repository.create_backfill_plan(
        "source_candle",
        observed_at - timedelta(hours=1),
        observed_at,
        [instrument_id],
    )
    repository = PostgresDataFoundationRepository(database_url)
    repository.sync_market_catalog(
        [_market(market_code, "승인상태검증")],
        observed_at=observed_at,
    )
    repository.set_market_target_state(
        market_code,
        state=inactive_state,
        actor="operator:e2e",
        reason="승인 전 시장 비활성화",
        changed_at=observed_at + timedelta(seconds=1),
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        status = connection.execute(
            "SELECT status FROM backfill_jobs WHERE plan ->> 'planId' = %s",
            (plan.plan_id,),
        ).fetchone()
    assert status == ("planned",)
    with pytest.raises(ValueError, match="비활성 수집 시장"):
        operations_repository.approve_backfill_job(plan.plan_id)

    repository.set_market_target_state(
        market_code,
        state="active",
        actor="operator:e2e",
        reason="승인 가능 상태 복구",
        changed_at=observed_at + timedelta(seconds=2),
    )
    approved = operations_repository.approve_backfill_job(plan.plan_id)
    assert approved.status == "pending"


def test_instrument_and_coverage_advisory_locks_use_distinct_two_key_namespaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    database_url = os.environ["GOODMONEYING_DATABASE_URL"]
    instrument_namespace = 0x474D494E
    coverage_namespace = 0x474D434F
    instrument_id = _insert_bare_instrument(database_url, "KRW-LOCK-NAMESPACE")
    operations_repository = PostgresOperationsRepository(database_url)
    instrument_connection = psycopg.connect(database_url, options="-c timezone=UTC")
    monkeypatch.setattr(
        operations_repository,
        "_connect",
        lambda: nullcontext(instrument_connection),
    )
    operations_repository.create_backfill_plan(
        "source_candle",
        datetime(2026, 7, 17, 4, 59, tzinfo=UTC),
        datetime(2026, 7, 17, 5, 59, tzinfo=UTC),
        [instrument_id],
    )
    instrument_locks = _advisory_locks(database_url, instrument_connection.info.backend_pid)
    instrument_connection.rollback()
    instrument_connection.close()

    data_repository = PostgresDataFoundationRepository(database_url)
    data_repository.sync_market_catalog(
        [_market("KRW-COVERAGE-LOCK", "커버리지잠금")],
        observed_at=datetime(2026, 7, 17, 5, 59, tzinfo=UTC),
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        target_spec = connection.execute(
            """
            SELECT spec.id
            FROM collection_target_specs spec
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = 'KRW-COVERAGE-LOCK'
              AND spec.data_type = 'source_candle'
            """
        ).fetchone()
    assert target_spec is not None
    coverage_connection = PostgresOperationsRepository(database_url)._connect()
    try:
        manifest = coverage_connection.execute(
            """
            INSERT INTO fetch_manifests (
              target_spec_id, source, endpoint, request_parameters,
              request_fingerprint, requested_at, responded_at, response_status,
              response_checksum, collector_version, schema_version, outcome
            ) VALUES (
              %s, 'UPBIT', 'e2e:lock-namespace', '{}'::jsonb,
              'e2e:lock-namespace', %s, %s, 200,
              'e2e-checksum', 'e2e', 'e2e', 'succeeded'
            )
            RETURNING id
            """,
            (
                target_spec[0],
                datetime(2026, 7, 17, 5, 58, tzinfo=UTC),
                datetime(2026, 7, 17, 5, 59, tzinfo=UTC),
            ),
        ).fetchone()
        assert manifest is not None
        operations_repository._replace_coverage_with_observed(
            coverage_connection,
            target_spec_id=int(target_spec[0]),
            range_start_at=datetime(2026, 7, 17, 5, 58, tzinfo=UTC),
            range_end_at=datetime(2026, 7, 17, 5, 59, tzinfo=UTC),
            manifest_id=int(manifest["id"]),
            natural_key={"test": "lock-namespace"},
        )
        coverage_locks = _advisory_locks(
            database_url,
            coverage_connection.info.backend_pid,
        )
    finally:
        coverage_connection.rollback()
        coverage_connection.close()

    assert (instrument_namespace, instrument_id, 2) in instrument_locks
    assert (coverage_namespace, int(target_spec[0]), 2) in coverage_locks
    assert instrument_namespace != coverage_namespace


def _market(market_code: str, korean_name: str) -> MarketCatalogItem:
    return MarketCatalogItem(
        market_code=market_code,
        korean_name=korean_name,
        english_name=market_code,
        market_warning="NONE",
        tradable=True,
    )


def _insert_bare_instrument(database_url: str, market_code: str) -> int:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        row = connection.execute(
            """
            INSERT INTO instruments (
              exchange, market_code, quote_currency, base_asset, display_name, status
            ) VALUES ('UPBIT', %s, 'KRW', %s, %s, 'active')
            ON CONFLICT (exchange, market_code) DO UPDATE SET display_name = excluded.display_name
            RETURNING id
            """,
            (market_code, market_code.removeprefix("KRW-"), market_code),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _job_target_states(
    database_url: str,
    job_id: int,
) -> tuple[str, list[tuple[str, str]], str | None]:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        job = connection.execute(
            "SELECT status, lease_owner FROM backfill_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
        targets = connection.execute(
            """
            SELECT instrument.market_code, target.status
            FROM backfill_job_targets target
            JOIN instruments instrument ON instrument.id = target.instrument_id
            WHERE target.backfill_job_id = %s
            ORDER BY instrument.market_code
            """,
            (job_id,),
        ).fetchall()
    assert job is not None
    return str(job[0]), [(str(row[0]), str(row[1])) for row in targets], job[1]


def _target_spec_states(
    database_url: str,
    market_code: str,
) -> list[tuple[str, str, bool, str | None, str]]:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        rows = connection.execute(
            """
            SELECT spec.data_type, spec.status, spec.auto_managed,
                   to_jsonb(spec) ->> 'state_reason', desire.desired_state
            FROM collection_target_specs spec
            JOIN collection_subscription_desires desire ON desire.target_spec_id = spec.id
            JOIN markets market ON market.id = spec.market_id
            WHERE market.market_code = %s
            ORDER BY spec.data_type
            """,
            (market_code,),
        ).fetchall()
    return [(str(row[0]), str(row[1]), bool(row[2]), row[3], str(row[4])) for row in rows]


def _expected_target_spec_states(
    *,
    status: str,
    auto_managed: bool,
    state_reason: str | None,
    desired_state: str,
) -> list[tuple[str, str, bool, str | None, str]]:
    return [
        (data_type, status, auto_managed, state_reason, desired_state)
        for data_type in (
            "orderbook_snapshot",
            "source_candle",
            "ticker_snapshot",
            "trade_event",
        )
    ]


def _advisory_locks(database_url: str, process_id: int) -> list[tuple[int, int, int]]:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        rows = connection.execute(
            """
            SELECT classid::bigint, objid::bigint, objsubid
            FROM pg_locks
            WHERE locktype = 'advisory' AND pid = %s AND granted
            ORDER BY classid, objid, objsubid
            """,
            (process_id,),
        ).fetchall()
    return [(int(row[0]), int(row[1]), int(row[2])) for row in rows]
