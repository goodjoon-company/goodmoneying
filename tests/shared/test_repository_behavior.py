from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.models import OrderbookSummary, SourceCandle, TickerSnapshot, TradeEvent
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import KST, minute_bucket, now_kst
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


def test_repository_dashboard_omits_segments_until_lazy_request() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())
    worker.refresh_candidate_universe()
    worker.collect_incremental()

    summary = repository.dashboard_summary()

    assert len(summary.targets) == 50
    first = summary.targets[0]
    assert first.instrument.market_code == "KRW-BTC"
    assert first.overall_status == "warning"
    assert first.overall_status_label == "주의"
    assert first.plan.is_continuous is True
    assert first.plan.range_time_zone == "KST"
    assert first.plan.display_range.endswith("~ NOW")
    assert [status.data_type for status in first.data_statuses] == [
        "source_candle",
        "ticker_snapshot",
        "orderbook_summary",
    ]
    assert first.coverage_segments == []

    lazy_segments = repository.coverage_segments_for(first.instrument.id)

    assert lazy_segments[0].status in {"collected", "missing"}
    assert lazy_segments[0].offset_percent == Decimal("0")
    assert lazy_segments[0].width_percent > Decimal("0")


def test_repository_dashboard_exposes_operations_observability_summaries() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())
    worker.refresh_candidate_universe()
    worker.collect_incremental()

    summary = repository.dashboard_summary()

    assert len(summary.collection_activity) == 7 * 24
    active_buckets = [bucket for bucket in summary.collection_activity if bucket.result_count > 0]
    assert active_buckets
    assert active_buckets[-1].status in {"low", "collecting", "high"}
    assert len(summary.realtime_collection_heatmap) == len(repository.list_active_targets())
    first_row = summary.realtime_collection_heatmap[0]
    assert len(first_row.hourly_buckets) == 24
    assert first_row.instrument_display_name
    assert first_row.hourly_buckets[0].trade_count >= 0
    assert first_row.hourly_buckets[0].status in {"red", "orange", "yellow", "blue", "green"}

    breakdown = {item.data_type: item for item in summary.storage_breakdown}
    assert set(breakdown) == {
        "source_candle",
        "ticker_snapshot",
        "orderbook_summary",
    }
    assert breakdown["source_candle"].row_count > 0
    assert breakdown["ticker_snapshot"].bytes > 0
    assert sum(item.bytes for item in breakdown.values()) == summary.storage_bytes_today
    assert sum(item.row_count for item in breakdown.values()) == summary.storage_rows_today

    assert len(summary.operations_trend) == 7
    today = summary.operations_trend[-1]
    assert today.storage_bytes == summary.storage_bytes_today
    assert today.warning_targets == summary.warning_targets
    assert len(summary.missing_range_top) > 0
    assert summary.missing_range_top[0].missing_segment_count >= 0
    assert summary.audit_log_summary.target_change_count_24h >= 50
    assert summary.audit_log_summary.latest_change_at is not None


def test_repository_dashboard_realtime_heatmap_uses_trade_frequency_metrics() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "1000000000")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    bucket_start = now_kst().replace(minute=0, second=0, microsecond=0)
    trades = [
        TradeEvent(
            instrument_id=instrument.id,
            sequential_id=1_000_000 + index,
            trade_timestamp_at=bucket_start + timedelta(seconds=index % 3600),
            trade_price=Decimal("100"),
            trade_volume=Decimal("1"),
            trade_amount=Decimal("100"),
            ask_bid="BID" if index < 400 else "ASK",
            collected_at=bucket_start + timedelta(seconds=index % 3600),
        )
        for index in range(600)
    ]

    assert repository.record_trade_events(trades) == 600

    row = repository.dashboard_realtime_heatmap()[0]
    current_bucket = row.hourly_buckets[-1]
    assert current_bucket.trade_count == 600
    assert current_bucket.average_trades_per_minute == Decimal("10")
    assert current_bucket.status == "orange"
    assert current_bucket.trade_volume == Decimal("600")
    assert current_bucket.trade_amount == Decimal("60000")
    assert current_bucket.trade_strength == Decimal("200")


def test_repository_dashboard_panel_methods_match_summary_source_data() -> None:
    repository = SQLiteOperationsRepository()
    worker = UpbitCollectionWorker(repository, FixtureUpbitClient())
    worker.refresh_candidate_universe()
    worker.collect_incremental()

    summary = repository.dashboard_summary()

    assert repository.collection_dashboard_targets() == summary.targets
    assert repository.dashboard_coverage() == summary.coverage
    assert repository.dashboard_collection_activity() == summary.collection_activity
    assert repository.dashboard_realtime_heatmap() == summary.realtime_collection_heatmap
    assert repository.dashboard_storage_breakdown() == summary.storage_breakdown
    assert repository.dashboard_operations_trend() == summary.operations_trend
    assert repository.dashboard_missing_ranges() == summary.missing_range_top
    assert repository.dashboard_audit_log_summary() == summary.audit_log_summary


def test_repository_computes_candle_coverage_and_missing_segments_from_saved_rows() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "1000000000")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    start_at = datetime(2026, 1, 1, 0, 0, tzinfo=KST)
    end_at = datetime(2026, 1, 1, 0, 4, tzinfo=KST)
    repository._execute(
        """
        UPDATE collection_plans
        SET range_start_at = ?, range_end_at = ?, is_continuous = 0
        WHERE instrument_id = ?
        """,
        (start_at.isoformat(), end_at.isoformat(), instrument.id),
    )
    repository.record_incremental_collection(
        tickers=[],
        orderbooks=[],
        candles=[
            _source_candle(instrument.id, start_at, "100"),
            _source_candle(instrument.id, start_at + timedelta(minutes=1), "101"),
            _source_candle(instrument.id, start_at + timedelta(minutes=3), "103"),
        ],
    )

    target = repository.collection_dashboard_targets()[0]
    candle_status = next(
        status for status in target.data_statuses if status.data_type == "source_candle"
    )
    candle_segments = [
        segment
        for segment in repository.coverage_segments_for(instrument.id)
        if segment.data_type == "source_candle"
    ]

    assert candle_status.progress_percent == Decimal("75")
    assert candle_status.missing_segment_count == 1
    assert candle_status.stored_row_count == 3
    assert target.coverage_segments == []
    actual_segments = [
        (segment.status, segment.segment_start_at, segment.segment_end_at)
        for segment in candle_segments
    ]
    assert actual_segments == [
        ("collected", start_at, start_at + timedelta(minutes=2)),
        ("missing", start_at + timedelta(minutes=2), start_at + timedelta(minutes=3)),
        ("collected", start_at + timedelta(minutes=3), end_at),
    ]


def test_repository_upserts_newer_market_snapshots_for_same_bucket() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    bucket_at = minute_bucket(now_kst())
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


def _source_candle(instrument_id: int, candle_start_at: datetime, close: str) -> SourceCandle:
    close_decimal = Decimal(close)
    return SourceCandle(
        instrument_id=instrument_id,
        candle_unit="1m",
        candle_start_at=candle_start_at,
        open_price=close_decimal,
        high_price=close_decimal,
        low_price=close_decimal,
        close_price=close_decimal,
        trade_volume=Decimal("1"),
        trade_amount=close_decimal,
        collected_at=candle_start_at,
    )


def test_backfill_plan_and_control_flow() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = now_kst() - timedelta(hours=2)
    end_at = now_kst()

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


def test_failed_backfill_job_can_resume() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = now_kst() - timedelta(hours=2)
    end_at = now_kst()
    plan = repository.create_backfill_plan("source_candle", start_at, end_at, [instrument.id])
    job = repository.approve_backfill_job(plan.plan_id)
    repository.claim_next_backfill_job()
    repository.mark_backfill_target(
        job.id,
        instrument.id,
        "failed",
        None,
        "UpbitBackfillError",
        "백필 캔들 조회 실패",
    )

    resumed = repository.control_backfill_job(job.id, "resume")
    claimed = repository.claim_next_backfill_job()

    assert resumed.status == "running"
    assert claimed is not None
    assert claimed.id == job.id


def test_backfill_job_claim_records_candle_chunk_and_progress() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.upsert_instrument("KRW-BTC", "비트코인")
    start_at = now_kst() - timedelta(minutes=2)
    end_at = now_kst()
    collected_at = now_kst()

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
