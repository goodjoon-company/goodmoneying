from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal

MicrostructureStatus = Literal["ready", "missing", "partial", "invalid", "undefined"]
SourceQuality = Literal["available", "no_trade", "missing", "unavailable", "unverified"]
CALCULATION_VERSION = "microstructure-v1"


@dataclass(frozen=True)
class MicrostructureOrderbookLevel:
    level_index: int
    ask_price: Decimal
    ask_size: Decimal
    bid_price: Decimal
    bid_size: Decimal


@dataclass(frozen=True)
class MicrostructureOrderbookInput:
    snapshot_id: int
    occurred_at: datetime
    knowledge_at: datetime
    level: Decimal | None
    levels: tuple[MicrostructureOrderbookLevel, ...]
    source_receipt_id: int


@dataclass(frozen=True)
class MicrostructureTradeInput:
    trade_event_id: int
    occurred_at: datetime
    knowledge_at: datetime
    direction: str
    volume: Decimal
    amount: Decimal
    source_receipt_id: int


@dataclass(frozen=True)
class MicrostructureCandleInput:
    source_candle_revision_id: int | None
    started_at: datetime
    knowledge_at: datetime
    source_as_of: datetime
    quality: SourceQuality
    volume: Decimal
    amount: Decimal
    quality_event_through_id: int | None

    def __post_init__(self) -> None:
        if self.quality == "available" and self.source_candle_revision_id is None:
            raise ValueError("available 품질에는 source candle revision이 필요하다.")
        if self.quality == "no_trade" and (
            self.source_candle_revision_id is not None or self.quality_event_through_id is None
        ):
            raise ValueError(
                "no_trade 품질은 candle revision 없이 quality event로만 증명해야 한다."
            )
        if self.volume < 0 or self.amount < 0:
            raise ValueError("캔들 거래량과 거래대금은 0 이상이어야 한다.")


@dataclass(frozen=True)
class MicrostructurePoint:
    started_at: datetime
    calculation_version: str
    closing_orderbook_snapshot_id: int | None
    closing_orderbook_source_receipt_id: int | None
    spread: Decimal | None
    spread_bps: Decimal | None
    bid_depth_10: Decimal | None
    ask_depth_10: Decimal | None
    orderbook_imbalance_10: Decimal | None
    trade_count: int | None
    trade_intensity_per_minute: Decimal | None
    volume_intensity_per_minute: Decimal | None
    buy_count: int | None
    sell_count: int | None
    buy_volume: Decimal | None
    sell_volume: Decimal | None
    buy_sell_imbalance: Decimal | None
    execution_strength: Decimal | None
    orderbook_status: MicrostructureStatus
    orderbook_quality: SourceQuality
    trade_status: MicrostructureStatus
    trade_quality: SourceQuality
    execution_strength_status: MicrostructureStatus
    orderbook_snapshot_through_id: int
    trade_event_through_id: int
    source_receipt_through_id: int
    source_candle_revision_id: int | None
    quality_event_through_id: int | None
    connection_quality_through_id: int
    source_as_of: datetime | None
    knowledge_at: datetime | None
    input_lineage_hash: str
    content_hash: str


def calculate_microstructure_bucket(
    started_at: datetime,
    ended_at: datetime,
    orderbooks: list[MicrostructureOrderbookInput] | tuple[MicrostructureOrderbookInput, ...],
    trades: list[MicrostructureTradeInput] | tuple[MicrostructureTradeInput, ...],
    *,
    orderbook_snapshot_through_id: int,
    trade_event_through_id: int,
    source_receipt_through_id: int,
    source_candle: MicrostructureCandleInput | None = None,
    orderbook_quality: SourceQuality | None = None,
    trade_quality: SourceQuality | None = None,
    connection_quality_through_id: int = 0,
    connection_quality_source_as_of: datetime | None = None,
    connection_quality_knowledge_at: datetime | None = None,
) -> MicrostructurePoint:
    if ended_at - started_at != timedelta(minutes=1):
        raise ValueError("지원하지 않는 미시구조 버킷 길이다.")
    if any(
        value < 0
        for value in (
            orderbook_snapshot_through_id,
            trade_event_through_id,
            source_receipt_through_id,
        )
    ):
        raise ValueError("미시구조 원천 프런티어는 0 이상이어야 한다.")

    selected_orderbooks = sorted(
        (
            item
            for item in orderbooks
            if started_at <= item.occurred_at < ended_at
            and item.snapshot_id <= orderbook_snapshot_through_id
            and item.source_receipt_id <= source_receipt_through_id
        ),
        key=lambda item: (item.occurred_at, item.snapshot_id),
    )
    selected_trades = sorted(
        (
            item
            for item in trades
            if started_at <= item.occurred_at < ended_at
            and item.trade_event_id <= trade_event_through_id
            and item.source_receipt_id <= source_receipt_through_id
        ),
        key=lambda item: (item.occurred_at, item.trade_event_id),
    )
    default_level_orderbooks = [item for item in selected_orderbooks if item.level == Decimal(0)]
    closing_candidate = (
        default_level_orderbooks[-1]
        if default_level_orderbooks
        else (selected_orderbooks[-1] if selected_orderbooks else None)
    )
    resolved_orderbook_quality: SourceQuality = orderbook_quality or (
        "available" if closing_candidate is not None else "unverified"
    )
    closing = closing_candidate if resolved_orderbook_quality == "available" else None
    candle_quality: SourceQuality = (
        source_candle.quality if source_candle is not None else "unverified"
    )
    resolved_trade_quality: SourceQuality = (
        trade_quality
        if trade_quality is not None and trade_quality != "available"
        else candle_quality
    )

    (
        spread,
        spread_bps,
        bid_depth,
        ask_depth,
        orderbook_imbalance,
        orderbook_status,
    ) = _orderbook_values(closing)
    (
        trade_count,
        intensity,
        buy_count,
        sell_count,
        buy_volume,
        sell_volume,
        trade_imbalance,
        strength,
        trade_status,
        strength_status,
    ) = _trade_values(selected_trades, source_candle, resolved_trade_quality)

    source_as_of_values = [item.occurred_at for item in selected_orderbooks]
    source_as_of_values.extend(item.occurred_at for item in selected_trades)
    knowledge_values = [item.knowledge_at for item in selected_orderbooks]
    knowledge_values.extend(item.knowledge_at for item in selected_trades)
    if source_candle is not None:
        source_as_of_values.append(source_candle.source_as_of)
        knowledge_values.append(source_candle.knowledge_at)
    if connection_quality_source_as_of is not None:
        source_as_of_values.append(connection_quality_source_as_of)
    if connection_quality_knowledge_at is not None:
        knowledge_values.append(connection_quality_knowledge_at)
    source_as_of = max(source_as_of_values, default=None)
    knowledge_at = max(knowledge_values, default=None)

    lineage_payload: Mapping[str, object] = {
        "orderbookSnapshotIds": [item.snapshot_id for item in selected_orderbooks],
        "tradeEventIds": [item.trade_event_id for item in selected_trades],
        "sourceReceiptIds": sorted(
            [item.source_receipt_id for item in selected_orderbooks]
            + [item.source_receipt_id for item in selected_trades]
        ),
        "sourceCandleRevisionId": (
            source_candle.source_candle_revision_id if source_candle else None
        ),
        "qualityEventThroughId": (
            source_candle.quality_event_through_id if source_candle else None
        ),
        "connectionQualityThroughId": connection_quality_through_id,
        "frontiers": {
            "orderbookSnapshotThroughId": orderbook_snapshot_through_id,
            "tradeEventThroughId": trade_event_through_id,
            "sourceReceiptThroughId": source_receipt_through_id,
        },
    }
    input_lineage_hash = _hash(lineage_payload)
    content_hash = _hash(
        {
            "startedAt": started_at.isoformat(),
            "calculationVersion": CALCULATION_VERSION,
            "closingOrderbookSnapshotId": closing.snapshot_id if closing else None,
            "values": {
                "spread": _canonical(spread),
                "spreadBps": _canonical(spread_bps),
                "bidDepth10": _canonical(bid_depth),
                "askDepth10": _canonical(ask_depth),
                "orderbookImbalance10": _canonical(orderbook_imbalance),
                "tradeCount": trade_count,
                "tradeIntensityPerMinute": _canonical(intensity),
                "volumeIntensityPerMinute": _canonical(
                    buy_volume + sell_volume
                    if buy_volume is not None and sell_volume is not None
                    else None
                ),
                "buyCount": buy_count,
                "sellCount": sell_count,
                "buyVolume": _canonical(buy_volume),
                "sellVolume": _canonical(sell_volume),
                "buySellImbalance": _canonical(trade_imbalance),
                "executionStrength": _canonical(strength),
            },
            "statuses": [orderbook_status, trade_status, strength_status],
            "qualities": [resolved_orderbook_quality, resolved_trade_quality],
            "inputLineageHash": input_lineage_hash,
        }
    )
    return MicrostructurePoint(
        started_at=started_at,
        calculation_version=CALCULATION_VERSION,
        closing_orderbook_snapshot_id=closing.snapshot_id if closing else None,
        closing_orderbook_source_receipt_id=closing.source_receipt_id if closing else None,
        spread=spread,
        spread_bps=spread_bps,
        bid_depth_10=bid_depth,
        ask_depth_10=ask_depth,
        orderbook_imbalance_10=orderbook_imbalance,
        trade_count=trade_count,
        trade_intensity_per_minute=intensity,
        volume_intensity_per_minute=(
            buy_volume + sell_volume if buy_volume is not None and sell_volume is not None else None
        ),
        buy_count=buy_count,
        sell_count=sell_count,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_sell_imbalance=trade_imbalance,
        execution_strength=strength,
        orderbook_status=orderbook_status,
        orderbook_quality=resolved_orderbook_quality,
        trade_status=trade_status,
        trade_quality=resolved_trade_quality,
        execution_strength_status=strength_status,
        orderbook_snapshot_through_id=orderbook_snapshot_through_id,
        trade_event_through_id=trade_event_through_id,
        source_receipt_through_id=source_receipt_through_id,
        source_candle_revision_id=(
            source_candle.source_candle_revision_id if source_candle else None
        ),
        quality_event_through_id=(
            source_candle.quality_event_through_id if source_candle else None
        ),
        connection_quality_through_id=connection_quality_through_id,
        source_as_of=source_as_of,
        knowledge_at=knowledge_at,
        input_lineage_hash=input_lineage_hash,
        content_hash=content_hash,
    )


def _orderbook_values(
    snapshot: MicrostructureOrderbookInput | None,
) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    MicrostructureStatus,
]:
    if snapshot is None:
        return None, None, None, None, None, "missing"
    ordered = sorted(snapshot.levels, key=lambda item: item.level_index)
    if snapshot.level != Decimal(0) or len(ordered) < 10:
        return None, None, None, None, None, "partial"
    first_ten = ordered[:10]
    if [item.level_index for item in first_ten] != list(range(10)) or any(
        item.ask_price <= 0 or item.bid_price <= 0 or item.ask_size < 0 or item.bid_size < 0
        for item in first_ten
    ):
        return None, None, None, None, None, "invalid"
    best = first_ten[0]
    if best.bid_price >= best.ask_price:
        return None, None, None, None, None, "invalid"
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        spread = best.ask_price - best.bid_price
        midpoint = (best.ask_price + best.bid_price) / Decimal(2)
        if midpoint <= 0:
            return None, None, None, None, None, "invalid"
        bid_depth = sum((item.bid_size for item in first_ten), Decimal(0))
        ask_depth = sum((item.ask_size for item in first_ten), Decimal(0))
        denominator = bid_depth + ask_depth
        if denominator == 0:
            return None, None, None, None, None, "undefined"
        return (
            spread,
            spread / midpoint * Decimal(10000),
            bid_depth,
            ask_depth,
            (bid_depth - ask_depth) / denominator,
            "ready",
        )


def _trade_values(
    trades: list[MicrostructureTradeInput],
    source_candle: MicrostructureCandleInput | None,
    resolved_quality: SourceQuality,
) -> tuple[
    int | None,
    Decimal | None,
    int | None,
    int | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
    MicrostructureStatus,
    MicrostructureStatus,
]:
    if resolved_quality == "no_trade" and source_candle is not None:
        if trades or source_candle.volume != 0 or source_candle.amount != 0:
            return None, None, None, None, None, None, None, None, "missing", "missing"
        return (
            0,
            Decimal(0),
            0,
            0,
            Decimal(0),
            Decimal(0),
            None,
            None,
            "ready",
            "undefined",
        )
    if source_candle is None or resolved_quality != "available" or not trades:
        return None, None, None, None, None, None, None, None, "missing", "missing"
    if any(
        item.direction not in {"BID", "ASK"} or item.volume <= 0 or item.amount <= 0
        for item in trades
    ):
        return None, None, None, None, None, None, None, None, "invalid", "invalid"
    raw_volume = sum((item.volume for item in trades), Decimal(0))
    raw_amount = sum((item.amount for item in trades), Decimal(0))
    if raw_volume != source_candle.volume or raw_amount != source_candle.amount:
        return None, None, None, None, None, None, None, None, "missing", "missing"
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        buys = [item for item in trades if item.direction == "BID"]
        sells = [item for item in trades if item.direction == "ASK"]
        buy_volume = sum((item.volume for item in buys), Decimal(0))
        sell_volume = sum((item.volume for item in sells), Decimal(0))
        total_volume = buy_volume + sell_volume
        imbalance = (buy_volume - sell_volume) / total_volume if total_volume > 0 else None
        strength = buy_volume / sell_volume * Decimal(100) if sell_volume > 0 else None
        return (
            len(trades),
            Decimal(len(trades)),
            len(buys),
            len(sells),
            buy_volume,
            sell_volume,
            imbalance,
            strength,
            "ready",
            "ready" if strength is not None else "undefined",
        )


def _hash(value: Mapping[str, object] | dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _canonical(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "0" if value == 0 else format(value.normalize(), "f")
