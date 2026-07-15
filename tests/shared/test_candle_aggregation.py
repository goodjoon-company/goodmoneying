import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from pathlib import Path
from typing import Any

import pytest

from goodmoneying_shared.aggregation import aggregate_candles
from goodmoneying_shared.models import SourceCandle
from goodmoneying_shared.sqlite_repository import SQLiteOperationsRepository
from goodmoneying_shared.time import KST
from goodmoneying_worker.aggregation_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_THREAD_NAME,
    CandleAggregationWorker,
)


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


def test_집계_작업_혼합_상태의_전체_건수와_진행률은_상태별_합계와_일치한다() -> None:
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
    assert latest.total_target_count == 7
    assert latest.completed_target_count == 1
    assert latest.running_target_count == 1
    assert latest.pending_target_count == 4
    assert latest.failed_target_count == 1
    assert latest.total_target_count == (
        latest.completed_target_count
        + latest.running_target_count
        + latest.pending_target_count
        + latest.failed_target_count
    )
    assert latest.progress_percent == Decimal("100") / Decimal("7")


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


def test_집계_워커는_첫_처리_구간이_32초_걸려도_31초_시점에_동작_중이다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "long-aggregation.sqlite3"
    repository = SQLiteOperationsRepository.from_path(database_path)
    observer = SQLiteOperationsRepository.from_path(database_path)
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
    original_materialize = repository.materialize_candle_rollups
    materialize_started = threading.Event()
    materialize_count = 0

    def delayed_materialize(*args: Any, **kwargs: Any) -> int:
        nonlocal materialize_count
        materialize_count += 1
        if materialize_count == 1:
            materialize_started.set()
            time.sleep(32.5)
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(repository, "materialize_candle_rollups", delayed_materialize)
    results: list[int] = []
    errors: list[BaseException] = []

    def run_worker() -> None:
        try:
            results.append(CandleAggregationWorker(repository).run_once())
        except BaseException as exc:
            errors.append(exc)

    worker_thread = threading.Thread(target=run_worker)
    worker_thread.start()
    assert materialize_started.wait(timeout=5)

    time.sleep(31)
    runtime = observer.collection_worker_runtime_status("candle_aggregation")

    worker_thread.join(timeout=10)
    assert runtime.status == "running"
    assert runtime.status_label == "동작 중"
    assert worker_thread.is_alive() is False
    assert errors == []
    assert results == [7]


def test_대량_집계의_heartbeat_쓰기는_처리량이_아닌_시간에_비례한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLiteOperationsRepository()
    instrument = repository.refresh_candidate_universe(
        [("KRW-BTC", "비트코인", "100")]
    )[0].instrument
    repository.ensure_default_active_targets(limit=1)
    started_at = datetime(2026, 7, 1, 0, 0, tzinfo=KST)
    repository.record_incremental_collection(
        [],
        [],
        [
            SourceCandle(
                instrument_id=instrument.id,
                candle_unit="1m",
                candle_start_at=started_at + timedelta(minutes=index),
                open_price=Decimal("100"),
                high_price=Decimal("100"),
                low_price=Decimal("100"),
                close_price=Decimal("100"),
                trade_volume=Decimal("1"),
                trade_amount=Decimal("100"),
                collected_at=started_at + timedelta(minutes=index),
            )
            for index in range(5_363)
        ],
    )
    heartbeat_count = 0
    count_lock = threading.Lock()

    def record_heartbeat(worker_type: str, status: str, error_message: str | None = None) -> None:
        nonlocal heartbeat_count
        with count_lock:
            heartbeat_count += 1

    monkeypatch.setattr(repository, "record_collection_worker_heartbeat", record_heartbeat)
    started = time.monotonic()

    completed = CandleAggregationWorker(repository).run_once()
    elapsed = time.monotonic() - started

    assert completed == 7
    assert heartbeat_count <= ceil(elapsed / HEARTBEAT_INTERVAL_SECONDS) + 1


def test_집계_실패_후에는_heartbeat_ticker_스레드가_남지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    ticker_seen: list[bool] = []

    def fail_materialize(*args: object, **kwargs: object) -> int:
        ticker_seen.append(
            any(thread.name == HEARTBEAT_THREAD_NAME for thread in threading.enumerate())
        )
        raise RuntimeError("집계 실패")

    monkeypatch.setattr(repository, "materialize_candle_rollups", fail_materialize)

    with pytest.raises(RuntimeError, match="집계 실패"):
        CandleAggregationWorker(repository).run_once()

    assert ticker_seen == [True]
    assert all(thread.name != HEARTBEAT_THREAD_NAME for thread in threading.enumerate())
