from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import psycopg
import pytest
from psycopg import sql

from goodmoneying_shared.models import Instrument, SourceCandle
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
from goodmoneying_shared.time import KST

pytestmark = pytest.mark.live


def live_database_url() -> str:
    if os.getenv("GOODMONEYING_LIVE_POSTGRES_TEST") != "1":
        pytest.skip("GOODMONEYING_LIVE_POSTGRES_TEST=1에서만 실제 PostgreSQL을 검증한다")
    database_url = os.getenv("GOODMONEYING_DATABASE_URL")
    assert database_url, "실제 PostgreSQL 검증에는 GOODMONEYING_DATABASE_URL이 필요하다"
    return database_url


def _prepare_p2_market(database_url: str) -> Instrument:
    with psycopg.connect(database_url) as connection:
        row = connection.execute(
            """
            SELECT market.market_code, market.korean_name
            FROM markets market
            JOIN collection_target_specs specification
              ON specification.market_id = market.id
            WHERE market.legacy_instrument_id IS NOT NULL
              AND specification.data_type = 'source_candle'
              AND specification.candle_unit = '1m'
            ORDER BY market.market_code
            LIMIT 1
            """
        ).fetchone()
    assert row is not None, "캔들 E2E 전에 데이터 기반 시장 fixture가 필요하다."
    return PostgresOperationsRepository(database_url).upsert_instrument(str(row[0]), str(row[1]))


def test_live_postgres_집계_작업_혼합_상태의_건수와_진행률이_sqlite와_동일하다() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    instrument = repository.refresh_candidate_universe(
        [("KRW-LIVEAGG", "실제 DB 집계 검증", "100")]
    )[0].instrument
    repository.update_active_targets([instrument.id], "실제 PostgreSQL 집계 E2E")
    started_at = datetime(2026, 7, 16, 9, 0, tzinfo=KST)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=started_at,
                open_price=Decimal("100"),
                high_price=Decimal("100"),
                low_price=Decimal("100"),
                close_price=Decimal("100"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("100"),
                collected_at=started_at,
            )
        ],
    )
    scheduled = repository.schedule_candle_aggregation()
    assert scheduled is not None
    job = repository.claim_next_candle_aggregation_job()
    assert job is not None
    targets = repository.candle_aggregation_job_targets(job.id)

    repository.mark_candle_aggregation_target(
        job.id, targets[0].instrument_id, targets[0].candle_unit, "succeeded", 1
    )
    repository.mark_candle_aggregation_target(
        job.id, targets[1].instrument_id, targets[1].candle_unit, "running", 0
    )
    repository.mark_candle_aggregation_target(
        job.id, targets[2].instrument_id, targets[2].candle_unit, "failed", 0
    )
    latest = repository.latest_candle_aggregation_job()

    assert latest is not None
    assert latest.total_target_count == 10
    assert latest.completed_target_count == 1
    assert latest.running_target_count == 1
    assert latest.pending_target_count == 7
    assert latest.failed_target_count == 1
    assert latest.total_target_count == (
        latest.completed_target_count
        + latest.running_target_count
        + latest.pending_target_count
        + latest.failed_target_count
    )
    assert latest.progress_percent == Decimal("100") / Decimal("10")


def test_live_postgres_source_revision_and_rollup_lineage_range_query() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    started_at = datetime(2026, 7, 17, tzinfo=UTC)
    instrument = _prepare_p2_market(database_url)

    def candle(close: str, collected_offset: int) -> SourceCandle:
        return SourceCandle(
            instrument_id=instrument.id,
            candle_unit="1m",
            candle_start_at=started_at,
            open_price=Decimal("100"),
            high_price=Decimal("101"),
            low_price=Decimal("99"),
            close_price=Decimal(close),
            trade_volume=Decimal("1.25"),
            trade_amount=Decimal("125.125"),
            collected_at=started_at + timedelta(seconds=collected_offset),
        )

    repository.record_incremental_collection([], [], [candle("100", 1)])
    repository.record_incremental_collection([], [], [candle("100", 1)])
    repository.record_incremental_collection([], [], [candle("100.5", 3)])
    repository.record_incremental_collection([], [], [candle("100.25", 2)])
    assert repository.materialize_candle_rollups(instrument.id, "3m") >= 1

    with psycopg.connect(database_url) as connection:
        revisions = connection.execute(
            """
            SELECT id, revision_number, close_price, input_content_hash
            FROM source_candle_revisions
            WHERE instrument_id = %s AND candle_start_at = %s
            ORDER BY revision_number
            """,
            (instrument.id, started_at),
        ).fetchall()
        rollup = connection.execute(
            """
            SELECT calculation_version, source_as_of, knowledge_at,
                   input_content_hash, quality, completeness, input_revision_ids,
                   close_price
            FROM candle_rollups
            WHERE instrument_id = %s AND candle_unit = '3m'
              AND candle_start_at >= %s AND candle_start_at < %s
            """,
            (instrument.id, started_at, started_at + timedelta(minutes=3)),
        ).fetchone()

    assert [(row[1], str(row[2])) for row in revisions] == [
        (1, "100"),
        (2, "100.5"),
        (3, "100.25"),
    ]
    assert len({row[3] for row in revisions}) == 3
    assert rollup is not None
    assert rollup[0] == "candle-rollup-v2"
    assert rollup[1] <= rollup[2]
    assert rollup[3]
    assert rollup[4] in {"available", "no_trade", "missing", "unavailable", "unverified"}
    assert rollup[5] in {"complete", "partial", "empty"}
    assert rollup[6] == [revisions[1][0]]
    assert str(rollup[7]) == "100.5"

    recurrence_start = started_at + timedelta(minutes=10)

    def recurrence(close: str, offset: int) -> SourceCandle:
        return SourceCandle(
            **{
                **candle(close, offset).__dict__,
                "candle_start_at": recurrence_start,
                "collected_at": recurrence_start + timedelta(seconds=offset),
            }
        )

    repository.record_incremental_collection([], [], [recurrence("100", 1), recurrence("100", 1)])
    repository.record_incremental_collection([], [], [recurrence("101", 2)])
    repository.record_incremental_collection([], [], [recurrence("100", 3)])
    with psycopg.connect(database_url) as connection:
        recurrence_rows = connection.execute(
            """
            SELECT revision_number, close_price
            FROM source_candle_revisions
            WHERE instrument_id = %s AND candle_start_at = %s
            ORDER BY revision_number
            """,
            (instrument.id, recurrence_start),
        ).fetchall()
    assert [(row[0], str(row[1])) for row in recurrence_rows] == [
        (1, "100"),
        (2, "101"),
        (3, "100"),
    ]


def test_limited_runtime_role_can_append_revision_and_use_identity_sequence() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    started_at = datetime(2026, 7, 17, 1, tzinfo=UTC)
    instrument = _prepare_p2_market(database_url)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=started_at,
                open_price=Decimal("10"),
                high_price=Decimal("11"),
                low_price=Decimal("9"),
                close_price=Decimal("10"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("10"),
                collected_at=started_at + timedelta(seconds=1),
            )
        ],
    )
    role = f"p2_candle_runtime_{os.getpid()}"
    with psycopg.connect(database_url) as connection:
        connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        for statement in (
            "GRANT USAGE ON SCHEMA public TO {}",
            "GRANT SELECT ON source_candles, source_candle_revisions TO {}",
            "GRANT INSERT ON source_candle_revisions TO {}",
            "GRANT USAGE, SELECT ON SEQUENCE source_candle_revisions_id_seq TO {}",
            "GRANT SELECT, INSERT, UPDATE ON candle_rollups TO {}",
        ):
            connection.execute(sql.SQL(statement).format(sql.Identifier(role)))
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
        created = connection.execute(
            """
            INSERT INTO source_candle_revisions (
              source_candle_id, revision_number, market_id, instrument_id, source,
              candle_unit, candle_start_at, open_price, high_price, low_price,
              close_price, trade_volume, trade_amount, source_as_of, knowledge_at,
              input_content_hash
            )
            SELECT candle.id, 2, candle.market_id, candle.instrument_id, candle.source,
                   candle.candle_unit, candle.candle_start_at, candle.open_price,
                   candle.high_price, candle.low_price, 10.5, candle.trade_volume,
                   candle.trade_amount, candle.collected_at, candle.knowledge_at,
                   source_candle_content_hash(
                     candle.open_price, candle.high_price, candle.low_price, 10.5,
                     candle.trade_volume, candle.trade_amount
                   )
            FROM source_candles candle
            WHERE candle.instrument_id = %s AND candle.candle_start_at = %s
            RETURNING revision_number
            """,
            (instrument.id, started_at),
        ).fetchone()
        connection.execute("RESET ROLE")
        assert created == (2,)
        connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
        connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_live_postgres_rollup_completeness_uses_no_trade_and_missing_coverage() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    started_at = datetime(2026, 7, 17, 2, tzinfo=UTC)
    instrument = _prepare_p2_market(database_url)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=started_at,
                open_price=Decimal("20"),
                high_price=Decimal("20"),
                low_price=Decimal("20"),
                close_price=Decimal("20"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("20"),
                collected_at=started_at + timedelta(seconds=1),
            )
        ],
    )
    with psycopg.connect(database_url) as connection:
        specification_row = connection.execute(
            """
            SELECT specification.id
            FROM collection_target_specs specification
            JOIN markets market ON market.id = specification.market_id
            WHERE market.legacy_instrument_id = %s
              AND specification.data_type = 'source_candle'
              AND specification.candle_unit = '1m'
            """,
            (instrument.id,),
        ).fetchone()
        assert specification_row is not None
        specification_id = specification_row[0]
        connection.execute(
            """
            UPDATE coverage_intervals SET status = 'no_trade'
            WHERE target_spec_id = %s
              AND range_start_at <= %s AND range_end_at >= %s
            """,
            (
                specification_id,
                started_at + timedelta(minutes=1),
                started_at + timedelta(minutes=3),
            ),
        )
    repository.materialize_candle_rollups(instrument.id, "3m")
    complete = repository.candle_rollups(
        instrument.id, "3m", started_at, started_at + timedelta(minutes=3)
    )[0]
    assert (complete.completeness, complete.quality) == ("complete", "available")

    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE coverage_intervals SET status = 'missing'
            WHERE target_spec_id = %s
              AND range_start_at <= %s AND range_end_at >= %s
            """,
            (
                specification_id,
                started_at + timedelta(minutes=1),
                started_at + timedelta(minutes=3),
            ),
        )
    repository.materialize_candle_rollups(instrument.id, "3m")
    partial = repository.candle_rollups(
        instrument.id, "3m", started_at, started_at + timedelta(minutes=3)
    )[0]
    assert (partial.completeness, partial.quality) == ("partial", "missing")


def test_live_postgres_fallback_pagination_is_bounded_and_has_no_gaps() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    instrument = _prepare_p2_market(database_url)
    started_at = datetime(2026, 8, 1, 4, tzinfo=UTC)

    minute_candles = [
        SourceCandle(
            instrument_id=instrument.id,
            candle_unit="1m",
            candle_start_at=started_at + timedelta(minutes=offset),
            open_price=Decimal(100 + offset),
            high_price=Decimal(101 + offset),
            low_price=Decimal(99 + offset),
            close_price=Decimal(100 + offset),
            trade_volume=Decimal("1"),
            trade_amount=Decimal(100 + offset),
            collected_at=started_at + timedelta(minutes=offset, seconds=1),
        )
        for offset in range(7)
    ]
    repository.record_incremental_collection([], [], minute_candles)

    first, first_cursor = repository.candle_page(
        instrument.id, "3m", started_at, started_at + timedelta(minutes=9), 1, None
    )
    second, second_cursor = repository.candle_page(
        instrument.id, "3m", started_at, started_at + timedelta(minutes=9), 1, first_cursor
    )
    third, third_cursor = repository.candle_page(
        instrument.id, "3m", started_at, started_at + timedelta(minutes=9), 1, second_cursor
    )

    assert [item.started_at for item in first + second + third] == [
        started_at,
        started_at + timedelta(minutes=3),
        started_at + timedelta(minutes=6),
    ]
    assert first_cursor == started_at
    assert second_cursor == started_at + timedelta(minutes=3)
    assert third_cursor is None

    daily, daily_cursor = repository.candle_page(
        instrument.id,
        "1d",
        started_at.replace(hour=0),
        started_at.replace(hour=0) + timedelta(days=1),
        1,
        None,
    )
    assert len(daily) == 1
    assert daily[0].close == Decimal("106")
    assert daily_cursor is None


def test_live_postgres_daily_fallback_prefers_direct_daily_over_minute_rows() -> None:
    database_url = live_database_url()
    repository = PostgresOperationsRepository(database_url)
    instrument = _prepare_p2_market(database_url)
    started_at = datetime(2026, 8, 3, tzinfo=UTC)

    def source(unit: Literal["1m", "1d"], close: str) -> SourceCandle:
        return SourceCandle(
            instrument_id=instrument.id,
            candle_unit=unit,
            candle_start_at=started_at,
            open_price=Decimal(close),
            high_price=Decimal(close),
            low_price=Decimal(close),
            close_price=Decimal(close),
            trade_volume=Decimal("1"),
            trade_amount=Decimal(close),
            collected_at=started_at + timedelta(seconds=1),
        )

    repository.record_incremental_collection([], [], [source("1m", "100"), source("1d", "200")])
    page, cursor = repository.candle_page(
        instrument.id, "1d", started_at, started_at + timedelta(days=1), 1, None
    )

    assert len(page) == 1
    assert page[0].close == Decimal("200")
    assert cursor is None


def test_live_postgres_heartbeat_저장소의_statement_timeout이_pg_sleep를_중단한다() -> None:
    repository = PostgresOperationsRepository(
        live_database_url(),
        connect_and_statement_timeout_seconds=0.1,
    )

    with pytest.raises(psycopg.errors.QueryCanceled), repository._connect() as connection:
        connection.execute("SELECT pg_sleep(1)")
