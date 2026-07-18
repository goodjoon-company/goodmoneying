from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from goodmoneying_shared.backtest_engine import (
    BacktestCandleEvent,
    BacktestEngineSpec,
    BacktestSignal,
    ExecutionModel,
    run_candle_backtest,
)


def test_P4_1_동일_입력은_동일한_hash와_결과를_반환한다() -> None:
    spec = _spec()
    candles = [
        _candle(2, close="110", volume="10"),
        _candle(0, close="100", volume="10"),
        _candle(1, close="105", volume="10"),
    ]
    signals = [
        BacktestSignal(
            occurred_at=_at(0), knowledge_at=_at(0), side="buy", base_quantity=Decimal("2")
        )
    ]

    first = run_candle_backtest(spec, candles=candles, signals=signals)
    second = run_candle_backtest(spec, candles=reversed(candles), signals=signals)

    assert first.input_hash == second.input_hash
    assert first.result_hash == second.result_hash
    assert first.equity_points == second.equity_points
    assert [event.knowledge_at for event in first.replay_events] == [_at(0), _at(1), _at(2)]
    assert first.trades[0].fill_price == Decimal("105.105")
    assert first.trades[0].fee_paid == Decimal("0.210210")
    assert first.metrics["finalEquity"] == Decimal("1009.579790")


def test_P4_1_미래_데이터를_요구하는_신호는_거부한다() -> None:
    spec = _spec()
    signal = BacktestSignal(
        occurred_at=_at(2),
        knowledge_at=_at(1),
        side="buy",
        base_quantity=Decimal("1"),
    )

    result = run_candle_backtest(spec, candles=[_candle(0)], signals=[signal])

    assert result.status == "failed"
    assert result.errors == ("look_ahead_signal",)
    assert result.trades == ()


def test_P4_1_부분체결과_호가부재_가정을_결과에_기록한다() -> None:
    spec = _spec(max_participation_rate=Decimal("0.25"))
    signal = BacktestSignal(
        occurred_at=_at(0),
        knowledge_at=_at(0),
        side="buy",
        base_quantity=Decimal("3"),
    )

    result = run_candle_backtest(
        spec, candles=[_candle(0, volume="4"), _candle(1, volume="4")], signals=[signal]
    )

    assert result.status == "succeeded"
    assert result.trades[0].requested_quantity == Decimal("3")
    assert result.trades[0].filled_quantity == Decimal("1.00")
    assert result.trades[0].remaining_quantity == Decimal("2.00")
    assert result.trades[0].status == "partially_filled"
    assert result.assumptions == (
        "orderbook_absent_uses_candle_close",
        "partial_fill_by_candle_volume_participation",
    )


def test_P4_1_지연_이후_체결_가능한_사건이_없으면_체결하지_않는다() -> None:
    signal = BacktestSignal(
        occurred_at=_at(2),
        knowledge_at=_at(2),
        side="buy",
        base_quantity=Decimal("1"),
    )

    result = run_candle_backtest(
        _spec(), candles=[_candle(0), _candle(1), _candle(2)], signals=[signal]
    )

    assert result.status == "succeeded"
    assert result.trades == ()
    assert result.metrics["finalEquity"] == Decimal("1000")


def test_P4_1_float_입력은_Decimal_손실로_거부한다() -> None:
    invalid_candle = replace(_candle(1), close=cast(Decimal, 100.0))
    signal = BacktestSignal(
        occurred_at=_at(0),
        knowledge_at=_at(0),
        side="buy",
        base_quantity=Decimal("1"),
    )

    result = run_candle_backtest(_spec(), candles=[_candle(0), invalid_candle], signals=[signal])

    assert result.status == "failed"
    assert result.errors == ("decimal_required",)
    assert result.trades == ()


def test_P4_1_golden_replay_신호는_입력_신호와_동일하다() -> None:
    signals = (
        BacktestSignal(
            occurred_at=_at(0), knowledge_at=_at(0), side="buy", base_quantity=Decimal("1")
        ),
        BacktestSignal(
            occurred_at=_at(2), knowledge_at=_at(2), side="sell", base_quantity=Decimal("1")
        ),
    )

    result = run_candle_backtest(
        _spec(), candles=[_candle(0), _candle(1), _candle(2), _candle(3)], signals=signals
    )

    assert result.golden_replay_signals == signals
    assert result.trades[0].side == "buy"
    assert result.trades[1].side == "sell"


def _spec(max_participation_rate: Decimal = Decimal("1")) -> BacktestEngineSpec:
    return BacktestEngineSpec(
        dataset_version_id=12,
        dataset_content_hash="d" * 64,
        strategy_version_id=41,
        strategy_graph_hash="a" * 64,
        engine_version="backtest-core-v1",
        parameter_hash="b" * 64,
        seed=7,
        initial_cash=Decimal("1000"),
        execution=ExecutionModel(
            fee_rate=Decimal("0.001"),
            slippage_bps=Decimal("10"),
            latency_seconds=60,
            max_participation_rate=max_participation_rate,
        ),
    )


def _candle(offset: int, *, close: str = "100", volume: str = "10") -> BacktestCandleEvent:
    return BacktestCandleEvent(
        instrument_id=41,
        market_code="KRW-BTC",
        occurred_at=_at(offset),
        knowledge_at=_at(offset),
        stable_sequence=f"candle-{offset}",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal(volume),
        quality="available",
        content_hash=str(offset) * 64,
    )


def _at(minutes: int) -> datetime:
    return datetime(2026, 7, 18, 0, 0, tzinfo=UTC) + timedelta(minutes=minutes)
