from datetime import datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.aggregation import aggregate_candles
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import KST
from goodmoneying_worker.aggregation_worker import CandleAggregationWorker


def test_5분_집계는_원천_1분봉을_하나의_ohlcv_봉으로_만든다() -> None:
    started_at = datetime(2026, 7, 14, 9, 0, tzinfo=KST)
    source = [
        SourceCandle(
            instrument_id=1,
            candle_unit="1m",
            candle_start_at=started_at + timedelta(minutes=offset),
            open_price=Decimal(100 + offset),
            high_price=Decimal(105 + offset),
            low_price=Decimal(95 + offset),
            close_price=Decimal(102 + offset),
            trade_volume=Decimal(offset + 1),
            trade_amount=Decimal((offset + 1) * 100),
            collected_at=started_at + timedelta(minutes=offset),
        )
        for offset in range(5)
    ]

    rollups = aggregate_candles("5m", source)

    assert len(rollups) == 1
    assert rollups[0].started_at == started_at
    assert rollups[0].open == Decimal("100")
    assert rollups[0].high == Decimal("109")
    assert rollups[0].low == Decimal("95")
    assert rollups[0].close == Decimal("106")
    assert rollups[0].volume == Decimal("15")
    assert rollups[0].trade_amount == Decimal("1500")
    assert rollups[0].completeness == "complete"


def test_집계_테이블은_동일_원천봉을_다시_처리해도_중복_봉을_만들지_않는다() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "100")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    started_at = datetime(2026, 7, 14, 9, 0, tzinfo=KST)
    source = [
        SourceCandle(
            instrument_id=instrument.id,
            candle_unit="1m",
            candle_start_at=started_at + timedelta(minutes=offset),
            open_price=Decimal(100 + offset),
            high_price=Decimal(105 + offset),
            low_price=Decimal(95 + offset),
            close_price=Decimal(102 + offset),
            trade_volume=Decimal("1"),
            trade_amount=Decimal("100"),
            collected_at=started_at + timedelta(minutes=offset),
        )
        for offset in range(5)
    ]
    repository.record_incremental_collection([], [], source)

    assert repository.materialize_candle_rollups(instrument.id, "5m") == 1
    assert repository.materialize_candle_rollups(instrument.id, "5m") == 1
    rollups = repository.candle_rollups(
        instrument.id, "5m", started_at, started_at + timedelta(minutes=5)
    )

    assert len(rollups) == 1
    assert rollups[0].volume == Decimal("5")


def test_오래된_집계는_활성_코인과_단위별_자동_집계_작업을_만든다() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "100")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    started_at = datetime(2026, 7, 14, 9, 0, tzinfo=KST)
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

    job = repository.schedule_candle_aggregation()

    assert job is not None
    assert job.total_target_count == 7
    assert job.completed_target_count == 0
    assert job.pending_target_count == 7
    assert job.progress_percent == Decimal("0")


def test_집계_워커는_자동_작업을_완료하고_진행률을_100으로_갱신한다() -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "100")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    started_at = datetime(2026, 7, 14, 9, 0, tzinfo=KST)
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

    completed = CandleAggregationWorker(repository).run_once()
    job = repository.latest_candle_aggregation_job()

    assert completed == 7
    assert job is not None
    assert job.status == "succeeded"
    assert job.completed_target_count == 7
    assert job.pending_target_count == 0
    assert job.progress_percent == Decimal("100")
