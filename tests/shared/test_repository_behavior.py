from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from goodmoneying_shared.models import OrderbookSummary, SourceCandle, TickerSnapshot
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import minute_bucket, now_utc
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.upbit_client import FixtureUpbitClient


def test_candidate_universe_defaults_to_top_50_active_targets() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())

    assert worker.refresh_candidate_universe() == 100

    ranked_at, entries = repository.list_candidate_universe()
    active_targets = repository.list_active_targets()
    assert ranked_at.tzinfo is not None
    assert len(entries) == 100
    assert len(active_targets) == 50
    assert entries[0].rank == 1
    assert entries[0].selected is True
    assert entries[49].selected is True
    assert entries[50].selected is False


def test_repository_keeps_existing_active_target_that_left_candidate_universe() -> None:
    repository = SQLiteOperationsRepository()
    initial_entries = [
        (f"KRW-GM{index:03d}", f"굿머니코인 {index}", str(100_000 - index))
        for index in range(1, 101)
    ]
    next_entries = [
        (f"KRW-GM{index:03d}", f"굿머니코인 {index}", str(100_000 - index))
        for index in range(2, 102)
    ]
    repository.refresh_candidate_universe(initial_entries)
    repository.ensure_default_active_targets()
    stale_active_id = repository.list_active_targets()[0].id

    repository.refresh_candidate_universe(next_entries)
    candidate_id = repository.list_candidate_universe()[1][0].instrument.id

    active_targets = repository.update_active_targets(
        [stale_active_id, candidate_id],
        "후보 밖 기존 관심 대상 유지",
    )

    assert {target.id for target in active_targets} == {stale_active_id, candidate_id}


def test_repository_rejects_new_active_target_outside_candidate_universe() -> None:
    repository = SQLiteOperationsRepository()
    repository.refresh_candidate_universe(
        [
            (f"KRW-GM{index:03d}", f"굿머니코인 {index}", str(100_000 - index))
            for index in range(1, 101)
        ]
    )
    outside_candidate = repository.upsert_instrument("KRW-OUT", "후보밖코인")

    with pytest.raises(ValueError, match="후보 유니버스"):
        repository.update_active_targets([outside_candidate.id], "후보 밖 신규 대상 차단")


def test_repository_dashboard_contains_collection_plan_rows_and_segments() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())
    worker.refresh_candidate_universe()
    worker.collect_incremental()

    summary = repository.dashboard_summary()

    assert len(summary.targets) == 50
    first = summary.targets[0]
    assert first.instrument.market_code == "KRW-BTC"
    assert first.overall_status == "latest_collecting"
    assert first.overall_status_label == "최신수집중"
    assert first.plan.is_continuous is True
    assert first.plan.range_time_zone == "KST"
    assert first.plan.display_range.endswith("현재(지속)")
    assert [status.data_type for status in first.data_statuses] == [
        "source_candle",
        "ticker_snapshot",
        "orderbook_summary",
    ]
    assert first.coverage_segments[0].status == "collected"
    assert first.coverage_segments[0].offset_percent == Decimal("0")
    assert first.coverage_segments[0].width_percent > Decimal("0")


def test_repository_candle_coverage_segments_follow_actual_stored_ranges() -> None:
    repository = SQLiteOperationsRepository()
    repository.refresh_candidate_universe([("KRW-BTC", "비트코인", "100000")])
    instrument = repository.ensure_default_active_targets(limit=1)[0]
    first_start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    second_start = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)

    repository.record_incremental_collection(
        tickers=[],
        orderbooks=[],
        candles=[
            _source_candle(instrument.id, first_start),
            _source_candle(instrument.id, first_start + timedelta(minutes=1)),
            _source_candle(instrument.id, second_start),
            _source_candle(instrument.id, second_start + timedelta(minutes=1)),
        ],
    )

    target = repository.collection_dashboard_targets()[0]
    candle_segments = [
        segment for segment in target.coverage_segments if segment.data_type == "source_candle"
    ]

    assert [segment.status for segment in candle_segments] == ["collected", "missing", "collected"]
    assert candle_segments[0].segment_start_at == first_start
    assert candle_segments[0].segment_end_at == first_start + timedelta(minutes=2)
    assert candle_segments[1].segment_start_at == first_start + timedelta(minutes=2)
    assert candle_segments[1].segment_end_at == second_start
    assert candle_segments[2].segment_start_at == second_start
    assert candle_segments[2].segment_end_at == second_start + timedelta(minutes=2)
    assert sum(segment.width_percent for segment in candle_segments) == Decimal("100")


def test_repository_upserts_newer_market_snapshots_for_same_bucket() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    bucket_at = minute_bucket(now_utc())
    old_collected_at = bucket_at
    new_collected_at = bucket_at + timedelta(seconds=30)

    repository.record_incremental_collection(
        tickers=[
            TickerSnapshot(
                instrument_id=instrument.id,
                bucket_at=bucket_at,
                trade_price=Decimal("100"),
                acc_trade_price_24h=Decimal("1000"),
                change_rate=Decimal("0.01"),
                collected_at=old_collected_at,
            ),
            TickerSnapshot(
                instrument_id=instrument.id,
                bucket_at=bucket_at,
                trade_price=Decimal("120"),
                acc_trade_price_24h=Decimal("2000"),
                change_rate=Decimal("0.02"),
                collected_at=new_collected_at,
            ),
        ],
        orderbooks=[
            OrderbookSummary(
                instrument_id=instrument.id,
                bucket_at=bucket_at,
                best_bid_price=Decimal("119"),
                best_bid_size=Decimal("1"),
                best_ask_price=Decimal("121"),
                best_ask_size=Decimal("1"),
                spread=Decimal("2"),
                bid_depth_10=Decimal("10"),
                ask_depth_10=Decimal("8"),
                imbalance_10=Decimal("0.1111"),
                collected_at=new_collected_at,
            )
        ],
        candles=[
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=bucket_at,
                open_price=Decimal("100"),
                high_price=Decimal("123"),
                low_price=Decimal("99"),
                close_price=Decimal("120"),
                trade_volume=Decimal("4"),
                trade_amount=Decimal("480"),
                collected_at=new_collected_at,
            )
        ],
    )

    latest = repository.latest_ticker(instrument.id)
    assert latest is not None
    assert latest.trade_price == Decimal("120")
    assert latest.acc_trade_price_24h == Decimal("2000")


def _source_candle(instrument_id: int, candle_start_at: datetime) -> SourceCandle:
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=candle_start_at,
        open_price=Decimal("100"),
        high_price=Decimal("120"),
        low_price=Decimal("90"),
        close_price=Decimal("110"),
        trade_volume=Decimal("1"),
        trade_amount=Decimal("110"),
        collected_at=candle_start_at + timedelta(seconds=1),
    )


def test_backfill_plan_and_control_flow() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = now_utc() - timedelta(hours=2)
    end_at = now_utc()

    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)
    paused = repository.control_backfill_job(job.id, "pause")
    resumed = repository.control_backfill_job(job.id, "resume")
    restarted = repository.control_backfill_job(job.id, "safe-restart")

    assert plan.estimated_request_count >= 1
    assert job.status == "pending"
    assert paused.status == "paused"
    assert resumed.status == "running"
    assert restarted.status == "pending"


def test_backfill_job_claim_records_candle_chunk_and_progress() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = now_utc() - timedelta(minutes=2)
    end_at = now_utc()
    collected_at = now_utc()

    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)

    claimed = repository.claim_next_backfill_job()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.target_start_at == plan.target_start_at
    assert claimed.target_end_at == plan.target_end_at

    targets = repository.backfill_job_targets(job.id)
    assert [target.instrument_id for target in targets] == [instrument.id]
    assert targets[0].status == "pending"

    rows_written = repository.record_backfill_candles(
        job.id,
        instrument.id,
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=start_at,
                open_price=Decimal("100"),
                high_price=Decimal("120"),
                low_price=Decimal("90"),
                close_price=Decimal("110"),
                trade_volume=Decimal("1.5"),
                trade_amount=Decimal("165"),
                collected_at=collected_at,
            )
        ],
    )
    repository.mark_backfill_target(
        job.id,
        instrument.id,
        status="succeeded",
        last_completed_at=start_at,
    )

    assert rows_written == 1
    assert repository.candles(instrument.id, "1m", start_at, end_at)[0].close == Decimal("110")
    completed = repository.backfill_jobs()[0]
    assert completed.status == "succeeded"
    assert completed.progress_percent == Decimal("100")
