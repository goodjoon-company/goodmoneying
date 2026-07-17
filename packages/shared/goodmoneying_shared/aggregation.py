from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from goodmoneying_shared.models import CandleView, SourceCandle

AGGREGATION_UNITS = ("1m", "3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d", "1w", "1M")
MATERIALIZED_AGGREGATION_UNITS = AGGREGATION_UNITS[1:]
CALCULATION_VERSION = "candle-rollup-v2"
SOURCE_FETCH_BATCH_SIZE = 1_000

CoverageQuality = Literal["available", "no_trade", "missing", "unavailable", "unverified"]


@dataclass(frozen=True)
class CoverageSlice:
    start_at: datetime
    end_at: datetime
    status: CoverageQuality


def rollup_bucket_start(unit: str, source_at: datetime) -> datetime:
    if source_at.tzinfo is None or source_at.utcoffset() is None:
        raise ValueError("캔들 시각은 UTC 오프셋을 포함해야 한다.")
    source_at = source_at.astimezone(UTC)
    minute_units = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
    if unit in minute_units:
        minute = source_at.minute - (source_at.minute % minute_units[unit])
        return source_at.replace(minute=minute, second=0, microsecond=0)
    if unit == "4h":
        return source_at.replace(
            hour=source_at.hour - source_at.hour % 4, minute=0, second=0, microsecond=0
        )
    if unit == "1d":
        return source_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "1w":
        return (source_at - timedelta(days=source_at.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    if unit == "1M":
        return source_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    raise ValueError(f"지원하지 않는 집계 단위다: {unit}")


def aggregate_candles(
    unit: str,
    source: list[SourceCandle],
    *,
    coverage: Sequence[CoverageSlice] = (),
) -> list[CandleView]:
    """원천 1분·일봉을 분석용 집계 봉으로 멱등 변환한다."""
    aliases = {"60m": "1h", "240m": "4h"}
    unit = aliases.get(unit, unit)
    if unit not in AGGREGATION_UNITS:
        raise ValueError(f"지원하지 않는 집계 단위다: {unit}")
    minute_units = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    source_1m: list[SourceCandle] = []
    source_1d: list[SourceCandle] = []
    for item in source:
        if item.candle_unit == "1m":
            source_1m.append(item)
        elif item.candle_unit == "1d":
            source_1d.append(item)
    if unit == "1m":
        return _to_candle_views(sorted(source_1m, key=lambda item: item.candle_start_at))
    if unit in minute_units:
        grouped: dict[datetime, list[SourceCandle | CandleView]] = {}
        bucket_size = minute_units[unit]
        for item in source_1m:
            bucket = rollup_bucket_start(unit, item.candle_start_at)
            grouped.setdefault(bucket, []).append(item)
        return _aggregate_groups(grouped, bucket_size, coverage, unit=unit)
    if unit == "1d":
        if source_1d:
            return _to_candle_views(source_1d)
        grouped_daily: dict[datetime, list[SourceCandle | CandleView]] = {}
        for item in source_1m:
            bucket = rollup_bucket_start("1d", item.candle_start_at)
            grouped_daily.setdefault(bucket, []).append(item)
        return _aggregate_groups(grouped_daily, 24 * 60, coverage, unit=unit)
    if unit in {"1w", "1M"}:
        daily: list[CandleView] = (
            _to_candle_views(source_1d) if source_1d else aggregate_candles("1d", source)
        )
        grouped_week_month: dict[datetime, list[CandleView]] = {}
        for daily_item in daily:
            if unit == "1w":
                week_start = daily_item.started_at - timedelta(days=daily_item.started_at.weekday())
                bucket = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                bucket = daily_item.started_at.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
            grouped_week_month.setdefault(bucket, []).append(daily_item)
        return _aggregate_groups(grouped_week_month, 7 if unit == "1w" else 0, coverage, unit=unit)
    raise ValueError(f"지원하지 않는 집계 단위다: {unit}")


def _to_candle_view(item: SourceCandle) -> CandleView:
    return CandleView(
        started_at=item.candle_start_at,
        open=item.open_price,
        high=item.high_price,
        low=item.low_price,
        close=item.close_price,
        volume=item.trade_volume,
        trade_amount=item.trade_amount,
        completeness="complete",
        source_as_of=item.collected_at,
        knowledge_at=item.knowledge_at or item.collected_at,
        input_content_hash=item.input_content_hash or _hash_inputs([item]),
        input_revision_ids=((item.revision_id,) if item.revision_id is not None else ()),
    )


def _to_candle_views(source: Sequence[SourceCandle]) -> list[CandleView]:
    return [_to_candle_view(item) for item in source]


def _aggregate_groups(
    grouped: Mapping[datetime, Sequence[SourceCandle | CandleView]],
    expected_size: int,
    coverage: Sequence[CoverageSlice],
    *,
    unit: str,
) -> list[CandleView]:
    result: list[CandleView] = []
    for bucket, items in sorted(grouped.items()):
        ordered = sorted(items, key=_started_at)
        required_size = (
            monthrange(bucket.year, bucket.month)[1] if expected_size == 0 else expected_size
        )
        missing_statuses = _missing_slot_statuses(
            bucket, required_size, ordered, coverage, unit=unit
        )
        quality = _worst_quality(missing_statuses)
        is_complete = len(ordered) == required_size or (
            len(ordered) < required_size
            and bool(missing_statuses)
            and all(status == "no_trade" for status in missing_statuses)
        )
        result.append(
            CandleView(
                started_at=bucket,
                open=_open(ordered[0]),
                high=max(_high(item) for item in ordered),
                low=min(_low(item) for item in ordered),
                close=_close(ordered[-1]),
                volume=sum((_volume(item) for item in ordered), Decimal("0")),
                trade_amount=sum((_trade_amount(item) for item in ordered), Decimal("0")),
                completeness="complete" if is_complete else "partial",
                source_as_of=max(
                    filter(None, (_source_as_of(item) for item in ordered)), default=None
                ),
                knowledge_at=max(
                    filter(None, (_knowledge_at(item) for item in ordered)), default=None
                ),
                input_content_hash=_hash_inputs(ordered),
                quality=quality,
                input_revision_ids=tuple(
                    sorted({revision_id for item in ordered for revision_id in _revision_ids(item)})
                ),
            )
        )
    return result


def _started_at(item: SourceCandle | CandleView) -> datetime:
    return item.candle_start_at if isinstance(item, SourceCandle) else item.started_at


def _open(item: SourceCandle | CandleView) -> Decimal:
    return item.open_price if isinstance(item, SourceCandle) else item.open


def _high(item: SourceCandle | CandleView) -> Decimal:
    return item.high_price if isinstance(item, SourceCandle) else item.high


def _low(item: SourceCandle | CandleView) -> Decimal:
    return item.low_price if isinstance(item, SourceCandle) else item.low


def _close(item: SourceCandle | CandleView) -> Decimal:
    return item.close_price if isinstance(item, SourceCandle) else item.close


def _volume(item: SourceCandle | CandleView) -> Decimal:
    return item.trade_volume if isinstance(item, SourceCandle) else item.volume


def _trade_amount(item: SourceCandle | CandleView) -> Decimal:
    return item.trade_amount if isinstance(item, SourceCandle) else item.trade_amount


def _source_as_of(item: SourceCandle | CandleView) -> datetime | None:
    return item.collected_at if isinstance(item, SourceCandle) else item.source_as_of


def _knowledge_at(item: SourceCandle | CandleView) -> datetime | None:
    if isinstance(item, SourceCandle):
        return item.knowledge_at or item.collected_at
    return item.knowledge_at


def _revision_ids(item: SourceCandle | CandleView) -> tuple[int, ...]:
    if isinstance(item, SourceCandle):
        return (item.revision_id,) if item.revision_id is not None else ()
    return item.input_revision_ids


def _hash_inputs(items: Sequence[SourceCandle | CandleView]) -> str:
    canonical = []
    for item in sorted(items, key=_started_at):
        canonical.append(
            {
                "at": _started_at(item).astimezone(UTC).isoformat(),
                "open": _canonical_decimal(_open(item)),
                "high": _canonical_decimal(_high(item)),
                "low": _canonical_decimal(_low(item)),
                "close": _canonical_decimal(_close(item)),
                "volume": _canonical_decimal(_volume(item)),
                "tradeAmount": _canonical_decimal(_trade_amount(item)),
                "sourceContentHash": (
                    item.input_content_hash
                    if isinstance(item, SourceCandle)
                    else item.input_content_hash
                )
                or None,
            }
        )
    payload = json.dumps(
        {"calculationVersion": CALCULATION_VERSION, "inputs": canonical},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _bucket_end(bucket: datetime, unit: str) -> datetime:
    if unit == "1M":
        if bucket.month == 12:
            return bucket.replace(year=bucket.year + 1, month=1)
        return bucket.replace(month=bucket.month + 1)
    if unit == "1w":
        return bucket + timedelta(days=7)
    if unit == "1d":
        return bucket + timedelta(days=1)
    minutes = {"3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
    return bucket + timedelta(minutes=minutes[unit])


def _missing_slot_statuses(
    bucket: datetime,
    required_size: int,
    items: Sequence[SourceCandle | CandleView],
    coverage: Sequence[CoverageSlice],
    *,
    unit: str,
) -> list[CoverageQuality]:
    slot_delta = timedelta(days=1) if unit in {"1w", "1M"} else timedelta(minutes=1)
    observed = {_started_at(item).astimezone(UTC) for item in items}
    statuses: list[CoverageQuality] = []
    for offset in range(required_size):
        slot_start = bucket + slot_delta * offset
        if slot_start in observed:
            continue
        slot_end = slot_start + slot_delta
        matches = [
            segment.status
            for segment in coverage
            if segment.start_at.astimezone(UTC) <= slot_start
            and segment.end_at.astimezone(UTC) >= slot_end
        ]
        statuses.append(_slot_quality(matches) if matches else "unverified")
    return statuses


def _slot_quality(statuses: Sequence[CoverageQuality]) -> CoverageQuality:
    for status in ("missing", "unavailable", "unverified", "available", "no_trade"):
        if status in statuses:
            return status
    return "unverified"


def _worst_quality(statuses: Sequence[CoverageQuality]) -> CoverageQuality:
    for status in ("missing", "unavailable", "unverified"):
        if status in statuses:
            return status
    if "available" in statuses:
        return "missing"
    return "available"
