from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Literal, cast

from goodmoneying_shared.models import CandleView
from goodmoneying_shared.versioned_indicators import _next_bucket

StatisticStatus = Literal["warming_up", "ready", "missing"]
CALCULATION_VERSION: Literal["market-statistics-v1"] = "market-statistics-v1"


@dataclass(frozen=True)
class MarketStatisticPoint:
    started_at: datetime
    close_return_1: Decimal | None
    realized_volatility_20: Decimal | None
    trade_volume: Decimal | None
    trade_amount: Decimal | None
    volatility_sample_count: int
    input_completeness_ratio: Decimal
    return_status: StatisticStatus
    volatility_status: StatisticStatus
    trade_status: StatisticStatus
    current_input_id: int | None
    current_input_is_rollup: bool
    source_revision_through_id: int
    quality_event_through_id: int | None
    source_as_of: datetime | None
    knowledge_at: datetime | None
    content_hash: str
    checkpoint_state: Mapping[str, object]


def calculate_market_statistics(
    candles: list[CandleView] | tuple[CandleView, ...],
    unit: str,
    *,
    requested_from: datetime | None = None,
    initial_checkpoint: Mapping[str, object] | None = None,
) -> tuple[MarketStatisticPoint, ...]:
    ordered = sorted(candles, key=lambda item: item.started_at)
    if len({item.started_at for item in ordered}) != len(ordered):
        raise ValueError("시장 통계 계산 전에 startedAt projection을 고정해야 한다.")
    result: list[MarketStatisticPoint] = []
    checkpoint = dict(initial_checkpoint or {})
    previous_close = _optional_decimal(checkpoint.get("previousClose"))
    previous_started_raw = checkpoint.get("previousStartedAt")
    previous_started_at = (
        datetime.fromisoformat(str(previous_started_raw)) if previous_started_raw else None
    )
    returns = [
        Decimal(str(value))
        for value in cast(list[object], checkpoint.get("recentReturns", []))
    ][-20:]
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        for candle in ordered:
            gap = previous_started_at is not None and candle.started_at != _next_bucket(
                previous_started_at, unit
            )
            valid = candle.quality == "available" and candle.completeness == "complete"
            if gap or not valid:
                previous_close = None
                returns = []
            if not valid:
                current_checkpoint = _checkpoint(
                    None, candle.started_at, [], candle.source_as_of
                )
                point = _point(
                    candle,
                    None,
                    None,
                    0,
                    "missing",
                    "missing",
                    "missing",
                    current_checkpoint,
                )
            else:
                close_return = (
                    candle.close / previous_close - Decimal(1)
                    if previous_close is not None and previous_close != 0
                    else None
                )
                if close_return is not None:
                    returns.append(close_return)
                    returns = returns[-20:]
                sample = min(len(returns), 20)
                volatility = None
                if sample == 20:
                    window = returns[-20:]
                    mean = sum(window, Decimal(0)) / Decimal(20)
                    volatility = (
                        sum(((value - mean) ** 2 for value in window), Decimal(0)) / Decimal(20)
                    ).sqrt()
                point = _point(
                    candle,
                    close_return,
                    volatility,
                    sample,
                    "ready" if close_return is not None else "warming_up",
                    "ready" if volatility is not None else "warming_up",
                    "ready",
                    _checkpoint(
                        candle.close, candle.started_at, returns, candle.source_as_of
                    ),
                )
                previous_close = candle.close
            previous_started_at = candle.started_at
            if requested_from is None or point.started_at >= requested_from:
                result.append(point)
    return tuple(result)


def _point(
    candle: CandleView,
    close_return: Decimal | None,
    volatility: Decimal | None,
    sample: int,
    return_status: StatisticStatus,
    volatility_status: StatisticStatus,
    trade_status: StatisticStatus,
    checkpoint_state: Mapping[str, object],
) -> MarketStatisticPoint:
    values = {
        "closeReturn1": _canonical(close_return),
        "realizedVolatility20": _canonical(volatility),
        "tradeVolume": _canonical(candle.volume) if trade_status == "ready" else None,
        "tradeAmount": _canonical(candle.trade_amount) if trade_status == "ready" else None,
        "sample": sample,
        "statuses": [return_status, volatility_status, trade_status],
        "checkpoint": dict(checkpoint_state),
    }
    content_hash = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return MarketStatisticPoint(
        started_at=candle.started_at,
        close_return_1=close_return,
        realized_volatility_20=volatility,
        trade_volume=candle.volume if trade_status == "ready" else None,
        trade_amount=candle.trade_amount if trade_status == "ready" else None,
        volatility_sample_count=sample,
        input_completeness_ratio=Decimal(sample) / Decimal(20),
        return_status=return_status,
        volatility_status=volatility_status,
        trade_status=trade_status,
        current_input_id=candle.rollup_id or max(candle.input_revision_ids, default=None),
        current_input_is_rollup=candle.rollup_id is not None,
        source_revision_through_id=candle.source_revision_through_id,
        quality_event_through_id=candle.quality_event_through_id,
        source_as_of=candle.source_as_of,
        knowledge_at=candle.knowledge_at,
        content_hash=content_hash,
        checkpoint_state=MappingProxyType(dict(checkpoint_state)),
    )


def _checkpoint(
    previous_close: Decimal | None,
    previous_started_at: datetime,
    returns: list[Decimal],
    source_as_of: datetime | None,
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "previousClose": _canonical(previous_close),
            "previousStartedAt": previous_started_at.isoformat(),
            "recentReturns": [_canonical(value) for value in returns[-20:]],
            "sourceAsOf": source_as_of.isoformat() if source_as_of is not None else None,
        }
    )


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _canonical(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "0" if value == 0 else format(value.normalize(), "f")
