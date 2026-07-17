from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import cast

import pytest

from goodmoneying_shared.versioned_microstructure import (
    MicrostructureCandleInput,
    MicrostructureOrderbookInput,
    MicrostructureOrderbookLevel,
    MicrostructureTradeInput,
    SourceQuality,
    calculate_microstructure_bucket,
)

START = datetime(2026, 7, 17, tzinfo=UTC)


def _snapshot(
    *,
    snapshot_id: int = 1,
    occurred_offset: int = 40,
    bid_size: str = "2",
    ask_size: str = "1",
    level_count: int = 10,
    level: str = "0",
    best_bid: str = "99",
    best_ask: str = "101",
) -> MicrostructureOrderbookInput:
    levels = tuple(
        MicrostructureOrderbookLevel(
            level_index=index,
            ask_price=Decimal(best_ask) + Decimal(index),
            ask_size=Decimal(ask_size),
            bid_price=Decimal(best_bid) - Decimal(index),
            bid_size=Decimal(bid_size),
        )
        for index in range(level_count)
    )
    return MicrostructureOrderbookInput(
        snapshot_id=snapshot_id,
        occurred_at=START + timedelta(seconds=occurred_offset),
        knowledge_at=START + timedelta(seconds=occurred_offset + 1),
        level=Decimal(level),
        levels=levels,
        source_receipt_id=snapshot_id + 100,
    )


def _trade(
    trade_id: int,
    direction: str,
    volume: str,
    occurred_offset: int,
) -> MicrostructureTradeInput:
    price = Decimal("100")
    return MicrostructureTradeInput(
        trade_event_id=trade_id,
        occurred_at=START + timedelta(seconds=occurred_offset),
        knowledge_at=START + timedelta(seconds=occurred_offset + 1),
        direction=direction,
        volume=Decimal(volume),
        amount=price * Decimal(volume),
        source_receipt_id=trade_id + 200,
    )


def _candle(
    *,
    quality: str = "available",
    volume: str = "8",
    amount: str = "800",
) -> MicrostructureCandleInput:
    return MicrostructureCandleInput(
        source_candle_revision_id=None if quality == "no_trade" else 11,
        started_at=START,
        knowledge_at=START + timedelta(minutes=1, seconds=2),
        source_as_of=START + timedelta(minutes=1),
        quality=cast(SourceQuality, quality),
        volume=Decimal(volume),
        amount=Decimal(amount),
        quality_event_through_id=7,
    )


def test_마지막_기본단위_10호가로_스프레드_깊이_불균형을_계산한다() -> None:
    older = _snapshot(snapshot_id=1, occurred_offset=10, bid_size="1", ask_size="1")
    closing = _snapshot(snapshot_id=2, occurred_offset=50, bid_size="2", ask_size="1")

    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [closing, older],
        [],
        orderbook_snapshot_through_id=2,
        trade_event_through_id=0,
        source_receipt_through_id=102,
    )

    assert point.closing_orderbook_snapshot_id == 2
    assert point.orderbook_status == "ready"
    assert point.orderbook_quality == "available"
    assert point.spread == Decimal("2")
    assert point.spread_bps == Decimal("200")
    assert point.bid_depth_10 == Decimal("20")
    assert point.ask_depth_10 == Decimal("10")
    with localcontext() as context:
        context.prec = 50
        assert point.orderbook_imbalance_10 == Decimal(1) / Decimal(3)


def test_마지막_모아보기_호가보다_앞선_마지막_level_0을_선택한다() -> None:
    default_level = _snapshot(snapshot_id=1, occurred_offset=40, level="0")
    grouped_level = _snapshot(snapshot_id=2, occurred_offset=50, level="1000")

    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [default_level, grouped_level],
        [],
        orderbook_snapshot_through_id=2,
        trade_event_through_id=0,
        source_receipt_through_id=102,
    )

    assert point.closing_orderbook_snapshot_id == 1
    assert point.orderbook_status == "ready"
    assert point.spread == Decimal("2")


@pytest.mark.parametrize(
    ("snapshot", "expected_status"),
    [
        (_snapshot(level_count=5), "partial"),
        (_snapshot(level="1000"), "partial"),
        (_snapshot(best_bid="102", best_ask="101"), "invalid"),
    ],
)
def test_비교할_수_없는_호가는_값을_합성하지_않는다(
    snapshot: MicrostructureOrderbookInput, expected_status: str
) -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [snapshot],
        [],
        orderbook_snapshot_through_id=snapshot.snapshot_id,
        trade_event_through_id=0,
        source_receipt_through_id=snapshot.source_receipt_id,
    )

    assert point.orderbook_status == expected_status
    assert point.spread is None
    assert point.spread_bps is None
    assert point.bid_depth_10 is None
    assert point.ask_depth_10 is None
    assert point.orderbook_imbalance_10 is None


def test_매수매도_체결은_강도_불균형_분당빈도를_계산한다() -> None:
    trades = [
        _trade(1, "BID", "3", 5),
        _trade(2, "ASK", "2", 20),
        _trade(3, "BID", "3", 40),
    ]

    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        trades,
        orderbook_snapshot_through_id=0,
        trade_event_through_id=3,
        source_receipt_through_id=203,
        source_candle=_candle(),
    )

    assert point.trade_status == "ready"
    assert point.trade_quality == "available"
    assert point.execution_strength_status == "ready"
    assert point.trade_count == 3
    assert point.trade_intensity_per_minute == Decimal("3")
    assert point.buy_volume == Decimal("6")
    assert point.sell_volume == Decimal("2")
    assert point.buy_sell_imbalance == Decimal("0.5")
    assert point.execution_strength == Decimal("300")


def test_매도체결_0인_일방향_구간은_강도를_0으로_만들지_않는다() -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [_trade(1, "BID", "4", 5)],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=1,
        source_receipt_through_id=201,
        source_candle=_candle(volume="4", amount="400"),
    )

    assert point.trade_status == "ready"
    assert point.buy_sell_imbalance == Decimal("1")
    assert point.execution_strength_status == "undefined"
    assert point.execution_strength is None


def test_무관측_구간은_거래_없음으로_추정하거나_0을_채우지_않는다() -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=0,
        source_receipt_through_id=0,
    )

    assert point.orderbook_status == "missing"
    assert point.trade_status == "missing"
    assert point.execution_strength_status == "missing"
    assert point.trade_count is None
    assert point.trade_intensity_per_minute is None
    assert point.buy_volume is None
    assert point.sell_volume is None
    assert point.buy_sell_imbalance is None
    assert point.execution_strength is None


def test_공식_무거래_캔들만_체결_0을_ready로_확정한다() -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=0,
        source_receipt_through_id=0,
        source_candle=_candle(quality="no_trade", volume="0", amount="0"),
    )

    assert point.trade_status == "ready"
    assert point.execution_strength_status == "undefined"
    assert point.trade_count == 0
    assert point.trade_intensity_per_minute == Decimal("0")
    assert point.volume_intensity_per_minute == Decimal("0")
    assert point.buy_volume == Decimal("0")
    assert point.sell_volume == Decimal("0")
    assert point.buy_sell_imbalance is None
    assert point.execution_strength is None


@pytest.mark.parametrize("quality", ["available", "unverified", "missing", "unavailable"])
def test_캔들_대사_실패나_불확정_품질은_체결값을_노출하지_않는다(quality: str) -> None:
    candle = (
        _candle(quality=quality, volume="999", amount="99900")
        if quality == "available"
        else _candle(quality=quality)
    )
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [_trade(1, "BID", "4", 5)],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=1,
        source_receipt_through_id=201,
        source_candle=candle,
    )

    assert point.trade_status == "missing"
    assert point.trade_count is None
    assert point.buy_volume is None
    assert point.source_candle_revision_id == (11 if quality != "no_trade" else None)
    assert point.quality_event_through_id == 7


@pytest.mark.parametrize("quality", ["unverified", "missing", "unavailable"])
def test_체결_원문과_캔들이_있어도_연결_품질이_불확정이면_값을_노출하지_않는다(
    quality: str,
) -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [_trade(1, "BID", "1", 5), _trade(2, "ASK", "1", 15)],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=2,
        source_receipt_through_id=202,
        source_candle=_candle(quality="available", volume="2", amount="200"),
        trade_quality=cast(SourceQuality, quality),
    )

    assert point.trade_quality == quality
    assert point.trade_status == "missing"
    assert point.trade_count is None
    assert point.execution_strength is None


def test_무거래_품질은_캔들_개정_대신_품질_이벤트를_요구한다() -> None:
    with pytest.raises(ValueError, match="no_trade"):
        MicrostructureCandleInput(
            source_candle_revision_id=11,
            started_at=START,
            knowledge_at=START + timedelta(minutes=1),
            source_as_of=START + timedelta(minutes=1),
            quality="no_trade",
            volume=Decimal("0"),
            amount=Decimal("0"),
            quality_event_through_id=7,
        )


def test_호가_무관측은_연결_증거가_없으면_unverified다() -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [],
        [],
        orderbook_snapshot_through_id=0,
        trade_event_through_id=0,
        source_receipt_through_id=0,
    )

    assert point.orderbook_quality == "unverified"
    assert point.trade_quality == "unverified"


@pytest.mark.parametrize("quality", ["unverified", "missing", "unavailable"])
def test_호가_원문이_있어도_연결_품질이_불확정이면_값을_노출하지_않는다(
    quality: str,
) -> None:
    point = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        [_snapshot(snapshot_id=1)],
        [],
        orderbook_snapshot_through_id=1,
        trade_event_through_id=0,
        source_receipt_through_id=201,
        orderbook_quality=cast(SourceQuality, quality),
    )

    assert point.orderbook_quality == quality
    assert point.orderbook_status == "missing"
    assert point.closing_orderbook_snapshot_id is None
    assert point.spread is None
    assert point.bid_depth_10 is None


def test_입력_순서가_달라도_내용_해시와_원천_계보가_같다() -> None:
    snapshots = [_snapshot(snapshot_id=1, occurred_offset=10), _snapshot(snapshot_id=2)]
    trades = [_trade(1, "BID", "1", 5), _trade(2, "ASK", "1", 15)]
    candle = _candle(quality="available", volume="2", amount="200")

    first = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        snapshots,
        trades,
        orderbook_snapshot_through_id=2,
        trade_event_through_id=2,
        source_receipt_through_id=202,
        source_candle=candle,
    )
    second = calculate_microstructure_bucket(
        START,
        START + timedelta(minutes=1),
        list(reversed(snapshots)),
        list(reversed(trades)),
        orderbook_snapshot_through_id=2,
        trade_event_through_id=2,
        source_receipt_through_id=202,
        source_candle=candle,
    )

    assert first.content_hash == second.content_hash
    assert first.input_lineage_hash == second.input_lineage_hash
    assert first.orderbook_snapshot_through_id == 2
    assert first.trade_event_through_id == 2
    assert first.source_receipt_through_id == 202


def test_지원하지_않는_버킷_길이는_명시적으로_거부한다() -> None:
    with pytest.raises(ValueError, match="지원하지 않는 미시구조 버킷"):
        calculate_microstructure_bucket(
            START,
            START + timedelta(seconds=30),
            [],
            [],
            orderbook_snapshot_through_id=0,
            trade_event_through_id=0,
            source_receipt_through_id=0,
        )
