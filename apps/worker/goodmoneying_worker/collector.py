from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from goodmoneying_shared.models import OrderbookSummary, SourceCandle, TickerSnapshot
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.time import minute_bucket, now_utc
from goodmoneying_worker.upbit_client import UpbitClient


class UpbitCollectionWorker:
    def __init__(self, repository: OperationsRepository, client: UpbitClient) -> None:
        self._repository = repository
        self._client = client

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
        collected_at = now_utc()
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
                candle_start_at=datetime.fromisoformat(row["candle_start_at"]).astimezone(UTC),
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

    def run_backfill_once(self, target_limit: int | None = None) -> int:
        job = self._repository.claim_next_backfill_job()
        if job is None:
            return 0
        written = 0
        processed_targets = 0
        for target in self._repository.backfill_job_targets(job.id):
            if self._backfill_job_status(job.id) in {"paused", "stopped", "failed", "succeeded"}:
                break
            if target.status in {"succeeded", "stopped"}:
                continue
            if target_limit is not None and processed_targets >= target_limit:
                break
            processed_targets += 1
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
                rows = self._client.fetch_minute_candles(
                    instrument.market_code,
                    job.target_start_at,
                    job.target_end_at,
                )
                collected_at = now_utc()
                candles = [
                    SourceCandle(
                        instrument_id=target.instrument_id,
                        candle_unit="1m",
                        candle_start_at=datetime.fromisoformat(row["candle_start_at"]).astimezone(
                            UTC
                        ),
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
                last_completed_at = (
                    max((item.candle_start_at for item in candles), default=job.target_end_at)
                    if candles
                    else job.target_end_at
                )
                self._repository.mark_backfill_target(
                    job.id,
                    target.instrument_id,
                    status="succeeded",
                    last_completed_at=last_completed_at,
                )
                written += rows_written
            except Exception as exc:
                self._repository.mark_backfill_target(
                    job.id,
                    target.instrument_id,
                    status="failed",
                    last_completed_at=target.last_completed_at,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
        return written

    def _backfill_job_status(self, job_id: int) -> str:
        for job in self._repository.backfill_jobs():
            if job.id == job_id:
                return job.status
        return "stopped"


def seed_repository(repository: OperationsRepository, client: UpbitClient) -> None:
    worker = UpbitCollectionWorker(repository, client)
    worker.refresh_candidate_universe()
    worker.collect_incremental()
