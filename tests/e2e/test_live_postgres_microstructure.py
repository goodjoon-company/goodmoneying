from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from goodmoneying_api.service import OperationsService
from goodmoneying_shared.data_foundation import MarketCatalogItem
from goodmoneying_shared.data_foundation_repository import PostgresDataFoundationRepository
from goodmoneying_shared.microstructure_store import (
    read_microstructure_statistics,
    run_next_microstructure_invalidation,
)
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_worker.realtime_stream_worker import run_realtime_stream_collection

pytestmark = pytest.mark.live


def _database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("실제 PostgreSQL 검증에서만 실행한다")
    return os.environ["GOODMONEYING_DATABASE_URL"]


def test_live_postgres_실시간_체결은_receipt와_같은_트랜잭션_계보를_보존한다() -> None:
    database_url = _database_url()
    market_code = f"KRW-MICRO-{uuid4().hex[:8].upper()}"
    observed_at = datetime(2026, 8, 10, 1, tzinfo=UTC)
    connected_at = datetime(2026, 7, 17, 5, tzinfo=UTC)
    received_at = connected_at + timedelta(minutes=2, seconds=5)
    PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [
            MarketCatalogItem(
                market_code=market_code,
                korean_name="미시구조 실증",
                english_name="Microstructure Evidence",
                market_warning="NONE",
                tradable=True,
            )
        ],
        observed_at=observed_at,
    )
    repository = PostgresOperationsRepository(database_url)
    times = iter((connected_at, received_at, received_at + timedelta(seconds=1)))
    trade_at = connected_at + timedelta(seconds=30)

    assert (
        run_realtime_stream_collection(
            repository,
            [
                {
                    "type": "trade",
                    "code": market_code,
                    "timestamp": int(trade_at.timestamp() * 1000),
                    "trade_timestamp": int(trade_at.timestamp() * 1000),
                    "trade_price": "1000",
                    "trade_volume": "2",
                    "ask_bid": "BID",
                    "sequential_id": 700001,
                }
            ],
            connection_id=str(uuid4()),
            allowed_market_types={(market_code, "trade")},
            flush_interval_seconds=0,
            now=lambda: next(times),
            purge_retention=False,
            subscription_generation=7,
        )
        == 1
    )

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        evidence = connection.execute(
            """
            SELECT trade.source_receipt_id, receipt.id, trade.fetch_manifest_id,
                   run.data_type, result.data_type, coverage.status,
                   session.subscription_generation, session.status
            FROM trade_events trade
            JOIN source_receipts receipt ON receipt.id=trade.source_receipt_id
            JOIN collection_runs run ON run.id=trade.collection_run_id
            JOIN target_collection_results result
              ON result.collection_run_id=run.id AND result.instrument_id=trade.instrument_id
            JOIN fetch_manifests manifest ON manifest.id=trade.fetch_manifest_id
            JOIN coverage_intervals coverage
              ON coverage.target_spec_id=manifest.target_spec_id
             AND coverage.fetch_manifest_id=manifest.id
            JOIN realtime_connection_sessions session
              ON session.connection_id=receipt.connection_id
            WHERE trade.instrument_id=(
              SELECT id FROM instruments WHERE market_code=%s
            ) AND trade.sequential_id=700001
            """,
            (market_code,),
        ).fetchone()
        quality_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM realtime_connection_quality_intervals quality
            JOIN realtime_connection_sessions session USING (connection_id)
            WHERE session.subscription_generation=7
              AND quality.data_type='trade_event' AND quality.quality='available'
            """
        ).fetchone()
        invalidation = connection.execute(
            """
            SELECT id, connection_quality_through_id
            FROM microstructure_invalidations
            WHERE instrument_id=(SELECT id FROM instruments WHERE market_code=%s)
              AND bucket_start_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (market_code, connected_at),
        ).fetchone()

    assert evidence is not None
    assert evidence[:6] == (
        evidence[1],
        evidence[1],
        evidence[2],
        "trade_event",
        "trade_event",
        "available",
    )
    assert evidence[2] is not None
    assert evidence[6:] == (7, "closed")
    assert quality_count is not None and quality_count[0] == 2
    assert invalidation is not None and invalidation[1] > 0

    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET max_attempts=1, next_retry_at=clock_timestamp()
            WHERE id=%s
            """,
            (invalidation[0],),
        )
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE id<>%s AND status IN ('pending','retry_wait')
            """,
            (invalidation[0],),
        )
    assert run_next_microstructure_invalidation(repository, "microstructure-live") == 0
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        status = connection.execute(
            "SELECT status, last_error_code FROM microstructure_invalidations WHERE id=%s",
            (invalidation[0],),
        ).fetchone()
    assert status == ("dead_letter", "candle_unverified")

    failed_connection_id = str(uuid4())
    failure_started_at = connected_at + timedelta(minutes=10, seconds=30)
    failure_ended_at = failure_started_at + timedelta(minutes=2)
    failure_bucket_at = failure_started_at.replace(second=0)

    def failed_messages() -> Iterator[Mapping[str, object]]:
        for _ in range(0):
            yield {}
        raise RuntimeError("websocket disconnected")

    failed_times = iter((failure_started_at, failure_ended_at))
    with pytest.raises(RuntimeError, match="websocket disconnected"):
        run_realtime_stream_collection(
            repository,
            failed_messages(),
            connection_id=failed_connection_id,
            allowed_market_types={(market_code, "trade")},
            now=lambda: next(failed_times),
            purge_retention=False,
        )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        closed_quality = connection.execute(
            """
            SELECT quality, range_start_at, range_end_at
            FROM realtime_connection_quality_intervals
            WHERE connection_id=%s
            ORDER BY range_start_at
            """,
            (failed_connection_id,),
        ).fetchall()
    assert closed_quality == [
        (
            "unverified",
            failure_started_at.replace(second=0),
            failure_started_at.replace(second=0) + timedelta(minutes=1),
        ),
        (
            "missing",
            failure_started_at.replace(second=0) + timedelta(minutes=1),
            failure_started_at.replace(second=0) + timedelta(minutes=2),
        ),
        (
            "unverified",
            failure_started_at.replace(second=0) + timedelta(minutes=2),
            failure_started_at.replace(second=0) + timedelta(minutes=3),
        ),
    ]
    reconnect_id = str(uuid4())
    reconnected_at = failure_ended_at + timedelta(minutes=2)
    repository.open_realtime_connection_session(
        reconnect_id,
        subscription_generation=8,
        instrument_ids=[target.id for target in repository.list_active_targets()],
        data_types=["trade_event"],
        connected_at=reconnected_at,
    )
    repository.close_realtime_connection_session(
        reconnect_id,
        disconnected_at=reconnected_at + timedelta(seconds=1),
        failed=False,
        reason=None,
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        reconnect_gap = connection.execute(
            """
            SELECT range_start_at, range_end_at, quality
            FROM realtime_connection_quality_intervals
            WHERE connection_id=%s AND reason_code='reconnection_gap'
            """,
            (failed_connection_id,),
        ).fetchone()
    assert reconnect_gap == (
        failure_bucket_at + timedelta(minutes=2),
        failure_bucket_at + timedelta(minutes=5),
        "missing",
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        identifiers = connection.execute(
            """
            SELECT market.id, market.legacy_instrument_id, specification.id
            FROM markets market
            JOIN collection_target_specs specification
              ON specification.market_id=market.id
             AND specification.data_type='source_candle'
             AND specification.candle_unit='1m'
            WHERE market.market_code=%s
            """,
            (market_code,),
        ).fetchone()
        assert identifiers is not None
        market_id, instrument_id, target_spec_id = identifiers
        event = connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence, detected_at
            ) VALUES (%s,'connection_gap',NULL,'unavailable',%s,%s,%s,'{}'::jsonb,%s)
            RETURNING id
            """,
            (
                target_spec_id,
                failure_bucket_at,
                failure_bucket_at + timedelta(minutes=1),
                uuid4().hex,
                failure_ended_at + timedelta(seconds=1),
            ),
        ).fetchone()
        assert event is not None
        target = connection.execute(
            """
            SELECT id FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s
              AND changed_quality_event_id=%s
            """,
            (instrument_id, failure_bucket_at, event[0]),
        ).fetchone()
        assert target is not None
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE id<>%s AND status IN ('pending','retry_wait')
            """,
            (target[0],),
        )
    assert run_next_microstructure_invalidation(repository, "microstructure-close-quality") > 0
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        close_point = connection.execute(
            """
            SELECT statistic.trade_status, statistic.trade_quality, statistic.trade_count
            FROM microstructure_statistics statistic
            JOIN microstructure_materializations materialization
              ON materialization.id=statistic.materialization_id
            WHERE materialization.instrument_id=%s
              AND materialization.bucket_start_at=%s
            ORDER BY materialization.id DESC LIMIT 1
            """,
            (instrument_id, failure_bucket_at),
        ).fetchone()
    assert close_point == ("missing", "unverified", None)


def test_live_postgres_수집_누락은_재시도_대신_품질_상태로_물질화한다() -> None:
    database_url = _database_url()
    market_code = f"KRW-MICRO-QUALITY-{uuid4().hex[:8].upper()}"
    observed_at = datetime(2026, 8, 11, 1, tzinfo=UTC)
    bucket_at = datetime(2026, 7, 17, 5, 10, tzinfo=UTC)
    PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [
            MarketCatalogItem(
                market_code=market_code,
                korean_name="미시구조 품질 실증",
                english_name="Microstructure Quality",
                market_warning="NONE",
                tradable=True,
            )
        ],
        observed_at=observed_at,
    )
    repository = PostgresOperationsRepository(database_url)
    connection_id = str(uuid4())
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        identifiers = connection.execute(
            """
            SELECT market.id, market.legacy_instrument_id, specification.id
            FROM markets market
            JOIN collection_target_specs specification
              ON specification.market_id=market.id
             AND specification.data_type='source_candle'
             AND specification.candle_unit='1m'
            WHERE market.market_code=%s
            """,
            (market_code,),
        ).fetchone()
        assert identifiers is not None
        market_id, instrument_id, target_spec_id = identifiers
        connection.execute(
            """
            INSERT INTO realtime_connection_sessions (
              connection_id, subscription_scope, connected_at, disconnected_at, status
            ) VALUES (%s, %s::jsonb, %s, %s, 'closed')
            """,
            (
                connection_id,
                '{"marketIds":[],"dataTypes":["trade_event"]}',
                bucket_at,
                bucket_at + timedelta(minutes=1),
            ),
        )
        quality = connection.execute(
            """
            INSERT INTO realtime_connection_quality_intervals (
              connection_id, market_id, data_type, range_start_at, range_end_at,
              quality, reason_code, fingerprint, detected_at
            ) VALUES (%s,%s,'trade_event',%s,%s,'unavailable','disconnect',%s,%s)
            RETURNING id
            """,
            (
                connection_id,
                market_id,
                bucket_at,
                bucket_at + timedelta(minutes=1),
                uuid4().hex * 2,
                bucket_at + timedelta(minutes=2),
            ),
        ).fetchone()
        assert quality is not None
        quality_event_ids: list[int] = []
        quality_detected_at: list[datetime] = []
        for index, source_quality in enumerate(("unavailable", "missing", "unavailable"), start=3):
            detected_at = bucket_at + timedelta(minutes=index)
            event = connection.execute(
                """
                INSERT INTO data_quality_events (
                  target_spec_id, event_type, previous_status, new_status,
                  range_start_at, range_end_at, fingerprint, evidence, detected_at
                ) VALUES (%s,'collection_gap',NULL,%s,%s,%s,%s,'{}'::jsonb,%s)
                RETURNING id
                """,
                (
                    target_spec_id,
                    source_quality,
                    bucket_at,
                    bucket_at + timedelta(minutes=1),
                    uuid4().hex,
                    detected_at,
                ),
            ).fetchone()
            assert event is not None
            quality_event_ids.append(int(event[0]))
            quality_detected_at.append(detected_at)
        serial_quality_invalidations = connection.execute(
            """
            SELECT COUNT(*) FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s
              AND changed_quality_event_id IS NOT NULL
            """,
            (instrument_id, bucket_at),
        ).fetchone()
        assert serial_quality_invalidations == (3,)
        quality_invalidations = connection.execute(
            """
            SELECT id FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s
              AND changed_quality_event_id = ANY(%s)
            ORDER BY id
            """,
            (instrument_id, bucket_at, quality_event_ids),
        ).fetchall()
        assert len(quality_invalidations) == 3
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='retry_wait', next_retry_at=clock_timestamp() + interval '1 hour'
            WHERE id=%s
            """,
            (quality_invalidations[0][0],),
        )
        connection.execute(
            """
            UPDATE microstructure_invalidations SET max_attempts=1
            WHERE id=ANY(%s)
            """,
            ([quality_invalidations[1][0], quality_invalidations[2][0]],),
        )
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE status IN ('pending','retry_wait')
              AND (
                changed_quality_event_id IS NULL
                OR changed_quality_event_id <> ALL(%s)
              )
            """,
            (quality_event_ids,),
        )
    assert run_next_microstructure_invalidation(repository, "blocked-frontier-1") == 0
    assert run_next_microstructure_invalidation(repository, "blocked-frontier-2") == 0
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        blocked_states = connection.execute(
            """
            SELECT status, attempt_count FROM microstructure_invalidations
            WHERE id=ANY(%s) ORDER BY id
            """,
            ([row[0] for row in quality_invalidations],),
        ).fetchall()
        assert blocked_states == [
            ("retry_wait", 0),
            ("pending", 0),
            ("pending", 0),
        ]
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='running', next_retry_at=clock_timestamp(),
                lease_owner='stale-worker',
                lease_expires_at=clock_timestamp() - interval '1 second',
                lease_generation=7
            WHERE id=%s
            """,
            (quality_invalidations[0][0],),
        )

    for index in range(3):
        assert (
            run_next_microstructure_invalidation(repository, f"microstructure-quality-{index}") > 0
        )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        reclaimed = connection.execute(
            """
            SELECT status, lease_generation FROM microstructure_invalidations
            WHERE id=%s
            """,
            (quality_invalidations[0][0],),
        ).fetchone()
        materializations = connection.execute(
            """
            SELECT materialization.id, materialization.parent_materialization_id,
                   materialization.quality_event_through_id,
                   materialization.knowledge_at, statistic.trade_status,
                   statistic.trade_quality, statistic.trade_count,
                   materialization.connection_quality_through_id
            FROM microstructure_statistics statistic
            JOIN microstructure_materializations materialization
              ON materialization.id=statistic.materialization_id
            WHERE materialization.instrument_id=%s
              AND materialization.bucket_start_at=%s
            ORDER BY materialization.id
            """,
            (instrument_id, bucket_at),
        ).fetchall()
    assert reclaimed == ("succeeded", 8)
    assert len(materializations) == 3
    assert [row[1] for row in materializations] == [
        None,
        materializations[0][0],
        materializations[1][0],
    ]
    assert [row[2] for row in materializations] == quality_event_ids
    assert [row[3] for row in materializations] == quality_detected_at
    assert all(row[4:] == ("missing", "unavailable", None, quality[0]) for row in materializations)
    for event_id, as_of in zip(quality_event_ids, quality_detected_at, strict=True):
        projected = read_microstructure_statistics(
            repository,
            int(instrument_id),
            bucket_at,
            bucket_at + timedelta(minutes=1),
            as_of,
        )
        assert len(projected) == 1
        assert projected[0].point.quality_event_through_id == event_id

    second_bucket_at = bucket_at + timedelta(minutes=1)
    initial_second_detected_at = bucket_at + timedelta(minutes=6)
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        connection.execute(
            """
            INSERT INTO realtime_connection_quality_intervals (
              connection_id, market_id, data_type, range_start_at, range_end_at,
              quality, reason_code, fingerprint, detected_at
            ) VALUES (%s,%s,'trade_event',%s,%s,'unavailable','disconnect',%s,%s)
            """,
            (
                connection_id,
                market_id,
                second_bucket_at,
                second_bucket_at + timedelta(minutes=1),
                uuid4().hex * 2,
                initial_second_detected_at,
            ),
        )
        initial_second_event = connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence, detected_at
            ) VALUES (%s,'collection_gap',NULL,'unavailable',%s,%s,%s,'{}'::jsonb,%s)
            RETURNING id
            """,
            (
                target_spec_id,
                second_bucket_at,
                second_bucket_at + timedelta(minutes=1),
                uuid4().hex,
                initial_second_detected_at,
            ),
        ).fetchone()
        assert initial_second_event is not None
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE status IN ('pending','retry_wait')
              AND NOT (
                instrument_id=%s AND bucket_start_at=%s
                AND changed_quality_event_id=%s
              )
            """,
            (instrument_id, second_bucket_at, initial_second_event[0]),
        )
    assert run_next_microstructure_invalidation(repository, "microstructure-page-initial") > 0

    service = OperationsService(repository)
    page_as_of = bucket_at + timedelta(minutes=10)
    first_page = service.microstructure_statistics(
        int(instrument_id),
        "1m",
        bucket_at,
        second_bucket_at + timedelta(minutes=1),
        as_of=page_as_of,
        page_size=1,
        cursor=None,
        calculation_version=None,
    )
    assert len(first_page.items) == 1
    assert first_page.items[0].startedAt == bucket_at
    assert first_page.nextCursor is not None
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        old_second = connection.execute(
            """
            SELECT id FROM microstructure_materializations
            WHERE instrument_id=%s AND bucket_start_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument_id, second_bucket_at),
        ).fetchone()
        assert old_second is not None
        old_second_id = int(old_second[0])
        late_second_event = connection.execute(
            """
            INSERT INTO data_quality_events (
              target_spec_id, event_type, previous_status, new_status,
              range_start_at, range_end_at, fingerprint, evidence, detected_at
            ) VALUES (%s,'collection_gap','unavailable','missing',%s,%s,%s,'{}'::jsonb,%s)
            RETURNING id
            """,
            (
                target_spec_id,
                second_bucket_at,
                second_bucket_at + timedelta(minutes=1),
                uuid4().hex,
                bucket_at + timedelta(minutes=7),
            ),
        ).fetchone()
        assert late_second_event is not None
        late_invalidation = connection.execute(
            """
            SELECT id FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s
              AND changed_quality_event_id=%s
            """,
            (instrument_id, second_bucket_at, late_second_event[0]),
        ).fetchone()
        assert late_invalidation is not None
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE status IN ('pending','retry_wait') AND id<>%s
            """,
            (late_invalidation[0],),
        )
    assert run_next_microstructure_invalidation(repository, "microstructure-page-late") > 0
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        new_second = connection.execute(
            """
            SELECT id FROM microstructure_materializations
            WHERE instrument_id=%s AND bucket_start_at=%s
            ORDER BY id DESC LIMIT 1
            """,
            (instrument_id, second_bucket_at),
        ).fetchone()
    assert new_second is not None and int(new_second[0]) > old_second_id

    second_page = service.microstructure_statistics(
        int(instrument_id),
        "1m",
        bucket_at,
        second_bucket_at + timedelta(minutes=1),
        as_of=page_as_of,
        page_size=1,
        cursor=first_page.nextCursor,
        calculation_version=None,
    )
    assert len(second_page.items) == 1
    assert second_page.items[0].startedAt == second_bucket_at
    assert second_page.items[0].materializationId == old_second_id
    with pytest.raises(ValueError, match="cursor의 미시구조 조회 문맥"):
        service.microstructure_statistics(
            int(instrument_id),
            "1m",
            bucket_at,
            second_bucket_at + timedelta(minutes=1),
            as_of=page_as_of + timedelta(seconds=1),
            page_size=1,
            cursor=first_page.nextCursor,
            calculation_version=None,
        )
    with pytest.raises(ValueError, match="cursor의 미시구조 조회 문맥"):
        service.microstructure_statistics(
            int(instrument_id),
            "1m",
            bucket_at,
            second_bucket_at + timedelta(minutes=2),
            as_of=page_as_of,
            page_size=1,
            cursor=first_page.nextCursor,
            calculation_version=None,
        )


def test_live_postgres_실제_호가_체결_캔들은_ready_미시구조로_물질화한다() -> None:
    database_url = _database_url()
    market_code = f"KRW-MICRO-READY-{uuid4().hex[:8].upper()}"
    bucket_at = datetime(2026, 7, 17, 6, tzinfo=UTC)
    PostgresDataFoundationRepository(database_url).sync_market_catalog(
        [
            MarketCatalogItem(
                market_code=market_code,
                korean_name="미시구조 성공 실증",
                english_name="Microstructure Ready",
                market_warning="NONE",
                tradable=True,
            )
        ],
        observed_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    repository = PostgresOperationsRepository(database_url)
    instrument = next(
        item for item in repository.list_active_targets() if item.market_code == market_code
    )
    received_at = bucket_at + timedelta(minutes=1, seconds=5)
    messages: list[Mapping[str, object]] = [
        {
            "type": "orderbook",
            "code": market_code,
            "timestamp": int((bucket_at + timedelta(seconds=50)).timestamp() * 1000),
            "total_ask_size": "55",
            "total_bid_size": "110",
            "level": 0,
            "stream_type": "REALTIME",
            "orderbook_units": [
                {
                    "ask_price": str(101 + index),
                    "ask_size": str(index + 1),
                    "bid_price": str(100 - index),
                    "bid_size": str((index + 1) * 2),
                }
                for index in range(10)
            ],
        },
        {
            "type": "trade",
            "code": market_code,
            "timestamp": int((bucket_at + timedelta(seconds=10)).timestamp() * 1000),
            "trade_timestamp": int((bucket_at + timedelta(seconds=10)).timestamp() * 1000),
            "trade_price": "100",
            "trade_volume": "2",
            "ask_bid": "BID",
            "sequential_id": 880001,
        },
        {
            "type": "trade",
            "code": market_code,
            "timestamp": int((bucket_at + timedelta(seconds=20)).timestamp() * 1000),
            "trade_timestamp": int((bucket_at + timedelta(seconds=20)).timestamp() * 1000),
            "trade_price": "101",
            "trade_volume": "1",
            "ask_bid": "ASK",
            "sequential_id": 880002,
        },
    ]
    clock_values = iter([bucket_at, received_at, received_at, received_at, received_at])
    assert (
        run_realtime_stream_collection(
            repository,
            messages,
            connection_id=str(uuid4()),
            allowed_market_types={(market_code, "orderbook"), (market_code, "trade")},
            flush_interval_seconds=0,
            now=lambda: next(clock_values),
            purge_retention=False,
        )
        == 3
    )
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=bucket_at,
                open_price=Decimal("100"),
                high_price=Decimal("101"),
                low_price=Decimal("100"),
                close_price=Decimal("101"),
                trade_volume=Decimal("3"),
                trade_amount=Decimal("301"),
                collected_at=received_at + timedelta(seconds=1),
            )
        ],
    )
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        target = connection.execute(
            """
            SELECT id FROM microstructure_invalidations
            WHERE instrument_id=%s AND bucket_start_at=%s
              AND changed_source_candle_revision_id IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (instrument.id, bucket_at),
        ).fetchone()
        assert target is not None
        connection.execute(
            """
            UPDATE microstructure_invalidations
            SET status='succeeded', finished_at=clock_timestamp()
            WHERE status IN ('pending','retry_wait') AND id<>%s
            """,
            (target[0],),
        )
    assert run_next_microstructure_invalidation(repository, "microstructure-ready") > 0
    with psycopg.connect(database_url, options="-c timezone=UTC") as connection:
        ready = connection.execute(
            """
            SELECT statistic.spread, statistic.bid_depth_10,
                   statistic.ask_depth_10, statistic.orderbook_imbalance_10,
                   statistic.trade_count, statistic.trade_intensity_per_minute,
                   statistic.volume_intensity_per_minute,
                   statistic.bid_count, statistic.ask_count,
                   statistic.bid_volume, statistic.ask_volume,
                   statistic.bid_ask_imbalance, statistic.execution_strength,
                   statistic.orderbook_status, statistic.trade_status,
                   statistic.execution_strength_status,
                   materialization.source_candle_revision_id,
                   materialization.connection_quality_through_id
            FROM microstructure_statistics statistic
            JOIN microstructure_materializations materialization
              ON materialization.id=statistic.materialization_id
            WHERE materialization.instrument_id=%s
              AND materialization.bucket_start_at=%s
            ORDER BY materialization.id DESC LIMIT 1
            """,
            (instrument.id, bucket_at),
        ).fetchone()
    assert ready is not None
    assert ready[:3] == (
        Decimal("1"),
        Decimal("110"),
        Decimal("55"),
    )
    assert abs(ready[3] - Decimal("1") / Decimal("3")) < Decimal("1e-27")
    assert ready[4:11] == (
        2,
        Decimal("2"),
        Decimal("3"),
        1,
        1,
        Decimal("2"),
        Decimal("1"),
    )
    assert abs(ready[11] - Decimal("1") / Decimal("3")) < Decimal("1e-27")
    assert ready[12] == Decimal("200")
    assert ready[13:16] == ("ready", "ready", "ready")
    assert ready[16] is not None
    assert ready[17] > 0
