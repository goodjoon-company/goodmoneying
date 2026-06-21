from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.models import OrderbookSummary, SourceCandle, TickerSnapshot
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.time import KST, minute_bucket, now_kst
from goodmoneying_worker.upbit_client import UpbitClient


class UpbitCollectionWorker:
    def __init__(self, repository: OperationsRepository, client: UpbitClient) -> None:
        self._repository = repository
        self._client = client

    @property
    def repository(self) -> OperationsRepository:
        return self._repository

    def refresh_candidate_universe(self) -> int:
        ticker_rows = sorted(
            self._client.get_krw_tickers(),
            key=lambda row: Decimal(row["acc_trade_price_24h"]),
            reverse=True,
        )
        entries = [
            (row["market"], row["display_name"], row["acc_trade_price_24h"])
            for row in ticker_rows[:100]
        ]
        candidate_entries = self._repository.refresh_candidate_universe(entries)
        active_targets = self._repository.list_active_targets()
        candidate_ids = {entry.instrument.id for entry in candidate_entries}
        if not active_targets:
            self._repository.ensure_default_active_targets(limit=50)
        elif any(target.id not in candidate_ids for target in active_targets):
            self._repository.update_active_targets(
                [entry.instrument.id for entry in candidate_entries[:50]],
                "후보 유니버스 갱신으로 현재 상위 50개 재동기화",
            )
        return len(entries)

    def collect_incremental(self) -> int:
        active_targets = self._repository.list_active_targets()
        if not active_targets:
            self.refresh_candidate_universe()
            active_targets = self._repository.list_active_targets()
        active_by_market = {item.market_code: item for item in active_targets}
        markets = list(active_by_market.keys())
        collected_at = now_kst()
        bucket_at = minute_bucket(collected_at)

        ticker_rows = self._client.get_krw_tickers()
        tickers = [
            TickerSnapshot(
                instrument_id=active_by_market[row["market"]].id,
                bucket_at=bucket_at,
                trade_price=Decimal(row["trade_price"]),
                acc_trade_price_24h=Decimal(row["acc_trade_price_24h"]),
                change_rate=Decimal(row["signed_change_rate"]),
                collected_at=collected_at,
            )
            for row in ticker_rows
            if row["market"] in active_by_market
        ]

        orderbooks = [
            OrderbookSummary(
                instrument_id=active_by_market[row["market"]].id,
                bucket_at=bucket_at,
                best_bid_price=Decimal(row["best_bid_price"]),
                best_bid_size=Decimal(row["best_bid_size"]),
                best_ask_price=Decimal(row["best_ask_price"]),
                best_ask_size=Decimal(row["best_ask_size"]),
                spread=Decimal(row["spread"]),
                bid_depth_10=Decimal(row["bid_depth_10"]),
                ask_depth_10=Decimal(row["ask_depth_10"]),
                imbalance_10=Decimal(row["imbalance_10"]),
                collected_at=collected_at,
            )
            for row in self._client.get_orderbooks(markets)
            if row["market"] in active_by_market
        ]

        candles = [
            SourceCandle(
                instrument_id=active_by_market[row["market"]].id,
                candle_unit="1m",
                candle_start_at=datetime.fromisoformat(row["candle_start_at"]).astimezone(KST),
                open_price=Decimal(row["open_price"]),
                high_price=Decimal(row["high_price"]),
                low_price=Decimal(row["low_price"]),
                close_price=Decimal(row["close_price"]),
                trade_volume=Decimal(row["trade_volume"]),
                trade_amount=Decimal(row["trade_amount"]),
                collected_at=collected_at,
            )
            for row in self._client.get_minute_candles(markets)
            if row["market"] in active_by_market
        ]
        self._repository.record_incremental_collection(tickers, orderbooks, candles)
        return len(tickers) + len(orderbooks) + len(candles)

    def run_backfill_once(
        self,
        target_limit: int | None = None,
        on_progress: Callable[[], object] | None = None,
    ) -> int:
        total_written = 0
        while True:
            job = self._repository.claim_next_backfill_job()
            if job is None:
                return total_written
            if on_progress is not None:
                on_progress()
            written = 0
            processed_targets = 0
            should_claim_next_job = False
            for target in self._repository.backfill_job_targets(job.id):
                job_status = self._backfill_job_status(job.id)
                if job_status in {"paused", "stopped", "failed", "succeeded"}:
                    should_claim_next_job = job_status in {"paused", "stopped"}
                    break
                if target.status in {"succeeded", "stopped"}:
                    continue
                if target_limit is not None and processed_targets >= target_limit:
                    return total_written + written
                processed_targets += 1
                if target.status != "running":
                    self._repository.mark_backfill_target(
                        job.id,
                        target.instrument_id,
                        status="running",
                        last_completed_at=target.last_completed_at,
                    )
                if on_progress is not None:
                    on_progress()
                instrument = self._repository.get_instrument(target.instrument_id)
                if instrument is None:
                    self._repository.mark_backfill_target(
                        job.id,
                        target.instrument_id,
                        status="failed",
                        last_completed_at=target.last_completed_at,
                        error_code="InstrumentNotFound",
                        error_message="백필 대상 거래 상품을 찾을 수 없다.",
                    )
                    continue
                try:
                    missing_ranges = self._missing_source_candle_ranges(
                        target.instrument_id,
                        job.target_start_at,
                        job.target_end_at,
                    )
                    if not missing_ranges:
                        self._repository.mark_backfill_target(
                            job.id,
                            target.instrument_id,
                            status="succeeded",
                            last_completed_at=job.target_end_at,
                        )
                        continue
                    target_written = 0
                    processed_missing_ranges = 0
                    estimated_missing_ranges = len(missing_ranges)
                    last_completed_at = target.last_completed_at
                    self._repository.record_backfill_target_progress(
                        job.id,
                        target.instrument_id,
                        processed_missing_range_count=processed_missing_ranges,
                        estimated_missing_range_count=estimated_missing_ranges,
                        rows_written_count=target_written,
                        last_completed_at=last_completed_at,
                    )
                    for fetch_start_at, fetch_end_at in missing_ranges:
                        if self._backfill_job_status(job.id) in {
                            "paused",
                            "stopped",
                            "failed",
                            "succeeded",
                        }:
                            break
                        if on_progress is not None:
                            on_progress()
                        rows = self._client.fetch_minute_candles(
                            instrument.market_code,
                            fetch_start_at,
                            fetch_end_at,
                        )
                        collected_at = now_kst()
                        candles = [
                            SourceCandle(
                                instrument_id=target.instrument_id,
                                candle_unit="1m",
                                candle_start_at=datetime.fromisoformat(
                                    row["candle_start_at"]
                                ).astimezone(KST),
                                open_price=Decimal(row["open_price"]),
                                high_price=Decimal(row["high_price"]),
                                low_price=Decimal(row["low_price"]),
                                close_price=Decimal(row["close_price"]),
                                trade_volume=Decimal(row["trade_volume"]),
                                trade_amount=Decimal(row["trade_amount"]),
                                collected_at=collected_at,
                            )
                            for row in rows
                        ]
                        rows_written = self._repository.record_backfill_candles(
                            job.id,
                            target.instrument_id,
                            candles,
                        )
                        if on_progress is not None:
                            on_progress()
                        target_written += rows_written
                        last_completed_at = (
                            max((item.candle_start_at for item in candles), default=fetch_end_at)
                            if candles
                            else fetch_end_at
                        )
                        processed_missing_ranges += 1
                        self._repository.record_backfill_target_progress(
                            job.id,
                            target.instrument_id,
                            processed_missing_range_count=processed_missing_ranges,
                            estimated_missing_range_count=estimated_missing_ranges,
                            rows_written_count=target_written,
                            last_completed_at=last_completed_at,
                        )
                    self._repository.mark_backfill_target(
                        job.id,
                        target.instrument_id,
                        status="succeeded",
                        last_completed_at=last_completed_at or job.target_end_at,
                    )
                    if on_progress is not None:
                        on_progress()
                    written += target_written
                    job_status = self._backfill_job_status(job.id)
                    if job_status in {"paused", "stopped"}:
                        should_claim_next_job = True
                        break
                except Exception as exc:
                    self._repository.mark_backfill_target(
                        job.id,
                        target.instrument_id,
                        status="failed",
                        last_completed_at=target.last_completed_at,
                        error_code=type(exc).__name__,
                        error_message=str(exc),
                    )
                    if on_progress is not None:
                        on_progress()
            total_written += written
            if should_claim_next_job:
                continue
            return total_written

    def _missing_source_candle_ranges(
        self,
        instrument_id: int,
        start_at: datetime,
        end_at: datetime,
    ) -> list[tuple[datetime, datetime]]:
        existing_starts = {
            item.started_at
            for item in self._repository.candles(instrument_id, "1m", start_at, end_at)
        }
        ranges: list[tuple[datetime, datetime]] = []
        current = start_at
        range_start: datetime | None = None
        while current < end_at:
            is_missing = current not in existing_starts
            if is_missing and range_start is None:
                range_start = current
            if not is_missing and range_start is not None:
                ranges.append((range_start, current))
                range_start = None
            current += timedelta(minutes=1)
        if range_start is not None:
            ranges.append((range_start, end_at))
        return ranges

    def _backfill_job_status(self, job_id: int) -> str:
        for job in self._repository.backfill_jobs():
            if job.id == job_id:
                return job.status
        return "stopped"


def seed_repository(repository: OperationsRepository, client: UpbitClient) -> None:
    worker = UpbitCollectionWorker(repository, client)
    worker.refresh_candidate_universe()
    worker.collect_incremental()
