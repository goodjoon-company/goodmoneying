from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from hashlib import sha256
from typing import Protocol, cast
from uuid import uuid4

from goodmoneying_shared.models import (
    Instrument,
    OrderbookSnapshot,
    OrderbookSnapshotLevel,
    OrderbookSummary,
    RealtimeSourceFrame,
    SourceCandle,
    SourceReceipt,
    TickerSnapshot,
    TradeDirection,
    TradeEvent,
)
from goodmoneying_shared.postgres_repository import PostgresOperationsRepository
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
DEFAULT_SUBSCRIPTION_REFRESH_SECONDS = 300.0


@dataclass(frozen=True)
class SubscriptionPlan:
    market_codes: tuple[str, ...]
    generation: int
    target_versions: tuple[tuple[int, int], ...] = ()
    market_codes_by_type: tuple[tuple[str, tuple[str, ...]], ...] = ()


class RealtimeStreamFactory(Protocol):
    def __call__(
        self,
        market_codes: list[str],
        *,
        market_codes_by_type: Mapping[str, tuple[str, ...]] | None,
        lifetime_seconds: float,
        on_connected: Callable[[], None],
    ) -> Iterator[Mapping[str, object]]: ...


MESSAGE_TYPES = ("ticker", "trade", "orderbook", "candle.1m")
DATA_TYPE_TO_MESSAGE_TYPE = {
    "source_candle": "candle.1m",
    "trade_event": "trade",
    "orderbook_snapshot": "orderbook",
    "ticker_snapshot": "ticker",
}
MESSAGE_TYPE_TO_DATA_TYPE = {
    message_type: data_type for data_type, message_type in DATA_TYPE_TO_MESSAGE_TYPE.items()
}


def build_upbit_websocket_subscription(
    market_codes: list[str],
    *,
    market_codes_by_type: Mapping[str, Iterable[str]] | None = None,
) -> list[dict[str, object]]:
    default_codes = tuple(dict.fromkeys(code.upper() for code in market_codes))
    selected_codes = (
        {message_type: default_codes for message_type in MESSAGE_TYPES}
        if market_codes_by_type is None
        else {
            message_type: tuple(
                dict.fromkeys(code.upper() for code in market_codes_by_type.get(message_type, ()))
            )
            for message_type in MESSAGE_TYPES
        }
    )
    requests: list[dict[str, object]] = [{"ticket": str(uuid4())}]
    requests.extend(
        {
            "type": message_type,
            "codes": list(selected_codes[message_type]),
            "is_only_realtime": False,
        }
        for message_type in MESSAGE_TYPES
        if selected_codes[message_type]
    )
    requests.append({"format": "DEFAULT"})
    return requests


class RealtimeStreamBuffer:
    def __init__(
        self,
        instruments_by_market: Mapping[str, Instrument],
        *,
        allowed_market_types: set[tuple[str, str]] | None = None,
        now: Callable[[], datetime] = now_kst,
    ) -> None:
        self._instruments_by_market = instruments_by_market
        self._allowed_market_types = allowed_market_types
        self._now = now
        self._default_connection_id = str(uuid4())
        self._default_frame_sequence = 0
        self._tickers: dict[int, TickerSnapshot] = {}
        self._candles: dict[tuple[int, datetime], SourceCandle] = {}
        self._trades: dict[tuple[int, int], TradeEvent] = {}
        self._source_frames: list[RealtimeSourceFrame] = []

    def apply(
        self,
        payload: Mapping[str, object],
        *,
        connection_id: str | None = None,
        frame_sequence: int | None = None,
    ) -> None:
        message_type = str(payload.get("type") or "")
        market_code = str(payload.get("code") or "")
        instrument = self._instruments_by_market.get(market_code)
        if instrument is None or (
            self._allowed_market_types is not None
            and (market_code, message_type) not in self._allowed_market_types
        ):
            return
        if connection_id is None:
            connection_id = self._default_connection_id
        if frame_sequence is None:
            self._default_frame_sequence += 1
            frame_sequence = self._default_frame_sequence
        received_at = self._now()
        occurred_at = (
            _parse_epoch_millis(payload["timestamp"])
            if payload.get("timestamp") is not None
            else received_at
        )
        raw_payload = dict(payload)
        payload_checksum = _payload_checksum(raw_payload)
        snapshot: OrderbookSnapshot | None = None
        summary: OrderbookSummary | None = None
        if message_type == "ticker":
            self._tickers[instrument.id] = self._ticker_snapshot(
                instrument.id, payload, occurred_at=occurred_at, received_at=received_at
            )
        elif message_type == "trade":
            trade = self._trade_event(instrument.id, payload, received_at=received_at)
            self._trades[(instrument.id, trade.sequential_id)] = trade
        elif message_type == "orderbook":
            summary = self._orderbook_summary(
                instrument.id, payload, occurred_at=occurred_at, received_at=received_at
            )
            snapshot = self._orderbook_snapshot(
                instrument.id,
                payload,
                occurred_at=occurred_at,
                received_at=received_at,
                payload_checksum=payload_checksum,
            )
        elif message_type == "candle.1m":
            candle = self._source_candle(instrument.id, payload, received_at=received_at)
            self._candles[(instrument.id, candle.candle_start_at)] = candle
        else:
            return
        self._source_frames.append(
            RealtimeSourceFrame(
                receipt=SourceReceipt(
                    data_type=MESSAGE_TYPE_TO_DATA_TYPE[message_type],
                    instrument_id=instrument.id,
                    connection_id=connection_id,
                    frame_sequence=frame_sequence,
                    occurred_at=occurred_at,
                    received_at=received_at,
                    payload_checksum=payload_checksum,
                    raw_payload=raw_payload,
                ),
                snapshot=snapshot,
                summary=summary,
            )
        )

    def flush(self, repository: OperationsRepository) -> int:
        tickers = list(self._tickers.values())
        candles = list(self._candles.values())
        trades = list(self._trades.values())
        source_frames = list(self._source_frames)
        row_count = len(source_frames)
        if row_count == 0:
            return 0
        repository.record_realtime_source_frames(source_frames)
        if tickers or candles:
            repository.record_incremental_collection(tickers, [], candles)
        if trades:
            repository.record_trade_events(trades)
        self._tickers.clear()
        self._candles.clear()
        self._trades.clear()
        self._source_frames.clear()
        return row_count

    def _ticker_snapshot(
        self,
        instrument_id: int,
        payload: Mapping[str, object],
        *,
        occurred_at: datetime,
        received_at: datetime,
    ) -> TickerSnapshot:
        return TickerSnapshot(
            instrument_id=instrument_id,
            bucket_at=minute_bucket(occurred_at),
            trade_price=_decimal(payload["trade_price"]),
            acc_trade_price_24h=_decimal(payload.get("acc_trade_price_24h", 0)),
            change_rate=_decimal(payload.get("signed_change_rate", 0)),
            occurred_at=occurred_at,
            received_at=received_at,
        )

    def _orderbook_summary(
        self,
        instrument_id: int,
        payload: Mapping[str, object],
        *,
        occurred_at: datetime,
        received_at: datetime,
    ) -> OrderbookSummary:
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
            bucket_at=minute_bucket(occurred_at),
            best_bid_price=best_bid,
            best_bid_size=_decimal(best["bid_size"]),
            best_ask_price=best_ask,
            best_ask_size=_decimal(best["ask_size"]),
            spread=best_ask - best_bid,
            bid_depth_10=bid_depth,
            ask_depth_10=ask_depth,
            imbalance_10=imbalance,
            occurred_at=occurred_at,
            received_at=received_at,
        )

    def _orderbook_snapshot(
        self,
        instrument_id: int,
        payload: Mapping[str, object],
        *,
        occurred_at: datetime,
        received_at: datetime,
        payload_checksum: str,
    ) -> OrderbookSnapshot:
        units = cast(list[Mapping[str, object]], payload["orderbook_units"])
        levels = tuple(
            OrderbookSnapshotLevel(
                level_index=index,
                ask_price=_decimal(unit["ask_price"]),
                ask_size=_decimal(unit["ask_size"]),
                bid_price=_decimal(unit["bid_price"]),
                bid_size=_decimal(unit["bid_size"]),
            )
            for index, unit in enumerate(units)
        )
        return OrderbookSnapshot(
            instrument_id=instrument_id,
            source="UPBIT",
            occurred_at=occurred_at,
            received_at=received_at,
            total_ask_size=_decimal(
                payload.get("total_ask_size", sum((level.ask_size for level in levels), Decimal()))
            ),
            total_bid_size=_decimal(
                payload.get("total_bid_size", sum((level.bid_size for level in levels), Decimal()))
            ),
            level_count=len(levels),
            level=_decimal(payload["level"]) if payload.get("level") is not None else None,
            stream_type=str(payload["stream_type"]) if payload.get("stream_type") else None,
            payload_checksum=payload_checksum,
            levels=levels,
        )

    def _source_candle(
        self,
        instrument_id: int,
        payload: Mapping[str, object],
        *,
        received_at: datetime,
    ) -> SourceCandle:
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
            collected_at=received_at,
        )

    def _trade_event(
        self,
        instrument_id: int,
        payload: Mapping[str, object],
        *,
        received_at: datetime,
    ) -> TradeEvent:
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
            collected_at=received_at,
        )


def run_realtime_stream_collection(
    repository: OperationsRepository,
    messages: Iterable[Mapping[str, object]],
    *,
    connection_id: str | None = None,
    allowed_market_types: set[tuple[str, str]] | None = None,
    flush_interval_seconds: float = 1.0,
    now_monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = now_kst,
    purge_retention: bool = True,
) -> int:
    active_targets = repository.list_active_targets()
    buffer = RealtimeStreamBuffer(
        {target.market_code: target for target in active_targets},
        allowed_market_types=allowed_market_types,
        now=now,
    )
    last_flush_at = now_monotonic()
    total_rows = 0
    resolved_connection_id = connection_id or str(uuid4())

    def flush_buffer() -> int:
        rows = buffer.flush(repository)
        if rows > 0:
            repository.record_collection_worker_heartbeat("realtime_collection", "running")
        return rows

    try:
        for frame_sequence, payload in enumerate(messages, start=1):
            buffer.apply(
                payload,
                connection_id=resolved_connection_id,
                frame_sequence=frame_sequence,
            )
            if now_monotonic() - last_flush_at >= flush_interval_seconds:
                total_rows += flush_buffer()
                last_flush_at = now_monotonic()
    finally:
        total_rows += flush_buffer()
        if purge_retention:
            repository.purge_expired_source_evidence()
    return total_rows


def upbit_websocket_messages(
    market_codes: list[str],
    *,
    market_codes_by_type: Mapping[str, tuple[str, ...]] | None = None,
    endpoint: str = UPBIT_WEBSOCKET_URL,
    lifetime_seconds: float = DEFAULT_SUBSCRIPTION_REFRESH_SECONDS,
    on_connected: Callable[[], None] = lambda: None,
    now_monotonic: Callable[[], float] = time.monotonic,
) -> Iterator[Mapping[str, object]]:
    from websockets.sync.client import connect

    subscription = build_upbit_websocket_subscription(
        market_codes,
        market_codes_by_type=market_codes_by_type,
    )
    with connect(endpoint, ping_interval=30, ping_timeout=10) as websocket:
        websocket.send(json.dumps(subscription))
        on_connected()
        deadline = now_monotonic() + lifetime_seconds
        while (remaining_seconds := deadline - now_monotonic()) > 0:
            try:
                raw_message = websocket.recv(timeout=remaining_seconds)
            except TimeoutError:
                break
            text = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
            yield cast(Mapping[str, object], json.loads(text))


def load_subscription_plan(repository: OperationsRepository) -> SubscriptionPlan:
    if not isinstance(repository, PostgresOperationsRepository):
        fallback_market_codes = tuple(
            sorted(target.market_code for target in repository.list_active_targets())
        )
        return SubscriptionPlan(market_codes=fallback_market_codes, generation=0)

    desires = repository.load_collection_subscription_desires()
    selected_desires = [
        desire
        for desire in desires
        if desire.desired_state == "subscribed"
        and desire.target_status == "active"
        and desire.trading_status == "active"
        and desire.continuous
    ]
    codes_by_type = tuple(
        (
            message_type,
            tuple(
                sorted(
                    {
                        desire.market_code
                        for desire in selected_desires
                        if DATA_TYPE_TO_MESSAGE_TYPE[desire.data_type] == message_type
                    }
                )
            ),
        )
        for message_type in MESSAGE_TYPES
        if any(
            DATA_TYPE_TO_MESSAGE_TYPE[desire.data_type] == message_type
            for desire in selected_desires
        )
    )
    market_codes = sorted(
        {market_code for _message_type, codes in codes_by_type for market_code in codes}
    )
    return SubscriptionPlan(
        market_codes=tuple(market_codes),
        generation=max((desire.generation for desire in desires), default=0),
        target_versions=tuple(
            (desire.target_spec_id, desire.generation) for desire in desires
        ),
        market_codes_by_type=codes_by_type,
    )


def mark_subscription_plan_applied(
    repository: OperationsRepository,
    plan: SubscriptionPlan,
    connection_id: str,
) -> None:
    if not isinstance(repository, PostgresOperationsRepository):
        return
    repository.mark_collection_subscription_desires_applied(
        plan.target_versions,
        connection_id=connection_id,
    )


def run_realtime_subscription_loop(
    repository: OperationsRepository,
    *,
    load_plan: Callable[[OperationsRepository], SubscriptionPlan] = load_subscription_plan,
    mark_applied: Callable[
        [OperationsRepository, SubscriptionPlan, str], None
    ] = mark_subscription_plan_applied,
    stream_factory: RealtimeStreamFactory = upbit_websocket_messages,
    refresh_interval_seconds: float = DEFAULT_SUBSCRIPTION_REFRESH_SECONDS,
    max_connections: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    total_rows = 0
    connection_count = 0
    while max_connections is None or connection_count < max_connections:
        plan = load_plan(repository)
        connection_id = str(uuid4())
        if not plan.market_codes:
            mark_applied(repository, plan, connection_id)
            connection_count += 1
            if max_connections is None or connection_count < max_connections:
                sleep(refresh_interval_seconds)
            continue

        messages = stream_factory(
            list(plan.market_codes),
            market_codes_by_type=dict(plan.market_codes_by_type) or None,
            lifetime_seconds=refresh_interval_seconds,
            on_connected=partial(mark_applied, repository, plan, connection_id),
        )
        allowed_market_types = {
            (market_code, message_type)
            for message_type, codes in plan.market_codes_by_type
            for market_code in codes
        }
        total_rows += run_realtime_stream_collection(
            repository,
            messages,
            connection_id=connection_id,
            allowed_market_types=allowed_market_types or None,
        )
        connection_count += 1
    return total_rows


def main() -> None:
    configure_logging_from_environment()
    repository = create_repository_from_environment()
    if not repository.list_active_targets():
        UpbitCollectionWorker(
            repository,
            create_upbit_client_from_environment(),
        ).refresh_candidate_universe()
    repository.record_collection_worker_heartbeat("realtime_collection", "running")
    try:
        rows = run_realtime_subscription_loop(repository)
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
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC)


def _payload_checksum(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    main()
