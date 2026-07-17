from datetime import UTC, datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.models import CandleView
from goodmoneying_shared.versioned_market_statistics import calculate_market_statistics


def _candles(closes: list[str]) -> list[CandleView]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        CandleView(
            started_at=start + timedelta(minutes=index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal(index + 1),
            trade_amount=Decimal(close) * Decimal(index + 1),
            completeness="complete",
            rollup_id=index + 1,
            source_revision_through_id=index + 1,
            source_as_of=start + timedelta(minutes=index, seconds=1),
            knowledge_at=start + timedelta(minutes=index, seconds=2),
        )
        for index, close in enumerate(closes)
    ]


def test_시장_통계는_20개_수익률과_21개_종가에서_변동성이_ready가_된다() -> None:
    points = calculate_market_statistics(_candles([str(value) for value in range(1, 22)]), "1m")

    assert points[0].close_return_1 is None
    assert points[0].return_status == "warming_up"
    assert points[1].close_return_1 == Decimal("1")
    assert points[19].volatility_status == "warming_up"
    assert points[19].volatility_sample_count == 19
    assert points[20].volatility_status == "ready"
    assert points[20].volatility_sample_count == 20
    assert points[20].realized_volatility_20 is not None
    assert points[20].realized_volatility_20 > 0
    assert points[20].input_completeness_ratio == Decimal("1")


def test_gap과_partial은_0을_합성하지_않고_연속성을_초기화한다() -> None:
    candles = _candles(["1"] * 25)
    candles[10] = CandleView(**{**candles[10].__dict__, "completeness": "partial"})
    points = calculate_market_statistics(candles, "1m")

    assert points[10].return_status == "missing"
    assert points[10].close_return_1 is None
    assert points[11].return_status == "warming_up"
    assert points[-1].volatility_status == "warming_up"


def test_통계_범위_filter는_warmup_입력을_버리지_않아_전체_계산과_같다() -> None:
    candles = _candles([str(value) for value in range(1, 41)])
    full = calculate_market_statistics(candles, "1m")
    requested = calculate_market_statistics(candles, "1m", requested_from=candles[30].started_at)

    assert requested == tuple(item for item in full if item.started_at >= candles[30].started_at)


def test_시장통계_checkpoint_최신_append는_전체_재계산과_같다() -> None:
    candles = _candles([str(value) for value in range(1, 42)])
    first = calculate_market_statistics(candles[:40], "1m")
    appended = calculate_market_statistics(
        candles[40:], "1m", initial_checkpoint=first[-1].checkpoint_state
    )
    full = calculate_market_statistics(candles, "1m")

    assert appended == full[40:]
