from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Literal, cast

from goodmoneying_shared.models import CandleView

IndicatorStatus = Literal["warming_up", "ready", "missing"]


@dataclass(frozen=True)
class IndicatorDefinitionVersion:
    key: str
    algorithm: str
    parameters: Mapping[str, str | int]
    decimal_precision: int
    rounding: str
    implementation_version: str
    definition_hash: str


@dataclass(frozen=True)
class IndicatorPoint:
    started_at: datetime
    values: Mapping[str, Decimal | None]
    statuses: Mapping[str, IndicatorStatus]
    definition_version_hashes: Mapping[str, str]
    lineage_by_indicator: Mapping[str, tuple[int, ...]]
    rollup_ids: tuple[int, ...]
    source_revision_through_id: int
    quality_event_through_id: int | None
    source_as_of: datetime | None
    knowledge_at: datetime | None
    current_input_id: int | None
    current_input_is_rollup: bool
    checkpoint_state: Mapping[str, object]


def _definition(
    key: str, algorithm: str, parameters: Mapping[str, str | int]
) -> IndicatorDefinitionVersion:
    payload = {
        "algorithm": algorithm,
        "decimal": {"precision": 50, "rounding": "ROUND_HALF_EVEN"},
        "implementationVersion": "indicator-engine-v1",
        "key": key,
        "parameters": dict(sorted(parameters.items())),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return IndicatorDefinitionVersion(
        key=key,
        algorithm=algorithm,
        parameters=MappingProxyType(dict(parameters)),
        decimal_precision=50,
        rounding="ROUND_HALF_EVEN",
        implementation_version="indicator-engine-v1",
        definition_hash=hashlib.sha256(encoded.encode()).hexdigest(),
    )


INDICATOR_DEFINITION_VERSIONS: Mapping[str, IndicatorDefinitionVersion] = MappingProxyType(
    {
        "sma20": _definition("sma20", "simple-moving-average", {"period": 20}),
        "sma60": _definition("sma60", "simple-moving-average", {"period": 60}),
        "ema20": _definition(
            "ema20", "exponential-moving-average", {"period": 20, "seed": "sma20"}
        ),
        "bollinger20": _definition(
            "bollinger20",
            "bollinger-bands-population-standard-deviation",
            {"period": 20, "standardDeviations": "2"},
        ),
        "rsi14": _definition(
            "rsi14",
            "wilder-relative-strength-index",
            {"period": 14, "flat": "50", "onlyGain": "100", "onlyLoss": "0"},
        ),
    }
)


def calculate_indicator_series(
    candles: list[CandleView] | tuple[CandleView, ...],
    *,
    requested_from: datetime | None = None,
    unit: str = "1m",
    initial_checkpoint: Mapping[str, object] | None = None,
) -> tuple[IndicatorPoint, ...]:
    """정렬된 불변 캔들 개정에서 범위와 무관한 v1 지표를 계산한다."""

    ordered = sorted(candles, key=lambda item: item.started_at)
    if len({item.started_at for item in ordered}) != len(ordered):
        raise ValueError(
            "지표 계산 전에 startedAt별 불변 rollup projection을 하나로 고정해야 한다."
        )
    checkpoint = dict(initial_checkpoint or {})
    closes = [
        Decimal(str(value))
        for value in cast(list[object], checkpoint.get("recentCloses", []))
    ]
    consecutive_count = int(str(checkpoint.get("consecutiveCount", 0)))
    result: list[IndicatorPoint] = []
    ema = _optional_decimal(checkpoint.get("ema20"))
    average_gain = _optional_decimal(checkpoint.get("rsiAverageGain"))
    average_loss = _optional_decimal(checkpoint.get("rsiAverageLoss"))
    previous_close = _optional_decimal(checkpoint.get("previousClose"))
    previous_started_raw = checkpoint.get("previousStartedAt")
    previous_started_at = (
        datetime.fromisoformat(str(previous_started_raw)) if previous_started_raw else None
    )
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        for candle in ordered:
            if previous_started_at is not None and candle.started_at != _next_bucket(
                previous_started_at, unit
            ):
                closes = []
                consecutive_count = 0
                ema = None
                average_gain = None
                average_loss = None
                previous_close = None
            if candle.quality != "available" or candle.completeness != "complete":
                closes = []
                consecutive_count = 0
                ema = None
                average_gain = None
                average_loss = None
                previous_close = None
                current_checkpoint = _checkpoint(
                    closes,
                    consecutive_count,
                    ema,
                    average_gain,
                    average_loss,
                    previous_close,
                    candle.started_at,
                    candle.source_as_of,
                )
                point = _missing_point(candle, current_checkpoint)
            else:
                consecutive_count += 1
                close = candle.close
                closes.append(close)
                closes = closes[-60:]
                sma20 = (
                    _average(closes[-20:])
                    if consecutive_count >= 20
                    else None
                )
                sma60 = (
                    _average(closes[-60:])
                    if consecutive_count >= 60
                    else None
                )
                if consecutive_count == 20:
                    ema = sma20
                elif consecutive_count > 20 and ema is not None:
                    ema = ema + (close - ema) * Decimal(2) / Decimal(21)
                if previous_close is not None:
                    gain = max(Decimal(0), close - previous_close)
                    loss = max(Decimal(0), previous_close - close)
                    if consecutive_count == 15:
                        changes = [
                            current - prior
                            for prior, current in zip(
                                closes[-15:-1], closes[-14:], strict=True
                            )
                        ]
                        average_gain = _average([max(Decimal(0), value) for value in changes])
                        average_loss = _average([max(Decimal(0), -value) for value in changes])
                    elif (
                        consecutive_count > 15
                        and average_gain is not None
                        and average_loss is not None
                    ):
                        average_gain = (average_gain * Decimal(13) + gain) / Decimal(14)
                        average_loss = (average_loss * Decimal(13) + loss) / Decimal(14)
                previous_close = close
                rsi = _rsi(average_gain, average_loss)
                if sma20 is None:
                    upper = middle = lower = None
                else:
                    middle = sma20
                    variance = _average([(item - middle) ** 2 for item in closes[-20:]])
                    deviation = variance.sqrt()
                    upper = middle + Decimal(2) * deviation
                    lower = middle - Decimal(2) * deviation
                values = MappingProxyType(
                    {
                        "sma20": sma20,
                        "sma60": sma60,
                        "ema20": ema,
                        "bollingerUpper": upper,
                        "bollingerMiddle": middle,
                        "bollingerLower": lower,
                        "rsi14": rsi,
                    }
                )
                statuses: dict[str, IndicatorStatus] = {
                    "sma20": "ready" if sma20 is not None else "warming_up",
                    "sma60": "ready" if sma60 is not None else "warming_up",
                    "ema20": "ready" if ema is not None else "warming_up",
                    "bollinger20": "ready" if middle is not None else "warming_up",
                    "rsi14": "ready" if rsi is not None else "warming_up",
                }
                current_checkpoint = _checkpoint(
                    closes,
                    consecutive_count,
                    ema,
                    average_gain,
                    average_loss,
                    previous_close,
                    candle.started_at,
                    candle.source_as_of,
                )
                point = _point(candle, values, statuses, current_checkpoint)
            previous_started_at = candle.started_at
            if requested_from is None or point.started_at >= requested_from:
                result.append(point)
    return tuple(result)


def _missing_point(
    candle: CandleView, checkpoint_state: Mapping[str, object]
) -> IndicatorPoint:
    values = MappingProxyType(
        {
            "sma20": None,
            "sma60": None,
            "ema20": None,
            "bollingerUpper": None,
            "bollingerMiddle": None,
            "bollingerLower": None,
            "rsi14": None,
        }
    )
    statuses: dict[str, IndicatorStatus] = {key: "missing" for key in INDICATOR_DEFINITION_VERSIONS}
    return _point(candle, values, statuses, checkpoint_state)


def _point(
    candle: CandleView,
    values: Mapping[str, Decimal | None],
    statuses: Mapping[str, IndicatorStatus],
    checkpoint_state: Mapping[str, object],
) -> IndicatorPoint:
    return IndicatorPoint(
        started_at=candle.started_at,
        values=values,
        statuses=MappingProxyType(dict(statuses)),
        definition_version_hashes=MappingProxyType(
            {key: value.definition_hash for key, value in INDICATOR_DEFINITION_VERSIONS.items()}
        ),
        lineage_by_indicator=MappingProxyType({}),
        rollup_ids=((candle.rollup_id,) if candle.rollup_id is not None else ()),
        source_revision_through_id=candle.source_revision_through_id,
        quality_event_through_id=candle.quality_event_through_id,
        source_as_of=candle.source_as_of,
        knowledge_at=candle.knowledge_at,
        current_input_id=(
            candle.rollup_id
            if candle.rollup_id is not None
            else max(candle.input_revision_ids, default=None)
        ),
        current_input_is_rollup=candle.rollup_id is not None,
        checkpoint_state=MappingProxyType(dict(checkpoint_state)),
    )


def _checkpoint(
    closes: list[Decimal],
    consecutive_count: int,
    ema: Decimal | None,
    average_gain: Decimal | None,
    average_loss: Decimal | None,
    previous_close: Decimal | None,
    previous_started_at: datetime,
    source_as_of: datetime | None,
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "recentCloses": [_canonical(value) for value in closes[-60:]],
            "consecutiveCount": consecutive_count,
            "ema20": _canonical(ema),
            "rsiAverageGain": _canonical(average_gain),
            "rsiAverageLoss": _canonical(average_loss),
            "previousClose": _canonical(previous_close),
            "previousStartedAt": previous_started_at.isoformat(),
            "sourceAsOf": source_as_of.isoformat() if source_as_of is not None else None,
        }
    )


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _canonical(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return "0" if value == 0 else format(value.normalize(), "f")


def _average(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _rsi(average_gain: Decimal | None, average_loss: Decimal | None) -> Decimal | None:
    if average_gain is None or average_loss is None:
        return None
    if average_gain == 0 and average_loss == 0:
        return Decimal(50)
    if average_loss == 0:
        return Decimal(100)
    if average_gain == 0:
        return Decimal(0)
    return Decimal(100) - Decimal(100) / (Decimal(1) + average_gain / average_loss)


def _next_bucket(started_at: datetime, unit: str) -> datetime:
    if unit == "1M":
        if started_at.month == 12:
            return started_at.replace(year=started_at.year + 1, month=1)
        return started_at.replace(month=started_at.month + 1)
    durations = {
        "1m": timedelta(minutes=1),
        "3m": timedelta(minutes=3),
        "5m": timedelta(minutes=5),
        "10m": timedelta(minutes=10),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
        "1w": timedelta(days=7),
    }
    try:
        return started_at + durations[unit]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 지표 단위다: {unit}") from exc
