from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import uuid4

from goodmoneying_shared.models import (
    Instrument,
    OrderbookSummary,
    SourceCandle,
    TickerSnapshot,
    TradeDirection,
    TradeEvent,
)
from goodmoneying_shared.repository import OperationsRepository
from goodmoneying_shared.time import KST, minute_bucket, now_kst
from goodmoneying_worker.collector import UpbitCollectionWorker
from goodmoneying_worker.runtime import (
    configure_logging_from_environment,
    create_repository_from_environment,
    create_upbit_client_from_environment,
)

logger = logging.getLogger(__name__)

UPBIT_WEBSOCKET_URL = "wss://api.upbit.com/websocket/v1"


def build_upbit_websocket_subscription(market_codes: list[str]) -> list[dict[str, object]]:
    codes = [code.upper() for code in market_codes]
    return [
        {"ticket": str(uuid4())},
        {"type": "ticker", "codes": codes, "is_only_realtime": False},
        {"type": "trade", "codes": codes, "is_only_realtime": False},
        {"type": "orderbook", "codes": codes, "is_only_realtime": False},
        {"type": "candle.1m", "codes": codes, "is_only_realtime": False},
        {"format": "DEFAULT"},
    ]


class RealtimeStreamBuffer:
    def __init__(
        self,
        instruments_by_market: Mapping[str, Instrument],
        *,
        now: Callable[[], datetime] = now_kst,
    ) -> None:
        self._instruments_by_market = instruments_by_market
        self._now = now
        self._tickers: dict[int, TickerSnapshot] = {}
        self._orderbooks: dict[int, OrderbookSummary] = {}
        self._candles: dict[tuple[int, datetime], SourceCandle] = {}
        self._trades: dict[tuple[int, int], TradeEvent] = {}

    def apply(self, payload: Mapping[str, object]) -> None:
        message_type = str(payload.get("type") or "")
        market_code = str(payload.get("code") or "")
        instrument = self._instruments_by_market.get(market_code)
        if instrument is None:
            return
        if message_type == "ticker":
            self._tickers[instrument.id] = self._ticker_snapshot(instrument.id, payload)
        elif message_type == "trade":
            trade = self._trade_event(instrument.id, payload)
            self._trades[(instrument.id, trade.sequential_id)] = trade
        elif message_type == "orderbook":
            self._orderbooks[instrument.id] = self._orderbook_summary(instrument.id, payload)
        elif message_type == "candle.1m":
            candle = self._source_candle(instrument.id, payload)
            self._candles[(instrument.id, candle.candle_start_at)] = candle

    def flush(self, repository: OperationsRepository) -> int:
        tickers = list(self._tickers.values())
        orderbooks = list(self._orderbooks.values())
        candles = list(self._candles.values())
        trades = list(self._trades.values())
        row_count = len(tickers) + len(orderbooks) + len(candles) + len(trades)
        if row_count == 0:
            return 0
        if tickers or orderbooks or candles:
            repository.record_incremental_collection(tickers, orderbooks, candles)
        if trades:
            repository.record_trade_events(trades)
        self._tickers.clear()
        self._orderbooks.clear()
        self._candles.clear()
        self._trades.clear()
        return row_count

    def _ticker_snapshot(
        self, instrument_id: int, payload: Mapping[str, object]
    ) -> TickerSnapshot:
        collected_at = self._now()
        return TickerSnapshot(
            instrument_id=instrument_id,
            bucket_at=minute_bucket(collected_at),
            trade_price=_decimal(payload["trade_price"]),
            acc_trade_price_24h=_decimal(payload.get("acc_trade_price_24h", 0)),
            change_rate=_decimal(payload.get("signed_change_rate", 0)),
            collected_at=collected_at,
        )

    def _orderbook_summary(
        self, instrument_id: int, payload: Mapping[str, object]
    ) -> OrderbookSummary:
        collected_at = self._now()
        units = cast(list[Mapping[str, object]], payload["orderbook_units"])[:10]
        best = units[0]
        bid_depth = sum((_decimal(unit["bid_size"]) for unit in units), Decimal("0"))
        ask_depth = sum((_decimal(unit["ask_size"]) for unit in units), Decimal("0"))
        denominator = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / denominator if denominator else Decimal("0")
        best_ask = _decimal(best["ask_price"])
        best_bid = _decimal(best["bid_price"])
        return OrderbookSummary(
            instrument_id=instrument_id,
            bucket_at=minute_bucket(collected_at),
            best_bid_price=best_bid,
            best_bid_size=_decimal(best["bid_size"]),
            best_ask_price=best_ask,
            best_ask_size=_decimal(best["ask_size"]),
            spread=best_ask - best_bid,
            bid_depth_10=bid_depth,
            ask_depth_10=ask_depth,
            imbalance_10=imbalance,
            collected_at=collected_at,
        )

    def _source_candle(self, instrument_id: int, payload: Mapping[str, object]) -> SourceCandle:
        return SourceCandle(
            instrument_id=instrument_id,
            candle_unit="1m",
            candle_start_at=_parse_kst_timestamp(payload["candle_date_time_kst"]),
            open_price=_decimal(payload["opening_price"]),
            high_price=_decimal(payload["high_price"]),
            low_price=_decimal(payload["low_price"]),
            close_price=_decimal(payload["trade_price"]),
            trade_volume=_decimal(payload["candle_acc_trade_volume"]),
            trade_amount=_decimal(payload["candle_acc_trade_price"]),
            collected_at=self._now(),
        )

    def _trade_event(self, instrument_id: int, payload: Mapping[str, object]) -> TradeEvent:
        trade_price = _decimal(payload["trade_price"])
        trade_volume = _decimal(payload["trade_volume"])
        return TradeEvent(
            instrument_id=instrument_id,
            sequential_id=int(str(payload["sequential_id"])),
            trade_timestamp_at=_parse_epoch_millis(payload["trade_timestamp"]),
            trade_price=trade_price,
            trade_volume=trade_volume,
            trade_amount=trade_price * trade_volume,
            ask_bid=cast(TradeDirection, payload["ask_bid"]),
            collected_at=self._now(),
        )


def run_realtime_stream_collection(
    repository: OperationsRepository,
    messages: Iterable[Mapping[str, object]],
    *,
    flush_interval_seconds: float = 1.0,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> int:
    active_targets = repository.list_active_targets()
    buffer = RealtimeStreamBuffer({target.market_code: target for target in active_targets})
    last_flush_at = now_monotonic()
    total_rows = 0

    def flush_buffer() -> int:
        rows = buffer.flush(repository)
        if rows > 0:
            repository.record_collection_worker_heartbeat("realtime_collection", "running")
        return rows

    try:
        for payload in messages:
            buffer.apply(payload)
            if now_monotonic() - last_flush_at >= flush_interval_seconds:
                total_rows += flush_buffer()
                last_flush_at = now_monotonic()
    finally:
        total_rows += flush_buffer()
    return total_rows


def upbit_websocket_messages(
    market_codes: list[str],
    *,
    endpoint: str = UPBIT_WEBSOCKET_URL,
) -> Iterator[Mapping[str, object]]:
    from websockets.sync.client import connect

    subscription = build_upbit_websocket_subscription(market_codes)
    with connect(endpoint, ping_interval=30, ping_timeout=10) as websocket:
        websocket.send(json.dumps(subscription))
        for raw_message in websocket:
            text = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
            yield cast(Mapping[str, object], json.loads(text))


def main() -> None:
    configure_logging_from_environment()
    repository = create_repository_from_environment()
    if not repository.list_active_targets():
        UpbitCollectionWorker(
            repository,
            create_upbit_client_from_environment(),
        ).refresh_candidate_universe()
    active_targets = repository.list_active_targets()
    market_codes = [target.market_code for target in active_targets]
    repository.record_collection_worker_heartbeat("realtime_collection", "running")
    try:
        rows = run_realtime_stream_collection(repository, upbit_websocket_messages(market_codes))
        logger.info("realtime_stream_collection_stopped rows=%s", rows)
    except Exception as exc:
        repository.record_collection_worker_heartbeat("realtime_collection", "failed", str(exc))
        logger.exception("realtime_stream_collection_failed error=%s", type(exc).__name__)
        raise


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _parse_kst_timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _parse_epoch_millis(value: object) -> datetime:
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=KST)


if __name__ == "__main__":
    main()
