from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from goodmoneying_api.schemas import CandleResponse


def indicator_points(candles: list[CandleResponse]) -> list[dict[str, str | None]]:
    closes = [Decimal(item.close) for item in candles]
    result: list[dict[str, str | None]] = []
    ema: Decimal | None = None
    for index, candle in enumerate(candles):
        window20 = closes[max(0, index - 19) : index + 1]
        window60 = closes[max(0, index - 59) : index + 1]
        sma20 = _average(window20) if len(window20) == 20 else None
        sma60 = _average(window60) if len(window60) == 60 else None
        ema = (
            closes[index] if ema is None else (closes[index] - ema) * Decimal(2) / Decimal(21) + ema
        )
        upper, middle, lower = _bollinger(window20) if len(window20) == 20 else (None, None, None)
        rsi = _rsi(closes[index - 14 : index + 1]) if index >= 14 else None
        result.append(
            {
                "startedAt": _rfc3339_utc(candle.startedAt),
                "sma20": _decimal_string(sma20),
                "sma60": _decimal_string(sma60),
                "ema20": _decimal_string(ema),
                "bollingerUpper": _decimal_string(upper),
                "bollingerMiddle": _decimal_string(middle),
                "bollingerLower": _decimal_string(lower),
                "rsi14": _decimal_string(rsi),
            }
        )
    return result


def _average(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _bollinger(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    middle = _average(values)
    variance = sum(((value - middle) ** 2 for value in values), Decimal("0")) / Decimal(len(values))
    deviation = variance.sqrt()
    return middle + Decimal(2) * deviation, middle, middle - Decimal(2) * deviation


def _rsi(values: list[Decimal]) -> Decimal:
    pairs = zip(values, values[1:], strict=False)
    gains = [max(Decimal("0"), current - previous) for previous, current in pairs]
    losses = [
        max(Decimal("0"), previous - current)
        for previous, current in zip(values, values[1:], strict=False)
    ]
    average_gain = _average(gains)
    average_loss = _average(losses)
    if average_loss == 0:
        return Decimal("100")
    return Decimal("100") - Decimal("100") / (Decimal("1") + average_gain / average_loss)


def _decimal_string(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _rfc3339_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
