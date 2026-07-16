from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_shared.data_foundation import MarketCatalogItem
from goodmoneying_shared.data_foundation_repository import PostgresDataFoundationRepository
from goodmoneying_shared.models import (
    FetchEvidence,
    OrderbookSummary,
    SourceCandle,
    TickerSnapshot,
    TradeEvent,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_worker.realtime_stream_worker import run_realtime_stream_collection

pytestmark = pytest.mark.live


def _orderbook_payload(market_code: str, occurred_at: datetime) -> dict[str, object]:
    units = [
        {
            "ask_price": str(1000 + index),
            "ask_size": str(index + 1),
            "bid_price": str(999 - index),
            "bid_size": str((index + 1) * 2),
        }
        for index in range(30)
    ]
    return {
        "type": "orderbook",
        "code": market_code,
        "timestamp": int(occurred_at.timestamp() * 1000),
        "total_ask_size": "465",
        "total_bid_size": "930",
        "level": 0,
        "stream_type": "REALTIME",
        "orderbook_units": units,
    }


def _fixed_now(value: datetime) -> Callable[[], datetime]:
    return lambda: value


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def _prepare_market(market_code: str, observed_at: datetime) -> tuple[str, int, int, int, int]:
    database_url = _database_url()
    PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [
            MarketCatalogItem(
                market_code=market_code,
                korean_name="원천 증거 검증",
                english_name="Source Evidence",
                market_warning="NONE",
                tradable=True,
            )
        ],
        observed_at=observed_at,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        row = connection.execute(
            """
            SELECT market.id, market.legacy_instrument_id, spec.id, target.backfill_job_id
            FROM markets market
            JOIN collection_target_specs spec
              ON spec.market_id = market.id
             AND spec.data_type = 'source_candle'
             AND spec.candle_unit = '1m'
            JOIN backfill_job_targets target ON target.target_spec_id = spec.id
            WHERE market.market_code = %s
            """,
            (market_code,),
        ).fetchone()
    assert row is not None
    return database_url, int(row[0]), int(row[1]), int(row[2]), int(row[3])


def _claim_job(
    database_url: str,
    repository: PostgresOperationsRepository,
    job_id: int,
) -> None:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET status = 'paused'
            WHERE id <> %s
              AND status IN ('pending', 'leased', 'running', 'retry_wait')
            """,
            (job_id,),
        )
        connection.execute(
            "UPDATE backfill_jobs SET priority = 10000 WHERE id = %s",
            (job_id,),
        )
    claimed = repository.claim_next_backfill_job()
    assert claimed is not None
    assert claimed.id == job_id


def test_backfill_source_row_has_manifest_timestamps_and_available_coverage() -> None:
    observed_at = datetime(2026, 7, 17, 6, tzinfo=UTC)
    database_url, market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-CANDLE", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    candle_start = observed_at - timedelta(minutes=10)
    received_at = candle_start + timedelta(seconds=3)

    assert (
        repository.record_backfill_candles(
            job_id,
            instrument_id,
            [
                SourceCandle(
                    instrument_id=instrument_id,
                    candle_unit="1m",
                    candle_start_at=candle_start,
                    open_price=Decimal("100"),
                    high_price=Decimal("120"),
                    low_price=Decimal("90"),
                    close_price=Decimal("110"),
                    trade_volume=Decimal("1.5"),
                    trade_amount=Decimal("165"),
                    collected_at=received_at,
                )
            ],
        )
        == 1
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        source = connection.execute(
            """
            SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                   fetch_manifest_id
            FROM source_candles
            WHERE instrument_id = %s AND candle_unit = '1m' AND candle_start_at = %s
            """,
            (instrument_id, candle_start),
        ).fetchone()
        assert source is not None
        assert source[5] is not None, "원천 행이 체크섬 매니페스트를 참조해야 한다"
        manifest = connection.execute(
            """
            SELECT target_spec_id, source, endpoint, response_status,
                   response_checksum, outcome, response_payload
            FROM fetch_manifests
            WHERE id = %s
            """,
            (source[5],),
        ).fetchone()
        coverage = connection.execute(
            """
            SELECT range_start_at, range_end_at, status, fetch_manifest_id
            FROM coverage_intervals
            WHERE target_spec_id = %s
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()

    assert manifest is not None
    assert source[0] == market_id
    assert source[1] == candle_start
    assert source[2] == received_at
    assert source[3] is not None
    assert source[3].utcoffset() == timedelta(0)
    assert source[4] == received_at
    assert source[5] is not None
    assert manifest == (
        target_spec_id,
        "UPBIT",
        "/v1/candles/minutes/1",
        200,
        manifest[4],
        "succeeded",
        None,
    )
    assert manifest[4]
    available = [row for row in coverage if row[2] == "available"]
    assert available == [
        (candle_start, candle_start + timedelta(minutes=1), "available", source[5])
    ]
    assert all(row[2] != "no_trade" for row in coverage)

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE source_candles
            SET market_id = NULL, occurred_at = NULL, received_at = NULL,
                stored_at = NULL, knowledge_at = NULL, fetch_manifest_id = NULL
            WHERE instrument_id = %s AND candle_unit = '1m' AND candle_start_at = %s
            """,
            (instrument_id, candle_start),
        )
    older_copy = SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=candle_start,
        open_price=Decimal("100"),
        high_price=Decimal("120"),
        low_price=Decimal("90"),
        close_price=Decimal("110"),
        trade_volume=Decimal("1.5"),
        trade_amount=Decimal("165"),
        collected_at=received_at - timedelta(seconds=1),
    )
    repository.record_backfill_candles(job_id, instrument_id, [older_copy])
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        repaired = connection.execute(
            """
            SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                   fetch_manifest_id
            FROM source_candles
            WHERE instrument_id = %s AND candle_unit = '1m' AND candle_start_at = %s
            """,
            (instrument_id, candle_start),
        ).fetchone()
    assert repaired is not None
    assert all(value is not None for value in repaired)


def test_realtime_source_rows_share_checksum_manifests_and_fill_five_timestamps() -> None:
    observed_at = datetime(2026, 7, 17, 9, tzinfo=UTC)
    database_url, market_id, instrument_id, _target_spec_id, _job_id = _prepare_market(
        "KRW-EVIDENCE-REALTIME", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    occurred_at = observed_at + timedelta(minutes=10)
    received_at = occurred_at + timedelta(seconds=1)
    ticker = TickerSnapshot(
        instrument_id=instrument_id,
        bucket_at=occurred_at,
        trade_price=Decimal("101"),
        acc_trade_price_24h=Decimal("1000"),
        change_rate=Decimal("0.01"),
        collected_at=received_at,
    )
    orderbook = OrderbookSummary(
        instrument_id=instrument_id,
        bucket_at=occurred_at,
        best_bid_price=Decimal("100"),
        best_bid_size=Decimal("1"),
        best_ask_price=Decimal("101"),
        best_ask_size=Decimal("2"),
        spread=Decimal("1"),
        bid_depth_10=Decimal("10"),
        ask_depth_10=Decimal("20"),
        imbalance_10=Decimal("-0.333"),
        collected_at=received_at,
    )
    candle = SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=occurred_at,
        open_price=Decimal("100"),
        high_price=Decimal("102"),
        low_price=Decimal("99"),
        close_price=Decimal("101"),
        trade_volume=Decimal("2"),
        trade_amount=Decimal("201"),
        collected_at=received_at,
    )
    trade = TradeEvent(
        instrument_id=instrument_id,
        sequential_id=202607160001,
        trade_timestamp_at=occurred_at,
        trade_price=Decimal("101"),
        trade_volume=Decimal("1"),
        trade_amount=Decimal("101"),
        ask_bid="BID",
        collected_at=received_at,
    )

    repository.record_incremental_collection([ticker], [orderbook], [candle])
    assert repository.record_trade_events([trade]) == 1

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        rows = connection.execute(
            """
            SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                   fetch_manifest_id
            FROM (
              SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                     fetch_manifest_id FROM ticker_snapshots WHERE instrument_id = %s
              UNION ALL
              SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                     fetch_manifest_id FROM orderbook_summaries WHERE instrument_id = %s
              UNION ALL
              SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                     fetch_manifest_id FROM source_candles WHERE instrument_id = %s
              UNION ALL
              SELECT market_id, occurred_at, received_at, stored_at, knowledge_at,
                     fetch_manifest_id FROM trade_events WHERE instrument_id = %s
            ) evidence
            """,
            (instrument_id, instrument_id, instrument_id, instrument_id),
        ).fetchall()
        manifests = connection.execute(
            """
            SELECT count(*), count(response_checksum), bool_and(outcome = 'succeeded')
            FROM fetch_manifests manifest
            JOIN collection_target_specs spec ON spec.id = manifest.target_spec_id
            WHERE spec.market_id = %s
            """,
            (market_id,),
        ).fetchone()

    assert len(rows) == 4
    assert all(row[0] == market_id for row in rows)
    assert all(row[1] == occurred_at for row in rows)
    assert all(row[2] == received_at for row in rows)
    assert all(row[3] is not None for row in rows)
    assert all(row[4] == received_at for row in rows)
    assert all(row[5] is not None for row in rows)
    assert manifests == (4, 4, True)


def test_empty_backfill_response_records_success_but_does_not_invent_coverage() -> None:
    observed_at = datetime(2026, 7, 17, 10, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-EMPTY", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)

    before = _coverage_rows(database_url, target_spec_id)
    assert repository.record_backfill_candles(job_id, instrument_id, []) == 0
    after = _coverage_rows(database_url, target_spec_id)

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifest = connection.execute(
            """
            SELECT response_status, response_checksum, outcome
            FROM fetch_manifests
            WHERE target_spec_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_spec_id,),
        ).fetchone()

    assert manifest is not None
    assert manifest[0] == 200
    assert manifest[1]
    assert manifest[2] == "succeeded"
    assert after == before
    assert all(row[2] != "no_trade" for row in after)


def test_failed_backfill_target_records_rate_limit_manifest_without_available_coverage() -> None:
    observed_at = datetime(2026, 7, 17, 11, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-FAILED", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)

    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_429",
        error_message="요청 수 제한",
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifest = connection.execute(
            """
            SELECT outcome, error_code, response_checksum
            FROM fetch_manifests
            WHERE target_spec_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_spec_id,),
        ).fetchone()
        statuses = connection.execute(
            "SELECT status FROM coverage_intervals WHERE target_spec_id = %s",
            (target_spec_id,),
        ).fetchall()

    assert manifest == ("rate_limited", "UPBIT_429", None)
    assert all(row[0] != "available" for row in statuses)
    assert all(row[0] != "no_trade" for row in statuses)


def test_failed_backfill_retries_after_delay_then_moves_to_dead_letter() -> None:
    observed_at = datetime(2026, 7, 17, 11, 30, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-RETRY", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)

    preserved_progress_at = observed_at - timedelta(minutes=10)
    repository.record_backfill_target_progress(
        job_id,
        instrument_id,
        processed_missing_range_count=1,
        estimated_missing_range_count=2,
        rows_written_count=20,
        last_completed_at=preserved_progress_at,
    )

    failed_at = datetime.now(UTC)
    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=preserved_progress_at - timedelta(minutes=1),
        error_code="UPBIT_418",
        error_message="일시 차단",
        retry_after_seconds=37,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        retrying = connection.execute(
            """
            SELECT status, attempt_count, next_retry_at, lease_owner, lease_expires_at,
                   last_error_code
            FROM backfill_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        target_status = connection.execute(
            """
            SELECT status, last_completed_at, processed_missing_range_count,
                   rows_written_count
            FROM backfill_job_targets
            WHERE backfill_job_id = %s AND instrument_id = %s
            """,
            (job_id, instrument_id),
        ).fetchone()
    assert retrying is not None
    assert retrying[0] == "retry_wait"
    assert retrying[1] == 1
    assert retrying[2] is not None
    assert retrying[3] is None
    assert retrying[4] is None
    assert retrying[5] == "UPBIT_418"
    assert retrying[2] >= failed_at + timedelta(seconds=36)
    assert target_status == ("pending", preserved_progress_at, 1, 20)
    assert repository.claim_next_backfill_job() is None

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET next_retry_at = now() - INTERVAL '1 second',
                attempt_count = max_attempts - 1
            WHERE id = %s
            """,
            (job_id,),
        )

    retried = repository.claim_next_backfill_job()
    assert retried is not None
    assert retried.id == job_id
    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_500",
        error_message="서버 오류",
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        exhausted = connection.execute(
            """
            SELECT status, attempt_count, max_attempts, next_retry_at, lease_owner,
                   lease_expires_at, last_error_code, dead_letter_reason
            FROM backfill_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
        target_status = connection.execute(
            """
            SELECT status
            FROM backfill_job_targets
            WHERE backfill_job_id = %s AND instrument_id = %s
            """,
            (job_id, instrument_id),
        ).fetchone()
        coverage_statuses = connection.execute(
            """
            SELECT DISTINCT status
            FROM coverage_intervals
            WHERE target_spec_id = %s
            """,
            (target_spec_id,),
        ).fetchall()
        quality_events = connection.execute(
            """
            SELECT event_type, previous_status, new_status, fetch_manifest_id
            FROM data_quality_events
            WHERE target_spec_id = %s
              AND event_type = 'backfill_attempts_exhausted'
            """,
            (target_spec_id,),
        ).fetchall()
    assert exhausted is not None
    assert exhausted[0] == "dead_letter"
    assert exhausted[1] == exhausted[2]
    assert exhausted[3] is None
    assert exhausted[4] is None
    assert exhausted[5] is None
    assert exhausted[6] == "UPBIT_500"
    assert exhausted[7]
    assert target_status == ("failed",)
    assert coverage_statuses == [("missing",)]
    assert [row[:3] for row in quality_events] == [
        ("backfill_attempts_exhausted", "unverified", "missing")
    ]
    assert quality_events[0][3] is not None


def test_live_backfill_lease_blocks_duplicate_claim_and_stale_worker_write() -> None:
    observed_at = datetime(2026, 7, 17, 11, 45, tzinfo=UTC)
    database_url, _market_id, instrument_id, _target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-FENCING", observed_at
    )
    first_worker = PostgresOperationsRepository(database_url)
    _claim_job(database_url, first_worker, job_id)

    assert first_worker.claim_next_backfill_job() is None

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET lease_expires_at = now() - INTERVAL '1 second'
            WHERE id = %s
            """,
            (job_id,),
        )

    second_worker = PostgresOperationsRepository(database_url)
    reclaimed = second_worker.claim_next_backfill_job()
    assert reclaimed is not None
    assert reclaimed.id == job_id

    with pytest.raises(RuntimeError, match="백필 쓰기 임대"):
        first_worker.record_backfill_target_progress(
            job_id,
            instrument_id,
            processed_missing_range_count=1,
            estimated_missing_range_count=1,
            rows_written_count=1,
            last_completed_at=observed_at,
        )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        lease = connection.execute(
            """
            SELECT status, attempt_count, lease_owner, lease_expires_at > now()
            FROM backfill_jobs
            WHERE id = %s
            """,
            (job_id,),
        ).fetchone()
    assert lease is not None
    assert lease[0] == "running"
    assert lease[1] == 2
    assert lease[2]
    assert lease[3] is True

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET attempt_count = max_attempts,
                lease_expires_at = now() - INTERVAL '1 second',
                last_error_code = 'WorkerTerminated'
            WHERE id = %s
            """,
            (job_id,),
        )

    assert PostgresOperationsRepository(database_url).claim_next_backfill_job() is None
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        isolated = connection.execute(
            """
            SELECT job.status, target.status, job.dead_letter_reason
            FROM backfill_jobs job
            JOIN backfill_job_targets target ON target.backfill_job_id = job.id
            WHERE job.id = %s AND target.instrument_id = %s
            """,
            (job_id, instrument_id),
        ).fetchone()
    assert isolated is not None
    assert isolated[0] == "dead_letter"
    assert isolated[1] == "failed"
    assert isolated[2]


def test_contiguous_backfill_candles_create_one_available_coverage_interval() -> None:
    observed_at = datetime(2026, 7, 17, 12, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-CONTIGUOUS", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    range_start = observed_at - timedelta(minutes=10)
    candles = [
        SourceCandle(
            instrument_id=instrument_id,
            candle_unit="1m",
            candle_start_at=range_start + timedelta(minutes=offset),
            open_price=Decimal("100"),
            high_price=Decimal("101"),
            low_price=Decimal("99"),
            close_price=Decimal("100"),
            trade_volume=Decimal("1"),
            trade_amount=Decimal("100"),
            collected_at=observed_at,
        )
        for offset in range(3)
    ]

    assert repository.record_backfill_candles(job_id, instrument_id, candles) == 3

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        available = connection.execute(
            """
            SELECT range_start_at, range_end_at, status,
                   evidence #>> '{naturalKey,rowCount}'
            FROM coverage_intervals
            WHERE target_spec_id = %s AND status = 'available'
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()

    assert available == [(range_start, range_start + timedelta(minutes=3), "available", "3")]


def test_backfill_page_records_available_and_only_internal_no_trade_events_once() -> None:
    observed_at = datetime(2026, 7, 17, 12, 30, tzinfo=UTC)
    database_url, market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-COVERAGE-STATES", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    requested_start_at = observed_at - timedelta(minutes=10)
    requested_end_at = requested_start_at + timedelta(minutes=5)
    candle_starts = (
        requested_start_at + timedelta(minutes=1),
        requested_start_at + timedelta(minutes=3),
    )
    payload = [
        {
            "market": "KRW-EVIDENCE-COVERAGE-STATES",
            "candle_date_time_utc": started_at.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        for started_at in reversed(candle_starts)
    ]
    evidence = FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={
            "market": "KRW-EVIDENCE-COVERAGE-STATES",
            "to": requested_end_at.isoformat().replace("+00:00", "Z"),
            "count": 200,
        },
        requested_at=observed_at,
        responded_at=observed_at + timedelta(milliseconds=10),
        response_status=200,
        response_payload=payload,
        requested_range_start_at=requested_start_at,
        requested_range_end_at=requested_end_at,
    )
    candles = [
        SourceCandle(
            instrument_id=instrument_id,
            candle_unit="1m",
            candle_start_at=started_at,
            open_price=Decimal("100"),
            high_price=Decimal("101"),
            low_price=Decimal("99"),
            close_price=Decimal("100"),
            trade_volume=Decimal("1"),
            trade_amount=Decimal("100"),
            collected_at=observed_at,
        )
        for started_at in candle_starts
    ]

    assert repository.record_backfill_candles(
        job_id, instrument_id, candles, fetch_evidence=evidence
    ) == 2
    assert repository.record_backfill_candles(
        job_id, instrument_id, candles, fetch_evidence=evidence
    ) == 2

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        coverage = connection.execute(
            """
            SELECT GREATEST(range_start_at, %s), LEAST(range_end_at, %s),
                   status, fetch_manifest_id
            FROM coverage_intervals
            WHERE target_spec_id = %s
              AND tstzrange(range_start_at, range_end_at, '[)')
                  && tstzrange(%s, %s, '[)')
            ORDER BY range_start_at
            """,
            (
                requested_start_at,
                requested_end_at,
                target_spec_id,
                requested_start_at,
                requested_end_at,
            ),
        ).fetchall()
        events = connection.execute(
            """
            SELECT previous_status, new_status, range_start_at, range_end_at,
                   event_type, fetch_manifest_id
            FROM data_quality_events
            WHERE target_spec_id = %s
              AND event_type IN ('source_row_observed', 'upbit_minute_candle_internal_gap')
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()
        initial_events = connection.execute(
            """
            SELECT event.new_status, event.event_type, event.previous_status
            FROM data_quality_events event
            JOIN collection_target_specs spec ON spec.id = event.target_spec_id
            WHERE spec.market_id = %s AND event.event_type = 'policy_initialization'
            ORDER BY event.new_status
            """,
            (market_id,),
        ).fetchall()

    assert [(row[0], row[1], row[2]) for row in coverage] == [
        (requested_start_at, requested_start_at + timedelta(minutes=1), "unverified"),
        (
            requested_start_at + timedelta(minutes=1),
            requested_start_at + timedelta(minutes=2),
            "available",
        ),
        (
            requested_start_at + timedelta(minutes=2),
            requested_start_at + timedelta(minutes=3),
            "no_trade",
        ),
        (
            requested_start_at + timedelta(minutes=3),
            requested_start_at + timedelta(minutes=4),
            "available",
        ),
        (requested_start_at + timedelta(minutes=4), requested_end_at, "unverified"),
    ]
    assert [(row[0], row[1], row[2], row[3], row[4]) for row in events] == [
        (
            "unverified",
            "available",
            requested_start_at + timedelta(minutes=1),
            requested_start_at + timedelta(minutes=2),
            "source_row_observed",
        ),
        (
            "unverified",
            "no_trade",
            requested_start_at + timedelta(minutes=2),
            requested_start_at + timedelta(minutes=3),
            "upbit_minute_candle_internal_gap",
        ),
        (
            "unverified",
            "available",
            requested_start_at + timedelta(minutes=3),
            requested_start_at + timedelta(minutes=4),
            "source_row_observed",
        ),
    ]
    assert all(row[5] is not None for row in events)
    assert initial_events == [
        ("unavailable", "policy_initialization", None),
        ("unavailable", "policy_initialization", None),
        ("unavailable", "policy_initialization", None),
        ("unverified", "policy_initialization", None),
    ]


def test_backfill_pages_store_raw_response_once_per_page_and_share_manifest_across_batches(
) -> None:
    observed_at = datetime(2026, 7, 17, 13, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-PAGES", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    page_one_raw = [
        {"market": "KRW-EVIDENCE-PAGES", "candle_date_time_utc": "2026-07-17T12:00:00"},
        {"market": "KRW-EVIDENCE-PAGES", "candle_date_time_utc": "2026-07-17T12:01:00"},
    ]
    page_two_raw = [
        {"market": "KRW-EVIDENCE-PAGES", "candle_date_time_utc": "2026-07-17T11:59:00"}
    ]
    first_evidence = _fetch_evidence(observed_at, "2026-07-17T12:02:00Z", page_one_raw)
    second_evidence = _fetch_evidence(
        observed_at + timedelta(seconds=1), "2026-07-17T12:00:00Z", page_two_raw
    )
    candles = [_evidence_candle(instrument_id, observed_at, offset) for offset in range(3)]

    assert repository.record_backfill_candles(
        job_id, instrument_id, candles[:1], fetch_evidence=first_evidence
    ) == 1
    assert repository.record_backfill_candles(
        job_id, instrument_id, candles[1:2], fetch_evidence=first_evidence
    ) == 1
    assert repository.record_backfill_candles(
        job_id, instrument_id, candles[2:], fetch_evidence=second_evidence
    ) == 1

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifests = connection.execute(
            """
            SELECT request_parameters, response_status, response_payload, outcome
            FROM fetch_manifests
            WHERE target_spec_id = %s
            ORDER BY requested_at
            """,
            (target_spec_id,),
        ).fetchall()
        row_manifest_ids = connection.execute(
            """
            SELECT fetch_manifest_id
            FROM source_candles
            WHERE instrument_id = %s AND candle_start_at >= %s
            ORDER BY candle_start_at
            """,
            (instrument_id, observed_at),
        ).fetchall()

    assert len(manifests) == 2
    assert manifests[0][0] == first_evidence.request_parameters
    assert manifests[0][1:] == (200, page_one_raw, "succeeded")
    assert manifests[1][0] == second_evidence.request_parameters
    assert manifests[1][1:] == (200, page_two_raw, "succeeded")
    assert row_manifest_ids[0][0] == row_manifest_ids[1][0]
    assert row_manifest_ids[2][0] != row_manifest_ids[0][0]


def test_empty_and_failed_backfill_pages_store_actual_raw_payloads() -> None:
    observed_at = datetime(2026, 7, 17, 14, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-EMPTY-FAILED", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    empty_evidence = _fetch_evidence(observed_at, "2026-07-17T14:00:00Z", [])
    error_payload = {"error": {"name": "too_many_requests", "message": "요청 수 제한"}}
    failed_evidence = _fetch_evidence(
        observed_at + timedelta(seconds=1),
        "2026-07-17T13:00:00Z",
        error_payload,
        status=429,
    )

    assert repository.record_backfill_candles(
        job_id, instrument_id, [], fetch_evidence=empty_evidence
    ) == 0
    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_429",
        error_message="요청 수 제한",
        fetch_evidence=failed_evidence,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifests = connection.execute(
            """
            SELECT response_status, response_payload, outcome, error_code
            FROM fetch_manifests
            WHERE target_spec_id = %s
            ORDER BY requested_at
            """,
            (target_spec_id,),
        ).fetchall()

    assert manifests == [
        (200, [], "succeeded", None),
        (429, error_payload, "rate_limited", "UPBIT_429"),
    ]


def test_transport_failure_manifest_distinguishes_no_raw_response() -> None:
    observed_at = datetime(2026, 7, 17, 15, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-NO-RESPONSE", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    evidence = FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={
            "market": "KRW-EVIDENCE-NO-RESPONSE",
            "to": "2026-07-17T15:01:00Z",
            "count": 200,
        },
        requested_at=observed_at,
        responded_at=observed_at + timedelta(seconds=10),
        response_status=None,
        response_payload=None,
        error_type="ReadTimeout",
        error_message="upstream timed out",
    )
    failed_at = datetime.now(UTC)

    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_ReadTimeout",
        error_message="upstream timed out",
        retry_after_seconds=None,
        fetch_evidence=evidence,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifest = connection.execute(
            """
            SELECT endpoint, request_parameters, requested_at, responded_at,
                   response_status, response_payload, outcome, error_code, error_message
            FROM fetch_manifests
            WHERE target_spec_id = %s
            ORDER BY requested_at DESC
            LIMIT 1
            """,
            (target_spec_id,),
        ).fetchone()
        job_retry = connection.execute(
            "SELECT next_retry_at FROM backfill_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()

    assert manifest == (
        "/v1/candles/minutes/1",
        {
            "market": "KRW-EVIDENCE-NO-RESPONSE",
            "to": "2026-07-17T15:01:00Z",
            "count": 200,
        },
        observed_at,
        observed_at + timedelta(seconds=10),
        None,
        None,
        "failed",
        "UPBIT_ReadTimeout",
        "upstream timed out",
    )
    assert job_retry is not None
    assert job_retry[0] >= failed_at + timedelta(seconds=4)


def test_418_without_retry_after_uses_repository_300_second_fallback() -> None:
    observed_at = datetime(2026, 7, 17, 16, tzinfo=UTC)
    database_url, _market_id, instrument_id, _target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-418-FALLBACK", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    evidence = FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={
            "market": "KRW-EVIDENCE-418-FALLBACK",
            "to": "2026-07-17T16:01:00Z",
            "count": 200,
        },
        requested_at=observed_at,
        responded_at=observed_at + timedelta(milliseconds=10),
        response_status=418,
        response_payload={"error": {"message": "요청 수 제한"}},
        error_type="HTTPStatusError",
        error_message="요청 수 제한",
    )
    failed_at = datetime.now(UTC)

    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_418",
        error_message="요청 수 제한",
        retry_after_seconds=None,
        fetch_evidence=evidence,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        job_retry = connection.execute(
            "SELECT next_retry_at FROM backfill_jobs WHERE id = %s",
            (job_id,),
        ).fetchone()

    assert job_retry is not None
    assert job_retry[0] >= failed_at + timedelta(seconds=299)


def test_final_failure_changes_only_job_range_and_links_current_job_manifest() -> None:
    observed_at = datetime(2026, 7, 17, 17, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-FAILED-RANGE", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    failed_start_at = observed_at - timedelta(minutes=10)
    failed_end_at = observed_at - timedelta(minutes=5)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET target_start_at = %s, target_end_at = %s,
                attempt_count = max_attempts
            WHERE id = %s
            """,
            (failed_start_at, failed_end_at, job_id),
        )
        unrelated_manifest_id = connection.execute(
            """
            INSERT INTO fetch_manifests (
              target_spec_id, source, endpoint, request_parameters,
              request_fingerprint, requested_at, responded_at, response_status,
              response_checksum, collector_version, schema_version, outcome, error_code
            )
            VALUES (
              %s, 'UPBIT', '/unrelated', '{}'::jsonb, 'unrelated-future-manifest',
              '2035-01-01T00:00:00Z', '2035-01-01T00:00:01Z', 500,
              NULL, 'test', 'test', 'failed', 'UNRELATED'
            )
            RETURNING id
            """,
            (target_spec_id,),
        ).fetchone()
    assert unrelated_manifest_id is not None
    failed_evidence = FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={
            "market": "KRW-EVIDENCE-FAILED-RANGE",
            "to": failed_end_at.isoformat().replace("+00:00", "Z"),
            "count": 200,
        },
        requested_at=observed_at + timedelta(seconds=1),
        responded_at=observed_at + timedelta(seconds=2),
        response_status=500,
        response_payload={"error": {"message": "현재 작업 실패"}},
        error_type="HTTPStatusError",
        error_message="현재 작업 실패",
        requested_range_start_at=failed_start_at,
        requested_range_end_at=failed_end_at,
    )

    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_500",
        error_message="현재 작업 실패",
        fetch_evidence=failed_evidence,
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        coverage = connection.execute(
            """
            SELECT range_start_at, range_end_at, status
            FROM coverage_intervals
            WHERE target_spec_id = %s
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()
        quality_event = connection.execute(
            """
            SELECT event.range_start_at, event.range_end_at, event.fetch_manifest_id,
                   manifest.endpoint, manifest.error_code
            FROM data_quality_events event
            LEFT JOIN fetch_manifests manifest ON manifest.id = event.fetch_manifest_id
            WHERE event.target_spec_id = %s
              AND event.event_type = 'backfill_attempts_exhausted'
            """,
            (target_spec_id,),
        ).fetchone()

    assert coverage == [
        (datetime(2024, 1, 1, tzinfo=UTC), failed_start_at, "unverified"),
        (failed_start_at, failed_end_at, "missing"),
        (failed_end_at, observed_at, "unverified"),
    ]
    assert quality_event is not None
    assert quality_event[:2] == (failed_start_at, failed_end_at)
    assert quality_event[2] != unrelated_manifest_id[0]
    assert quality_event[3:] == ("/v1/candles/minutes/1", "UPBIT_500")


def test_final_failure_preserves_classified_ranges_and_marks_only_gaps_and_unverified() -> None:
    observed_at = datetime(2026, 7, 17, 18, tzinfo=UTC)
    database_url, _market_id, instrument_id, target_spec_id, job_id = _prepare_market(
        "KRW-EVIDENCE-FAILED-MIXED", observed_at
    )
    repository = PostgresOperationsRepository(database_url)
    _claim_job(database_url, repository, job_id)
    failed_start_at = observed_at - timedelta(minutes=10)
    failed_end_at = failed_start_at + timedelta(minutes=7)
    minute = timedelta(minutes=1)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE backfill_jobs
            SET target_start_at = %s, target_end_at = %s,
                attempt_count = max_attempts
            WHERE id = %s
            """,
            (failed_start_at, failed_end_at, job_id),
        )
        connection.execute(
            "DELETE FROM coverage_intervals WHERE target_spec_id = %s",
            (target_spec_id,),
        )
        connection.cursor().executemany(
            """
            INSERT INTO coverage_intervals (
              target_spec_id, range_start_at, range_end_at, status, evidence, assessed_at
            )
            VALUES (%s, %s, %s, %s, '{}'::jsonb, %s)
            """,
            [
                (
                    target_spec_id,
                    datetime(2024, 1, 1, tzinfo=UTC),
                    failed_start_at,
                    "unverified",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_start_at,
                    failed_start_at + minute,
                    "available",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_start_at + 2 * minute,
                    failed_start_at + 3 * minute,
                    "no_trade",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_start_at + 3 * minute,
                    failed_start_at + 4 * minute,
                    "unverified",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_start_at + 4 * minute,
                    failed_start_at + 5 * minute,
                    "unavailable",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_start_at + 6 * minute,
                    failed_end_at,
                    "available",
                    observed_at,
                ),
                (
                    target_spec_id,
                    failed_end_at,
                    observed_at,
                    "unverified",
                    observed_at,
                ),
            ],
        )

    repository.mark_backfill_target(
        job_id,
        instrument_id,
        status="failed",
        last_completed_at=None,
        error_code="UPBIT_500",
        error_message="혼합 구간 최종 실패",
        fetch_evidence=FetchEvidence(
            endpoint="/v1/candles/minutes/1",
            request_parameters={"market": "KRW-EVIDENCE-FAILED-MIXED", "count": 200},
            requested_at=observed_at + timedelta(seconds=1),
            responded_at=observed_at + timedelta(seconds=2),
            response_status=500,
            response_payload={"error": {"message": "혼합 구간 최종 실패"}},
            error_type="HTTPStatusError",
            error_message="혼합 구간 최종 실패",
            requested_range_start_at=failed_start_at,
            requested_range_end_at=failed_end_at,
        ),
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        coverage = connection.execute(
            """
            SELECT range_start_at, range_end_at, status
            FROM coverage_intervals
            WHERE target_spec_id = %s
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()
        events = connection.execute(
            """
            SELECT previous_status, range_start_at, range_end_at
            FROM data_quality_events
            WHERE target_spec_id = %s
              AND event_type = 'backfill_attempts_exhausted'
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()

    assert coverage == [
        (datetime(2024, 1, 1, tzinfo=UTC), failed_start_at, "unverified"),
        (failed_start_at, failed_start_at + minute, "available"),
        (failed_start_at + minute, failed_start_at + 2 * minute, "missing"),
        (failed_start_at + 2 * minute, failed_start_at + 3 * minute, "no_trade"),
        (failed_start_at + 3 * minute, failed_start_at + 4 * minute, "missing"),
        (failed_start_at + 4 * minute, failed_start_at + 5 * minute, "unavailable"),
        (failed_start_at + 5 * minute, failed_start_at + 6 * minute, "missing"),
        (failed_start_at + 6 * minute, failed_end_at, "available"),
        (failed_end_at, observed_at, "unverified"),
    ]
    assert events == [
        (None, failed_start_at + minute, failed_start_at + 2 * minute),
        ("unverified", failed_start_at + 3 * minute, failed_start_at + 4 * minute),
        (None, failed_start_at + 5 * minute, failed_start_at + 6 * minute),
    ]


def _coverage_rows(database_url: str, target_spec_id: int) -> list[tuple[object, ...]]:
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        return connection.execute(
            """
            SELECT range_start_at, range_end_at, status, fetch_manifest_id
            FROM coverage_intervals
            WHERE target_spec_id = %s
            ORDER BY range_start_at
            """,
            (target_spec_id,),
        ).fetchall()


def test_realtime_orderbook_keeps_delivery_receipts_and_deduplicates_economic_snapshot() -> None:
    occurred_at = datetime(2026, 7, 25, 12, 34, 56, 789000, tzinfo=UTC)
    received_at = occurred_at - timedelta(milliseconds=250)
    market_code = f"KRW-EVIDENCE-ORDERBOOK-{uuid4().hex[:8].upper()}"
    connection_a = str(uuid4())
    connection_b = str(uuid4())
    database_url, market_id, instrument_id, _target_spec_id, _job_id = _prepare_market(
        market_code, occurred_at - timedelta(minutes=1)
    )
    repository = PostgresOperationsRepository(database_url)
    payload = _orderbook_payload(market_code, occurred_at)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        storage_started_at = connection.execute("SELECT clock_timestamp()").fetchone()
    assert storage_started_at is not None

    run_realtime_stream_collection(
        repository,
        [payload],
        connection_id=connection_a,
        flush_interval_seconds=0,
        now=_fixed_now(received_at),
    )
    conflicting_payload = {**payload, "total_bid_size": "931"}
    with pytest.raises(ValueError, match="connection_id/frame_sequence payload 불일치"):
        run_realtime_stream_collection(
            repository,
            [conflicting_payload],
            connection_id=connection_a,
            flush_interval_seconds=0,
            now=_fixed_now(received_at + timedelta(milliseconds=50)),
        )
    run_realtime_stream_collection(
        repository,
        [payload],
        connection_id=connection_a,
        flush_interval_seconds=0,
        now=_fixed_now(received_at),
    )
    run_realtime_stream_collection(
        repository,
        [payload],
        connection_id=connection_b,
        flush_interval_seconds=0,
        now=_fixed_now(received_at),
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        receipts = connection.execute(
            """
            SELECT connection_id::text, frame_sequence, occurred_at, received_at,
                   payload_checksum, raw_payload ->> 'code'
            FROM source_receipts
            WHERE market_id = %s
            ORDER BY connection_id
            """,
            (market_id,),
        ).fetchall()
        snapshots = connection.execute(
            """
            SELECT id, occurred_at, received_at, stored_at, knowledge_at,
                   total_ask_size, total_bid_size, level_count, level, stream_type,
                   payload_checksum
            FROM orderbook_snapshots
            WHERE market_id = %s
            """,
            (market_id,),
        ).fetchall()
        storage_finished_at = connection.execute("SELECT clock_timestamp()").fetchone()
        levels = connection.execute(
            """
            SELECT level_index, ask_price, ask_size, bid_price, bid_size
            FROM orderbook_snapshot_levels
            WHERE snapshot_id = %s
            ORDER BY level_index
            """,
            (snapshots[0][0],),
        ).fetchall()
        summary = connection.execute(
            """
            SELECT occurred_at, received_at, best_ask_price, best_bid_price,
                   ask_depth_10, bid_depth_10, imbalance_10
            FROM orderbook_summaries
            WHERE instrument_id = %s
            ORDER BY bucket_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()

    assert [(row[0], row[1]) for row in receipts] == [
        (connection_id, 1) for connection_id in sorted((connection_a, connection_b))
    ]
    assert all(row[2] == occurred_at for row in receipts)
    assert all(row[3] == received_at for row in receipts)
    assert all(row[3] < row[2] for row in receipts), "서버·로컬 시계 순서를 강제하지 않는다"
    assert len({row[4] for row in receipts}) == 1
    assert all(row[5] == market_code for row in receipts)
    assert len(snapshots) == 1
    assert storage_finished_at is not None
    assert snapshots[0][1] == occurred_at
    assert snapshots[0][2] == received_at
    assert storage_started_at[0] <= snapshots[0][3] <= storage_finished_at[0]
    assert snapshots[0][4] == received_at
    assert snapshots[0][5:10] == (
        Decimal("465"),
        Decimal("930"),
        30,
        Decimal("0"),
        "REALTIME",
    )
    assert snapshots[0][10] == receipts[0][4]
    assert len(levels) == 30
    assert levels == [
        (
            index,
            Decimal(1000 + index),
            Decimal(index + 1),
            Decimal(999 - index),
            Decimal((index + 1) * 2),
        )
        for index in range(30)
    ]
    assert summary is not None
    assert summary[0] == occurred_at
    assert summary[1] == received_at
    assert summary[2:6] == (
        Decimal("1000"),
        Decimal("999"),
        Decimal("55"),
        Decimal("110"),
    )
    assert summary[6] == Decimal("1") / Decimal("3")


def test_raw_source_retention_removes_only_rows_strictly_before_boundary() -> None:
    as_of = datetime(2026, 7, 30, tzinfo=UTC)
    market_code = f"KRW-EVIDENCE-RETENTION-{uuid4().hex[:8].upper()}"
    database_url, market_id, instrument_id, _target_spec_id, _job_id = _prepare_market(
        market_code, as_of
    )
    repository = PostgresOperationsRepository(database_url)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE collection_target_specs
            SET retention_days = 1
            WHERE market_id = %s AND data_type = 'orderbook_snapshot'
            """,
            (market_id,),
        )
    moments = (
        as_of - timedelta(days=1, milliseconds=1),
        as_of - timedelta(days=1),
        as_of - timedelta(hours=12),
    )
    for occurred_at in moments:
        run_realtime_stream_collection(
            repository,
            [_orderbook_payload(market_code, occurred_at)],
            connection_id=str(uuid4()),
            flush_interval_seconds=0,
            now=_fixed_now(occurred_at + timedelta(milliseconds=250)),
            purge_retention=False,
        )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        manifest_row = connection.execute(
            """
            SELECT fetch_manifest_id
            FROM orderbook_summaries
            WHERE instrument_id = %s
            ORDER BY bucket_at DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()
        assert manifest_row is not None
        manifest_id = int(manifest_row[0])
        connection.execute(
            """
            UPDATE source_receipts
            SET fetch_manifest_id = %s
            WHERE market_id = %s AND occurred_at = %s
            """,
            (manifest_id, market_id, moments[0]),
        )
        connection.execute(
            """
            UPDATE orderbook_snapshots
            SET fetch_manifest_id = %s
            WHERE market_id = %s AND occurred_at = %s
            """,
            (manifest_id, market_id, moments[0]),
        )
        policy_row = connection.execute(
            """
            INSERT INTO collection_policies (
              exchange, quote_currency, name, default_start_at, retention_days,
              priority, auto_include_new_markets, status
            )
            VALUES ('UPBIT', 'KRW', %s, %s, NULL, 100, false, 'active')
            RETURNING id
            """,
            (f"retention-{market_code}", as_of - timedelta(days=365)),
        ).fetchone()
        assert policy_row is not None
        secondary_spec_row = connection.execute(
            """
            INSERT INTO collection_target_specs (
              policy_id, market_id, data_type, candle_unit, range_start_at,
              retention_days, priority, continuous, auto_managed, status
            )
            VALUES (%s, %s, 'orderbook_snapshot', NULL, %s, NULL, 100, true, false, 'active')
            RETURNING id
            """,
            (policy_row[0], market_id, as_of - timedelta(days=365)),
        ).fetchone()
        assert secondary_spec_row is not None
        secondary_spec_id = int(secondary_spec_row[0])

    assert repository.purge_expired_source_evidence(as_of=as_of) == (0, 0)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            "UPDATE collection_target_specs SET retention_days = 2 WHERE id = %s",
            (secondary_spec_id,),
        )
    assert repository.purge_expired_source_evidence(as_of=as_of) == (0, 0)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            "UPDATE collection_target_specs SET retention_days = 1 WHERE id = %s",
            (secondary_spec_id,),
        )
    assert repository.purge_expired_source_evidence(as_of=as_of) == (1, 1)

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        receipt_times = connection.execute(
            "SELECT occurred_at FROM source_receipts WHERE market_id = %s ORDER BY occurred_at",
            (market_id,),
        ).fetchall()
        snapshot_times = connection.execute(
            "SELECT occurred_at FROM orderbook_snapshots WHERE market_id = %s ORDER BY occurred_at",
            (market_id,),
        ).fetchall()
        level_count = connection.execute(
            """
            SELECT count(*)
            FROM orderbook_snapshot_levels level_row
            JOIN orderbook_snapshots snapshot ON snapshot.id = level_row.snapshot_id
            WHERE snapshot.market_id = %s
            """,
            (market_id,),
        ).fetchone()
        summary_count = connection.execute(
            "SELECT count(*) FROM orderbook_summaries WHERE instrument_id = %s",
            (instrument_id,),
        ).fetchone()
        manifest_count = connection.execute(
            "SELECT count(*) FROM fetch_manifests WHERE id = %s",
            (manifest_id,),
        ).fetchone()

    assert receipt_times == [(moments[1],), (moments[2],)]
    assert snapshot_times == [(moments[1],), (moments[2],)]
    assert level_count == (60,)
    assert summary_count == (3,)
    assert manifest_count == (1,)


def _fetch_evidence(
    requested_at: datetime,
    to: str,
    payload: object,
    *,
    status: int = 200,
) -> FetchEvidence:
    return FetchEvidence(
        endpoint="/v1/candles/minutes/1",
        request_parameters={"market": "KRW-EVIDENCE-PAGES", "to": to, "count": 200},
        requested_at=requested_at,
        responded_at=requested_at + timedelta(milliseconds=10),
        response_status=status,
        response_payload=payload,
    )


def _evidence_candle(instrument_id: int, started_at: datetime, offset: int) -> SourceCandle:
    value = Decimal(100 + offset)
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=started_at + timedelta(minutes=offset),
        open_price=value,
        high_price=value,
        low_price=value,
        close_price=value,
        trade_volume=Decimal("1"),
        trade_amount=value,
        collected_at=started_at + timedelta(minutes=offset, seconds=1),
    )
