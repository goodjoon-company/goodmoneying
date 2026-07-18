from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from goodmoneying_shared.backtest_engine import (
    BacktestCandleEvent,
    BacktestEngineSpec,
    BacktestSignal,
    ExecutionModel,
    run_candle_backtest,
)


def test_P4_1_백테스트_엔진은_소비자_관점에서_재현_가능한_결과를_만든다() -> None:
    spec = BacktestEngineSpec(
        dataset_version_id=100,
        dataset_content_hash="d" * 64,
        strategy_version_id=200,
        strategy_graph_hash="a" * 64,
        engine_version="backtest-core-v1",
        parameter_hash="b" * 64,
        seed=13,
        initial_cash=Decimal("100000"),
        execution=ExecutionModel(
            fee_rate=Decimal("0.001"),
            slippage_bps=Decimal("5"),
            latency_seconds=60,
            max_participation_rate=Decimal("0.50"),
        ),
    )
    signals = (
        BacktestSignal(
            occurred_at=_at(0),
            knowledge_at=_at(0),
            side="buy",
            base_quantity=Decimal("3"),
        ),
        BacktestSignal(
            occurred_at=_at(2),
            knowledge_at=_at(2),
            side="sell",
            base_quantity=Decimal("1"),
        ),
    )
    candles = (
        _candle(2, close="120", volume="4"),
        _candle(0, close="100", volume="4"),
        _candle(3, close="130", volume="4"),
        _candle(1, close="110", volume="4"),
    )

    first = run_candle_backtest(spec, candles=candles, signals=signals)
    second = run_candle_backtest(spec, candles=reversed(candles), signals=signals)

    assert first.status == "succeeded"
    assert first.input_hash == second.input_hash
    assert first.result_hash == second.result_hash
    assert first.golden_replay_signals == signals
    assert [trade.status for trade in first.trades] == ["partially_filled", "filled"]
    assert first.metrics["finalEquity"] == Decimal("100039.474955")
    assert first.assumptions == (
        "orderbook_absent_uses_candle_close",
        "partial_fill_by_candle_volume_participation",
    )


def _candle(offset: int, *, close: str, volume: str) -> BacktestCandleEvent:
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
