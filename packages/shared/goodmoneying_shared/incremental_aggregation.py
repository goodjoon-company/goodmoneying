from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from goodmoneying_shared.aggregation import MATERIALIZED_AGGREGATION_UNITS, rollup_bucket_start
from goodmoneying_shared.models import CandleView

MAX_OUTPUT_BUCKETS_PER_JOB = 512


def rollup_result_content_hash_values(
    calculation_version: object,
    open_price: object,
    high_price: object,
    low_price: object,
    close_price: object,
    trade_volume: object,
    trade_amount: object,
    completeness: object,
    quality: object,
) -> str:
    decimals = (open_price, high_price, low_price, close_price, trade_volume, trade_amount)

    def canonical_decimal(value: object) -> str:
        number = Decimal(str(value))
        return "0" if number == 0 else format(number.normalize(), "f")

    canonical = "|".join(
        (
            str(calculation_version),
            *(canonical_decimal(value) for value in decimals),
            str(completeness),
            str(quality),
        )
    )
    return sha256(canonical.encode()).hexdigest()


def rollup_result_content_hash(item: CandleView) -> str:
    return rollup_result_content_hash_values(
        item.calculation_version,
        item.open,
        item.high,
        item.low,
        item.close,
        item.volume,
        item.trade_amount,
        item.completeness,
        item.quality,
    )


@dataclass(frozen=True)
class RollupInvalidationRange:
    unit: str
    start_at: datetime
    end_at: datetime
    output_bucket_count: int


def rollup_bucket_end(unit: str, bucket_start: datetime) -> datetime:
    if unit == "1M":
        if bucket_start.month == 12:
            return bucket_start.replace(year=bucket_start.year + 1, month=1)
        return bucket_start.replace(month=bucket_start.month + 1)
    durations = {
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
        return bucket_start + durations[unit]
    except KeyError as exc:
        raise ValueError(f"지원하지 않는 파생 집계 단위다: {unit}") from exc


def affected_rollup_ranges(
    changed_at: list[datetime],
    *,
    units: tuple[str, ...] = MATERIALIZED_AGGREGATION_UNITS,
) -> list[RollupInvalidationRange]:
    """변경된 원천 시각을 연속·상한이 있는 파생 버킷 범위로 압축한다."""

    if any(value.tzinfo is None or value.utcoffset() != timedelta(0) for value in changed_at):
        raise ValueError("변경 원천 시각은 UTC timezone-aware datetime이어야 한다.")
    result: list[RollupInvalidationRange] = []
    for unit in units:
        starts = sorted({rollup_bucket_start(unit, value.astimezone(UTC)) for value in changed_at})
        if not starts:
            continue
        group_start = starts[0]
        previous = starts[0]
        count = 1
        for current in starts[1:]:
            contiguous = current == rollup_bucket_end(unit, previous)
            if contiguous and count < MAX_OUTPUT_BUCKETS_PER_JOB:
                previous = current
                count += 1
                continue
            result.append(
                RollupInvalidationRange(unit, group_start, rollup_bucket_end(unit, previous), count)
            )
            group_start = previous = current
            count = 1
        result.append(
            RollupInvalidationRange(unit, group_start, rollup_bucket_end(unit, previous), count)
        )
    return result


def affected_rollup_ranges_for_interval(
    start_at: datetime,
    end_at: datetime,
    *,
    units: tuple[str, ...] = MATERIALIZED_AGGREGATION_UNITS,
) -> list[RollupInvalidationRange]:
    """연속 커버리지 전이를 파생 버킷별 최대 512개 작업 범위로 나눈다."""

    if (
        start_at.tzinfo is None
        or end_at.tzinfo is None
        or start_at.utcoffset() != timedelta(0)
        or end_at.utcoffset() != timedelta(0)
    ):
        raise ValueError("커버리지 범위는 UTC timezone-aware datetime이어야 한다.")
    if start_at >= end_at:
        return []
    result: list[RollupInvalidationRange] = []
    last_instant = end_at - timedelta(microseconds=1)
    for unit in units:
        current = rollup_bucket_start(unit, start_at)
        last = rollup_bucket_start(unit, last_instant)
        while current <= last:
            group_start = current
            count = 0
            while current <= last and count < MAX_OUTPUT_BUCKETS_PER_JOB:
                current = rollup_bucket_end(unit, current)
                count += 1
            result.append(RollupInvalidationRange(unit, group_start, current, count))
    return result
