from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, cast

from goodmoneying_shared.dataset_versions import CanonicalValue, canonical_payload_hash

BacktestQuality = Literal["available", "no_trade", "missing", "unavailable", "unverified"]
BacktestSide = Literal["buy", "sell"]
BacktestRunStatus = Literal["succeeded", "failed"]
BacktestTradeStatus = Literal["filled", "partially_filled", "rejected"]

_ORDERBOOK_ABSENT_ASSUMPTION = "orderbook_absent_uses_candle_close"
_PARTIAL_FILL_ASSUMPTION = "partial_fill_by_candle_volume_participation"
_BPS_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True, slots=True)
class ExecutionModel:
    fee_rate: Decimal
    slippage_bps: Decimal
    latency_seconds: int
    max_participation_rate: Decimal


@dataclass(frozen=True, slots=True)
class BacktestEngineSpec:
    dataset_version_id: int
    dataset_content_hash: str
    strategy_version_id: int
    strategy_graph_hash: str
    engine_version: str
    parameter_hash: str
    seed: int
    initial_cash: Decimal
    execution: ExecutionModel


@dataclass(frozen=True, slots=True)
class BacktestCandleEvent:
    instrument_id: int
    market_code: str
    occurred_at: datetime
    knowledge_at: datetime
    stable_sequence: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quality: BacktestQuality
    content_hash: str
    source_priority: int = 20


@dataclass(frozen=True, slots=True)
class BacktestSignal:
    occurred_at: datetime
    knowledge_at: datetime
    side: BacktestSide
    base_quantity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    side: BacktestSide
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    fill_price: Decimal
    fee_paid: Decimal
    status: BacktestTradeStatus
    occurred_at: datetime
    knowledge_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestEquityPoint:
    occurred_at: datetime
    knowledge_at: datetime
    cash: Decimal
    base_position: Decimal
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    status: BacktestRunStatus
    input_hash: str
    result_hash: str
    assumptions: tuple[str, ...]
    replay_events: tuple[BacktestCandleEvent, ...]
    trades: tuple[BacktestTrade, ...]
    equity_points: tuple[BacktestEquityPoint, ...]
    metrics: Mapping[str, Decimal]
    golden_replay_signals: tuple[BacktestSignal, ...]
    errors: tuple[str, ...] = ()


def run_candle_backtest(
    spec: BacktestEngineSpec,
    *,
    candles: Iterable[BacktestCandleEvent],
    signals: Iterable[BacktestSignal],
) -> BacktestResult:
    """캔들 사건과 사전 계산된 전략 신호를 결정론적으로 재생한다."""

    replay_events = tuple(sorted(candles, key=_event_sort_key))
    golden_replay_signals = tuple(signals)
    assumptions = (_ORDERBOOK_ABSENT_ASSUMPTION, _PARTIAL_FILL_ASSUMPTION)

    decimal_error = _decimal_validation_error(spec, replay_events, golden_replay_signals)
    if decimal_error is not None:
        input_hash = _invalid_input_hash(spec, decimal_error)
        return _result(
            status="failed",
            input_hash=input_hash,
            assumptions=assumptions,
            replay_events=replay_events,
            trades=(),
            equity_points=(),
            metrics={
                "finalEquity": (
                    spec.initial_cash if isinstance(spec.initial_cash, Decimal) else Decimal("0")
                )
            },
            golden_replay_signals=golden_replay_signals,
            errors=(decimal_error,),
        )

    input_hash = _input_hash(spec, replay_events, golden_replay_signals, assumptions)

    if any(_is_look_ahead_signal(signal) for signal in golden_replay_signals):
        return _result(
            status="failed",
            input_hash=input_hash,
            assumptions=assumptions,
            replay_events=replay_events,
            trades=(),
            equity_points=(),
            metrics={"finalEquity": spec.initial_cash},
            golden_replay_signals=golden_replay_signals,
            errors=("look_ahead_signal",),
        )

    cash = spec.initial_cash
    base_position = Decimal("0")
    trades: list[BacktestTrade] = []
    equity_points: list[BacktestEquityPoint] = []
    ordered_signals = sorted(enumerate(golden_replay_signals), key=_signal_sort_key)

    for _, signal in ordered_signals:
        event = _execution_event(spec, replay_events, signal)
        if event is None:
            continue

        requested_quantity = signal.base_quantity
        filled_quantity = min(
            requested_quantity, event.volume * spec.execution.max_participation_rate
        )
        remaining_quantity = requested_quantity - filled_quantity
        fill_price = _fill_price(spec.execution, event.close, signal.side)
        notional = fill_price * filled_quantity
        fee_paid = notional * spec.execution.fee_rate

        if signal.side == "buy":
            cash -= notional + fee_paid
            base_position += filled_quantity
        else:
            cash += notional - fee_paid
            base_position -= filled_quantity

        trade = BacktestTrade(
            side=signal.side,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            remaining_quantity=remaining_quantity,
            fill_price=fill_price,
            fee_paid=fee_paid,
            status="filled" if remaining_quantity == Decimal("0") else "partially_filled",
            occurred_at=event.occurred_at,
            knowledge_at=event.knowledge_at,
        )
        trades.append(trade)
        equity_points.append(
            BacktestEquityPoint(
                occurred_at=event.occurred_at,
                knowledge_at=event.knowledge_at,
                cash=cash,
                base_position=base_position,
                equity=cash + base_position * event.close,
            )
        )

    final_equity = _final_equity(spec, replay_events, cash, base_position)
    metrics = {"finalEquity": final_equity}
    return _result(
        status="succeeded",
        input_hash=input_hash,
        assumptions=assumptions,
        replay_events=replay_events,
        trades=tuple(trades),
        equity_points=tuple(equity_points),
        metrics=metrics,
        golden_replay_signals=golden_replay_signals,
    )


def _result(
    *,
    status: BacktestRunStatus,
    input_hash: str,
    assumptions: tuple[str, ...],
    replay_events: tuple[BacktestCandleEvent, ...],
    trades: tuple[BacktestTrade, ...],
    equity_points: tuple[BacktestEquityPoint, ...],
    metrics: Mapping[str, Decimal],
    golden_replay_signals: tuple[BacktestSignal, ...],
    errors: tuple[str, ...] = (),
) -> BacktestResult:
    result_hash = _result_hash(
        status=status,
        input_hash=input_hash,
        assumptions=assumptions,
        trades=trades,
        equity_points=equity_points,
        metrics=metrics,
        golden_replay_signals=golden_replay_signals,
        errors=errors,
    )
    return BacktestResult(
        status=status,
        input_hash=input_hash,
        result_hash=result_hash,
        assumptions=assumptions,
        replay_events=replay_events,
        trades=trades,
        equity_points=equity_points,
        metrics=metrics,
        golden_replay_signals=golden_replay_signals,
        errors=errors,
    )


def _event_sort_key(event: BacktestCandleEvent) -> tuple[datetime, int, str]:
    return (event.knowledge_at, event.source_priority, event.stable_sequence)


def _signal_sort_key(item: tuple[int, BacktestSignal]) -> tuple[datetime, datetime, int]:
    index, signal = item
    return (signal.knowledge_at, signal.occurred_at, index)


def _is_look_ahead_signal(signal: BacktestSignal) -> bool:
    return signal.knowledge_at < signal.occurred_at


def _decimal_validation_error(
    spec: BacktestEngineSpec,
    replay_events: Sequence[BacktestCandleEvent],
    signals: Sequence[BacktestSignal],
) -> str | None:
    values = [
        spec.initial_cash,
        spec.execution.fee_rate,
        spec.execution.slippage_bps,
        spec.execution.max_participation_rate,
    ]
    for event in replay_events:
        values.extend((event.open, event.high, event.low, event.close, event.volume))
    for signal in signals:
        values.append(signal.base_quantity)
    if any(not isinstance(value, Decimal) for value in values):
        return "decimal_required"
    return None


def _invalid_input_hash(spec: BacktestEngineSpec, error: str) -> str:
    payload = {
        "engineVersion": spec.engine_version,
        "datasetVersionId": spec.dataset_version_id,
        "strategyVersionId": spec.strategy_version_id,
        "error": error,
    }
    return canonical_payload_hash(cast(CanonicalValue, payload))


def _execution_event(
    spec: BacktestEngineSpec,
    replay_events: Sequence[BacktestCandleEvent],
    signal: BacktestSignal,
) -> BacktestCandleEvent | None:
    target_knowledge_at = signal.knowledge_at + timedelta(seconds=spec.execution.latency_seconds)
    return next(
        (
            event
            for event in replay_events
            if event.quality == "available" and event.knowledge_at >= target_knowledge_at
        ),
        None,
    )


def _fill_price(execution: ExecutionModel, candle_close: Decimal, side: BacktestSide) -> Decimal:
    slippage_ratio = execution.slippage_bps / _BPS_DENOMINATOR
    if side == "buy":
        return candle_close * (Decimal("1") + slippage_ratio)
    return candle_close * (Decimal("1") - slippage_ratio)


def _final_equity(
    spec: BacktestEngineSpec,
    replay_events: Sequence[BacktestCandleEvent],
    cash: Decimal,
    base_position: Decimal,
) -> Decimal:
    if not replay_events:
        return spec.initial_cash
    return cash + base_position * replay_events[-1].close


def _input_hash(
    spec: BacktestEngineSpec,
    replay_events: Sequence[BacktestCandleEvent],
    signals: Sequence[BacktestSignal],
    assumptions: Sequence[str],
) -> str:
    payload = {
        "spec": _spec_payload(spec),
        "events": [_event_payload(event) for event in replay_events],
        "signals": [_signal_payload(index, signal) for index, signal in enumerate(signals)],
        "assumptions": list(assumptions),
    }
    return canonical_payload_hash(cast(CanonicalValue, payload))


def _result_hash(
    *,
    status: BacktestRunStatus,
    input_hash: str,
    assumptions: Sequence[str],
    trades: Sequence[BacktestTrade],
    equity_points: Sequence[BacktestEquityPoint],
    metrics: Mapping[str, Decimal],
    golden_replay_signals: Sequence[BacktestSignal],
    errors: Sequence[str],
) -> str:
    payload = {
        "status": status,
        "inputHash": input_hash,
        "assumptions": list(assumptions),
        "trades": [_trade_payload(index, trade) for index, trade in enumerate(trades)],
        "equityPoints": [_equity_payload(point) for point in equity_points],
        "metrics": dict(sorted(metrics.items())),
        "goldenReplaySignals": [
            _signal_payload(index, signal) for index, signal in enumerate(golden_replay_signals)
        ],
        "errors": list(errors),
    }
    return canonical_payload_hash(cast(CanonicalValue, payload))


def _spec_payload(spec: BacktestEngineSpec) -> dict[str, CanonicalValue]:
    return {
        "datasetVersionId": spec.dataset_version_id,
        "datasetContentHash": spec.dataset_content_hash,
        "strategyVersionId": spec.strategy_version_id,
        "strategyGraphHash": spec.strategy_graph_hash,
        "engineVersion": spec.engine_version,
        "parameterHash": spec.parameter_hash,
        "seed": spec.seed,
        "initialCash": spec.initial_cash,
        "execution": {
            "fee_rate": spec.execution.fee_rate,
            "slippage_bps": spec.execution.slippage_bps,
            "latency_seconds": spec.execution.latency_seconds,
            "max_participation_rate": spec.execution.max_participation_rate,
        },
    }


def _event_payload(event: BacktestCandleEvent) -> dict[str, CanonicalValue]:
    return {
        "instrumentId": event.instrument_id,
        "marketCode": event.market_code,
        "occurredAt": cast(CanonicalValue, event.occurred_at),
        "knowledgeAt": cast(CanonicalValue, event.knowledge_at),
        "stableSequence": event.stable_sequence,
        "open": event.open,
        "high": event.high,
        "low": event.low,
        "close": event.close,
        "volume": event.volume,
        "quality": event.quality,
        "contentHash": event.content_hash,
        "sourcePriority": event.source_priority,
    }


def _signal_payload(index: int, signal: BacktestSignal) -> dict[str, CanonicalValue]:
    return {
        "sequence": index,
        "occurredAt": cast(CanonicalValue, signal.occurred_at),
        "knowledgeAt": cast(CanonicalValue, signal.knowledge_at),
        "side": signal.side,
        "baseQuantity": signal.base_quantity,
    }


def _trade_payload(index: int, trade: BacktestTrade) -> dict[str, CanonicalValue]:
    return {
        "sequence": index,
        "side": trade.side,
        "requestedQuantity": trade.requested_quantity,
        "filledQuantity": trade.filled_quantity,
        "remainingQuantity": trade.remaining_quantity,
        "fillPrice": trade.fill_price,
        "feePaid": trade.fee_paid,
        "status": trade.status,
        "occurredAt": cast(CanonicalValue, trade.occurred_at),
        "knowledgeAt": cast(CanonicalValue, trade.knowledge_at),
    }


def _equity_payload(point: BacktestEquityPoint) -> dict[str, CanonicalValue]:
    return {
        "occurredAt": cast(CanonicalValue, point.occurred_at),
        "knowledgeAt": cast(CanonicalValue, point.knowledge_at),
        "cash": point.cash,
        "basePosition": point.base_position,
        "equity": point.equity,
    }
