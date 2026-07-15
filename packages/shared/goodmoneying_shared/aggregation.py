from __future__ import annotations

from calendar import monthrange
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.models import CandleView, SourceCandle

AGGREGATION_UNITS = ("5m", "10m", "30m", "60m", "1d", "1w", "1M")
PROGRESS_INTERVAL = 1_000


def rollup_bucket_start(unit: str, source_at: datetime) -> datetime:
    minute_units = {"5m": 5, "10m": 10, "30m": 30, "60m": 60}
    if unit in minute_units:
        minute = source_at.minute - (source_at.minute % minute_units[unit])
        return source_at.replace(minute=minute, second=0, microsecond=0)
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
    on_progress: Callable[[], None] | None = None,
) -> list[CandleView]:
    """원천 1분·일봉을 분석용 집계 봉으로 멱등 변환한다."""
    minute_units = {"5m": 5, "10m": 10, "30m": 30, "60m": 60}
    _notify_progress(on_progress)
    source_1m: list[SourceCandle] = []
    source_1d: list[SourceCandle] = []
    for index, item in enumerate(source, start=1):
        if item.candle_unit == "1m":
            source_1m.append(item)
        elif item.candle_unit == "1d":
            source_1d.append(item)
        if index % PROGRESS_INTERVAL == 0:
            _notify_progress(on_progress)
    if unit in minute_units:
        grouped: dict[datetime, list[SourceCandle | CandleView]] = {}
        bucket_size = minute_units[unit]
        for index, item in enumerate(source_1m, start=1):
            minute = item.candle_start_at.minute - (item.candle_start_at.minute % bucket_size)
            bucket = item.candle_start_at.replace(minute=minute, second=0, microsecond=0)
            grouped.setdefault(bucket, []).append(item)
            if index % PROGRESS_INTERVAL == 0:
                _notify_progress(on_progress)
        return _aggregate_groups(grouped, bucket_size, on_progress)
    if unit == "1d":
        if source_1d:
            return _to_candle_views(source_1d, on_progress)
        grouped_daily: dict[datetime, list[SourceCandle | CandleView]] = {}
        for index, item in enumerate(source_1m, start=1):
            bucket = item.candle_start_at.replace(hour=0, minute=0, second=0, microsecond=0)
            grouped_daily.setdefault(bucket, []).append(item)
            if index % PROGRESS_INTERVAL == 0:
                _notify_progress(on_progress)
        return _aggregate_groups(grouped_daily, 24 * 60, on_progress)
    if unit in {"1w", "1M"}:
        daily: list[CandleView] = (
            _to_candle_views(source_1d, on_progress)
            if source_1d
            else aggregate_candles("1d", source, on_progress)
        )
        grouped_week_month: dict[datetime, list[CandleView]] = {}
        for index, daily_item in enumerate(daily, start=1):
            if unit == "1w":
                week_start = daily_item.started_at - timedelta(days=daily_item.started_at.weekday())
                bucket = week_start.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            else:
                bucket = daily_item.started_at.replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                )
            grouped_week_month.setdefault(bucket, []).append(daily_item)
            if index % PROGRESS_INTERVAL == 0:
                _notify_progress(on_progress)
        return _aggregate_groups(
            grouped_week_month, 7 if unit == "1w" else 0, on_progress
        )
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
    )


def _to_candle_views(
    source: Sequence[SourceCandle], on_progress: Callable[[], None] | None
) -> list[CandleView]:
    result: list[CandleView] = []
    for index, item in enumerate(source, start=1):
        result.append(_to_candle_view(item))
        if index % PROGRESS_INTERVAL == 0:
            _notify_progress(on_progress)
    _notify_progress(on_progress)
    return result


def _aggregate_groups(
    grouped: Mapping[datetime, Sequence[SourceCandle | CandleView]],
    expected_size: int,
    on_progress: Callable[[], None] | None = None,
) -> list[CandleView]:
    result: list[CandleView] = []
    for index, (bucket, items) in enumerate(sorted(grouped.items()), start=1):
        ordered = sorted(items, key=_started_at)
        required_size = (
            monthrange(bucket.year, bucket.month)[1] if expected_size == 0 else expected_size
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
                completeness="complete" if len(ordered) == required_size else "partial",
            )
        )
        if index % PROGRESS_INTERVAL == 0:
            _notify_progress(on_progress)
    _notify_progress(on_progress)
    return result


def _notify_progress(on_progress: Callable[[], None] | None) -> None:
    if on_progress is not None:
        on_progress()


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
